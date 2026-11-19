"""Real-process lifecycle: start, single instance, SIGKILL recovery, clean shutdown."""

from __future__ import annotations

import signal
import subprocess
import sys
from pathlib import Path

from tests.local_api_support import SEASON, make_workspace


def _start(ws: Path):
    proc = subprocess.Popen([sys.executable, "-m", "tournament_scheduler.local_api", "--workspace", str(ws),
                             "--season", SEASON, "--no-browser"], stdout=subprocess.PIPE, text=True)
    line = proc.stdout.readline()
    return proc, int(line.split("http://127.0.0.1:")[1].split("/")[0])


def _tree(ws: Path):
    return {str(p): p.read_bytes() for p in sorted((ws / "season").rglob("*")) if p.is_file() and ".lock" not in p.name}


def test_start_refuses_second_instance_survives_sigkill_and_shuts_down_cleanly(tmp_path):
    ws = make_workspace(tmp_path)
    before = _tree(ws)
    first, port = _start(ws)
    try:
        assert (ws / ".operate" / "bootstrap").exists()
        second = subprocess.run([sys.executable, "-m", "tournament_scheduler.local_api", "--workspace", str(ws),
                                 "--season", SEASON, "--no-browser"], capture_output=True, text=True, timeout=60)
        assert second.returncode == 2 and "already runs" in second.stdout
        first.send_signal(signal.SIGKILL)  # process interruption: lock must not outlive the process
        first.wait(10)
        again, port2 = _start(ws)
        again.send_signal(signal.SIGTERM)
        assert again.wait(20) == 0
        assert not (ws / ".operate" / "bootstrap").exists()
    finally:
        if first.poll() is None:
            first.kill()
    assert _tree(ws) == before  # starting, killing and restarting never touched season data
