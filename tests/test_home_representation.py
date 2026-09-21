"""Intra-club home-representation accounting and sibling-swap repair tests.

These cover the deterministic accounting, the shared quality objective
integration, the bounded sibling-swap provider (a simple rotation and a coupled
home + away swap that preserves half-season participation counts) and the audit
evidence projection.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from tournament_scheduler.home_representation import (
    home_representation_facts,
    home_representation_rows,
    multi_team_pools,
)
from tournament_scheduler.home_representation_repair import (
    apply_home_representation_repair_option,
    enumerate_home_representation_repairs,
    home_representation_finding_id,
)
from tournament_scheduler.planning_contract import (
    build_planning_problem,
    score_candidate,
    verify_candidate,
)
from tournament_scheduler.pipeline.audit_evidence import build_evidence_index
from tournament_scheduler.season_maintenance import (
    apply_repair,
    list_findings,
    repair_options,
)
from tournament_scheduler.season_state import schedule_fingerprint

YEAR = "2026-2027"


def _teams(club: str, label_prefix: str, age_group: str, count: int = 1) -> List[Dict[str, str]]:
    return [
        {"club": club, "label": f"{label_prefix}{index}", "age_group": age_group}
        for index in range(1, count + 1)
    ]


def _other_teams() -> List[Dict[str, str]]:
    return [
        {"club": "Nordby", "label": "Nordby 1", "age_group": "U10"},
        {"club": "Sorby", "label": "Sorby 1", "age_group": "U10"},
        {"club": "Vestby", "label": "Vestby 1", "age_group": "U10"},
    ]


def _problem() -> Dict[str, Any]:
    teams = _teams("Ringerike", "Ringerike ", "U10", 2) + _other_teams()
    config: Dict[str, Any] = {
        "teams": teams,
        "age_groups": ["U10"],
        "parallel_games": {"U10": 2},
        "round_length_minutes": {"U10": 30},
        "ice_time_minutes": {"U10": 120},
        "rounds_per_tournament": {"U10": 3},
        "participation_targets_by_age_group": {
            "U10": {"before_christmas": 2, "after_christmas": 2}
        },
    }
    problem = build_planning_problem(config, None, date(2026, 9, 1), date(2027, 4, 30))
    problem["clubs"] = {
        "Ringerike": "Ringerike Arena",
        "Nordby": "Nordby Arena",
        "Sorby": "Sorby Arena",
        "Vestby": "Vestby Arena",
    }
    return problem


def _four_team_games(labels: List[str]) -> List[Dict[str, Any]]:
    """A complete 3-round, no-bye round robin for exactly four teams."""
    a, b, c, d = labels
    return [
        {"home": a, "away": b, "parallel_slot": 0, "round_number": 1},
        {"home": c, "away": d, "parallel_slot": 1, "round_number": 1},
        {"home": a, "away": c, "parallel_slot": 0, "round_number": 2},
        {"home": b, "away": d, "parallel_slot": 1, "round_number": 2},
        {"home": a, "away": d, "parallel_slot": 0, "round_number": 3},
        {"home": b, "away": c, "parallel_slot": 1, "round_number": 3},
    ]


def _tournament(
    tournament_id: str,
    day: str,
    host: str,
    labels: List[str],
    teams: List[Dict[str, str]],
) -> Dict[str, Any]:
    by_label = {team["label"]: team for team in teams}
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


def _plan(tournaments: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "schema_version": 1,
        "start_date": "2026-09-01",
        "end_date": "2027-04-30",
        "tournaments": tournaments,
    }


def _write_season(
    root: Path,
    plan: Dict[str, Any],
    problem: Dict[str, Any],
    *,
    approved: Optional[Iterable[str]] = None,
) -> str:
    season_dir = root / YEAR
    season_dir.mkdir(parents=True, exist_ok=True)
    revision = schedule_fingerprint(plan)
    approved_ids = set(approved or ())
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
                "participants_locked": False,
                "approved_fingerprint": None,
            }
            for tournament in plan["tournaments"]
        },
    }
    (season_dir / "schedule.json").write_text(
        json.dumps(schedule, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (season_dir / "decisions.json").write_text(
        json.dumps(decisions, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return revision


# ---------------------------------------------------------------------------
# Accounting
# ---------------------------------------------------------------------------


def test_accounting_counts_sibling_home_appearances_and_spread() -> None:
    teams = _teams("Ringerike", "Ringerike ", "U10", 2) + _teams("Nordby", "Nordby 1", "U10")
    tournaments = [
        {"id": "H1", "date": "2026-09-19", "host_club": "Ringerike", "age_group": "U10",
         "teams": [{"club": "Ringerike", "label": "Ringerike 2", "age_group": "U10"}]},
        {"id": "H2", "date": "2026-10-17", "host_club": "Ringerike", "age_group": "U10",
         "teams": [{"club": "Ringerike", "label": "Ringerike 2", "age_group": "U10"}]},
        {"id": "H3", "date": "2026-11-14", "host_club": "Ringerike", "age_group": "U10",
         "teams": [{"club": "Ringerike", "label": "Ringerike 1", "age_group": "U10"}]},
        {"id": "A1", "date": "2026-09-05", "host_club": "Nordby", "age_group": "U10",
         "teams": [{"club": "Ringerike", "label": "Ringerike 1", "age_group": "U10"}]},
    ]

    rows = home_representation_rows(teams, tournaments)

    assert multi_team_pools(teams) == [("Ringerike", "U10")]
    assert len(rows) == 1
    row = rows[0]
    assert row["home_appearances"] == {"Ringerike 1": 1, "Ringerike 2": 2}
    assert row["spread"] == 1
    assert row["material_spread"] == 0
    assert row["balanced"] is True
    assert row["evidence"] == "Ringerike U10: Ringerike 2=2, Ringerike 1=1 home appearance(s)"


def test_accounting_marks_material_skew_and_summarizes() -> None:
    teams = _teams("Ringerike", "Ringerike ", "U10", 2)
    tournaments = [
        {"id": f"H{index}", "date": "2026-09-01", "host_club": "Ringerike", "age_group": "U10",
         "teams": [{"club": "Ringerike", "label": "Ringerike 2", "age_group": "U10"}]}
        for index in range(6)
    ]
    tournaments.append(
        {"id": "H0", "date": "2026-09-01", "host_club": "Ringerike", "age_group": "U10",
         "teams": [{"club": "Ringerike", "label": "Ringerike 1", "age_group": "U10"}]}
    )

    summary = home_representation_facts(teams, tournaments)

    assert summary["pool_count"] == 1
    assert summary["skewed_pool_count"] == 1
    assert summary["max_spread"] == 5
    assert summary["max_material_spread"] == 4
    assert summary["pools"][0]["home_appearances"] == {"Ringerike 1": 1, "Ringerike 2": 6}


def test_score_candidate_exposes_home_representation_metric() -> None:
    problem = _problem()
    teams = _teams("Ringerike", "Ringerike ", "U10", 2) + _other_teams()
    plan = _plan(
        [
            _tournament("H1", "2026-09-19", "Ringerike", ["Ringerike 2", "Nordby 1", "Sorby 1", "Vestby 1"], teams),
            _tournament("H2", "2026-10-17", "Ringerike", ["Ringerike 2", "Nordby 1", "Sorby 1", "Vestby 1"], teams),
        ]
    )
    score = score_candidate(plan, problem=problem)
    assert score["home_representation"]["max_spread"] == 2
    assert score["home_representation"]["material_skew_pool_count"] == 1


# ---------------------------------------------------------------------------
# Provider: simple rotation and coupled swap
# ---------------------------------------------------------------------------


def _all_teams() -> List[Dict[str, str]]:
    return _teams("Ringerike", "Ringerike ", "U10", 2) + _other_teams()


def _simple_rotation_plan() -> Dict[str, Any]:
    all_teams = _all_teams()
    return _plan(
        [
            _tournament("A1", "2026-09-05", "Nordby", ["Ringerike 1", "Nordby 1", "Sorby 1", "Vestby 1"], all_teams),
            _tournament("H1", "2026-09-19", "Ringerike", ["Ringerike 2", "Nordby 1", "Sorby 1", "Vestby 1"], all_teams),
            _tournament("H2", "2026-10-17", "Ringerike", ["Ringerike 2", "Nordby 1", "Sorby 1", "Vestby 1"], all_teams),
            _tournament("H3", "2026-11-14", "Ringerike", ["Ringerike 2", "Nordby 1", "Sorby 1", "Vestby 1"], all_teams),
        ]
    )


def _coupled_swap_plan() -> Dict[str, Any]:
    all_teams = _all_teams()
    return _plan(
        [
            _tournament("A1", "2026-09-05", "Nordby", ["Ringerike 1", "Nordby 1", "Sorby 1", "Vestby 1"], all_teams),
            # One day after A1: a simple rotation would create a same-weekend
            # (<7-day) double for Ringerike 1, so only the coupled home + away
            # swap is acceptable.
            _tournament("H1", "2026-09-06", "Ringerike", ["Ringerike 2", "Nordby 1", "Sorby 1", "Vestby 1"], all_teams),
            _tournament("H2", "2026-10-17", "Ringerike", ["Ringerike 2", "Nordby 1", "Sorby 1", "Vestby 1"], all_teams),
        ]
    )


def _option_by_swaps(result: Dict[str, Any], count: int, tournament_ids: Iterable[str]) -> Dict[str, Any]:
    wanted = set(tournament_ids)
    for option in result["options"]:
        swaps = option["arguments"]["swaps"]
        if len(swaps) == count and {swap["tournament_id"] for swap in swaps} == wanted:
            return option
    raise AssertionError(f"no option with {count} swaps on {sorted(wanted)}")


def test_enumerate_offers_a_simple_sibling_rotation() -> None:
    problem = _problem()
    plan = _simple_rotation_plan()

    result = enumerate_home_representation_repairs(plan, problem)
    assert verify_candidate(plan, problem)["ok"]

    option = _option_by_swaps(result, 1, ["H1"])
    assert option["effects"]["consequence_acceptable"] is True
    assert option["effects"]["home_spread_before"] == 3
    assert option["effects"]["home_spread_after"] == 1
    assert option["effects"]["home_appearances_after"] == {"Ringerike 1": 1, "Ringerike 2": 2}


def test_enumerate_offers_a_coupled_home_away_swap_preserving_participation() -> None:
    problem = _problem()
    plan = _coupled_swap_plan()

    result = enumerate_home_representation_repairs(plan, problem)
    option = _option_by_swaps(result, 2, ["H1", "A1"])
    assert option["effects"]["consequence_acceptable"] is True
    assert option["effects"]["home_spread_after"] == 0
    # The coupled swap improves the home split without regressing any shared
    # quality metric -- the Ringerike-shape "improve without regressions" case.
    assert option["effects"]["quality_regression_count"] == 0

    applied = apply_home_representation_repair_option(
        plan,
        problem,
        option_id=option["option_id"],
        expected_fingerprint=result["candidate_fingerprint"],
        arguments=option["arguments"],
    )
    assert applied["ok"] is True
    candidate = applied["candidate"]
    assert verify_candidate(candidate, problem)["ok"]

    def counts(candidate_plan: Dict[str, Any]) -> Dict[str, int]:
        out: Dict[str, int] = {}
        for tournament in candidate_plan["tournaments"]:
            for team in tournament["teams"]:
                if team["club"] == "Ringerike":
                    out[team["label"]] = out.get(team["label"], 0) + 1
        return out

    before = counts(plan)
    after = counts(candidate)
    # Both siblings keep their exact season participation counts.
    assert after == before == {"Ringerike 1": 1, "Ringerike 2": 2}


def test_simple_rotation_is_rejected_when_it_creates_a_same_weekend_double() -> None:
    problem = _problem()
    plan = _coupled_swap_plan()

    result = enumerate_home_representation_repairs(plan, problem)

    simple = [
        option
        for option in result["options"]
        if len(option["arguments"]["swaps"]) == 1 and option["arguments"]["swaps"][0]["tournament_id"] == "H1"
    ]
    assert simple
    # The simple rotation exists as enumerated evidence but is not acceptable.
    assert all(option["effects"]["consequence_acceptable"] is False for option in simple)


# ---------------------------------------------------------------------------
# Promoted-season maintenance integration
# ---------------------------------------------------------------------------


def test_maintenance_finding_and_repair_round_trip(tmp_path: Path) -> None:
    problem = _problem()
    plan = _simple_rotation_plan()
    root = tmp_path / "season"
    _write_season(root, plan, problem)

    findings = list_findings(YEAR, root=root)
    finding_id = home_representation_finding_id("U10", "Ringerike")
    finding = next(item for item in findings["findings"] if item["finding_id"] == finding_id)
    assert finding["code"] == "home_representation_skew"
    assert finding["severity"] == "quality"

    options = repair_options(YEAR, finding_id, root=root)
    assert options["option_count"] >= 1
    chosen = options["options"][0]
    applied = apply_repair(
        YEAR,
        chosen["option_id"],
        options["revision"],
        root=root,
        finding_id=finding_id,
    )
    assert applied["ok"] is True
    assert applied["delta"]["hard_violations_after"] == 0
    after = list_findings(YEAR, root=root)
    assert home_representation_finding_id("U10", "Ringerike") not in {
        item["finding_id"] for item in after["findings"]
    }


# ---------------------------------------------------------------------------
# Audit evidence
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Club-pool-aware consequence evaluation (#411)
# ---------------------------------------------------------------------------


def _aggregate_preserving_plan() -> Dict[str, Any]:
    """Ringerike 2 over-represented at home (2/0) while both sit far under

    their season target -- a single swap keeps the club-pool aggregate exactly
    unchanged (2 -> 2) but makes the sibling split materially more even
    (season spread 2 -> 0), matching the issue's "Blå/Hvit" example.
    """
    all_teams = _all_teams()
    return _plan(
        [
            _tournament("H1", "2026-09-19", "Ringerike", ["Ringerike 2", "Nordby 1", "Sorby 1", "Vestby 1"], all_teams),
            _tournament("H2", "2026-10-17", "Ringerike", ["Ringerike 2", "Nordby 1", "Sorby 1", "Vestby 1"], all_teams),
        ]
    )


def test_swap_accepted_when_aggregate_preserved_and_distribution_improves() -> None:
    problem = _problem()
    plan = _aggregate_preserving_plan()

    result = enumerate_home_representation_repairs(plan, problem)
    option = _option_by_swaps(result, 1, ["H1"])

    assert option["effects"]["consequence_acceptable"] is True
    assert option["effects"]["home_spread_before"] == 2
    assert option["effects"]["home_spread_after"] == 0

    # The exact per-team season target changed for Ringerike 2 (an individual
    # "participation_shortfall_worsened" fact), but it must not block the
    # option: the club-pool aggregate is unchanged and the sibling season
    # distribution got strictly more even, exactly the redistribution the
    # issue describes.
    consequences = result["rejected_candidates"]
    assert not any(
        entry.get("reason") == "team_schedule_regression"
        and entry.get("swaps", [{}])[0].get("tournament_id") == "H1"
        for entry in consequences
    )


def _distribution_worsening_plan() -> Dict[str, Any]:
    """Ringerike 2 has more home appearances than Ringerike 1 (2 vs 0), but

    Ringerike 1 already has *more* season participation overall (3 away games
    vs Ringerike 2's 2 home games). A simple rotation therefore improves home
    representation while making the sibling *season* distribution materially
    worse (spread 1 -> 3): this must stay rejected even though the club-pool
    aggregate is preserved.
    """
    all_teams = _all_teams()
    return _plan(
        [
            _tournament("A1", "2026-09-05", "Sorby", ["Ringerike 1", "Nordby 1", "Sorby 1", "Vestby 1"], all_teams),
            _tournament("A2", "2026-10-03", "Vestby", ["Ringerike 1", "Nordby 1", "Sorby 1", "Vestby 1"], all_teams),
            _tournament("A3", "2026-10-31", "Nordby", ["Ringerike 1", "Nordby 1", "Sorby 1", "Vestby 1"], all_teams),
            _tournament("H1", "2026-09-19", "Ringerike", ["Ringerike 2", "Nordby 1", "Sorby 1", "Vestby 1"], all_teams),
            _tournament("H2", "2026-10-17", "Ringerike", ["Ringerike 2", "Nordby 1", "Sorby 1", "Vestby 1"], all_teams),
        ]
    )


def test_swap_rejected_when_sibling_distribution_materially_worsens() -> None:
    import tournament_scheduler.home_representation_repair as module

    problem = _problem()
    plan = _distribution_worsening_plan()
    rows = home_representation_rows(problem["teams"], plan["tournaments"])
    row = next(row for row in rows if row["club"] == "Ringerike" and row["age_group"] == "U10")

    # A direct simple rotation improves home representation but makes the
    # sibling *season* distribution materially worse (spread 1 -> 3): the
    # narrow exemption must not apply here, so the option stays rejected.
    swaps = [
        {
            "tournament_id": "H1",
            "remove": {"club": "Ringerike", "label": "Ringerike 2", "age_group": "U10"},
            "add": {"club": "Ringerike", "label": "Ringerike 1", "age_group": "U10"},
        }
    ]
    trial = module._apply_swaps(plan, problem, swaps)
    consequences = module.changed_team_consequences(
        plan, trial, problem, swapped_identities=module._swapped_identities(swaps)
    )
    assert consequences["consequence_acceptable"] is False
    assert any(
        entry["regression"]["code"] == "participation_shortfall_worsened"
        for entry in consequences["strict_material_regressions"]
    )


def test_hard_maximum_still_blocks_a_swap_despite_aggregate_preservation() -> None:
    import tournament_scheduler.home_representation_repair as module

    problem = _problem()
    for team in problem["teams"]:
        if team.get("club") == "Ringerike" and team.get("label") == "Ringerike 1":
            team["participation_hard_max"] = 0
    plan = _aggregate_preserving_plan()

    swaps = [
        {
            "tournament_id": "H1",
            "remove": {"club": "Ringerike", "label": "Ringerike 2", "age_group": "U10"},
            "add": {"club": "Ringerike", "label": "Ringerike 1", "age_group": "U10"},
        }
    ]
    trial = module._apply_swaps(plan, problem, swaps)
    consequences = module.changed_team_consequences(
        plan, trial, problem, swapped_identities=module._swapped_identities(swaps)
    )
    assert consequences["consequence_acceptable"] is False
    codes = {entry["regression"]["code"] for entry in consequences["strict_material_regressions"]}
    assert "participation_hard_max_exceeded" in codes


# ---------------------------------------------------------------------------
# Tier-2 bounded search: 3-sibling participation-preserving cycle
# ---------------------------------------------------------------------------


def test_cycle_moves_yields_a_participation_preserving_three_swap_plan() -> None:
    import tournament_scheduler.home_representation_repair as module

    teams = _teams("Ringerike", "Ringerike ", "U10", 3) + _other_teams()
    all_teams = teams
    plan = _plan(
        [
            _tournament("H1", "2026-09-19", "Ringerike", ["Ringerike 1", "Nordby 1", "Sorby 1", "Vestby 1"], all_teams),
            _tournament("H2", "2026-10-17", "Ringerike", ["Ringerike 1", "Nordby 1", "Sorby 1", "Vestby 1"], all_teams),
            _tournament("A2", "2026-11-14", "Sorby", ["Ringerike 2", "Nordby 1", "Sorby 1", "Vestby 1"], all_teams),
            _tournament("A3", "2026-12-05", "Vestby", ["Ringerike 3", "Nordby 1", "Sorby 1", "Vestby 1"], all_teams),
        ]
    )
    problem = _problem()
    problem["teams"] = teams

    rows = home_representation_rows(teams, plan["tournaments"])
    row = next(row for row in rows if row["club"] == "Ringerike" and row["age_group"] == "U10")
    assert row["home_appearances"] == {"Ringerike 1": 2, "Ringerike 2": 0, "Ringerike 3": 0}

    cycles = list(module._cycle_moves(plan, problem, row))
    assert cycles, "expected at least one bounded 3-sibling cycle"

    swaps = cycles[0]
    assert len(swaps) == 3
    trial = module._apply_swaps(plan, problem, swaps)
    assert trial is not None

    def counts(candidate_plan: Dict[str, Any]) -> Dict[str, int]:
        out: Dict[str, int] = {}
        for tournament in candidate_plan["tournaments"]:
            for team in tournament["teams"]:
                if team["club"] == "Ringerike":
                    out[team["label"]] = out.get(team["label"], 0) + 1
        return out

    # Every sibling's own season participation count is unchanged: the cycle
    # only moves *which* sibling represents the club at each tournament.
    assert counts(trial) == counts(plan)


def test_home_representation_is_projected_into_audit_evidence() -> None:
    raw = {
        "plan_audit_facts": {
            "home_representation": [
                {
                    "club": "Ringerike",
                    "age_group": "U12",
                    "team_count": 2,
                    "home_tournament_count": 6,
                    "home_appearances": {"Ringerike 1": 1, "Ringerike 2": 6},
                    "spread": 5,
                    "material_spread": 4,
                    "balanced": False,
                    "evidence": "Ringerike U12: Ringerike 2=6, Ringerike 1=1 home appearance(s)",
                }
            ]
        }
    }

    index = build_evidence_index(raw)
    records = [record for record in index["records"] if record["category"] == "home_representation"]
    assert len(records) == 1
    assert records[0]["finding_type"] == "home_representation_skew"
    assert "Ringerike U12" in records[0]["summary"]
    assert records[0]["club"] == "Ringerike"
    assert records[0]["age_group"] == "U12"
