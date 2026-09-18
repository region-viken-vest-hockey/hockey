"""Promoted-season finding/repair maintenance loop tests.

These exercise the Ringerike-style production regression on a synthetic
promoted season: fresh findings, finding-directed repair options, an atomic
revision-bound apply, and a deterministic before/after delta. They never set up
a ``.pipeline`` work directory, which is the point -- repairing an already
promoted season must not require re-running Stage 1/2.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from tournament_scheduler.participation_deviation_repair import _classification
from tournament_scheduler.pareto import non_dominated_indices
from tournament_scheduler.planning_contract import build_planning_problem, verify_candidate
from tournament_scheduler.season_maintenance import (
    PARETO_DIMENSIONS,
    accept_finding,
    apply_repair,
    list_findings,
    repair_options,
    revoke_acceptance,
    search,
)
from tournament_scheduler.season_state import (
    load_participation_acceptances,
    schedule_fingerprint,
)

YEAR = "2026-2027"


def _teams(clubs: Iterable[str]) -> List[Dict[str, str]]:
    return [
        {"club": club, "label": f"{club} {index}", "age_group": "U10"}
        for club in clubs
        for index in (1, 2)
    ]


def _problem(
    teams: List[Dict[str, str]],
    *,
    parallel_games: int = 2,
    participation_targets: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    config: Dict[str, Any] = {
        "teams": teams,
        "age_groups": ["U10"],
        "parallel_games": {"U10": parallel_games},
        "round_length_minutes": {"U10": 30},
        "ice_time_minutes": {"U10": 90},
        "rounds_per_tournament": {"U10": 3},
    }
    if participation_targets is not None:
        config["participation_targets_by_age_group"] = participation_targets
    problem = build_planning_problem(config, None, date(2026, 9, 1), date(2027, 4, 30))
    problem["clubs"] = {club: f"{club} Arena" for club in {team["club"] for team in teams}}
    return problem


def _tournament(
    tournament_id: str,
    day: str,
    host: str,
    teams: List[Dict[str, str]],
) -> Dict[str, Any]:
    labels = [team["label"] for team in teams]
    games = [
        {
            "home": labels[index],
            "away": labels[(index + 1) % len(labels)],
            "parallel_slot": 0,
            "round_number": 1,
        }
        for index in range(len(labels))
    ]
    return {
        "id": tournament_id,
        "date": day,
        "arena": f"{host} Arena",
        "age_group": "U10",
        "host_club": host,
        "teams": teams,
        "games": games,
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
        json.dumps(schedule, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (season_dir / "decisions.json").write_text(
        json.dumps(decisions, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return revision


def _two_club_season(tmp_path: Path, *, approved: Optional[Iterable[str]] = None):
    teams = _teams(["Nordby", "Sorby"])
    problem = _problem(teams)
    plan = _plan(
        [
            _tournament("T1", "2026-10-10", "Nordby", teams),
            _tournament("T2", "2026-11-14", "Nordby", teams),
        ]
    )
    root = tmp_path / "season"
    revision = _write_season(root, plan, problem, approved=approved)
    return root, plan, problem, revision


def test_findings_expose_unresolved_hosting_obligation_bound_to_revision(tmp_path: Path) -> None:
    root, _plan, _problem_dict, revision = _two_club_season(tmp_path)

    findings = list_findings(YEAR, root=root)

    assert findings["revision"] == revision
    hosting = [finding for finding in findings["findings"] if finding["category"] == "hosting"]
    assert [finding["finding_id"] for finding in hosting] == ["hosting_balance:U10:Sorby"]
    assert hosting[0]["code"] == "unresolved_hosting_obligation"
    assert hosting[0]["club"] == "Sorby"
    assert hosting[0]["deficit"] == 1


def test_unrelated_findings_do_not_impose_queue_order(tmp_path: Path) -> None:
    teams = _teams(["Nordby", "Sorby"])
    problem = _problem(
        teams,
        participation_targets={"U10": {"before_christmas": 3, "after_christmas": 3}},
    )
    plan = _plan(
        [
            _tournament("T1", "2026-10-10", "Nordby", teams),
            _tournament("T2", "2026-11-14", "Nordby", teams),
        ]
    )
    root = tmp_path / "season"
    _write_season(root, plan, problem)

    findings = list_findings(YEAR, root=root)

    # A hosting finding and a participation finding coexist; either may be
    # selected directly without first processing the other.
    categories = {finding["category"] for finding in findings["findings"]}
    assert {"hosting", "participation"} <= categories
    hosting = repair_options(YEAR, "hosting_balance:U10:Sorby", root=root)
    assert hosting["option_count"] >= 1
    participation = [f for f in findings["findings"] if f["category"] == "participation"]
    assert participation
    assert search(YEAR, participation[0]["finding_id"], root=root)["finding"]["finding_id"] == participation[0]["finding_id"]


def test_repair_options_offer_surplus_donor_and_preserve_unrelated(tmp_path: Path) -> None:
    root, plan, _problem_dict, revision = _two_club_season(tmp_path)

    options = repair_options(YEAR, "hosting_balance:U10:Sorby", root=root)

    assert options["revision"] == revision
    assert options["options"]
    assert all(option["family"] == "hosting_balance" for option in options["options"])
    assert {option["effects"]["donor_host"] for option in options["options"]} == {"Nordby"}
    assert all(option["effects"]["donor_host_excess_before"] == 1 for option in options["options"])
    # Only one tournament changes; the other must survive byte-identically.
    assert all(option["effects"]["changed_tournament_count"] == 1 for option in options["options"])
    assert plan["tournaments"][1]["id"] == "T2"


def test_apply_repair_is_revision_bound_and_returns_fresh_delta(tmp_path: Path) -> None:
    root, plan, _problem_dict, revision = _two_club_season(tmp_path)
    options = repair_options(YEAR, "hosting_balance:U10:Sorby", root=root)
    chosen = options["options"][0]

    result = apply_repair(
        YEAR,
        chosen["option_id"],
        revision,
        root=root,
        finding_id="hosting_balance:U10:Sorby",
    )

    assert result["ok"] is True
    assert result["revision_before"] == revision
    assert result["revision_after"] and result["revision_after"] != revision
    delta = result["delta"]
    assert delta["hard_violations_after"] == 0
    assert (delta["unresolved_hosting_obligations_before"], delta["unresolved_hosting_obligations_after"]) == (1, 0)
    assert (delta["hosting_balance_imbalances_before"], delta["hosting_balance_imbalances_after"]) == (2, 0)
    assert delta["changed_tournament_count"] == 1
    # Fresh, revision-consistent evidence: no findings remain on the new revision.
    assert result["fresh_findings"]["revision"] == result["revision_after"]
    assert result["fresh_findings"]["finding_count"] == 0


def test_search_then_apply_with_explicit_dimensions(tmp_path: Path) -> None:
    root, _plan_dict, _problem_dict, revision = _two_club_season(tmp_path)

    result = search(
        YEAR,
        "hosting_balance:U10:Sorby",
        root=root,
        dimensions=("participants", "host"),
    )

    assert result["requested_dimensions"] == ["host", "participants"]
    assert result["options"]
    chosen = result["options"][0]
    applied = apply_repair(
        YEAR,
        chosen["option_id"],
        revision,
        root=root,
        finding_id="hosting_balance:U10:Sorby",
        dimensions=("participants", "host"),
    )
    assert applied["ok"] is True
    assert applied["delta"]["unresolved_hosting_obligations_after"] == 0


def test_search_option_identity_is_stable_and_applyable_without_replaying_dimensions(
    tmp_path: Path,
) -> None:
    """A bounded search option is reproducible across enumerations and applyable.

    The option id must not embed the produced candidate's fingerprint: an
    optimizer result carries wall-clock ``source.timings``, so that id changed
    on every enumeration and ``apply-repair`` could never reproduce it.
    """
    root, _plan_dict, _problem_dict, revision = _two_club_season(tmp_path)
    dimensions = ("participants", "host", "date", "slot")

    first = search(YEAR, "hosting_balance:U10:Sorby", root=root, dimensions=dimensions)
    second = search(YEAR, "hosting_balance:U10:Sorby", root=root, dimensions=dimensions)

    assert [option["option_id"] for option in first["options"]] == [
        option["option_id"] for option in second["options"]
    ]
    option = next(entry for entry in first["options"] if entry["action"] == "search")
    assert option["arguments"]["dimensions"] == sorted(dimensions)

    # The default apply dimensions differ from the search; the option's own
    # dimension tag is authoritative, so no flag replay is required.
    applied = apply_repair(
        YEAR,
        option["option_id"],
        revision,
        root=root,
        finding_id="hosting_balance:U10:Sorby",
    )

    assert applied["ok"] is True, applied
    assert applied["delta"]["unresolved_hosting_obligations_after"] == 0


def test_search_dimension_tag_round_trips() -> None:
    from tournament_scheduler.host_team_missing_repair import (
        search_dimension_tag,
        search_dimensions_from_option_id,
    )

    option_id = f"abc:hosting_balance:U10:Sorby:search:0:{search_dimension_tag(('slot', 'date', 'host', 'participants'))}"

    assert search_dimensions_from_option_id(option_id) == (
        "date",
        "host",
        "participants",
        "slot",
    )
    # A non-search option id carries no dimension tag.
    assert search_dimensions_from_option_id("abc:hosting_balance:U10:Sorby:rehost:T1") is None


def test_repair_options_expose_a_non_dominated_pareto_front(tmp_path: Path) -> None:
    """Every option is measured on the shared vector; equal trade-offs de-duplicate.

    Two donor rehosts for the same deficit commit the same candidate vector, so
    only the first survives the dominance/deduplication step. That is the exact
    behaviour that keeps a verified option set from being presented as if every
    legal mutation were an independent trade-off.
    """
    root, _plan, _problem_dict, _revision = _two_club_season(tmp_path)

    report = repair_options(YEAR, "hosting_balance:U10:Sorby", root=root)

    pareto = report["pareto"]
    assert pareto["dimensions"] == list(PARETO_DIMENSIONS)
    assert pareto["measured_option_count"] == report["option_count"] == 2
    assert pareto["front_size"] == 1
    assert set(pareto["representative_option_ids"]) <= set(pareto["non_dominated_option_ids"])
    for option in report["options"]:
        assert set(option["objectives"]) == set(PARETO_DIMENSIONS)
        # The vector describes the candidate the option would actually commit.
        assert option["objectives"]["unresolved_hosting_obligations"] == 0.0
        assert option["objectives"]["hosting_balance_imbalances"] == 0.0
        assert option["objectives"]["changed_tournament_count"] == 1.0
    assert [option["non_dominated"] for option in report["options"]].count(True) == 1

    # The reported front is exactly the non-dominated set of the measured vectors.
    vectors = [option["objectives"] for option in report["options"]]
    expected_front = [report["options"][i]["option_id"] for i in non_dominated_indices(vectors)]
    assert pareto["non_dominated_option_ids"] == expected_front


def test_search_reports_the_same_pareto_surface(tmp_path: Path) -> None:
    root, _plan_dict, _problem_dict, _revision = _two_club_season(tmp_path)

    result = search(YEAR, "hosting_balance:U10:Sorby", root=root)

    assert "pareto" in result
    assert result["pareto"]["dimensions"] == list(PARETO_DIMENSIONS)
    assert result["pareto"]["measured_option_count"] == result["option_count"]
    for option in result["options"]:
        assert isinstance(option["non_dominated"], bool)
        assert option["objectives"] is not None


def test_search_measures_bounded_search_options(tmp_path: Path) -> None:
    """A non-cheap bounded-search option is measured by reproducing its commit.

    The bounded neighborhood option's apply path reruns the seeded search, so
    this exercises an option family beyond direct rehosts through the shared
    objective measurement.
    """
    teams = _teams(["Nordby", "Sorby", "Tredje"])
    problem = _problem(teams)
    participants = teams[:4]
    plan = _plan(
        [
            _tournament("T1", "2026-10-10", "Tredje", participants),
            _tournament("T2", "2026-11-14", "Nordby", participants),
        ]
    )
    root = tmp_path / "season"
    _write_season(root, plan, problem)

    result = search(YEAR, "host_team_missing:T1", root=root)

    assert result["option_count"] >= 1
    assert "search_neighborhood" in {option["family"] for option in result["options"]}
    assert result["pareto"]["measured_option_count"] == result["option_count"]
    assert result["pareto"]["front_size"] >= 1
    for option in result["options"]:
        assert set(option["objectives"]) == set(PARETO_DIMENSIONS)
        assert option["objectives"]["hard_violations"] == 0.0


def test_stale_revision_is_rejected_without_mutating_canonical_state(tmp_path: Path) -> None:
    root, _plan, _problem_dict, revision = _two_club_season(tmp_path)
    options = repair_options(YEAR, "hosting_balance:U10:Sorby", root=root)
    option_id = options["options"][0]["option_id"]
    schedule_file = root / YEAR / "schedule.json"
    before = schedule_file.read_bytes()

    result = apply_repair(YEAR, option_id, "deadbeef", root=root)

    assert result["ok"] is False
    assert result["reason"] == "stale_canonical_revision"
    assert result["canonical_revision_unchanged"] is True
    assert schedule_file.read_bytes() == before
    assert list_findings(YEAR, root=root)["counts_by_code"].get("unresolved_hosting_obligation") == 1


def test_placement_locked_donor_is_not_moved(tmp_path: Path) -> None:
    root, _plan, _problem_dict, _revision = _two_club_season(tmp_path, approved=["T2"])

    options = repair_options(YEAR, "hosting_balance:U10:Sorby", root=root)

    assert [option["tournament_id"] for option in options["options"]] == ["T1"]
    rejected = [entry for entry in options["rejected_candidates"] if entry.get("tournament_id") == "T2"]
    assert rejected and "canonical_placement_locked" in rejected[0]["violation_codes"]


def test_rehost_that_creates_another_deficit_is_rejected(tmp_path: Path) -> None:
    teams = _teams(["Alpha", "Bravo", "Charlie"])
    problem = _problem(teams, parallel_games=3)
    plan = _plan(
        [
            _tournament("A1", "2026-10-10", "Alpha", teams),
            _tournament("A2", "2026-11-14", "Alpha", teams),
            _tournament("B1", "2026-12-05", "Bravo", teams),
        ]
    )
    assert verify_candidate(plan, problem)["ok"]
    root = tmp_path / "season"
    _write_season(root, plan, problem)

    options = repair_options(YEAR, "hosting_balance:U10:Charlie", root=root)

    # Only the surplus club's (Alpha) tournaments may satisfy Charlie; moving
    # Bravo's tournament would merely relocate the shortfall.
    assert {option["tournament_id"] for option in options["options"]} == {"A1", "A2"}
    rejected = [entry for entry in options["rejected_candidates"] if entry.get("tournament_id") == "B1"]
    assert rejected and rejected[0]["reason"] == "no_deficit_improvement"


def test_participation_finding_exposes_avoidability(tmp_path: Path) -> None:
    teams = _teams(["Nordby", "Sorby"])
    problem = _problem(
        teams,
        participation_targets={"U10": {"before_christmas": 3, "after_christmas": 3}},
    )
    plan = _plan(
        [
            _tournament("T1", "2026-10-10", "Nordby", teams),
            _tournament("T2", "2026-11-14", "Nordby", teams),
        ]
    )
    root = tmp_path / "season"
    _write_season(root, plan, problem)

    findings = list_findings(YEAR, root=root)
    participation = [f for f in findings["findings"] if f["category"] == "participation"]

    assert participation
    assert all("avoidability" in finding for finding in participation)
    assert all(finding["proven_infeasible"] == (finding["avoidability"] == "proven_infeasible") for finding in participation)


def test_bounded_search_exhausted_is_not_proven_infeasible() -> None:
    classification = _classification({"avoidability": "bounded_search_exhausted"})

    assert classification["proven_infeasible"] is False
    assert "not proof of infeasibility" in classification["note"]

    proven = _classification({"avoidability": "proven_infeasible"})
    assert proven["proven_infeasible"] is True


def test_hard_finding_reuses_the_common_stage3_repair_providers(tmp_path: Path) -> None:
    teams = _teams(["Nordby", "Sorby", "Tredje"])
    problem = _problem(teams)
    participants = teams[:4]
    plan = _plan(
        [
            _tournament("T1", "2026-10-10", "Tredje", participants),
            _tournament("T2", "2026-11-14", "Nordby", participants),
        ]
    )
    root = tmp_path / "season"
    revision = _write_season(root, plan, problem)

    findings = list_findings(YEAR, root=root)
    hard = [
        finding
        for finding in findings["findings"]
        if finding["category"] == "hard_violation" and finding["code"] == "host_team_missing"
    ]
    assert [finding["finding_id"] for finding in hard] == ["host_team_missing:T1"]

    options = repair_options(YEAR, "host_team_missing:T1", root=root)
    assert options["option_count"] > 0
    assert {option["family"] for option in options["options"]} == {"host_team_missing"}

    applied = apply_repair(
        YEAR,
        options["options"][0]["option_id"],
        revision,
        root=root,
        finding_id="host_team_missing:T1",
    )
    assert applied["ok"] is True
    assert applied["delta"]["hard_violations_before"] == 1
    assert applied["delta"]["hard_violations_after"] == 0


def test_promoted_season_exposes_placement_preserving_roster_repair(tmp_path: Path) -> None:
    """A double-booked team is repaired by roster only, never by moving the slot.

    This is the season-maintenance counterpart of the production Kongsberg
    movable-capacity case: the tournament's host/date/arena/start time are
    already legal, so the first repair must keep that placement and only
    reselect the conflicting participant.
    """
    teams = _teams(["Nordby", "Sorby", "Tredje", "Fjerde"])
    problem = _problem(teams)
    plan = _plan(
        [
            _tournament("T1", "2026-10-10", "Nordby", [teams[0], teams[2], teams[4], teams[6]]),
            _tournament("T2", "2026-10-10", "Sorby", [teams[3], teams[2], teams[5], teams[7]]),
        ]
    )
    root = tmp_path / "season"
    revision = _write_season(root, plan, problem)

    findings = list_findings(YEAR, root=root)
    conflict = next(
        finding
        for finding in findings["findings"]
        if finding["code"] == "duplicate_participation_same_date"
    )
    assert conflict["finding_id"] == "duplicate_participation_same_date:T1+T2"
    assert conflict["tournament_ids"] == ["T1", "T2"]

    options = repair_options(YEAR, conflict["finding_id"], root=root)
    t1_options = [option for option in options["options"] if option["tournament_id"] == "T1"]
    assert t1_options
    chosen = t1_options[0]
    assert chosen["family"] == "placement_preserving_roster"
    assert chosen["evidence"]["placement_unchanged"] is True
    assert chosen["evidence"]["host_club"] == "Nordby"
    assert chosen["evidence"]["date"] == "2026-10-10"

    applied = apply_repair(
        YEAR,
        chosen["option_id"],
        revision,
        root=root,
        finding_id=conflict["finding_id"],
    )

    assert applied["ok"] is True, applied
    assert applied["delta"]["hard_violations_after"] == 0
    assert applied["delta"]["changed_tournament_count"] == 1
    repaired = next(
        tournament
        for tournament in json.loads(
            (root / YEAR / "schedule.json").read_text(encoding="utf-8")
        )["plan"]["tournaments"]
        if tournament["id"] == "T1"
    )
    assert repaired["date"] == "2026-10-10"
    assert repaired["arena"] == "Nordby Arena"
    assert repaired["host_club"] == "Nordby"
    assert repaired["start_time"] == "10:00"
    assert "Sorby 1" not in {team["label"] for team in repaired["teams"]}


# ---------------------------------------------------------------------------
# Explicit operator acceptance of a participation strong-goal deviation
# ---------------------------------------------------------------------------


def _participation_season(tmp_path: Path, *, scope_team: str = "Nordby 1"):
    teams = _teams(["Nordby", "Sorby"])
    problem = _problem(
        teams,
        participation_targets={"U10": {"before_christmas": 3, "after_christmas": 3}},
    )
    plan = _plan(
        [
            _tournament("T1", "2026-10-10", "Nordby", teams),
            _tournament("T2", "2026-11-14", "Nordby", teams),
        ]
    )
    root = tmp_path / "season"
    revision = _write_season(root, plan, problem)
    return root, plan, problem, revision


def _participation_finding(findings: Dict[str, Any], **scope: Any) -> Dict[str, Any]:
    return next(
        finding
        for finding in findings["findings"]
        if finding["category"] == "participation"
        and all(finding.get(key) == value for key, value in scope.items())
    )


def _rewrite_plan(root: Path, plan: Dict[str, Any], problem: Dict[str, Any]) -> str:
    """Replace only the canonical plan/verification context, keeping decisions."""
    schedule_path = root / YEAR / "schedule.json"
    schedule = json.loads(schedule_path.read_text(encoding="utf-8"))
    revision = schedule_fingerprint(plan)
    schedule["plan"] = plan
    schedule["revision"] = revision
    schedule["fingerprint"] = revision
    schedule["verification_context"] = {"problem": problem}
    schedule_path.write_text(
        json.dumps(schedule, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return revision


def test_operator_acceptance_persists_and_reclassifies_only_that_scope(tmp_path: Path) -> None:
    root, _plan_dict, _problem_dict, revision = _participation_season(tmp_path)

    before = list_findings(YEAR, root=root)
    finding = _participation_finding(
        before, club="Nordby", team="Nordby 1", scope="before_christmas"
    )
    assert finding["avoidability"] != "operator_accepted"
    assert "accepted" not in finding

    result = accept_finding(
        YEAR,
        finding["finding_id"],
        root=root,
        actor="operator",
        note="ice unavailable",
    )

    record = result["acceptance"]
    assert record["status"] == "operator_accepted"
    assert record["accepted_deviation"] == finding["deviation"]
    assert record["target"] == finding["target"]
    assert record["scope"] == "before_christmas"
    assert record["accepted_by"] == "operator"
    assert record["note"] == "ice unavailable"
    # Accepting is a decision record, not a schedule mutation.
    assert result["revision"] == revision
    assert [entry["id"] for entry in load_participation_acceptances(YEAR, root=root)] == [record["id"]]

    after = list_findings(YEAR, root=root)
    accepted = _participation_finding(
        after, club="Nordby", team="Nordby 1", scope="before_christmas"
    )
    assert accepted["avoidability"] == "operator_accepted"
    assert accepted["accepted"] is True
    assert accepted["acceptance_id"] == record["id"]
    # The acceptance is scoped: a different scope for the same team is untouched.
    season_scope = _participation_finding(
        after, club="Nordby", team="Nordby 1", scope="season"
    )
    assert season_scope["avoidability"] != "operator_accepted"


def test_revoke_acceptance_restores_the_finding(tmp_path: Path) -> None:
    root, _plan_dict, _problem_dict, _revision = _participation_season(tmp_path)
    finding = _participation_finding(
        list_findings(YEAR, root=root), club="Nordby", team="Nordby 1", scope="before_christmas"
    )
    accept_finding(YEAR, finding["finding_id"], root=root, note="accepted")

    result = revoke_acceptance(YEAR, finding["finding_id"], root=root, note="reopen")

    assert result["ok"] is True
    assert result["revoked"]["revoked_at"]
    assert load_participation_acceptances(YEAR, root=root) == []
    restored = _participation_finding(
        list_findings(YEAR, root=root), club="Nordby", team="Nordby 1", scope="before_christmas"
    )
    assert restored["avoidability"] == finding["avoidability"]
    assert "accepted" not in restored


def test_acceptance_stops_applying_when_deviation_gets_worse(tmp_path: Path) -> None:
    root, plan, problem, _revision = _participation_season(tmp_path)
    finding = _participation_finding(
        list_findings(YEAR, root=root), club="Nordby", team="Nordby 1", scope="before_christmas"
    )
    accept_finding(YEAR, finding["finding_id"], root=root, note="accepted")

    # Remove one Nordby 1 participation: the team falls further below target, so
    # the persisted acceptance must stop explaining the worse deviation.
    worse_plan = json.loads(json.dumps(plan))
    t1 = next(tournament for tournament in worse_plan["tournaments"] if tournament["id"] == "T1")
    t1["teams"] = [team for team in t1["teams"] if team["label"] != "Nordby 1"]
    _rewrite_plan(root, worse_plan, problem)

    findings = list_findings(YEAR, root=root)
    stale = _participation_finding(
        findings, club="Nordby", team="Nordby 1", scope="before_christmas"
    )
    assert stale["deviation"] < finding["deviation"]
    assert stale["avoidability"] != "operator_accepted"
    assert stale["accepted"] is False
    assert stale["acceptance_stale"] is True
    assert stale["acceptance_id"]


def test_acceptance_stops_applying_when_target_changes(tmp_path: Path) -> None:
    root, plan, problem, _revision = _participation_season(tmp_path)
    finding = _participation_finding(
        list_findings(YEAR, root=root), club="Nordby", team="Nordby 1", scope="before_christmas"
    )
    accept_finding(YEAR, finding["finding_id"], root=root, note="accepted")

    # A target change (for example a registration/fairness recompute) invalidates
    # the old acceptance rather than silently covering the new target.
    retargeted = json.loads(json.dumps(problem))
    retargeted["participation_targets_by_age_group"]["U10"]["before_christmas"] = 5
    _rewrite_plan(root, plan, retargeted)

    findings = list_findings(YEAR, root=root)
    stale = _participation_finding(
        findings, club="Nordby", team="Nordby 1", scope="before_christmas"
    )
    assert stale["target"] == 5
    assert stale["avoidability"] != "operator_accepted"
    assert stale["acceptance_stale"] is True


def test_repair_options_cli_reports_the_pareto_surface(tmp_path: Path, capsys) -> None:
    from tournament_scheduler.cli.rvv_cli import main

    root, _plan_dict, _problem_dict, _revision = _two_club_season(tmp_path)

    rc = main(
        [
            "season",
            "repair-options",
            "--season",
            YEAR,
            "--finding",
            "hosting_balance:U10:Sorby",
            "--root",
            str(root),
            "--json",
        ]
    )

    assert rc == 0
    output = json.loads(capsys.readouterr().out)
    assert output["pareto"]["dimensions"] == list(PARETO_DIMENSIONS)
    assert output["pareto"]["front_size"] >= 1
    assert any(option["non_dominated"] for option in output["options"])


def test_accept_deviation_cli_round_trip(tmp_path: Path, capsys) -> None:
    from tournament_scheduler.cli.rvv_cli import main

    root, _plan_dict, _problem_dict, _revision = _participation_season(tmp_path)
    finding = _participation_finding(
        list_findings(YEAR, root=root), club="Nordby", team="Nordby 1", scope="before_christmas"
    )

    rc = main(
        [
            "season",
            "accept-deviation",
            "--season",
            YEAR,
            "--finding",
            finding["finding_id"],
            "--root",
            str(root),
            "--note",
            "ice unavailable",
            "--json",
        ]
    )
    assert rc == 0
    output = json.loads(capsys.readouterr().out)
    assert output["acceptance"]["status"] == "operator_accepted"

    rc = main(
        [
            "season",
            "revoke-acceptance",
            "--season",
            YEAR,
            "--finding",
            finding["finding_id"],
            "--root",
            str(root),
            "--json",
        ]
    )
    assert rc == 0
    assert json.loads(capsys.readouterr().out)["revoked"]["revoked_at"]
    assert load_participation_acceptances(YEAR, root=root) == []


def test_unplaced_obligation_is_a_manual_finding_without_a_tournament_id() -> None:
    """An unplaced obligation is planning work, not a scheduled tournament:
    it must surface as a stable-id manual finding whose identity does not
    depend on a tournament id."""
    from tournament_scheduler.season_maintenance import _unplaced_findings

    findings = _unplaced_findings(
        {
            "unresolved_tournament_placements": [
                {
                    "id": "unplaced_placement:U10:2026-10-10:1",
                    "age_group": "U10",
                    "date": "2026-10-10",
                    "responsible_host": "Jar",
                    "search_attempted": True,
                    "bounded_repair_exhausted": True,
                    "reason": "no_participant_host_slot",
                }
            ]
        }
    )

    (finding,) = findings
    assert finding["finding_id"] == "unplaced_placement:U10:2026-10-10:1"
    assert finding["category"] == "manual_placement"
    assert finding["code"] == "unplaced_tournament_placement"
    assert finding["host_club"] == "Jar"
    assert "tournament_id" not in finding
