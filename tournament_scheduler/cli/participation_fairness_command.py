"""``rvv-miniputt season participation-fairness`` — read-only booked-reality fairness."""

from __future__ import annotations

import argparse
import json


def _cmd_season_participation_fairness(args: argparse.Namespace) -> int:
    from ..application.canonical_season.participation_fairness import participation_fairness_report
    from ..application.canonical_season_service import CanonicalSeasonService

    service = CanonicalSeasonService(root=args.root)
    report = participation_fairness_report(service, season=args.season, age_group=args.age_group)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0
