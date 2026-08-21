"""Operator-driven GitHub Pages publishing (issue #17).

Lets the operator publish an already-exported season plan (Stage 4 output)
to a dedicated ``gh-pages`` branch without running the planning pipeline in
GitHub Actions. This module only moves already-produced files into a Pages
branch and commits/pushes them — it does not generate or sanitize content
(see issue #18 for a sanitized public bundle). It also does not decide
*whether* a given publish should be allowed to proceed — :func:`publish`
always writes when called; the bundle-fingerprint/target-fingerprint
identity and per-bundle approval gate live in
``operator_action._execute_publish_pages`` (see issue #19). What this
module provides for that gate: :func:`bundle_fingerprint` and
:func:`target_fingerprint` (a stable identity for "this exact content to
this exact place") and :func:`diff_latest` (a read-only preview of what a
publish would change, for showing a human before they approve it).

Publish layout on the ``gh-pages`` branch::

    /index.html          redirect to /latest/
    /.nojekyll
    /latest/...           overwritten on every publish
    /runs/<run-id>/...    written once per run id, never removed

Implementation notes:

- A short-lived ``git worktree`` is used so publishing never touches the
  caller's current checkout (working tree, index, or branch).
- History is preserved: pushes are always plain fast-forward pushes, never
  ``--force``. A local branch that has diverged from its remote is reported
  as a failure rather than silently overwritten.
- Every operation returns a :class:`~.capability_result.CapabilityResult`
  instead of raising, matching every other operator action executor.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from tournament_scheduler.html.templates import PAGES_EMPTY_INDEX, PAGES_ROOT_INDEX

from .capability_result import CapabilityResult

_GIT_TIMEOUT_SECONDS = 120

# These assets are published independently from the season-plan bundle.
# A season-plan publish must never delete them from /latest/.
_PRESERVED_LATEST_PATHS = ("activities", "activities.json", "registered-teams")


class PagesPublishError(RuntimeError):
    """A git operation needed to set up the Pages branch could not proceed.

    Raised only for setup problems (e.g. not a git repo at all); everything
    past that point is reported as a :class:`CapabilityResult` instead, so
    callers never need to catch this directly.
    """


def _git(args: list[str], *, cwd: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=_GIT_TIMEOUT_SECONDS,
    )


def _require_git_repo_root(repo_dir: str) -> str:
    proc = _git(["rev-parse", "--show-toplevel"], cwd=repo_dir)
    if proc.returncode != 0:
        raise PagesPublishError(
            f"'{repo_dir}' er ikke inne i et git-repo: {proc.stderr.strip()}"
        )
    return proc.stdout.strip()


def _remote_url(repo_root: str, remote: str) -> str:
    proc = _git(["config", "--get", f"remote.{remote}.url"], cwd=repo_root)
    return proc.stdout.strip() if proc.returncode == 0 else ""


def _parse_owner_repo(remote_url: str) -> tuple[str, str] | None:
    """Extract ``(owner, repo)`` from an SSH or HTTPS GitHub remote URL."""
    remote_url = remote_url.strip()
    match = re.match(r"^git@[^:]+:([^/]+)/(.+?)(\.git)?/?$", remote_url)
    if match is None:
        match = re.match(r"^https?://[^/]+/([^/]+)/(.+?)(\.git)?/?$", remote_url)
    if match is None:
        return None
    return match.group(1), match.group(2)


def _pages_urls(owner: str, repo: str, run_id: str) -> tuple[str, str]:
    base = f"https://{owner}.github.io/{repo}"
    return f"{base}/latest/", f"{base}/runs/{run_id}/"


def _branch_exists_locally(repo_root: str, branch: str) -> bool:
    proc = _git(["show-ref", "--verify", "--quiet", f"refs/heads/{branch}"], cwd=repo_root)
    return proc.returncode == 0


def _branch_exists_on_remote(repo_root: str, remote: str, branch: str) -> bool:
    proc = _git(["ls-remote", "--exit-code", "--heads", remote, branch], cwd=repo_root)
    return proc.returncode == 0


def _copy_path(source: Path, destination: Path) -> None:
    """Copy one file or directory, creating its destination parent."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    if source.is_dir():
        shutil.copytree(source, destination)
    else:
        shutil.copyfile(source, destination)


