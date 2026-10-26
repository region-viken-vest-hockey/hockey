"""Read-only publication history listing for the Pages branch (issue #20).

Moved out of :mod:`pages_publish` to keep that module within its file-length
baseline. It only reads the ``gh-pages`` commit log and is never used by the
publish or rollback path.
"""

from __future__ import annotations

from pathlib import Path

from .pages_publish import (
    PagesPublishError,
    _branch_exists_locally,
    _git,
    _require_git_repo_root,
)
from .pages_history import PUBLISH_COMMIT_RE, ROLLBACK_COMMIT_RE


def list_publication_history(*, repo_dir: str = ".", branch: str = "gh-pages") -> list[dict[str, str]]:
    """Read-only, newest-first publication history parsed from *branch*'s commit log.

    Each entry includes ``commit_sha``, ``date``, ``kind`` (``publish`` or
    ``rollback``), ``run_id`` and, when available, matching source export
    lifecycle metadata (path/fingerprint/status). Returns an empty list if
    *repo_dir* isn't a git repo or *branch* doesn't exist yet — never raises.
    """
    try:
        repo_root = _require_git_repo_root(repo_dir)
    except PagesPublishError:
        return []
    if not _branch_exists_locally(repo_root, branch):
        return []

    proc = _git(["log", branch, "--date=iso-strict", "--format=%H%x1f%ad%x1f%s"], cwd=repo_root)
    if proc.returncode != 0:
        return []

    source_exports: dict[str, dict[str, str]] = {}
    try:
        from tournament_scheduler.publication.export_lifecycle import find_export_manifests

        for record in find_export_manifests(Path(repo_root) / "export"):
            pages_run_id = str(record.get("pages_run_id") or "")
            if pages_run_id:
                source_exports[pages_run_id] = {
                    "source_export_dir": str(record.get("export_dir") or ""),
                    "source_export_fingerprint": str(record.get("export_fingerprint") or ""),
                    "source_export_status": str(record.get("lifecycle_status") or ""),
                }
    except Exception:
        source_exports = {}

    history: list[dict[str, str]] = []
    for line in proc.stdout.splitlines():
        parts = line.split("\x1f")
        if len(parts) != 3:
            continue
        sha, date, subject = parts
        publish_match = PUBLISH_COMMIT_RE.match(subject)
        rollback_match = ROLLBACK_COMMIT_RE.match(subject)
        if publish_match:
            run_id = publish_match.group(2)
            entry = {"commit_sha": sha, "date": date, "kind": "publish", "run_id": run_id}
            entry.update(source_exports.get(run_id, {}))
            history.append(entry)
        elif rollback_match:
            run_id = rollback_match.group(2)
            entry = {"commit_sha": sha, "date": date, "kind": "rollback", "run_id": run_id}
            entry.update(source_exports.get(run_id, {}))
            history.append(entry)
    if history:
        current_run_id = history[0].get("run_id")
        for entry in history:
            entry["current"] = "true" if entry.get("run_id") == current_run_id else "false"
    return history
