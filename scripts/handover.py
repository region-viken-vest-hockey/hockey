#!/usr/bin/env python3
"""Read-only, revision-aware handover for the RVV hockey repository.

GitHub issues own unfinished work. This command does not persist session state,
make planning decisions, refresh calendars, export, mutate a season, or publish.
A missing source is explicitly unverified rather than inferred from old notes.
"""
from __future__ import annotations

import argparse
import base64
import binascii
from datetime import datetime, timezone
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import subprocess
import sys
from typing import Any, Callable

DEFAULT_REPO = "region-viken-vest-hockey/hockey"
Runner = Callable[[list[str], Path], str]


def _run(command: list[str], cwd: Path) -> str:
    completed = subprocess.run(
        command,
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    if completed.returncode:
        # Never echo `gh` stderr: authentication/platform errors may contain secrets.
        if command[0] == "gh":
            raise RuntimeError(f"{command[0]} exited with status {completed.returncode}")
        detail = " ".join(str(completed.stderr or "").split())[-200:]
        raise RuntimeError(
            f"{command[0]} exited with status {completed.returncode}"
            + (f": {detail}" if detail else "")
        )
    return completed.stdout.strip()


def _probe(label: str, command: list[str], root: Path, runner: Runner, missing: list[str]) -> str | None:
    try:
        return runner(command, root)
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
        detail = " ".join(str(exc).split())[:200]
        missing.append(f"{label}: {detail}")
        return None


def _infer_season(root: Path) -> str | None:
    """Return the single canonical season id under season/, if unambiguous."""
    seasons = sorted(path.name for path in (root / "season").glob("*") if path.is_dir())
    return seasons[0] if len(seasons) == 1 else None


def _infer_repo(root: Path) -> str | None:
    """Return owner/name parsed from the origin remote, if available."""
    try:
        raw = _run(["git", "remote", "get-url", "origin"], root)
    except (OSError, RuntimeError, subprocess.TimeoutExpired):
        return None
    match = re.search(r"[:/]([\w.-]+/[\w.-]+?)(?:\.git)?$", raw)
    return match.group(1) if match else None


def _api(repo: str, endpoint: str, root: Path, runner: Runner, missing: list[str]) -> dict[str, Any] | None:
    raw = _probe(f"GitHub {endpoint.split('?')[0]}", ["gh", "api", f"repos/{repo}/{endpoint}"], root, runner, missing)
    if raw is None:
        return None
    try:
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ValueError("expected object")
        return value
    except ValueError:
        missing.append(f"GitHub {endpoint.split('?')[0]}: malformed JSON")
        return None


class _RevisionParser(HTMLParser):
    revision: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        fields = dict(attrs)
        if tag == "meta" and fields.get("name") == "season-revision":
            self.revision = fields.get("content")


def _public_revision(payload: dict[str, Any] | None, missing: list[str]) -> str | None:
    if payload is None:
        return None
    try:
        raw = base64.b64decode("".join(str(payload["content"]).split()), validate=True)
        parser = _RevisionParser()
        parser.feed(raw.decode("utf-8"))
        if not parser.revision:
            raise ValueError("missing season-revision")
        return parser.revision
    except (KeyError, ValueError, UnicodeError, binascii.Error):
        missing.append("gh-pages latest/index.html: unreadable season revision")
        return None


def _select_active_published(
    published: list[dict[str, Any]],
    active_publication_id: str | None,
    missing: list[str],
) -> dict[str, Any] | None:
    """Select the active published export by canonical identity, not generation.

    ``export_lifecycle.find_export_manifests`` orders records by ``generated_at``,
    which is not publication order. The canonical baseline ``publication_id`` owns
    that identity; without it we use the newest ``published_at`` and fail closed
    when no publication time exists rather than falling back to generation time.
    """
    if active_publication_id:
        for record in published:
            if str(record.get("export_id") or "") == active_publication_id:
                return record
        missing.append(
            f"canonical active publication {active_publication_id}: "
            "not found among published export manifests"
        )
        return None
    dated = [record for record in published if record.get("published_at")]
    if not dated:
        missing.append(
            "repository published export manifests: no published_at to order by publication"
        )
        return None
    return max(dated, key=lambda record: str(record.get("published_at")))


def _publication_evidence(
    root: Path,
    season: str,
    missing: list[str],
    active_publication_id: str | None = None,
) -> tuple[dict[str, Any] | None, list[dict[str, str]]]:
    """Read publication evidence through the canonical export-lifecycle owner.

    The canonical reader resolves the repository publication-history root and
    scopes published exports to the requested season, so this report cannot
    drift from the export/publication contract owned by the pipeline.
    """
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    try:
        from tournament_scheduler.pipeline import export_lifecycle
    except Exception:
        missing.append("canonical export lifecycle: unavailable")
        return None, []

    def relative(record: dict[str, Any]) -> str:
        export_dir = record.get("export_dir")
        if not export_dir:
            return ""
        try:
            return str(Path(export_dir).relative_to(root))
        except ValueError:
            return str(export_dir)

    try:
        history_root = export_lifecycle.publication_history_root(root / "season")
        manifests = export_lifecycle.find_export_manifests(history_root)
        published = export_lifecycle.find_published_exports_for_season(
            season, season_root=root / "season"
        )
    except Exception:
        missing.append("canonical export lifecycle: read failed")
        return None, []

    candidates = [
        {
            "export_id": str(
                record.get("export_id") or Path(str(record.get("export_dir") or "")).name
            ),
            "lifecycle_status": str(record.get("lifecycle_status") or "unknown"),
            "canonical_revision": str(record.get("canonical_revision") or ""),
            "path": relative(record),
        }
        for record in manifests
        if record.get("lifecycle_status") != export_lifecycle.PUBLISHED_STATUS
        and record.get("canonical_season") in (None, season)
    ][-5:]
    if not published:
        missing.append(f"repository published export manifest for {season}: not found")
        return None, candidates
    latest = _select_active_published(published, active_publication_id, missing)
    if latest is None:
        return None, candidates
    return {
        "export_id": latest.get("export_id"),
        "canonical_revision": latest.get("canonical_revision"),
        "export_fingerprint": latest.get("export_fingerprint"),
        "pages_commit_at_publication": latest.get("pages_commit"),
        "published_at": latest.get("published_at"),
        "source_path": relative(latest),
    }, candidates


def collect(
    *,
    root: Path,
    season: str,
    repo: str = DEFAULT_REPO,
    issue: int | None = None,
    runner: Runner = _run,
    remote: bool = True,
) -> dict[str, Any]:
    """Collect evidence without changing the working tree or external systems."""
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", repo):
        raise ValueError("repository must be owner/name")
    missing: list[str] = []
    risks: list[str] = []
    local: dict[str, Any] = {"branch": None, "head": None, "dirty": None}
    for key, args in (
        ("branch", ["git", "branch", "--show-current"]),
        ("head", ["git", "rev-parse", "HEAD"]),
        ("dirty", ["git", "status", "--porcelain"]),
    ):
        value = _probe(f"local {key}", args, root, runner, missing)
        if value is not None:
            local[key] = bool(value) if key == "dirty" else value
    recent = _probe(
        "local commits", ["git", "log", "-5", "--format=%h %s"], root, runner, missing
    )
    local["recent_commits"] = recent.splitlines() if recent is not None else []
    if local["dirty"] is True:
        risks.append("Local worktree is dirty; do not infer it equals committed main.")

    lifecycle_raw = _probe(
        "canonical lifecycle",
        [str(root / "scripts" / "rvv-miniputt"), "season", "lifecycle", "--season", season, "--json"],
        root,
        runner,
        missing,
    )
    lifecycle: dict[str, Any] | None = None
    if lifecycle_raw is not None:
        try:
            parsed = json.loads(lifecycle_raw)
            if not isinstance(parsed, dict):
                raise ValueError("expected object")
            lifecycle = parsed
        except ValueError:
            missing.append("canonical lifecycle: malformed JSON")
    baseline = lifecycle.get("published_baseline") if lifecycle else None
    active_publication_id = (
        str(baseline.get("publication_id")).strip()
        if isinstance(baseline, dict) and baseline.get("publication_id")
        else None
    )
    published, candidates = _publication_evidence(
        root, season, missing, active_publication_id
    )
    if lifecycle is None:
        risks.append("Current canonical lifecycle and reconciliation were not verified.")
    elif lifecycle.get("state") == "published_sealed":
        reconciliation = lifecycle.get("reconciliation") or {}
        if not isinstance(reconciliation, dict) or reconciliation.get("ok") is not True:
            risks.append("Published baseline does not reconcile with current canonical season.")
        # A sealed state is only verifiable with a complete active baseline and a
        # current canonical revision; a missing field must fail closed, not pass
        # merely because other mocked sources happen to agree.
        if not active_publication_id:
            risks.append("Published baseline is missing an active publication ID; cannot verify the sealed state.")
        if not isinstance(baseline, dict) or not str(baseline.get("canonical_revision") or "").strip():
            risks.append("Published baseline is missing its canonical revision; cannot verify the sealed state.")
        if not str(lifecycle.get("canonical_state_revision") or "").strip():
            risks.append("Canonical state revision is missing; cannot verify the sealed state.")
    else:
        risks.append("Current canonical season was not confirmed published_sealed.")

    github: dict[str, Any] = {
        "main_head": None,
        "gh_pages_head": None,
        "ci": None,
        "gh_pages_latest_revision": None,
    }
    active_issue: dict[str, Any] | None = None
    open_issues: list[dict[str, Any]] = []
    if remote:
        main = _api(repo, "branches/main", root, runner, missing)
        pages = _api(repo, "branches/gh-pages", root, runner, missing)
        github["main_head"] = ((main or {}).get("commit") or {}).get("sha")
        github["gh_pages_head"] = ((pages or {}).get("commit") or {}).get("sha")
        if github["main_head"]:
            runs = _api(repo, f"actions/runs?head_sha={github['main_head']}&per_page=10", root, runner, missing)
            rows = (runs or {}).get("workflow_runs") or []
            matching = [row for row in rows if row.get("head_sha") == github["main_head"]]
            ci = next((row for row in matching if row.get("name") == "CI"), matching[0] if matching else None)
            if ci:
                github["ci"] = {
                    "name": ci.get("name"), "status": ci.get("status"),
                    "conclusion": ci.get("conclusion"), "url": ci.get("html_url"),
                    "head_sha": ci.get("head_sha"),
                }
            else:
                missing.append("GitHub CI: no run for current main HEAD")
        # Read the season revision from the gh-pages branch source, not a
        # separately fetched deployed Pages URL; the hosted site can briefly
        # differ from the branch and this is not a live deployment check.
        public = _api(repo, "contents/latest/index.html?ref=gh-pages", root, runner, missing)
        github["gh_pages_latest_revision"] = _public_revision(public, missing)
        raw = _probe(
            "GitHub open issues",
            ["gh", "api", f"repos/{repo}/issues?state=open&sort=updated&direction=desc&per_page=30"],
            root, runner, missing,
        )
        if raw is not None:
            try:
                rows = json.loads(raw)
                if not isinstance(rows, list):
                    raise ValueError("expected list")
                open_issues = [
                    {"number": item.get("number"), "title": item.get("title"),
                     "url": item.get("html_url"), "updated_at": item.get("updated_at")}
                    for item in rows if isinstance(item, dict) and "pull_request" not in item
                ][:12]
            except ValueError:
                missing.append("GitHub open issues: malformed JSON")
        if issue is not None:
            item = _api(repo, f"issues/{issue}", root, runner, missing)
            if item:
                active_issue = {
                    "number": item.get("number"), "title": item.get("title"),
                    "state": item.get("state"), "url": item.get("html_url"),
                    "updated_at": item.get("updated_at"), "latest_comment": None,
                }
                count = int(item.get("comments") or 0)
                if count:
                    # Request just the last issue comment, not a transcript dump.
                    raw_comments = _probe(
                        "GitHub issue last comment",
                        ["gh", "api", f"repos/{repo}/issues/{issue}/comments?per_page=1&page={count}"],
                        root, runner, missing,
                    )
                    if raw_comments is not None:
                        try:
                            comments = json.loads(raw_comments)
                            if not isinstance(comments, list) or len(comments) != 1:
                                raise ValueError("missing last comment")
                            comment = comments[0]
                            active_issue["latest_comment"] = {
                                "url": comment.get("html_url"),
                                "created_at": comment.get("created_at"),
                                "excerpt": str(comment.get("body") or "")[:500],
                            }
                        except (ValueError, TypeError, AttributeError):
                            missing.append("GitHub issue last comment: malformed or unavailable")
        # A remote update during the snapshot invalidates the claimed reference.
        final_main = _api(repo, "branches/main", root, runner, missing)
        final_pages = _api(repo, "branches/gh-pages", root, runner, missing)
        if github["main_head"] != ((final_main or {}).get("commit") or {}).get("sha"):
            risks.append("Remote main changed during handover; rerun against current HEAD.")
        if github["gh_pages_head"] != ((final_pages or {}).get("commit") or {}).get("sha"):
            risks.append("gh-pages changed during handover; re-verify public revision.")
    else:
        missing.append("live GitHub state: skipped by --no-remote")

    if published is not None:
        published["gh_pages_latest_revision"] = github["gh_pages_latest_revision"]
        published["gh_pages_head"] = github["gh_pages_head"]
        if github["gh_pages_latest_revision"] and published["canonical_revision"] != github["gh_pages_latest_revision"]:
            risks.append(
                "gh-pages latest/index.html revision does not match the repository's published manifest."
            )
        if isinstance(baseline, dict):
            if baseline.get("publication_id") != published["export_id"]:
                risks.append("Canonical active publication ID differs from the repository published manifest.")
            if (
                baseline.get("canonical_revision")
                and published.get("canonical_revision")
                and baseline["canonical_revision"] != published["canonical_revision"]
            ):
                risks.append("Canonical active baseline revision differs from the repository published manifest.")
            if (
                baseline.get("published_at")
                and published.get("published_at")
                and baseline["published_at"] != published["published_at"]
            ):
                risks.append("Canonical active baseline publication time differs from the repository published manifest.")
    if github["main_head"] and local["head"] != github["main_head"]:
        risks.append("Local HEAD differs from remote main; recheck the branch and changes.")
    if github["ci"]:
        conclusion = github["ci"].get("conclusion")
        if conclusion is None:
            risks.append("Current main CI has not completed; treat as unverified.")
        elif conclusion != "success":
            risks.append("Current main CI is not successful.")

    # Missing evidence always blocks a fully verified context. This is never
    # a publication approval, even when the context is fully verified.
    verdict = "REVIEW_REQUIRED" if missing or risks else "CONTEXT_VERIFIED"
    return {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "repository": repo,
        "season": season,
        "verdict": verdict,
        "publish_ready": False,
        "local": local,
        "github": github,
        "published": published,
        "canonical": lifecycle,
        "unpublished_export_candidates": candidates,
        "active_issue": active_issue,
        "recent_open_issues": open_issues,
        "audit": {"status": "not_verified", "note": "Verify the exact export fingerprint and semantic audit separately."},
        "calendar_booking": {"status": "not_verified", "note": "Calendar overlap is not proof of this tournament's booking."},
        "missing_evidence": missing,
        "risks": risks,
        "next_action": (
            f"Read current issue {issue} and its latest comments, then verify code and revisions."
            if issue is not None else
            "Select the relevant live GitHub issue/PR from the current task; do not infer a single active task."
        ),
        "safety": "Read-only context, not authorization to replan, mutate, export or publish.",
    }


def render(report: dict[str, Any]) -> str:
    local, remote = report["local"], report["github"]
    pub, current = report["published"] or {}, report["canonical"] or {}
    rec = current.get("reconciliation")
    if not isinstance(rec, dict):
        # A malformed (non-object) reconciliation is already classified as
        # REVIEW_REQUIRED by collect(); the human view must render that verdict
        # instead of raising. Never describe an unreadable baseline as verified.
        rec = {}
    lines = [
        f"RVV handover · {report['verdict']} · {report['season']}",
        f"Local: {local['branch'] or '?'} @ {local['head'] or '?'}; dirty={local['dirty']}",
        f"Remote main: {remote['main_head'] or 'UNVERIFIED'}",
        f"CI: {(remote['ci'] or {}).get('conclusion') or 'UNVERIFIED'}",
        f"gh-pages HEAD: {remote['gh_pages_head'] or 'UNVERIFIED'}",
        f"Published: {pub.get('export_id') or 'UNVERIFIED'} @ {pub.get('canonical_revision') or 'UNVERIFIED'}",
        f"gh-pages latest/index.html revision: {remote['gh_pages_latest_revision'] or 'UNVERIFIED'}",
        f"Canonical: {current.get('state') or 'UNVERIFIED'} @ {current.get('canonical_state_revision') or 'UNVERIFIED'}",
        f"Baseline reconciliation: {rec.get('ok', 'UNVERIFIED')}",
        f"Export candidates (not published): {len(report['unpublished_export_candidates'])}",
        f"Active issue: {(report['active_issue'] or {}).get('url') or 'not selected/verified'}",
        "Recent open issues (leads, not a ranked backlog):",
    ]
    lines.extend(f"  #{item['number']} {item['title']}" for item in report["recent_open_issues"])
    lines.extend(f"REVIEW: {reason}" for reason in report["risks"] + report["missing_evidence"])
    lines += [
        "Audit: NOT VERIFIED; calendar bookings: NOT VERIFIED.",
        f"Next: {report['next_action']}",
        "This is not publication approval. Never replan or publish as a handover action.",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--season", default=None, help="Season id; inferred from season/ when unambiguous")
    parser.add_argument("--repo", default=None, help="owner/name; inferred from the origin remote")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument("--issue", type=int, help="Explicit active GitHub issue; never inferred from chat history")
    parser.add_argument("--no-remote", action="store_true", help="Use local read-only evidence only")
    parser.add_argument("--json", action="store_true", help="Emit the structured evidence report")
    args = parser.parse_args()
    root = args.root.resolve()
    season = args.season or _infer_season(root)
    if not season:
        parser.error("--season is required; could not infer a single season from season/")
    repo = args.repo or _infer_repo(root)
    if not repo:
        parser.error(
            "--repo is required; could not verify owner/name from the origin remote"
        )
    result = collect(
        root=root, season=season, repo=repo,
        issue=args.issue, remote=not args.no_remote,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) if args.json else render(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