def _copy_bundle(
    export_dir: Path,
    dest_dir: Path,
    *,
    preserve_existing: tuple[str, ...] = (),
) -> None:
    """Replace *dest_dir* with *export_dir*, preserving independent assets.

    Entries named by *preserve_existing* survive when they already exist in
    *dest_dir* and the new bundle does not contain a replacement. This keeps
    independently published activity and registration pages available when a
    season-plan bundle is published.

    Adds an ``index.html`` when the bundle doesn't already have one (falling
    back to a copy of ``season_plan.html``) so the directory resolves on its
    own as a Pages URL.
    """
    preserved_root = Path(tempfile.mkdtemp(prefix="rvv-pages-preserved-"))
    try:
        if dest_dir.exists():
            for relative_name in preserve_existing:
                source = dest_dir / relative_name
                if not source.exists() or (export_dir / relative_name).exists():
                    continue
                _copy_path(source, preserved_root / relative_name)
            shutil.rmtree(dest_dir)

        shutil.copytree(export_dir, dest_dir)

        for relative_name in preserve_existing:
            source = preserved_root / relative_name
            if source.exists():
                _copy_path(source, dest_dir / relative_name)
    finally:
        shutil.rmtree(preserved_root, ignore_errors=True)

    index_path = dest_dir / "index.html"
    if not index_path.exists():
        season_html = dest_dir / "season_plan.html"
        if season_html.exists():
            shutil.copyfile(season_html, index_path)
        else:
            index_path.write_text(PAGES_EMPTY_INDEX, encoding="utf-8")


def _write_root_index(branch_root: Path) -> None:
    (branch_root / "index.html").write_text(PAGES_ROOT_INDEX, encoding="utf-8")
    (branch_root / ".nojekyll").write_text("", encoding="utf-8")


# ---------------------------------------------------------------------------
# Identity and preview (issue #19 — approval gating support)
# ---------------------------------------------------------------------------


