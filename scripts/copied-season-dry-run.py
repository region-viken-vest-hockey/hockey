#!/usr/bin/env python3
"""Hermetic publication dry run on a copy of the canonical season (#820).

Copies the live season state and the gh-pages history into a scratch repo, then
runs export, audit-context build, publication preflight and the seal there. The
live season is hashed before and after; any change to it fails the run.

Nothing is published, and no operational season or Pages branch is written.
Exit status is 0 only when every step passes. A refusal at any step is reported
with its exact message and fails the run.

Usage:
    scripts/copied-season-dry-run.py [--season 2026-2027] [--scratch DIR] [--keep]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RVV = ROOT / "scripts" / "rvv-miniputt"


def tree_digest(path: Path) -> str:
    digest = hashlib.sha256()
    for file in sorted(path.rglob("*")):
        if file.is_file():
            digest.update(str(file.relative_to(path)).encode())
            digest.update(hashlib.sha256(file.read_bytes()).digest())
    return digest.hexdigest()


def run(args: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(args, check=False, capture_output=True, text=True, **kwargs)


def step(report: dict, name: str, ok: bool, detail: str) -> None:
    report["steps"].append({"step": name, "ok": ok, "detail": detail})
    print(f"[{'PASS' if ok else 'FAIL'}] {name}: {detail}", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--season", default="2026-2027")
    parser.add_argument("--scratch", help="Scratch directory (default: a new temporary directory)")
    parser.add_argument("--keep", action="store_true", help="Keep the scratch directory after the run")
    args = parser.parse_args()

    live_season = ROOT / "season"
    live_pipeline = ROOT / ".pipeline"
    origin = run(["git", "-C", str(ROOT), "remote", "get-url", "origin"]).stdout.strip()
    scratch = Path(args.scratch) if args.scratch else Path(tempfile.mkdtemp(prefix="season-dry-run-"))
    scratch.mkdir(parents=True, exist_ok=True)
    report: dict = {"season": args.season, "scratch": str(scratch), "steps": []}

    live_before = tree_digest(live_season)
    repo = scratch / "repo"
    work = scratch / "work"
    export_root = repo / "export"

    clone = run(["git", "clone", "-q", "--single-branch", "--branch", "gh-pages", origin, str(repo)])
    step(report, "clone gh-pages history", clone.returncode == 0, clone.stderr.strip() or "scratch repo cloned")
    if clone.returncode != 0:
        return finish(report, live_season, live_before, args.keep, scratch, 1)

    shutil.copytree(live_season, repo / "season")
    if live_pipeline.exists():
        shutil.copytree(live_pipeline, work)
    else:
        work.mkdir(parents=True)
    step(report, "copy season and pipeline state", True, f"season copied to {repo / 'season'}")

    export = run(
        [
            str(RVV), "season", "export", "--season", args.season,
            "--root", str(repo / "season"), "--work-dir", str(work),
            "--export-dir", str(export_root / "dry-run"), "--json",
        ],
        cwd=str(ROOT),
    )
    step(report, "export", export.returncode == 0, export.stderr.strip().splitlines()[-1] if export.returncode else "export written")
    if export.returncode != 0:
        return finish(report, live_season, live_before, args.keep, scratch, 1)

    manifests = sorted(export_root.glob("dry-run/*/export_manifest.json"))
    if not manifests:
        step(report, "export manifest", False, "no export_manifest.json under the scratch export")
        return finish(report, live_season, live_before, args.keep, scratch, 1)
    export_dir = manifests[-1].parent

    sys.path.insert(0, str(ROOT))
    from tournament_scheduler.pipeline.audit_context import build_audit_context
    from tournament_scheduler.pipeline.publication_lifecycle import (
        assert_publication_allowed,
        record_publication_seal,
    )

    manifest = json.loads(manifests[-1].read_text(encoding="utf-8"))
    projected = len(manifest.get("schedule_projection") or {})
    context = build_audit_context(work_dir=work)
    audited = context["plan_audit_summary"]["tournament_count"]
    step(
        report,
        "audit context",
        audited == projected,
        f"audited {audited} tournaments, export projection holds {projected}",
    )

    try:
        assert_publication_allowed(export_dir, repo_dir=repo)
        step(report, "preflight", True, "pre-push gate accepted the export")
    except RuntimeError as exc:
        step(report, "preflight", False, str(exc))
        return finish(report, live_season, live_before, args.keep, scratch, 1)

    try:
        seal = record_publication_seal(export_dir, repo_dir=repo)
        ok = seal is not None and seal.get("state") == "published_sealed"
        step(report, "seal", ok, f"state {seal.get('state') if seal else None}")
    except RuntimeError as exc:
        step(report, "seal", False, str(exc))
        return finish(report, live_season, live_before, args.keep, scratch, 1)

    return finish(report, live_season, live_before, args.keep, scratch, 0)


def finish(report: dict, live_season: Path, live_before: str, keep: bool, scratch: Path, code: int) -> int:
    live_unchanged = tree_digest(live_season) == live_before
    step(report, "live season unchanged", live_unchanged, "live season hash matches before the run" if live_unchanged else "LIVE SEASON CHANGED")
    if not live_unchanged:
        code = 1
    report["passed"] = code == 0 and live_unchanged
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not keep:
        shutil.rmtree(scratch, ignore_errors=True)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
