"""Publish the activity calendar as its own scope, without a reviewed season export.

The activity calendar is maintained separately from the season schedule, so its publish
checks the staged Pages snapshot itself rather than asking for a Stage 4 export:

- only ``latest/activities.json`` and ``latest/activities/index.html`` may differ from the
  live gh-pages base;
- every other live file must be present and byte-identical, with no deletions, new files or
  symlinks in the snapshot;
- the two activity files must pass the public text checks (secrets, contact details, paths);
- the snapshot digest must equal the approved digest when one is given, so an edit after
  approval blocks publication.

The season publish keeps its full gates in ``operator_action``; this module is reached only
for the ``activities`` publication scope.
"""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path
from typing import Any

from .capability_result import CapabilityResult
from .pages_bundle import _find_secrets, _redact_contact_and_paths

# Paths relative to the Pages ``latest/`` root.
ACTIVITY_FILES: tuple[str, ...] = ("activities.json", "activities/index.html")
_LATEST = "latest/"


class ActivityScopeError(RuntimeError):
    """Raised when the staged snapshot cannot be read safely."""


def _blob_sha(data: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def snapshot_files(snapshot_dir: str | Path) -> dict[str, str]:
    """Return ``{relative posix path: git blob sha}`` for every file in *snapshot_dir*."""

    root = Path(snapshot_dir)
    files: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root).as_posix()
        if path.is_symlink():
            raise ActivityScopeError(f"symlink i publiseringsbunnen: {rel}")
        if path.is_file():
            files[rel] = _blob_sha(path.read_bytes())
    return files