def bundle_fingerprint(bundle_dir: str) -> str:
    """Stable content hash of every file in *bundle_dir*.

    Hashes each file's path (relative to *bundle_dir*) and bytes, in sorted
    order, so the same content always fingerprints identically regardless
    of filesystem iteration order, and any change to any file's content,
    name, or presence changes the result.
    """
    digest = hashlib.sha256()
    root = Path(bundle_dir)
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        digest.update(str(path.relative_to(root)).encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()


def target_fingerprint(*, repo_dir: str, branch: str, remote: str, run_id: str) -> str:
    """Stable identity for "this exact place a bundle would be published to".

    Combines the repo's resolved path, its remote URL (if any), the target
    branch, and the run id (which determines the immutable ``/runs/<id>/``
    path) — so approving a publish to one target never silently covers a
    different repo, branch, or run.
    """
    try:
        repo_root = _require_git_repo_root(repo_dir)
        remote_url = _remote_url(repo_root, remote)
    except PagesPublishError:
        repo_root = str(Path(repo_dir).resolve())
        remote_url = ""
    payload = "|".join([repo_root, remote_url, branch, run_id])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def resolve_urls(*, repo_dir: str, remote: str, run_id: str) -> tuple[str, str] | None:
    """Return ``(latest_url, run_url)`` derived from *remote*'s GitHub URL, or ``None``."""
    try:
        repo_root = _require_git_repo_root(repo_dir)
    except PagesPublishError:
        return None
    owner_repo = _parse_owner_repo(_remote_url(repo_root, remote))
    return _pages_urls(owner_repo[0], owner_repo[1], run_id) if owner_repo else None


def _planned_bundle_contents(export_dir: Path) -> dict[str, bytes]:
    """Relative path -> bytes as :func:`_copy_bundle` would actually write them.

    Mirrors its ``index.html`` fallback (copy of ``season_plan.html``, or a
    placeholder if neither exists) so a preview never reports a false
    "removal" of an ``index.html`` that publishing would simply regenerate.
    """
    contents = {
        path.relative_to(export_dir).as_posix(): path.read_bytes()
        for path in sorted(p for p in export_dir.rglob("*") if p.is_file())
    }
    if "index.html" not in contents:
        if "season_plan.html" in contents:
            contents["index.html"] = contents["season_plan.html"]
        else:
            contents["index.html"] = (
                PAGES_EMPTY_INDEX.encode("utf-8")
            )
    return contents


def diff_latest(bundle_dir: str, *, repo_dir: str = ".", branch: str = "gh-pages") -> dict[str, list[str]]:
    """Read-only preview of what publishing *bundle_dir* would change under ``/latest/``.

    Compares against the *local* branch tip only (no fetch) via plain git
    plumbing (``ls-tree``/``show``) — no worktree, no writes, safe to call
    before any approval exists. Returns ``{"add": [...], "update": [...],
    "remove": [...]}`` (paths relative to the branch root, e.g.
    ``latest/season_plan.html``). If *repo_dir* isn't a git repo or
    *branch* doesn't exist yet, everything in the bundle is reported as an
    addition.
    """
    bundle_contents = _planned_bundle_contents(Path(bundle_dir))

    try:
        repo_root = _require_git_repo_root(repo_dir)
    except PagesPublishError:
        repo_root = None

    if repo_root is None or not _branch_exists_locally(repo_root, branch):
        return {"add": sorted(f"latest/{name}" for name in bundle_contents), "update": [], "remove": []}

    tree_proc = _git(["ls-tree", "-r", "--name-only", branch], cwd=repo_root)
    existing = {
        line for line in tree_proc.stdout.splitlines() if line.startswith("latest/")
    } if tree_proc.returncode == 0 else set()

    add: list[str] = []
    update: list[str] = []
    for name, content in bundle_contents.items():
        rel = f"latest/{name}"
        show_proc = subprocess.run(
            ["git", "show", f"{branch}:{rel}"],
            cwd=repo_root,
            capture_output=True,
            timeout=_GIT_TIMEOUT_SECONDS,
        )
        if show_proc.returncode != 0:
            add.append(rel)
        elif show_proc.stdout != content:
            update.append(rel)

    bundle_rels = {f"latest/{name}" for name in bundle_contents}
    preserved_roots = tuple(
        root
        for root in _PRESERVED_LATEST_PATHS
        if not any(name == root or name.startswith(f"{root}/") for name in bundle_contents)
    )
    remove = sorted(
        path
        for path in existing - bundle_rels
        if not any(
            path == f"latest/{root}" or path.startswith(f"latest/{root}/")
            for root in preserved_roots
        )
    )

    return {"add": sorted(add), "update": sorted(update), "remove": remove}


def publish(
    *,
    export_dir: str,
    run_id: str,
    repo_dir: str = ".",
    branch: str = "gh-pages",
    remote: str = "origin",
    push: bool = True,
    bundle_fingerprint: str | None = None,
) -> CapabilityResult:
    """Publish *export_dir* (a Stage 4 export bundle) to the Pages branch.

    Writes the bundle under both ``/latest/`` (overwritten every call) and
    ``/runs/<run_id>/`` (written once per run id) on *branch*, commits the
    result with a plain (non-orphan-clobbering, non-force) commit, and
    pushes it to *remote* unless ``push=False``. Returns a
    :class:`CapabilityResult` describing the outcome — never raises for
    anything past initial argument/repo validation.

    When *bundle_fingerprint* is given, a ``_meta.json`` (``{"run_id":
    ..., "bundle_fingerprint": ...}``) is written into both directories —
    this is what :mod:`pages_verify` polls to confirm the *expected*
    content (not a stale cached page with different content) is actually
    reachable. Deliberately has no timestamp or other varying field: a
    republish of byte-identical content must still produce byte-identical
    output so the existing no-op-on-no-changes behavior isn't broken by
    this file.
    """
    export_path = Path(export_dir)
    if not export_path.is_dir() or not any(export_path.iterdir()):
        return CapabilityResult.failed(
            f"Eksportmappe '{export_dir}' finnes ikke eller er tom — kjør Stage 4-eksport først.",
            capability="pages_publish",
        )

    try:
        repo_root = _require_git_repo_root(repo_dir)
    except PagesPublishError as exc:
        return CapabilityResult.failed(str(exc), capability="pages_publish")

    owner_repo = _parse_owner_repo(_remote_url(repo_root, remote))

    tmp_dir = tempfile.mkdtemp(prefix="rvv-pages-")
    worktree_added = False
    try:
        if push:
            _git(["fetch", remote, branch], cwd=repo_root)

        local_exists = _branch_exists_locally(repo_root, branch)
        remote_exists = push and _branch_exists_on_remote(repo_root, remote, branch)

        if not local_exists and remote_exists:
            proc = _git(["branch", "--track", branch, f"{remote}/{branch}"], cwd=repo_root)
            if proc.returncode != 0:
                return CapabilityResult.failed(
                    f"Kunne ikke opprette lokal branch '{branch}': {proc.stderr.strip()}",
                    capability="pages_publish",
                )
            local_exists = True

        if local_exists:
            proc = _git(["worktree", "add", tmp_dir, branch], cwd=repo_root)
            if proc.returncode != 0:
                return CapabilityResult.failed(
                    f"Kunne ikke sette opp arbeidskatalog for '{branch}': {proc.stderr.strip()}",
                    capability="pages_publish",
                )
            worktree_added = True
            if remote_exists:
                ff_proc = _git(["merge", "--ff-only", f"{remote}/{branch}"], cwd=tmp_dir)
                if ff_proc.returncode != 0:
                    return CapabilityResult.failed(
                        f"Lokal '{branch}' har divergert fra {remote}/{branch} og kan ikke "
                        f"fast-forwardes automatisk — løs manuelt før publisering.",
                        capability="pages_publish",
                    )
        else:
            proc = _git(["worktree", "add", "--detach", tmp_dir, "HEAD"], cwd=repo_root)
            if proc.returncode != 0:
                return CapabilityResult.failed(
                    f"Kunne ikke opprette midlertidig arbeidskatalog: {proc.stderr.strip()}",
                    capability="pages_publish",
                )
            worktree_added = True
            orphan_proc = _git(["checkout", "--orphan", branch], cwd=tmp_dir)
            if orphan_proc.returncode != 0:
                return CapabilityResult.failed(
                    f"Kunne ikke opprette branch '{branch}': {orphan_proc.stderr.strip()}",
                    capability="pages_publish",
                )
            _git(["rm", "-rf", "--quiet", "."], cwd=tmp_dir)
            for child in Path(tmp_dir).iterdir():
                if child.name == ".git":
                    continue
                if child.is_dir():
                    shutil.rmtree(child)
                else:
                    child.unlink()

        branch_root = Path(tmp_dir)
        _write_root_index(branch_root)
        _copy_bundle(
            export_path,
            branch_root / "latest",
            preserve_existing=_PRESERVED_LATEST_PATHS,
        )
        run_dir = branch_root / "runs" / run_id
        is_new_run_snapshot = not run_dir.exists()
        _copy_bundle(branch_root / "latest", run_dir)

        if bundle_fingerprint is not None:
            meta_payload = json.dumps(
                {"run_id": run_id, "bundle_fingerprint": bundle_fingerprint}, indent=2
            )
            (branch_root / "latest" / "_meta.json").write_text(meta_payload, encoding="utf-8")
            (run_dir / "_meta.json").write_text(meta_payload, encoding="utf-8")

        _git(["add", "-A"], cwd=tmp_dir)
        status_proc = _git(["status", "--porcelain"], cwd=tmp_dir)

        latest_url, run_url = (
            _pages_urls(owner_repo[0], owner_repo[1], run_id) if owner_repo else (None, None)
        )
        artifacts = [url for url in (latest_url, run_url) if url]

        if not status_proc.stdout.strip():
            sha = _git(["rev-parse", "HEAD"], cwd=tmp_dir).stdout.strip()
            return CapabilityResult.ok(
                f"Ingen endringer å publisere for kjøring {run_id} — allerede oppdatert.",
                capability="pages_publish",
                evidence=[f"commit_sha={sha}", f"branch={branch}"],
                artifacts=artifacts,
            )

        commit_proc = _git(
            [
                "-c", "user.email=rvv-miniputt-operator@localhost",
                "-c", "user.name=RVV Miniputt operator",
                "commit", "-m", f"Publish run {run_id}",
            ],
            cwd=tmp_dir,
        )
        if commit_proc.returncode != 0:
            return CapabilityResult.failed(
                f"Kunne ikke committe Pages-publisering: {commit_proc.stderr.strip()}",
                capability="pages_publish",
            )
        sha = _git(["rev-parse", "HEAD"], cwd=tmp_dir).stdout.strip()

        if push:
            push_proc = _git(["push", remote, f"{branch}:{branch}"], cwd=repo_root)
            if push_proc.returncode != 0:
                return CapabilityResult.failed(
                    f"Publiserte lokalt (commit {sha}), men push til {remote}/{branch} feilet: "
                    f"{push_proc.stderr.strip()}",
                    capability="pages_publish",
                    evidence=[f"commit_sha={sha}", f"branch={branch}"],
                    problems=[push_proc.stderr.strip()],
                )

        return CapabilityResult.ok(
            f"Publiserte kjøring {run_id} til {branch} (commit {sha[:8]})",
            capability="pages_publish",
            evidence=[
                f"commit_sha={sha}",
                f"branch={branch}",
                f"new_run_snapshot={is_new_run_snapshot}",
            ],
            artifacts=artifacts,
        )
    finally:
        if worktree_added:
            _git(["worktree", "remove", "--force", tmp_dir], cwd=repo_root)
        shutil.rmtree(tmp_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Rollback and publication history (issue #20)
# ---------------------------------------------------------------------------

_PUBLISH_COMMIT_RE = re.compile(r"^Publish run (\S+)$")
_ROLLBACK_COMMIT_RE = re.compile(r"^Rollback latest to run (\S+)$")


def rollback_to_run(
    *,
    run_id: str,
    repo_dir: str = ".",
    branch: str = "gh-pages",
    remote: str = "origin",
    push: bool = True,
) -> CapabilityResult:
    """Restore ``/latest/`` to a previously published run's immutable snapshot.

    Copies ``/runs/<run_id>/`` (which must already exist on *branch* — a
    rollback can only target a run that was actually published before,
    never fabricate one) over ``/latest/`` and commits/pushes the result
    exactly like :func:`publish`. Never touches ``/runs/`` itself — every
    historical run remains reachable at its own immutable URL regardless
    of what ``/latest/`` currently points to.
    """
    try:
        repo_root = _require_git_repo_root(repo_dir)
    except PagesPublishError as exc:
        return CapabilityResult.failed(str(exc), capability="pages_publish")

    owner_repo = _parse_owner_repo(_remote_url(repo_root, remote))

    tmp_dir = tempfile.mkdtemp(prefix="rvv-pages-rollback-")
    worktree_added = False
    try:
        if push:
            _git(["fetch", remote, branch], cwd=repo_root)

        local_exists = _branch_exists_locally(repo_root, branch)
        remote_exists = push and _branch_exists_on_remote(repo_root, remote, branch)

        if not local_exists and remote_exists:
            proc = _git(["branch", "--track", branch, f"{remote}/{branch}"], cwd=repo_root)
            if proc.returncode != 0:
                return CapabilityResult.failed(
                    f"Kunne ikke opprette lokal branch '{branch}': {proc.stderr.strip()}",
                    capability="pages_publish",
                )
            local_exists = True

        if not local_exists:
            return CapabilityResult.failed(
                f"Branch '{branch}' finnes ikke ennå — ingenting å rulle tilbake til.",
                capability="pages_publish",
            )

        proc = _git(["worktree", "add", tmp_dir, branch], cwd=repo_root)
        if proc.returncode != 0:
            return CapabilityResult.failed(
                f"Kunne ikke sette opp arbeidskatalog for '{branch}': {proc.stderr.strip()}",
                capability="pages_publish",
            )
        worktree_added = True

        if remote_exists:
            ff_proc = _git(["merge", "--ff-only", f"{remote}/{branch}"], cwd=tmp_dir)
            if ff_proc.returncode != 0:
                return CapabilityResult.failed(
                    f"Lokal '{branch}' har divergert fra {remote}/{branch} og kan ikke "
                    f"fast-forwardes automatisk — løs manuelt før tilbakerulling.",
                    capability="pages_publish",
                )

        run_dir = Path(tmp_dir) / "runs" / run_id
        if not run_dir.is_dir():
            return CapabilityResult.failed(
                f"Fant ingen publisert kjøring '{run_id}' under runs/ på '{branch}'.",
                capability="pages_publish",
            )

        latest_dir = Path(tmp_dir) / "latest"
        _copy_bundle(
            run_dir,
            latest_dir,
            preserve_existing=_PRESERVED_LATEST_PATHS,
        )

        _git(["add", "-A"], cwd=tmp_dir)
        status_proc = _git(["status", "--porcelain"], cwd=tmp_dir)

        latest_url, run_url = (
            _pages_urls(owner_repo[0], owner_repo[1], run_id) if owner_repo else (None, None)
        )
        artifacts = [url for url in (latest_url, run_url) if url]

        if not status_proc.stdout.strip():
            sha = _git(["rev-parse", "HEAD"], cwd=tmp_dir).stdout.strip()
            return CapabilityResult.ok(
                f"'/latest/' er allerede kjøring {run_id} — ingen tilbakerulling nødvendig.",
                capability="pages_publish",
                evidence=[f"commit_sha={sha}", f"branch={branch}"],
                artifacts=artifacts,
            )

        commit_proc = _git(
            [
                "-c", "user.email=rvv-miniputt-operator@localhost",
                "-c", "user.name=RVV Miniputt operator",
                "commit", "-m", f"Rollback latest to run {run_id}",
            ],
            cwd=tmp_dir,
        )
        if commit_proc.returncode != 0:
            return CapabilityResult.failed(
                f"Kunne ikke committe tilbakerulling: {commit_proc.stderr.strip()}",
                capability="pages_publish",
            )
        sha = _git(["rev-parse", "HEAD"], cwd=tmp_dir).stdout.strip()

        if push:
            push_proc = _git(["push", remote, f"{branch}:{branch}"], cwd=repo_root)
            if push_proc.returncode != 0:
                return CapabilityResult.failed(
                    f"Rullet tilbake lokalt (commit {sha}), men push til {remote}/{branch} feilet: "
                    f"{push_proc.stderr.strip()}",
                    capability="pages_publish",
                    evidence=[f"commit_sha={sha}", f"branch={branch}"],
                    problems=[push_proc.stderr.strip()],
                )

        return CapabilityResult.ok(
            f"Rullet '/latest/' tilbake til kjøring {run_id} (commit {sha[:8]})",
            capability="pages_publish",
            evidence=[f"commit_sha={sha}", f"branch={branch}", f"rolled_back_to_run_id={run_id}"],
            artifacts=artifacts,
        )
    finally:
        if worktree_added:
            _git(["worktree", "remove", "--force", tmp_dir], cwd=repo_root)
        shutil.rmtree(tmp_dir, ignore_errors=True)


def list_publication_history(*, repo_dir: str = ".", branch: str = "gh-pages") -> list[dict[str, str]]:
    """Read-only, newest-first publication history parsed from *branch*'s commit log.

    Each entry: ``{"commit_sha", "date", "kind" ("publish"|"rollback"), "run_id"}``.
    Returns an empty list if *repo_dir* isn't a git repo or *branch*
    doesn't exist yet — never raises.
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

    history: list[dict[str, str]] = []
    for line in proc.stdout.splitlines():
        parts = line.split("\x1f")
        if len(parts) != 3:
            continue
        sha, date, subject = parts
        publish_match = _PUBLISH_COMMIT_RE.match(subject)
        rollback_match = _ROLLBACK_COMMIT_RE.match(subject)
        if publish_match:
            history.append({"commit_sha": sha, "date": date, "kind": "publish", "run_id": publish_match.group(1)})
        elif rollback_match:
            history.append(
                {"commit_sha": sha, "date": date, "kind": "rollback", "run_id": rollback_match.group(1)}
            )
    return history
