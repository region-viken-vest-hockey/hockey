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

_console = Console()

_EXIT_CODES = {STATUS_PASS: 0, STATUS_FAIL: 1, STATUS_NOT_CHECKABLE: 2}


def _cmd_export_parity(args: argparse.Namespace) -> int:
    export_dir = Path(args.export_dir)
    manifest = {}
    try:
        from ..pipeline.export_lifecycle import read_export_manifest

        manifest = read_export_manifest(export_dir) or {}
    except Exception:  # noqa: BLE001 - best-effort read-only provenance
        manifest = {}

    required_revision = ""
    requires_fresh = False
    if not args.allow_historical:
        season = str(args.season or manifest.get("canonical_season") or "")
        if season:
            required_revision, requires_fresh = resolve_canonical_freshness(
                season=season, season_root=args.season_root
            )
        if args.expected_revision:
            required_revision = args.expected_revision

    report = verify_export_parity(
        export_dir,
        basename=args.basename,
        required_canonical_revision=required_revision,
        requires_fresh_export=requires_fresh,
        manifest=manifest,
    )
    if args.write_report:
        write_parity_report(export_dir, report)

    if args.json:
        _console.print_json(json.dumps(report, ensure_ascii=False, default=str))
    else:
        _print_summary(report)
    return _EXIT_CODES.get(report["status"], 2)


def _print_summary(report: dict) -> None:
    status = report["status"]
    colour = {STATUS_PASS: "green", STATUS_FAIL: "red", STATUS_NOT_CHECKABLE: "yellow"}.get(status, "white")
    _console.print(f"[bold {colour}]Artefaktparitet: {status}[/bold {colour}]")
    _console.print(f"  xlsx: {report['primary'].get('path')} ({report['primary'].get('sha256') or 'ikke lest'})")
    _console.print(f"  html: {report['secondary'].get('path')} ({report['secondary'].get('sha256') or 'ikke lest'})")
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
