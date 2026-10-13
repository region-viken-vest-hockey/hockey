"""``rvv-miniputt season export --presentation-only`` transport.

Runs one canonical export through the presentation-only regeneration contract
and prints its bounded JSON report. Exit codes follow the export-parity CLI:
0 PASS, 1 FAIL, 2 NOT_CHECKABLE.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import sys

from ..application.canonical_season.presentation_regeneration import (
    run_presentation_only_regeneration,
)
from ..application.canonical_season.store_facade import load_decisions, load_schedule
from ..pipeline.export_parity import STATUS_NOT_CHECKABLE, STATUS_PASS
from ..season.state import canonical_state_revision
from .season_export_core import run_canonical_season_export

_EXIT_CODES = {STATUS_PASS: 0, STATUS_NOT_CHECKABLE: 2}


def _read_canonical_revision(season: str, root: str) -> str:
    schedule = load_schedule(season, root=root)
    decisions = load_decisions(season, root=root)
    return canonical_state_revision(schedule, decisions)


def cmd_season_presentation_export(args: argparse.Namespace) -> int:
    # The exporter narrates progress on stdout; keep stdout reserved for the
    # JSON report so a harness can parse it without filtering.
    with contextlib.redirect_stdout(sys.stderr):
        report = run_presentation_only_regeneration(
            season=args.season,
            read_canonical_revision=lambda: _read_canonical_revision(args.season, args.root),
            export=lambda: run_canonical_season_export(args),
        )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True, default=str))
    return _EXIT_CODES.get(report["status"], 1)
