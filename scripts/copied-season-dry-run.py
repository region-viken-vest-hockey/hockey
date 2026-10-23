#!/usr/bin/env python3
"""Hermetic publication dry run on a copy of the canonical season (#820).

Copies the live season state, pipeline state and gh-pages history into a scratch
repo, then runs export, audit-context build, publication preflight and the seal
there. The live season and pipeline state are hashed before and after, and any
change to them fails the run.

Nothing is published, and no operational season, pipeline state or Pages branch
is written. The run passes only if every step passes and the live state is
unchanged. A refusal or an unexpected error at any step is reported with its
message and fails the run.

Usage:
    scripts/copied-season-dry-run.py [--season 2026-2027] [--scratch DIR] [--keep]

--scratch must name a directory that does not exist yet, or an empty directory,
outside the repository. The script deletes only a scratch directory it created,
and only unless --keep is given.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RVV = ROOT / "scripts" / "rvv-miniputt"


def tree_digest(path: Path) -> str:
    digest = hashlib.sha256()
    if not path.exists():
        return "absent"
    for file in sorted(path.rglob("*")):
        if file.is_file():
            digest.update(str(file.relative_to(path)).encode())
            digest.update(hashlib.sha256(file.read_bytes()).digest())
    return digest.hexdigest()


def live_state() -> dict[str, str]:
    return {"season": tree_digest(ROOT / "season"), "pipeline": tree_digest(ROOT / ".pipeline")}


def step(report: dict, name: str, ok: bool, detail: str) -> bool:
    report["steps"].append({"step": name, "ok": ok, "detail": detail})
    print(f"[{'PASS' if ok else 'FAIL'}] {name}: {detail}", flush=True)
    return ok


def choose_scratch(value: str | None) -> tuple[Path, bool]:
    """Return (scratch, created_by_us). A supplied path must be new or empty and outside the repo."""

    if value is None:
        scratch = Path(tempfile.mkdtemp(prefix="season-dry-run-")).resolve()
        if scratch.is_relative_to(ROOT):
            shutil.rmtree(scratch, ignore_errors=True)
            raise SystemExit(f"the temporary directory is inside the repository ({scratch}); pass --scratch outside it")
        return scratch, True
    scratch = Path(value).resolve()
    if scratch.is_relative_to(ROOT) or ROOT.is_relative_to(scratch):
        raise SystemExit(f"--scratch must be outside the repository: {scratch}")
    if scratch.exists():
        if not scratch.is_dir() or any(scratch.iterdir()):
            raise SystemExit(f"--scratch must be a new or empty directory: {scratch}")
        return scratch, False
    scratch.mkdir(parents=True)
    return scratch, True


def run_publication_path(args: argparse.Namespace, report: dict, scratch: Path) -> bool:
    origin = subprocess.run(
        ["git", "-C", str(ROOT), "remote", "get-url", "origin"], check=False, capture_output=True, text=True
    )
    if origin.returncode != 0 or not origin.stdout.strip():
        return step(report, "origin", False, "no origin remote to clone gh-pages from")

    repo = scratch / "repo"
    work = scratch / "work"
    clone = subprocess.run(
        ["git", "clone", "-q", "--single-branch", "--branch", "gh-pages", origin.stdout.strip(), str(repo)],
        check=False, capture_output=True, text=True,
    )
    if not step(report, "clone gh-pages history", clone.returncode == 0, clone.stderr.strip() or "scratch repo cloned"):
        return False

    shutil.copytree(ROOT / "season", repo / "season")
    if (ROOT / ".pipeline").exists():
        shutil.copytree(ROOT / ".pipeline", work)
    else:
        work.mkdir(parents=True)
    step(report, "copy season and pipeline state", True, f"copied into {scratch}")

    export = subprocess.run(
        [
            str(RVV), "season", "export", "--season", args.season,
            "--root", str(repo / "season"), "--work-dir", str(work),
            "--export-dir", str(repo / "export" / "dry-run"), "--json",
        ],
        cwd=str(repo), check=False, capture_output=True, text=True,
    )
    output_lines = (export.stdout + "\n" + export.stderr).strip().splitlines()
    refusal = next((line.strip() for line in output_lines if "Refusing" in line), "")
    export_detail = (refusal or (output_lines[-1] if output_lines else "export failed")) if export.returncode else "export written"
    if not step(report, "export", export.returncode == 0, export_detail):
        return False

    manifests = sorted((repo / "export" / "dry-run").glob("*/export_manifest.json"))
    if not manifests:
        return step(report, "export manifest", False, "no export_manifest.json in the scratch export")
    export_dir = manifests[-1].parent

    sys.path.insert(0, str(ROOT))
    from tournament_scheduler.pipeline.audit_context import build_audit_context
    from tournament_scheduler.pipeline.publication_lifecycle import assert_publication_allowed, record_publication_seal

    manifest = json.loads(manifests[-1].read_text(encoding="utf-8"))
    projected = len(manifest.get("schedule_projection") or {})
    audited = build_audit_context(work_dir=work)["plan_audit_summary"]["tournament_count"]
    if not step(report, "audit context", audited == projected, f"audited {audited} tournaments, export projection holds {projected}"):
        return False

    try:
        reconciliation = assert_publication_allowed(export_dir, repo_dir=repo)
    except RuntimeError as exc:
        return step(report, "preflight", False, str(exc))
    # The copy is a sealed season, so the pre-push gate must reconcile against its baseline.
    # None means that did not happen, and the run must not count that as a pass.
    if reconciliation is None:
        return step(report, "preflight", False, "pre-push gate returned no sealed reconciliation for a sealed season")
    step(report, "preflight", True, "pre-push gate reconciled the export against the sealed baseline")

    try:
        seal = record_publication_seal(export_dir, repo_dir=repo)
    except RuntimeError as exc:
        return step(report, "seal", False, str(exc))
    return step(report, "seal", seal is not None and seal.get("state") == "published_sealed", f"state {seal.get('state') if seal else None}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--season", default="2026-2027")
    parser.add_argument("--scratch", help="New or empty directory outside the repository (default: a temporary one)")
    parser.add_argument("--keep", action="store_true", help="Keep the scratch directory after the run")
    args = parser.parse_args()

    scratch, created = choose_scratch(args.scratch)
    report: dict = {"season": args.season, "scratch": str(scratch), "steps": []}
    before = live_state()
    passed = False
    try:
        passed = run_publication_path(args, report, scratch)
    except Exception:  # noqa: BLE001 - every failure must reach the report and the live check
        step(report, "unexpected error", False, traceback.format_exc(limit=3).strip().splitlines()[-1])
    finally:
        live_unchanged = live_state() == before
        step(report, "live state unchanged", live_unchanged, "season and pipeline hashes match before the run" if live_unchanged else "LIVE STATE CHANGED")
        if created and not args.keep:
            shutil.rmtree(scratch, ignore_errors=True)
        report["passed"] = bool(passed and live_unchanged and all(item["ok"] for item in report["steps"]))
        print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
