"""Intra-club participation distribution findings and sibling-substitution repair.

These cover the canonical ``participation_targets`` pool classification being
surfaced as an actionable, revision-bound maintenance finding, the bounded
same-club sibling-substitution provider (away-first, home only without
worsening home representation, plus a bounded coupled sibling-only search), and
the ordinary ``season repair-options/search/apply-repair`` round trip.

The provider is deliberately narrower than a general roster repair: it never
moves a date/host/arena/slot, never changes hosting responsibility and never
touches guest reservations.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from tournament_scheduler.intra_club_distribution_repair import (
    apply_intra_club_distribution_repair_option,
    enumerate_intra_club_distribution_repairs,
    intra_club_distribution_finding_id,
)
from tournament_scheduler.planning_contract import build_planning_problem, verify_candidate
from tournament_scheduler.season_maintenance import (
    apply_repair,
    list_findings,
    repair_options,
    search,
)
from tournament_scheduler.season_state import schedule_fingerprint

YEAR = "2026-2027"

FILLERS = [f"F{index}" for index in range(1, 13)]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _teams() -> List[Dict[str, Any]]:
    teams: List[Dict[str, Any]] = [
        {"club": "Jar", "label": "Jar 1", "age_group": "U10", "target_tournament_count": 4},
        {"club": "Jar", "label": "Jar 2", "age_group": "U10", "target_tournament_count": 4},
    ]
    for club in FILLERS:
        teams.append(
            {
                "club": club,
                "label": f"{club} 1",
                "age_group": "U10",
                "target_tournament_count": 2,
            }
        )
    return teams


def _problem() -> Dict[str, Any]:
    config: Dict[str, Any] = {
        "teams": _teams(),
        "age_groups": ["U10"],
        "parallel_games": {"U10": 2},
        "round_length_minutes": {"U10": 30},
        "ice_time_minutes": {"U10": 90},
        "rounds_per_tournament": {"U10": 3},
    }
    problem = build_planning_problem(config, None, date(2026, 9, 1), date(2027, 4, 30))
    problem["clubs"] = {team["club"]: f"{team['club']} Arena" for team in _teams()}
    return problem


def _four_team_games(labels: Sequence[str]) -> List[Dict[str, Any]]:
    return [
        {
            "home": labels[index],
            "away": labels[(index + 1) % len(labels)],
            "parallel_slot": 0,
            "round_number": 1,
        }
        for index in range(len(labels))
    ]


def _tournament(
    tournament_id: str,
    day: str,
    host: str,
    labels: Sequence[str],
    all_teams: Sequence[Dict[str, Any]],
) -> Dict[str, Any]:
    by_label = {team["label"]: team for team in all_teams}
    return {
        "id": tournament_id,
        "date": day,
        "arena": f"{host} Arena",
        "age_group": "U10",
        "host_club": host,
        "teams": [dict(by_label[label]) for label in labels],
        "games": _four_team_games(labels),
        "start_time": "10:00",
    }


def _specs_base() -> List[Tuple[str, str, str, List[str]]]:
    # Jar 1 before Christmas: B1, B2, B3. Jar 2 before: B4.
    # Jar 1 after Christmas: A1, A2. Jar 2 after: A3, A4.
    # Every filler plays exactly once in each half, and the Jar-2 tournaments
    # use disjoint filler sets so a sibling substitution adds no repeated
    # opponent.
    return [
        ("B1", "2026-09-12", "F1", ["Jar 1", "F1 1", "F2 1", "F3 1"]),
        ("B2", "2026-09-26", "F4", ["Jar 1", "F4 1", "F5 1", "F6 1"]),
        ("B3", "2026-10-10", "F7", ["Jar 1", "F7 1", "F8 1", "F9 1"]),
        ("B4", "2026-10-24", "F10", ["Jar 2", "F10 1", "F11 1", "F12 1"]),
        ("A1", "2027-01-16", "F1", ["Jar 1", "F1 1", "F2 1", "F3 1"]),
        ("A2", "2027-01-30", "F4", ["Jar 1", "F4 1", "F5 1", "F6 1"]),
        ("A3", "2027-02-13", "F7", ["Jar 2", "F7 1", "F8 1", "F9 1"]),
        ("A4", "2027-02-27", "F10", ["Jar 2", "F10 1", "F11 1", "F12 1"]),
    ]


def _plan(
    specs: Optional[Sequence[Tuple[str, str, str, List[str]]]] = None,
    *,
    all_teams: Optional[Sequence[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    resolved_teams = list(all_teams or _teams())
    resolved_specs = list(specs if specs is not None else _specs_base())
    return {
        "schema_version": 1,
        "start_date": "2026-09-01",
        "end_date": "2027-04-30",
        "tournaments": [
            _tournament(tournament_id, day, host, labels, resolved_teams)
            for (tournament_id, day, host, labels) in resolved_specs
        ],
    }


def _write_season(
    root: Path,
    plan: Dict[str, Any],
    problem: Dict[str, Any],
    *,
    approved: Optional[Iterable[str]] = None,
    participants_locked: Optional[Iterable[str]] = None,
) -> str:
    season_dir = root / YEAR
    season_dir.mkdir(parents=True, exist_ok=True)
    revision = schedule_fingerprint(plan)
    approved_ids = set(approved or ())
    locked_ids = set(participants_locked or ())
    schedule = {
        "schema_version": 1,
        "season": YEAR,
        "revision": revision,
        "fingerprint": revision,
        "plan_schema_version": 1,
        "plan": plan,
        "verification_context": {"problem": problem},
    }
    decisions = {
        "schema_version": 1,
        "season": YEAR,
        "schedule_fingerprint": revision,
        "decisions": {
            tournament["id"]: {
                "status": "approved" if tournament["id"] in approved_ids else "pending_review",
                "placement_locked": tournament["id"] in approved_ids,
                "participants_locked": tournament["id"] in locked_ids,
                "approved_fingerprint": None,
            }
            for tournament in plan["tournaments"]
        },
    }
    (season_dir / "schedule.json").write_text(
        json.dumps(schedule, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (season_dir / "decisions.json").write_text(
        json.dumps(decisions, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return revision


def _distribution_findings(findings: Dict[str, Any], club: str = "Jar") -> List[Dict[str, Any]]:
    return [
        finding
        for finding in findings["findings"]
        if finding["code"] == "intra_club_participation_distribution" and finding["club"] == club
    ]


def _jar_counts(plan: Dict[str, Any]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for tournament in plan["tournaments"]:
        for team in tournament["teams"]:
            if team["club"] == "Jar":
                counts[team["label"]] = counts.get(team["label"], 0) + 1
    return counts


# ---------------------------------------------------------------------------
# Finding semantics
# ---------------------------------------------------------------------------


def test_aggregate_complete_uneven_pool_produces_one_actionable_finding(tmp_path: Path) -> None:
    problem = _problem()
    plan = _plan()
    assert verify_candidate(plan, problem)["ok"]
    root = tmp_path / "season"
    revision = _write_season(root, plan, problem)

    findings = list_findings(YEAR, root=root)

    assert findings["revision"] == revision
    before = [
        finding
        for finding in _distribution_findings(findings)
        if finding["scope"] == "before_christmas"
    ]
    assert len(before) == 1
    finding = before[0]
    assert finding["finding_id"] == intra_club_distribution_finding_id(
        "Jar", "U10", "before_christmas"
    )
    assert finding["rule_id"] == "intra_club_participation_distribution"
    assert finding["code"] == "intra_club_participation_distribution"
    assert finding["category"] == "club_distribution"
    # Soft/informational quality issue, never an unresolved participation
    # shortfall and never publication-blocking.
    assert finding["severity"] == "quality"
    assert finding["club_pool_actual"] == 4
    assert finding["club_pool_target"] == 4
    assert finding["spread"] == 2
    assert {(row["team"], row["actual"], row["target"]) for row in finding["team_distribution"]} == {
        ("Jar 1", 3, 2),
        ("Jar 2", 1, 2),
    }
    assert findings["counts_by_rule_id"]["intra_club_participation_distribution"] == 2


def test_even_pool_produces_no_finding(tmp_path: Path) -> None:
    problem = _problem()
    specs = [
        ("B1", "2026-09-12", "F1", ["Jar 1", "F1 1", "F2 1", "F3 1"]),
        ("B2", "2026-09-26", "F4", ["Jar 1", "F4 1", "F5 1", "F6 1"]),
        ("B3", "2026-10-10", "F7", ["Jar 2", "F7 1", "F8 1", "F9 1"]),
        ("B4", "2026-10-24", "F10", ["Jar 2", "F10 1", "F11 1", "F12 1"]),
        ("A1", "2027-01-16", "F1", ["Jar 1", "F1 1", "F2 1", "F3 1"]),
        ("A2", "2027-01-30", "F4", ["Jar 1", "F4 1", "F5 1", "F6 1"]),
        ("A3", "2027-02-13", "F7", ["Jar 2", "F7 1", "F8 1", "F9 1"]),
        ("A4", "2027-02-27", "F10", ["Jar 2", "F10 1", "F11 1", "F12 1"]),
    ]
    plan = _plan(specs)
    assert verify_candidate(plan, problem)["ok"]
    root = tmp_path / "season"
    _write_season(root, plan, problem)

    findings = list_findings(YEAR, root=root)

    assert _distribution_findings(findings) == []


def test_genuine_club_pool_shortfall_stays_a_shortage(tmp_path: Path) -> None:
    problem = _problem()
    for team in problem["teams"]:
        if team["club"] == "Jar":
            team["target_tournament_count"] = 8
    plan = _plan()
    root = tmp_path / "season"
    _write_season(root, plan, problem)

    findings = list_findings(YEAR, root=root)

    # The pool is genuinely short, so it stays an unresolved participation
    # finding and is never relabeled as pure distribution.
    assert _distribution_findings(findings) == []
    participation = [
        finding
        for finding in findings["findings"]
        if finding["category"] == "participation" and finding["club"] == "Jar"
    ]
    assert participation
    assert all(finding["avoidability"] != "operator_accepted" for finding in participation)


def test_scope_is_respected_for_before_and_after_christmas(tmp_path: Path) -> None:
    problem = _problem()
    plan = _plan()
    root = tmp_path / "season"
    revision = _write_season(root, plan, problem)

    before_options = repair_options(
        YEAR,
        intra_club_distribution_finding_id("Jar", "U10", "before_christmas"),
        root=root,
    )
    assert before_options["option_count"] >= 1

    applied = apply_repair(
        YEAR,
        before_options["options"][0]["option_id"],
        before_options["revision"],
        root=root,
        finding_id=intra_club_distribution_finding_id("Jar", "U10", "before_christmas"),
    )
    assert applied["ok"] is True, applied

    schedule = json.loads((root / YEAR / "schedule.json").read_text(encoding="utf-8"))
    after_plan = schedule["plan"]
    # The after-Christmas half was even and stays byte-identical; a
    # before-Christmas-scoped finding cannot change the other half's roster.
    before_ids = {"A1", "A2", "A3", "A4"}
    for tournament in after_plan["tournaments"]:
        if tournament["id"] in before_ids:
            assert tournament == next(
                item for item in plan["tournaments"] if item["id"] == tournament["id"]
            )
    # No after-Christmas distribution finding appeared.
    fresh = list_findings(YEAR, root=root)
    assert not [
        finding
        for finding in _distribution_findings(fresh)
        if finding["scope"] == "after_christmas"
    ]


# ---------------------------------------------------------------------------
# Direct substitution
# ---------------------------------------------------------------------------


def test_direct_substitution_improves_distribution_and_preserves_aggregate(tmp_path: Path) -> None:
    problem = _problem()
    plan = _plan()
    root = tmp_path / "season"
    _write_season(root, plan, problem)
    finding_id = intra_club_distribution_finding_id("Jar", "U10", "before_christmas")

    options = repair_options(YEAR, finding_id, root=root)

    assert options["option_count"] >= 1
    option = options["options"][0]
    effects = option["effects"]
    assert effects["scope"] == "before_christmas"
    assert effects["spread_before"] == 2
    assert effects["spread_after"] < effects["spread_before"]
    assert effects["club_pool_actual_before"] == effects["club_pool_actual_after"] == 4
    assert effects["distribution_before"] != effects["distribution_after"]

    applied = apply_repair(
        YEAR,
        option["option_id"],
        options["revision"],
        root=root,
        finding_id=finding_id,
    )

    assert applied["ok"] is True, applied
    schedule = json.loads((root / YEAR / "schedule.json").read_text(encoding="utf-8"))
    after_plan = schedule["plan"]
    # Aggregate participation is redistributed, never created or destroyed.
    assert sum(_jar_counts(after_plan).values()) == sum(_jar_counts(plan).values()) == 8
    before_counts = _jar_counts(plan)
    after_counts = _jar_counts(after_plan)
    assert set(after_counts) == set(before_counts)
    assert after_counts["Jar 1"] < before_counts["Jar 1"]
    assert after_counts["Jar 2"] > before_counts["Jar 2"]


def test_option_keeps_placement_host_and_guest_reservations_unchanged(tmp_path: Path) -> None:
    problem = _problem()
    plan = _plan()
    # A1 carries three RVV teams plus one filled guest place, so adding the
    # reservation does not change the tournament shape. The before-Christmas
    # repair must leave that reservation and every placement field untouched.
    guest_team = {"club": "Guest", "label": "Guest 1", "age_group": "U10", "guest": True}
    a1 = _tournament(
        "A1",
        "2027-01-16",
        "F1",
        ["Jar 1", "F1 1", "F2 1", "Guest 1"],
        [*_teams(), guest_team],
    )
    a1["guest_slots"] = [
        {"id": "guest:1", "status": "filled", "external_club": "Guest", "external_label": "Guest 1"}
    ]
    a1["reserved_guest_slots"] = 1
    plan["tournaments"] = [
        a1 if tournament["id"] == "A1" else tournament for tournament in plan["tournaments"]
    ]
    assert verify_candidate(plan, problem)["ok"]
    root = tmp_path / "season"
    _write_season(root, plan, problem)
    finding_id = intra_club_distribution_finding_id("Jar", "U10", "before_christmas")

    options = repair_options(YEAR, finding_id, root=root)
    assert options["option_count"] >= 1

    applied = apply_repair(
        YEAR,
        options["options"][0]["option_id"],
        options["revision"],
        root=root,
        finding_id=finding_id,
    )
    assert applied["ok"] is True, applied

    schedule = json.loads((root / YEAR / "schedule.json").read_text(encoding="utf-8"))
    after_by_id = {t["id"]: t for t in schedule["plan"]["tournaments"]}
    before_by_id = {t["id"]: t for t in plan["tournaments"]}
    assert set(after_by_id) == set(before_by_id)
    for tournament_id, before in before_by_id.items():
        after = after_by_id[tournament_id]
        # No date, arena, start time, host or age group ever moves.
        for key in ("date", "arena", "start_time", "host_club", "age_group"):
            assert after[key] == before[key], (tournament_id, key)
        # Guest reservations (reserved/filled/open) are byte-identical.
        assert after.get("guest_slots") == before.get("guest_slots")
        assert after.get("reserved_guest_slots") == before.get("reserved_guest_slots")
    assert _active_guest_counts(after_by_id) == _active_guest_counts(before_by_id)


def _active_guest_counts(by_id: Dict[str, Dict[str, Any]]) -> Dict[str, int]:
    return {
        tournament_id: len(
            [
                slot
                for slot in tournament.get("guest_slots") or []
                if str(slot.get("status") or "open") != "released"
            ]
        )
        for tournament_id, tournament in by_id.items()
    }


def test_same_club_hosting_responsibility_is_unchanged(tmp_path: Path) -> None:
    problem = _problem()
    plan = _plan()
    root = tmp_path / "season"
    _write_season(root, plan, problem)
    finding_id = intra_club_distribution_finding_id("Jar", "U10", "before_christmas")
    options = repair_options(YEAR, finding_id, root=root)

    applied = apply_repair(
        YEAR,
        options["options"][0]["option_id"],
        options["revision"],
        root=root,
        finding_id=finding_id,
    )

    assert applied["ok"] is True, applied
    schedule = json.loads((root / YEAR / "schedule.json").read_text(encoding="utf-8"))
    before_hosts = {t["id"]: t["host_club"] for t in plan["tournaments"]}
    after_hosts = {t["id"]: t["host_club"] for t in schedule["plan"]["tournaments"]}
    assert after_hosts == before_hosts


# ---------------------------------------------------------------------------
# Rejection gates
# ---------------------------------------------------------------------------


def test_duplicate_day_candidate_is_rejected() -> None:
    problem = _problem()
    # Jar 2 already plays on B1's date, so inserting it into B1 is a
    # duplicate-day conflict. B2/B3 remain available single substitutions.
    specs = [
        ("B1", "2026-09-12", "F1", ["Jar 1", "F1 1", "F2 1", "F3 1"]),
        ("B2", "2026-09-12", "F4", ["Jar 2", "F4 1", "F5 1", "F6 1"]),
        ("B3", "2026-09-26", "F7", ["Jar 1", "F7 1", "F8 1", "F9 1"]),
        ("B4", "2026-10-10", "F10", ["Jar 1", "F10 1", "F11 1", "F12 1"]),
    ]
    plan = _plan(specs)
    assert verify_candidate(plan, problem)["ok"]

    result = enumerate_intra_club_distribution_repairs(
        plan,
        problem,
        scope={"club": "Jar", "age_group": "U10", "scope": "before_christmas"},
    )

    duplicate = [
        entry
        for entry in result["rejected_candidates"]
        if entry.get("reason") == "duplicate_day_conflict"
    ]
    assert duplicate
    assert duplicate[0]["tournament_id"] == "B1"


def test_team_consequence_regression_is_rejected() -> None:
    problem = _problem()
    plan = _plan()

    result = enumerate_intra_club_distribution_repairs(
        plan,
        problem,
        scope={"club": "Jar", "age_group": "U10", "scope": "before_christmas"},
    )

    # B3 would remove Jar 1's only remaining pre-Christmas participation in
    # that part of the season and materially worsen its temporal coverage.
    regressions = [
        entry
        for entry in result["rejected_candidates"]
        if entry.get("reason") == "team_schedule_regression"
        and entry.get("tournament_id") == "B3"
    ]
    assert regressions
    assert regressions[0]["consequences"]["material_team_regressions"]


def test_home_representation_regression_is_rejected() -> None:
    problem = _problem()
    # Jar hosts H1 (Jar 1) and H2 (Jar 2): home representation is balanced.
    # Jar 1 is over-represented in the away tournaments, so the only way to fix
    # the distribution through a home tournament would skew home representation.
    specs = [
        ("H1", "2026-09-12", "Jar", ["Jar 1", "F1 1", "F2 1", "F3 1"]),
        ("H2", "2026-09-26", "Jar", ["Jar 2", "F4 1", "F5 1", "F6 1"]),
        ("A1", "2026-10-10", "F7", ["Jar 1", "F7 1", "F8 1", "F9 1"]),
        ("A2", "2026-10-24", "F10", ["Jar 1", "F10 1", "F11 1", "F12 1"]),
    ]
    plan = _plan(specs)
    assert verify_candidate(plan, problem)["ok"]

    result = enumerate_intra_club_distribution_repairs(
        plan,
        problem,
        scope={"club": "Jar", "age_group": "U10", "scope": "before_christmas"},
    )

    home_rejections = [
        entry
        for entry in result["rejected_candidates"]
        if entry.get("reason") == "home_representation_regression"
    ]
    assert home_rejections
    assert home_rejections[0]["tournament_id"] == "H1"
    # Away substitutions remain available so the pool is still repairable.
    assert any(
        option["arguments"]["swaps"][0]["tournament_id"] in {"A1", "A2"}
        for option in result["options"]
    )


def test_guest_reservations_are_not_consumed_or_released(tmp_path: Path) -> None:
    problem = _problem()
    plan = _plan()
    guest_team = {"club": "Guest", "label": "Guest 1", "age_group": "U10", "guest": True}
    a1 = _tournament(
        "A1",
        "2027-01-16",
        "F1",
        ["Jar 1", "F1 1", "F2 1", "Guest 1"],
        [*_teams(), guest_team],
    )
    a1["guest_slots"] = [
        {"id": "guest:1", "status": "filled", "external_club": "Guest", "external_label": "Guest 1"}
    ]
    a1["reserved_guest_slots"] = 1
    plan["tournaments"] = [
        a1 if tournament["id"] == "A1" else tournament for tournament in plan["tournaments"]
    ]
    root = tmp_path / "season"
    _write_season(root, plan, problem)
    finding_id = intra_club_distribution_finding_id("Jar", "U10", "before_christmas")

    options = repair_options(YEAR, finding_id, root=root)
    assert options["option_count"] >= 1
    applied = apply_repair(
        YEAR,
        options["options"][0]["option_id"],
        options["revision"],
        root=root,
        finding_id=finding_id,
    )
    assert applied["ok"] is True, applied
    schedule = json.loads((root / YEAR / "schedule.json").read_text(encoding="utf-8"))
    slots = {
        tournament["id"]: tournament.get("guest_slots")
        for tournament in schedule["plan"]["tournaments"]
    }
    assert slots["A1"] == [
        {"id": "guest:1", "status": "filled", "external_club": "Guest", "external_label": "Guest 1"}
    ]


def test_stale_revision_is_rejected_without_mutating_canonical_state(tmp_path: Path) -> None:
    problem = _problem()
    plan = _plan()
    root = tmp_path / "season"
    _write_season(root, plan, problem)
    finding_id = intra_club_distribution_finding_id("Jar", "U10", "before_christmas")
    options = repair_options(YEAR, finding_id, root=root)
    option_id = options["options"][0]["option_id"]
    schedule_file = root / YEAR / "schedule.json"
    before = schedule_file.read_bytes()

    result = apply_repair(YEAR, option_id, "deadbeef", root=root, finding_id=finding_id)

    assert result["ok"] is False
    assert result["reason"] == "stale_canonical_revision"
    assert result["canonical_revision_unchanged"] is True
    assert schedule_file.read_bytes() == before


def test_stale_option_fingerprint_is_rejected() -> None:
    problem = _problem()
    plan = _plan()
    result = enumerate_intra_club_distribution_repairs(
        plan,
        problem,
        scope={"club": "Jar", "age_group": "U10", "scope": "before_christmas"},
    )
    option = result["options"][0]

    applied = apply_intra_club_distribution_repair_option(
        plan,
        problem,
        option_id=option["option_id"],
        expected_fingerprint="deadbeef",
        arguments=option["arguments"],
    )

    assert applied["ok"] is False
    assert applied["reason"] == "stale_candidate_fingerprint"


# ---------------------------------------------------------------------------
# Search / apply round trip
# ---------------------------------------------------------------------------


def test_repair_options_search_and_apply_round_trip(tmp_path: Path) -> None:
    problem = _problem()
    plan = _plan()
    root = tmp_path / "season"
    revision = _write_season(root, plan, problem)
    finding_id = intra_club_distribution_finding_id("Jar", "U10", "before_christmas")

    options = repair_options(YEAR, finding_id, root=root)
    assert options["revision"] == revision
    assert options["option_count"] >= 1
    assert all(option["family"] == "intra_club_distribution" for option in options["options"])
    for option in options["options"]:
        assert option["effects"]["distribution_before"]
        assert option["effects"]["distribution_after"]
        assert option["effects"]["spread_before"] >= option["effects"]["spread_after"]

    searched = search(YEAR, finding_id, root=root)
    assert searched["finding"]["finding_id"] == finding_id
    assert searched["option_count"] >= 1
    chosen = searched["options"][0]

    applied = apply_repair(
        YEAR,
        chosen["option_id"],
        searched["revision"],
        root=root,
        finding_id=finding_id,
    )

    assert applied["ok"] is True, applied
    assert applied["delta"]["hard_violations_after"] == 0
    assert applied["fresh_findings"]["revision"] == applied["revision_after"]
    remaining = [
        finding
        for finding in _distribution_findings(applied["fresh_findings"])
        if finding["scope"] == "before_christmas"
    ]
    assert remaining == [], remaining


def test_bounded_search_with_no_option_is_not_proven_infeasible(tmp_path: Path) -> None:
    problem = _problem()
    plan = _plan()
    # Every before-Christmas Jar 1 tournament is participant-locked, so no
    # substitution can change anything. The bounded search finds nothing but
    # never claims infeasibility.
    root = tmp_path / "season"
    _write_season(root, plan, problem, participants_locked=["B1", "B2", "B3"])
    finding_id = intra_club_distribution_finding_id("Jar", "U10", "before_christmas")

    result = search(YEAR, finding_id, root=root)

    coverage = result["finding"]["search_coverage"]
    assert result["option_count"] == 0
    assert coverage["status"] == "bounded_search_exhausted"
    assert coverage["proven_infeasible"] is False


def test_search_coverage_stays_incomplete_for_cheap_listing(tmp_path: Path) -> None:
    problem = _problem()
    plan = _plan()
    root = tmp_path / "season"
    _write_season(root, plan, problem)
    finding_id = intra_club_distribution_finding_id("Jar", "U10", "before_christmas")

    findings = list_findings(YEAR, root=root)
    finding = next(item for item in findings["findings"] if item["finding_id"] == finding_id)

    assert finding["search_coverage"]["status"] == "search_incomplete"
    assert finding["search_coverage"]["proven_infeasible"] is False
    assert finding["search_coverage"]["supported"] == ["participants"]


def test_participants_locked_tournament_is_not_chosen(tmp_path: Path) -> None:
    problem = _problem()
    plan = _plan()
    root = tmp_path / "season"
    _write_season(root, plan, problem, participants_locked=["B1"])
    finding_id = intra_club_distribution_finding_id("Jar", "U10", "before_christmas")

    options = repair_options(YEAR, finding_id, root=root)

    assert options["option_count"] >= 1
    assert all(
        option["arguments"]["swaps"][0]["tournament_id"] != "B1" for option in options["options"]
    )


__all__ = []
