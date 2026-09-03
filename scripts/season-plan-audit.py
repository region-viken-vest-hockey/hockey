#!/usr/bin/env python3
"""Independent deterministic audit of a generated season-plan overview.

This intentionally does not use SeasonPlanner's fairness result. It compares
published host assignments with the canonical registration snapshot so a
second reviewer can detect regressions such as a club hosting an age group in
which it has no registered team, or a large mismatch between registration
share and hosting share.

The audit also reports host days and hosted-game workload. Tournament count is
the primary proportional fairness measure because it can be compared directly
with the registered-team share per age group. Host days and hosted games are
secondary workload signals: they expose cases where similar tournament counts
still mean materially different practical burden.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
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


def _int_value(value: str | None) -> int:
    try:
        return int((value or "0").strip() or 0)
    except ValueError:
        return 0


def audit(registrations_path: Path, overview_path: Path, max_deviation: float) -> dict[str, object]:
    registration_bytes = registrations_path.read_bytes()
    registrations_sha256 = hashlib.sha256(registration_bytes).hexdigest()
    registration_team_count = 0
    registrations_by_age: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    with registrations_path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {"club", "label", "age_group"}
        if not required.issubset(reader.fieldnames or []):
            raise SystemExit(f"{registrations_path} mangler kolonner: {sorted(required)}")
        for row in reader:
            age_group = (row.get("age_group") or "").strip()
            club = (row.get("club") or "").strip()
            label = (row.get("label") or "").strip()
            if not age_group or not club or not label:
                continue
            registration_team_count += 1
            for physical_club, weight in _physical_club_weights(club).items():
                registrations_by_age[age_group][physical_club] += weight

    actual_by_age: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    host_days_by_age: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    hosted_games_by_age: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
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
            tournament_date = (row.get("date") or "").strip()
            if tournament_date:
                host_days_by_age[age_group][host].add(tournament_date)
            hosted_games_by_age[age_group][host] += _int_value(row.get("game_count"))

    rows: list[dict[str, object]] = []
    issues: list[dict[str, object]] = []
    club_totals: dict[str, dict[str, float]] = defaultdict(
        lambda: {"teams": 0.0, "expected": 0.0, "actual": 0.0, "hosted_games": 0.0}
    )
    club_host_days: dict[str, set[str]] = defaultdict(set)

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
            registered_share = (team_weight / total_weight) if total_weight else 0.0
            hosting_share = (actual / tournament_count) if tournament_count else 0.0
            host_days = host_days_by_age.get(age_group, {}).get(club, set())
            hosted_games = int(hosted_games_by_age.get(age_group, {}).get(club, 0))
            row = {
                "age_group": age_group,
                "club": club,
                "registered_team_weight": round(team_weight, 2),
                "registered_share": round(registered_share, 4),
                "tournaments": tournament_count,
                "expected_hosts": round(expected, 2),
                "actual_hosts": actual,
                "hosting_share": round(hosting_share, 4),
                "host_days": len(host_days),
                "hosted_games": hosted_games,
                "deviation": round(deviation, 2),
            }
            rows.append(row)
            totals = club_totals[club]
            totals["teams"] += team_weight
            totals["expected"] += expected
            totals["actual"] += actual
            totals["hosted_games"] += hosted_games
            club_host_days[club].update(host_days)

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

    total_registered_weight = sum(values["teams"] for values in club_totals.values())
    total_hosts = sum(values["actual"] for values in club_totals.values())
    total_host_days = sum(len(days) for days in club_host_days.values())
    total_hosted_games = sum(values["hosted_games"] for values in club_totals.values())

    club_summary = []
    for club, values in sorted(club_totals.items()):
        expected = values["expected"]
        actual = values["actual"]
        host_days = len(club_host_days.get(club, set()))
        hosted_games = int(values["hosted_games"])
        club_summary.append(
            {
                "club": club,
                "registered_team_weight": round(values["teams"], 2),
                "registered_team_share": round(values["teams"] / total_registered_weight, 4)
                if total_registered_weight
                else 0.0,
                "expected_hosts": round(expected, 2),
                "actual_hosts": int(actual),
                "hosting_share": round(actual / total_hosts, 4) if total_hosts else 0.0,
                "host_days": host_days,
                "host_day_share": round(host_days / total_host_days, 4) if total_host_days else 0.0,
                "hosted_games": hosted_games,
                "hosted_game_share": round(hosted_games / total_hosted_games, 4)
                if total_hosted_games
                else 0.0,
                "deviation": round(actual - expected, 2),
            }
        )

    status = (
        "fail"
        if any(issue["severity"] == "fail" for issue in issues)
        else ("warn" if issues else "pass")
    )
    return {
        "status": status,
        "registrations_path": str(registrations_path),
        "registrations_sha256": registrations_sha256,
        "registration_team_count": registration_team_count,
        "overview_path": str(overview_path),
        "max_age_group_host_deviation": max_deviation,
        "workload_basis": {
            "primary": "proportional tournament hosting per age group",
            "secondary": ["unique host days", "hosted games"],
            "note": "Hosted games is a workload proxy, not exact ice-time minutes.",
        },
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
            "registered_share",
            "tournaments",
            "expected_hosts",
            "actual_hosts",
            "hosting_share",
            "host_days",
            "hosted_games",
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
