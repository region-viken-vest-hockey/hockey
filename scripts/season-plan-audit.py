#!/usr/bin/env python3
"""Independent deterministic audit of a generated season-plan overview.

This intentionally does not use SeasonPlanner's fairness result. It compares
published host assignments with the canonical registration snapshot so a
second reviewer can detect regressions such as a club hosting an age group in
which it has no registered team, or a large mismatch between registration
share and hosting share.
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path

from tournament_scheduler.html.data_computation import canonical_rvv_club_name


def _physical_club_weights(club: str) -> dict[str, float]:
    parts = [part.strip() for part in club.split("/") if part.strip()]
    if len(parts) <= 1:
        return {canonical_rvv_club_name(club): 1.0}
    weight = 1.0 / len(parts)
    return {canonical_rvv_club_name(part): weight for part in parts}


def audit(registrations_path: Path, overview_path: Path, max_deviation: float) -> dict[str, object]:
    registrations_by_age: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    with registrations_path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {"club", "label", "age_group"}
        if not required.issubset(reader.fieldnames or []):
            raise SystemExit(f"{registrations_path} mangler kolonner: {sorted(required)}")
        for row in reader:
            age_group = (row.get("age_group") or "").strip()
            club = (row.get("club") or "").strip()
            if not age_group or not club:
                continue
            for physical_club, weight in _physical_club_weights(club).items():
                registrations_by_age[age_group][physical_club] += weight

    actual_by_age: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    tournaments_by_age: dict[str, int] = defaultdict(int)
    with overview_path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {"age_group", "host_club"}
        if not required.issubset(reader.fieldnames or []):
            raise SystemExit(f"{overview_path} mangler kolonner: {sorted(required)}")
        for row in reader:
            if (row.get("cancelled") or "").strip().casefold() in {"ja", "yes", "true", "1"}:
                continue
            age_group = (row.get("age_group") or "").strip()
            host = canonical_rvv_club_name((row.get("host_club") or "").strip())
            if not age_group or not host:
                continue
            tournaments_by_age[age_group] += 1
            actual_by_age[age_group][host] += 1

    rows: list[dict[str, object]] = []
    issues: list[dict[str, object]] = []
    club_totals: dict[str, dict[str, float]] = defaultdict(lambda: {"teams": 0.0, "expected": 0.0, "actual": 0.0})

    for age_group in sorted(set(tournaments_by_age) | set(registrations_by_age)):
        tournament_count = tournaments_by_age.get(age_group, 0)
        weights = registrations_by_age.get(age_group, {})
        total_weight = sum(weights.values())
        clubs = sorted(set(weights) | set(actual_by_age.get(age_group, {})))
        for club in clubs:
            team_weight = float(weights.get(club, 0.0))
            expected = (team_weight / total_weight * tournament_count) if total_weight else 0.0
            actual = int(actual_by_age.get(age_group, {}).get(club, 0))
            deviation = actual - expected
            row = {
                "age_group": age_group,
                "club": club,
                "registered_team_weight": round(team_weight, 2),
                "tournaments": tournament_count,
                "expected_hosts": round(expected, 2),
                "actual_hosts": actual,
                "deviation": round(deviation, 2),
            }
            rows.append(row)
            totals = club_totals[club]
            totals["teams"] += team_weight
            totals["expected"] += expected
            totals["actual"] += actual

            if actual > 0 and team_weight <= 0:
                issues.append(
                    {
                        "severity": "fail",
                        "type": "host_without_registered_team",
                        "age_group": age_group,
                        "club": club,
                        "detail": f"{club} er satt som vert {actual} gang(er) i {age_group}, men har ingen registrerte lag i aldersgruppen.",
                    }
                )
            elif abs(deviation) > max_deviation:
                issues.append(
                    {
                        "severity": "warn",
                        "type": "hosting_share_deviation",
                        "age_group": age_group,
                        "club": club,
                        "detail": f"{club} {age_group}: faktisk {actual}, forventet ~{expected:.1f} ut fra registrerte lag.",
                    }
                )

    club_summary = []
    for club, values in sorted(club_totals.items()):
        expected = values["expected"]
        actual = values["actual"]
        club_summary.append(
            {
                "club": club,
                "registered_team_weight": round(values["teams"], 2),
                "expected_hosts": round(expected, 2),
                "actual_hosts": int(actual),
                "deviation": round(actual - expected, 2),
            }
        )

    status = "fail" if any(issue["severity"] == "fail" for issue in issues) else ("warn" if issues else "pass")
    return {
        "status": status,
        "registrations_path": str(registrations_path),
        "overview_path": str(overview_path),
        "max_age_group_host_deviation": max_deviation,
        "issues": issues,
        "age_group_breakdown": rows,
        "club_summary": club_summary,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--registrations", default="inputs/registrations/registered-teams.csv")
    parser.add_argument("--overview", required=True)
    parser.add_argument("--json-out")
    parser.add_argument("--csv-out")
    parser.add_argument("--max-deviation", type=float, default=1.0)
    parser.add_argument("--fail-on-impossible-host", action="store_true")
    args = parser.parse_args()

    result = audit(Path(args.registrations), Path(args.overview), args.max_deviation)

    if args.json_out:
        output = Path(args.json_out)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    if args.csv_out:
        output = Path(args.csv_out)
        output.parent.mkdir(parents=True, exist_ok=True)
        fieldnames = [
            "age_group",
            "club",
            "registered_team_weight",
            "tournaments",
            "expected_hosts",
            "actual_hosts",
            "deviation",
        ]
        with output.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(result["age_group_breakdown"])

    print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.fail_on_impossible_host and result["status"] == "fail":
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
