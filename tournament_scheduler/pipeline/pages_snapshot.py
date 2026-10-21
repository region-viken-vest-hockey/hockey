"""Immutable ``/runs/<run-id>/`` snapshot verification (republish safety).

Read-only check of the retained snapshot on ``gh-pages`` that a republish or
rollback relies on. Split out of :mod:`pages_publish` to keep that module within
its file-length baseline; it never writes to the branch.
"""

from __future__ import annotations

import json
import subprocess
from typing import Any

from .pages_publish import (
    _GIT_TIMEOUT_SECONDS,
    PagesPublishError,
    _git,
    _refresh_and_resolve_ref,
    _require_git_repo_root,
)


def verify_published_run_snapshot(
    run_id: str,
    *,
    repo_dir: str = ".",
    branch: str = "gh-pages",
    remote: str = "origin",
    expected_bundle_fingerprint: str | None = None,
) -> dict[str, Any]:
    """Verify one immutable ``/runs/<run_id>/`` snapshot is retained on *branch*.

    Refreshes the authoritative remote target before inspecting it (falling back
    to a local branch only when no usable remote exists, e.g. a local-only
    ``push=False`` publication). A retained snapshot is only trustworthy when its
    embedded ``_meta.json`` names the same run and the recorded public-bundle
    fingerprint, so a stale local ref or a different bundle cannot masquerade as
    the rollback target. Read-only: it fetches and inspects, never writes.

    Returns a report with ``verifiable``/``retained`` booleans and ``problems``.
    ``verifiable=False`` (not a git repo, no inspectable target) and any
    non-empty ``problems`` must be treated as blocking by callers.
    """

    report: dict[str, Any] = {
        "run_id": str(run_id or ""),
        "verifiable": False,
        "retained": False,
        "ref": None,
        "meta_run_id": None,
        "meta_bundle_fingerprint": None,
        "problems": [],
    }
    if not report["run_id"]:
        report["problems"].append("no previous run id to verify")
        return report
    try:
        repo_root = _require_git_repo_root(repo_dir)
    except PagesPublishError as exc:
        report["problems"].append(f"not a git repository: {exc}")
        return report

    compare_ref = _refresh_and_resolve_ref(repo_root, remote=remote, branch=branch)
    if compare_ref is None:
        report["problems"].append(
            f"branch '{branch}' could not be inspected locally or on remote '{remote}'"
        )
        return report
    report["verifiable"] = True
    report["ref"] = compare_ref

    entries = _git(["ls-tree", "-r", "--name-only", compare_ref, f"runs/{run_id}/"], cwd=repo_root)
    if entries.returncode != 0 or not entries.stdout.strip():
        report["problems"].append(f"no immutable /runs/{run_id}/ snapshot on '{branch}'")
        return report
    report["retained"] = True

    meta_proc = subprocess.run(
        ["git", "show", f"{compare_ref}:runs/{run_id}/_meta.json"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        timeout=_GIT_TIMEOUT_SECONDS,
    )
    if meta_proc.returncode != 0:
        report["problems"].append(
            f"/runs/{run_id}/_meta.json is missing; the retained bundle identity cannot be verified"
        )
        return report
    try:
        meta = json.loads(meta_proc.stdout)
    except json.JSONDecodeError:
        report["problems"].append(f"/runs/{run_id}/_meta.json is malformed")
        return report
    if not isinstance(meta, dict):
        report["problems"].append(f"/runs/{run_id}/_meta.json is not an object")
        return report
    report["meta_run_id"] = str(meta.get("run_id") or "")
    report["meta_bundle_fingerprint"] = str(meta.get("bundle_fingerprint") or "")
    if report["meta_run_id"] != report["run_id"]:
        report["problems"].append(
            f"/runs/{run_id}/_meta.json names run {report['meta_run_id']!r}, not the requested run"
        )
    if expected_bundle_fingerprint and report["meta_bundle_fingerprint"] != expected_bundle_fingerprint:
        report["problems"].append(
            "retained bundle fingerprint does not match the recorded publication "
            f"({report['meta_bundle_fingerprint']!r} != {expected_bundle_fingerprint!r})"
        )
    return report
