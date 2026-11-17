#!/usr/bin/env python3
"""Refresh and verify the committed workflow contract used by ``operate``.

This gate updates a tracked-clean local ``main`` only by fast-forwarding it to
the freshly fetched ``origin/main``. Unrelated untracked artifacts are allowed;
Git still refuses a merge that would overwrite one. The gate never resets,
reverts, cleans, stashes, or otherwise discards local work.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
from typing import Any

_READINESS_FILE = (
    Path(__file__).resolve().parent.parent
    / "tournament_scheduler"
    / "application"
    / "ports"
    / "readiness.py"
)


def _load_readiness() -> Any:
    """Load the stdlib-only readiness contract without importing the package."""
    spec = importlib.util.spec_from_file_location("rvv_readiness_contract", _READINESS_FILE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


readiness = _load_readiness()
Reason = readiness.ReadinessReason
BACKEND = "git"


def _git(
    root: Path, *args: str, allowed: tuple[int, ...] = (0,)
) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        ["git", *args],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    if completed.returncode not in allowed:
        raise RuntimeError(f"git {args[0]} exited with status {completed.returncode}")
    return completed


def _value(root: Path, *args: str) -> str:
    return _git(root, *args).stdout.strip()


def prepare(root: Path) -> dict[str, Any]:
    """Fetch ``origin/main`` and safely make a clean local main current."""
    root = root.resolve()
    report: dict[str, Any] = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "REVIEW_REQUIRED",
        "branch": None,
        "local_head_before": None,
        "local_head": None,
        "origin_main": None,
        "refresh_performed": False,
        "update_performed": False,
        "workflow_commit": None,
        "reason": None,
        "reason_code": None,
        "backend": BACKEND,
        "workflow_version": None,
        "readiness_schema_version": readiness.READINESS_SCHEMA_VERSION,
    }

    def refuse(code: Any, reason: str) -> dict[str, Any]:
        report["reason"] = reason
        report["reason_code"] = code.value
        return report

    try:
        _git(
            root,
            "fetch",
            "--no-tags",
            "origin",
            "refs/heads/main:refs/remotes/origin/main",
        )
        report["refresh_performed"] = True
    except (OSError, RuntimeError, subprocess.TimeoutExpired):
        return refuse(
            Reason.REFRESH_FAILED,
            "Could not fetch origin/main; repository freshness is unverified."
        )

    try:
        report["branch"] = _value(root, "branch", "--show-current") or None
        report["local_head_before"] = _value(root, "rev-parse", "HEAD")
        report["local_head"] = report["local_head_before"]
        report["origin_main"] = _value(root, "rev-parse", "refs/remotes/origin/main")
        tracked_dirty = bool(
            _value(root, "status", "--porcelain", "--untracked-files=no")
        )
    except (OSError, RuntimeError, subprocess.TimeoutExpired):
        return refuse(
            Reason.INSPECTION_FAILED,
            "Could not inspect the local checkout after refreshing origin/main."
        )

    if report["branch"] != "main":
        return refuse(
            Reason.NOT_ON_DEFAULT_LINE,
            f"Current branch is {report['branch'] or 'detached HEAD'}, not main; "
            "no automatic branch switch or reset was attempted."
        )
    if tracked_dirty:
        return refuse(
            Reason.LOCAL_CHANGES,
            "Tracked files or the index are dirty; no automatic stash, reset, "
            "revert, clean, or update was attempted."
        )

    if report["local_head"] != report["origin_main"]:
        try:
            ancestor = _git(
                root,
                "merge-base",
                "--is-ancestor",
                str(report["local_head"]),
                str(report["origin_main"]),
                allowed=(0, 1),
            )
            if ancestor.returncode != 0:
                return refuse(
                    Reason.DIVERGED,
                    "Local main has diverged from origin/main; a safe fast-forward "
                    "is not possible."
                )
            _git(root, "merge", "--ff-only", "--no-edit", "refs/remotes/origin/main")
            report["update_performed"] = True
            report["local_head"] = _value(root, "rev-parse", "HEAD")
        except (OSError, RuntimeError, subprocess.TimeoutExpired):
            return refuse(
                Reason.UPDATE_FAILED,
                "The clean local main could not be fast-forwarded; no destructive "
                "recovery was attempted."
            )

    try:
        final_branch = _value(root, "branch", "--show-current") or None
        final_tracked_dirty = bool(
            _value(root, "status", "--porcelain", "--untracked-files=no")
        )
        final_head = _value(root, "rev-parse", "HEAD")
    except (OSError, RuntimeError, subprocess.TimeoutExpired):
        return refuse(
            Reason.INSPECTION_FAILED,
            "Could not verify the checkout after the freshness update.",
        )

    report["branch"] = final_branch
    report["local_head"] = final_head
    if (
        final_branch != "main"
        or final_tracked_dirty
        or final_head != report["origin_main"]
    ):
        return refuse(
            Reason.CHANGED_DURING_CHECK,
            "Checkout changed during the freshness gate; do not run a repository-owned workflow."
        )

    report["status"] = "READY"
    report["workflow_commit"] = final_head
    report["workflow_version"] = final_head
    report["reason_code"] = Reason.READY_CURRENT.value
    report["reason"] = (
        "Local main is tracked-clean and equals the freshly fetched origin/main."
    )
    # Validate the verdict against the backend-neutral contract before reporting it.
    readiness.ReadinessReport(
        backend=BACKEND,
        status=readiness.ReadinessStatus.READY,
        reason_code=Reason.READY_CURRENT,
        detail=report["reason"],
        workflow_version=final_head,
    )
    return report


def render(report: dict[str, Any]) -> str:
    return "\n".join(
        (
            f"Operate preflight: {report['status']}",
            f"branch: {report['branch'] or 'UNVERIFIED'}",
            f"local_head: {report['local_head'] or 'UNVERIFIED'}",
            f"origin_main: {report['origin_main'] or 'UNVERIFIED'}",
            f"refresh_performed: {str(report['refresh_performed']).lower()}",
            f"update_performed: {str(report['update_performed']).lower()}",
            f"workflow_commit: {report['workflow_commit'] or 'UNVERIFIED'}",
            f"reason_code: {report['reason_code']}",
            f"reason: {report['reason']}",
        )
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root", type=Path, default=Path(__file__).resolve().parent.parent
    )
    parser.add_argument(
        "--json", action="store_true", help="Emit the structured freshness report"
    )
    args = parser.parse_args()
    result = prepare(args.root)
    print(json.dumps(result, indent=2, sort_keys=True) if args.json else render(result))
    return 0 if result["status"] == "READY" else 1


if __name__ == "__main__":
    raise SystemExit(main())
