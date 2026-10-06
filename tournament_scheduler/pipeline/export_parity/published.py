"""Read-only parity verification of the currently published Pages bundle."""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path
from typing import Any

from .records import STATUS_NOT_CHECKABLE
from .verify import verify_export_parity

_REQUIRED = ("season_plan.html", "season_plan.xlsx", "season_plan_spond.xlsx")
_OPTIONAL = ("cancelled_tournaments.html",)


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        check=check,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def resolve_published_commit(
    repo_dir: str | Path = ".", *, branch: str = "gh-pages", remote: str = "origin"
) -> tuple[str, str]:
    """Resolve the remote Pages head when available, else a controlled local branch.

    ``ls-remote`` reads the hosting source directly. Fetching an otherwise
    unknown commit only populates the local object database; it never checks
    out, rewrites, commits, pushes, or republishes the Pages branch.
    """

    repo = Path(repo_dir).resolve()
    remote_head = _git(
        repo, "ls-remote", "--exit-code", remote, f"refs/heads/{branch}", check=False
    )
    if remote_head.returncode == 0 and remote_head.stdout.strip():
        commit = remote_head.stdout.decode("utf-8").split()[0]
        if _git(repo, "cat-file", "-e", f"{commit}^{{commit}}", check=False).returncode != 0:
            fetched = _git(repo, "fetch", "--quiet", "--no-tags", remote, commit, check=False)
            if fetched.returncode != 0:
                raise RuntimeError(f"could not fetch published commit {commit}")
        return commit, f"{remote}/{branch}"

    local = _git(repo, "rev-parse", "--verify", f"refs/heads/{branch}", check=False)
    if local.returncode == 0:
        return local.stdout.decode("utf-8").strip(), branch
    raise RuntimeError(f"could not resolve published branch {remote}/{branch}")


def _read_blob(repo: Path, commit: str, path: str, *, required: bool) -> bytes | None:
    result = _git(repo, "show", f"{commit}:{path}", check=False)
    if result.returncode == 0:
        return result.stdout
    if required:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"published artifact {path!r} is missing or unreadable: {detail}")
    return None


def verify_published_export_parity(
    *,
    repo_dir: str | Path = ".",
    branch: str = "gh-pages",
    remote: str = "origin",
    canonical_revision: str = "",
    canonical_lookup_failed: bool = False,
) -> dict[str, Any]:
    """Verify actual ``latest/`` artifact bytes at the resolved Pages commit.

    Artifact parity and canonical freshness are intentionally separate output
    dimensions. The parity verifier never receives the current canonical
    revision; it judges only mutual artifact semantics/provenance.
    """

    repo = Path(repo_dir).resolve()
    try:
        commit, source = resolve_published_commit(repo, branch=branch, remote=remote)
    except Exception as exc:  # noqa: BLE001 - diagnostic command fails closed
        return {
            "published_commit": None,
            "published_source": f"{remote}/{branch}",
            "artifact_parity": {"status": STATUS_NOT_CHECKABLE, "reasons": [{"code": "published_branch_unreadable", "message": str(exc)}]},
            "freshness": {"status": "UNKNOWN", "canonical_revision": canonical_revision or None, "published_revision": None},
        }

    try:
        with tempfile.TemporaryDirectory(prefix="rvv-published-parity-") as temporary:
            root = Path(temporary)
            for name in _REQUIRED:
                data = _read_blob(repo, commit, f"latest/{name}", required=True)
                assert data is not None
                (root / name).write_bytes(data)
            for name in _OPTIONAL:
                data = _read_blob(repo, commit, f"latest/{name}", required=False)
                if data is not None:
                    (root / name).write_bytes(data)
            parity = verify_export_parity(root, require_spond=True)
    except Exception as exc:  # noqa: BLE001
        parity = {
            "status": STATUS_NOT_CHECKABLE,
            "reasons": [{"code": "published_artifact_unreadable", "message": str(exc)}],
        }

    artifacts = [
        parity.get("primary") or {},
        parity.get("secondary") or {},
        parity.get("spond") or {},
    ]
    revisions = {str(item.get("season_revision") or "") for item in artifacts if item}
    revisions.discard("")
    published_revision = next(iter(revisions)) if len(revisions) == 1 else ""
    if canonical_lookup_failed or not canonical_revision or not published_revision:
        freshness_status = "UNKNOWN"
    elif published_revision == canonical_revision:
        freshness_status = "FRESH"
    else:
        freshness_status = "STALE"
    return {
        "published_commit": commit,
        "published_source": source,
        "artifact_parity": parity,
        "freshness": {
            "status": freshness_status,
            "canonical_revision": canonical_revision or None,
            "published_revision": published_revision or None,
        },
    }


__all__ = ["resolve_published_commit", "verify_published_export_parity"]
