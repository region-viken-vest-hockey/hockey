"""Single-instance protection for one workspace, with clean shutdown.

An exclusive ``flock`` on a lock file in the workspace's private state directory
is the whole mechanism: the kernel drops it when the process dies, so a crash
never leaves a stale lock that blocks the next start. The lock file records only
the port (never a token).
"""

from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path

STATE_DIR = ".operate"


class InstanceRunningError(RuntimeError):
    pass


class WorkspaceInstance:
    def __init__(self, workspace: Path) -> None:
        self.state_dir = Path(workspace) / STATE_DIR
        self._fh = None

    @property
    def bootstrap_file(self) -> Path:
        return self.state_dir / "bootstrap"

    def acquire(self) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        fh = open(self.state_dir / "instance.lock", "a+")
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            fh.close()
            raise InstanceRunningError(f"another operate instance already runs on {self.state_dir.parent}") from None
        self._fh = fh

    def publish(self, port: int, bootstrap_token: str) -> None:
        """Record the port and a 0600 bootstrap file for the local launcher only."""
        self._fh.seek(0)
        self._fh.truncate()
        self._fh.write(json.dumps({"pid": os.getpid(), "port": port}))
        self._fh.flush()
        path = self.bootstrap_file
        if path.is_symlink():
            raise OSError(f"refusing to write the bootstrap token through a symlink: {path}")
        tmp = path.with_name(path.name + ".tmp")
        tmp.unlink(missing_ok=True)
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "w") as out:
            out.write(bootstrap_token)
        os.replace(tmp, path)  # atomic: a reader never sees a partial or wrong-mode token file

    def release(self) -> None:
        self.bootstrap_file.unlink(missing_ok=True)
        if self._fh is not None:
            fcntl.flock(self._fh, fcntl.LOCK_UN)
            self._fh.close()
            self._fh = None
