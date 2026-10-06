"""Read-only ``rvv-miniputt export-parity`` command.

Inspects an already generated season-plan export pair and reports
``PASS``/``FAIL``/``NOT_CHECKABLE`` without mutating canonical state, the
artifacts or ``gh-pages``. This is the operator-facing way to inspect historical
artifacts; the normal publication preflight uses the same verifier.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from rich.console import Console

from ..pipeline.export_parity import (
    STATUS_FAIL,
    STATUS_NOT_CHECKABLE,
    STATUS_PASS,
    verify_export_parity,
    write_parity_report,
)
from ..pipeline.export_parity.gate import resolve_canonical_freshness
from ..pipeline.export_parity.published import verify_published_export_parity

_console = Console()

_EXIT_CODES = {STATUS_PASS: 0, STATUS_FAIL: 1, STATUS_NOT_CHECKABLE: 2}


def _cmd_export_parity(args: argparse.Namespace) -> int:
    if getattr(args, "published", False):
        freshness = resolve_canonical_freshness(
            repo_dir=args.repo_dir,
            season=str(args.season or ""),
            season_root=args.season_root,
        )
        report = verify_published_export_parity(
            repo_dir=args.repo_dir,
            branch=args.branch,
            remote=args.remote,
            canonical_revision=freshness.revision,
            canonical_lookup_failed=bool(args.season) and not freshness.determined,
        )
        if args.json:
            _console.print_json(json.dumps(report, ensure_ascii=False, default=str))
        else:
            _print_published_summary(report)
        parity_status = str((report.get("artifact_parity") or {}).get("status") or STATUS_NOT_CHECKABLE)
        if parity_status != STATUS_PASS:
            return _EXIT_CODES.get(parity_status, 2)
        return 0 if (report.get("freshness") or {}).get("status") in {"FRESH", "UNKNOWN"} else 1

    export_dir = Path(args.export_dir)
    manifest = {}
    try:
        from ..pipeline.export_lifecycle import read_export_manifest

        manifest = read_export_manifest(export_dir) or {}
    except Exception:  # noqa: BLE001 - best-effort read-only provenance
        manifest = {}

    required_revision = ""
    requires_fresh = False
    canonical_publication = False
    canonical_lookup_failed = False
    if not args.allow_historical:
        season = str(args.season or manifest.get("canonical_season") or "")
        if season:
            freshness = resolve_canonical_freshness(season=season, season_root=args.season_root)
            required_revision = freshness.revision
            requires_fresh = freshness.requires_fresh_export
            canonical_publication = True
            canonical_lookup_failed = not freshness.determined
        if args.expected_revision:
            required_revision = args.expected_revision

    report = verify_export_parity(
        export_dir,
        basename=args.basename,
        required_canonical_revision=required_revision,
        requires_fresh_export=requires_fresh,
        manifest=manifest,
        canonical_publication=canonical_publication,
        canonical_lookup_failed=canonical_lookup_failed,
        require_spond=True,
        require_spond_revision=not args.allow_historical,
    )
    if args.write_report:
        write_parity_report(export_dir, report)

    if args.json:
        _console.print_json(json.dumps(report, ensure_ascii=False, default=str))
    else:
        _print_summary(report)
    return _EXIT_CODES.get(report["status"], 2)


def _print_published_summary(report: dict) -> None:
    parity = report.get("artifact_parity") or {}
    freshness = report.get("freshness") or {}
    parity_status = parity.get("status") or STATUS_NOT_CHECKABLE
    colour = {STATUS_PASS: "green", STATUS_FAIL: "red", STATUS_NOT_CHECKABLE: "yellow"}.get(parity_status, "white")
    _console.print(f"[bold {colour}]Publisert HTML ↔ Spond: {parity_status}[/bold {colour}]")
    _console.print(f"  kilde: {report.get('published_source') or 'ukjent'} @ {report.get('published_commit') or 'ukjent'}")
    _console.print(f"  publisert pakke ↔ kanonisk revisjon: {freshness.get('status') or 'UNKNOWN'}")
    _console.print(f"  publisert revisjon: {freshness.get('published_revision') or 'ukjent'}")
    _console.print(f"  kanonisk revisjon: {freshness.get('canonical_revision') or 'ukjent'}")
    for reason in parity.get("reasons", []):
        _console.print(f"  - {reason.get('code')}: {reason.get('message')}")


def _print_summary(report: dict) -> None:
    status = report["status"]
    colour = {STATUS_PASS: "green", STATUS_FAIL: "red", STATUS_NOT_CHECKABLE: "yellow"}.get(status, "white")
    _console.print(f"[bold {colour}]Artefaktparitet: {status}[/bold {colour}]")
    _console.print(f"  xlsx: {report['primary'].get('path')} ({report['primary'].get('sha256') or 'ikke lest'})")
    _console.print(f"  html: {report['secondary'].get('path')} ({report['secondary'].get('sha256') or 'ikke lest'})")
    if report.get("spond"):
        _console.print(f"  spond: {report['spond'].get('path')} ({report['spond'].get('sha256') or 'ikke lest'})")
    _console.print(f"  kanonisk revisjon: {report.get('canonical_revision') or 'ukjent'}")
    for reason in report.get("reasons", []):
        _console.print(f"  - {reason.get('code')}: {reason.get('message')}")
    mismatches = (report.get("comparison") or {}).get("mismatches") or []
    for mismatch in mismatches[:20]:
        _console.print(
            f"  - {mismatch.get('tournament_id')} {mismatch.get('field')}: "
            f"xlsx={mismatch.get('xlsx')!r} html={mismatch.get('html')!r}"
        )
    if len(mismatches) > 20:
        _console.print(f"  ... og {len(mismatches) - 20} flere feltavvik")
    spond_mismatches = (report.get("spond_comparison") or {}).get("mismatches") or []
    for mismatch in spond_mismatches[:20]:
        _console.print(
            f"  - {mismatch.get('tournament_id')} {mismatch.get('field')}: "
            f"html={mismatch.get('html')!r} spond={mismatch.get('spond')!r}"
        )