def snapshot_digest(snapshot_dir: str | Path) -> str:
    """Return one digest over the whole staged snapshot (paths and file contents)."""

    files = snapshot_files(snapshot_dir)
    payload = "".join(f"{rel}\0{sha}\n" for rel, sha in sorted(files.items()))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def live_files(repo_dir: str | Path, ref: str) -> dict[str, str]:
    """Return ``{path relative to latest/: blob sha}`` for the live Pages base at *ref*."""

    proc = subprocess.run(
        ["git", "ls-tree", "-r", "-z", ref, "--", "latest"],
        cwd=repo_dir,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        raise ActivityScopeError(f"kunne ikke lese {ref}: {proc.stderr.decode('utf-8', 'replace').strip()}")
    files: dict[str, str] = {}
    for record in proc.stdout.split(b"\0"):
        if not record:
            continue
        meta, _, path = record.decode("utf-8").partition("\t")
        _mode, _kind, sha = meta.split(" ")
        if path.startswith(_LATEST):
            files[path[len(_LATEST):]] = sha
    return files


def integrity_problems(
    snapshot_dir: str | Path,
    base_files: dict[str, str],
    expected_digest: str | None,
) -> list[str]:
    """Return every reason the snapshot must not be published. An empty list means it may be."""

    snap = snapshot_files(snapshot_dir)
    problems: list[str] = []
    for rel in ACTIVITY_FILES:
        if rel not in snap:
            problems.append(f"mangler {rel}")
    for rel, sha in base_files.items():
        if rel in ACTIVITY_FILES:
            continue
        if rel not in snap:
            problems.append(f"sletter {rel}")
        elif snap[rel] != sha:
            problems.append(f"endret {rel}")
    for rel in snap:
        if rel not in ACTIVITY_FILES and rel not in base_files:
            problems.append(f"ny fil {rel}")
    if expected_digest is not None and snapshot_digest(snapshot_dir) != expected_digest:
        problems.append("snapshot-digest endret etter godkjenning")
    return problems


def public_text_findings(text: str) -> list[str]:
    """Return why *text* is not safe to publish as-is: secrets, contact details or local paths."""

    findings = [name for name, _ in _find_secrets(text, frozenset())]
    _, redactions = _redact_contact_and_paths(text)
    findings.extend(f"{category}:{count}" for category, count in redactions)
    return findings


def privacy_problems(snapshot_dir: str | Path) -> list[str]:
    """Return public-text findings in the two activity files."""

    problems: list[str] = []
    for rel in ACTIVITY_FILES:
        path = Path(snapshot_dir) / rel
        if not path.is_file():
            continue
        findings = public_text_findings(path.read_text(encoding="utf-8"))
        if findings:
            problems.append(f"{rel}: {', '.join(findings)}")
    return problems


def execute_activity_publish(
    *,
    work_dir: str,
    export_dir: str | None,
    repo_dir: str = ".",
    branch: str = "gh-pages",
    remote: str = "origin",
    push: bool = True,
    confirm_public: bool = False,
    dry_run: bool = False,
    verify: bool = True,
    run_id: str | None = None,
    expected_export_content_sha256: str | None = None,
    verify_max_attempts: int | None = None,
    verify_retry_delay_seconds: float | None = None,
    fetch: Any = None,
    sleep: Any = None,
) -> CapabilityResult:
    """Publish a staged activity snapshot through the shared Pages publisher."""

    from . import pages_publish, pages_verify

    if not export_dir:
        return CapabilityResult.failed(
            "Aktivitetspublisering krever en eksplisitt eksportmappe.", capability="pages_publish"
        )
    if not confirm_public:
        return CapabilityResult.blocked(
            "Aktivitetskalenderen publiseres ikke uten --confirm-public.", capability="pages_publish"
        )

    ref = f"{remote}/{branch}"
    if push:
        fetched = subprocess.run(["git", "fetch", remote, branch], cwd=repo_dir, capture_output=True, check=False)
        if fetched.returncode != 0:
            return CapabilityResult.failed(
                f"Kunne ikke hente {ref}: {fetched.stderr.decode('utf-8', 'replace').strip()}",
                capability="pages_publish",
            )
    base_rev = _rev(repo_dir, ref)
    try:
        base = live_files(repo_dir, ref)
        problems = integrity_problems(export_dir, base, expected_export_content_sha256)
    except (ActivityScopeError, OSError, UnicodeDecodeError) as exc:
        return CapabilityResult.failed(f"Aktivitetspublisering avbrutt: {exc}", capability="pages_publish")
    if problems:
        return CapabilityResult.failed(
            "Publisering blokkert: snapshot-kontrollen fant uventede endringer: " + "; ".join(problems),
            capability="pages_publish",
            problems=problems,
        )

    privacy = privacy_problems(export_dir)
    if privacy:
        return CapabilityResult.blocked(
            "Publisering blokkert: aktivitetsfilene inneholder ikke-offentlig innhold.",
            capability="pages_publish",
            problems=privacy,
        )

    digest = snapshot_digest(export_dir)
    if dry_run:
        return CapabilityResult.ok(
            f"Tørrkjøring: aktivitetssnapshot {digest[:12]} er gyldig og ville blitt publisert.",
            capability="pages_publish",
            evidence=[f"snapshot_digest={digest}"],
        )

    if push:
        # Narrow the window in which gh-pages can move under the snapshot. It cannot be
        # closed entirely here: the shared publisher fetches and fast-forwards on its own.
        refetched = subprocess.run(["git", "fetch", remote, branch], cwd=repo_dir, capture_output=True, check=False)
        if refetched.returncode != 0 or _rev(repo_dir, ref) != base_rev:
            return CapabilityResult.failed(
                f"{ref} endret seg under publiseringen. Kjør aktivitetspublisering på nytt.",
                capability="pages_publish",
            )

    result = pages_publish.publish(
        export_dir=export_dir,
        run_id=run_id or "activities",
        repo_dir=repo_dir,
        branch=branch,
        remote=remote,
        push=push,
        bundle_fingerprint=digest,
    )
    result.evidence = list(result.evidence) + [f"snapshot_digest={digest}", "publication_scope=activities"]

    if push and verify and result.status in ("ok", "warning"):
        urls = [a for a in result.artifacts if isinstance(a, str) and a.startswith("http")]
        if urls:
            verify_kwargs: dict[str, Any] = {}
            if verify_max_attempts is not None:
                verify_kwargs["max_attempts"] = verify_max_attempts
            if verify_retry_delay_seconds is not None:
                verify_kwargs["retry_delay_seconds"] = verify_retry_delay_seconds
            if fetch is not None:
                verify_kwargs["fetch"] = fetch
            if sleep is not None:
                verify_kwargs["sleep"] = sleep
            verified = pages_verify.verify_publication(
                latest_url=urls[0],
                run_url=urls[1] if len(urls) > 1 else None,
                bundle_fingerprint=digest,
                run_id=run_id or "activities",
                published_at="",
                **verify_kwargs,
            )
            result.evidence = list(result.evidence) + [f"verify_status={verified.status}"] + list(verified.evidence)
            if verified.status != "ok":
                result.status = verified.status
                result.summary = f"{result.summary} {verified.summary}"
    return result


def _rev(repo_dir: str | Path, ref: str) -> str:
    proc = subprocess.run(["git", "rev-parse", ref], cwd=repo_dir, capture_output=True, text=True, check=False)
    return proc.stdout.strip()
