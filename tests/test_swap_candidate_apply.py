"""Discovery → reviewed candidate → apply, and discovery → intervening change → refused apply.

Uses the promoted ranking fixture because its same-age tournaments offer real
safe swaps. The unit-level discovery tests live in ``test_swap_candidates``.
"""

from __future__ import annotations

import copy
from pathlib import Path

import pytest

from tests.test_season_inspection import _promote_replacement_ranking_fixture
from tournament_scheduler.application.canonical_season.store_facade import (
    load_schedule,
    swap_participants,
)
from tournament_scheduler.application.canonical_season.swap_search import (
    MAX_EVALUATED_CANDIDATES,
    search_participant_swaps,
)
from tournament_scheduler.application.canonical_season_service import CanonicalSeasonService
from tournament_scheduler.infrastructure.canonical_season_store import (
    CanonicalRevisionConflictError,
    CanonicalSeasonStore,
)
from tournament_scheduler.season.state import CANONICAL_STATE_REVISION_KEY

SEASON = "2026-2027"
SCOPE = ["t1", "t2", "t3", "t4", "t5"]


def _service(root: Path) -> CanonicalSeasonService:
    return CanonicalSeasonService(root=root)


def _apply(root: Path, candidate: dict, expected_revision: str) -> dict:
    return swap_participants(
        season=SEASON,
        root=root,
        tournament_a_id=candidate["tournament_a_id"],
        team_a_label=candidate["team_a_label"],
        tournament_b_id=candidate["tournament_b_id"],
        team_b_label=candidate["team_b_label"],
        actor="tester",
        request_id=f"apply-{candidate['tournament_a_id']}-{candidate['team_a_label']}",
        expected_revision=expected_revision,
    )


def _roster(root: Path, tournament_id: str) -> set[str]:
    schedule = load_schedule(SEASON, root=root)
    tournament = next(
        item for item in schedule["plan"]["tournaments"] if str(item["id"]) == tournament_id
    )
    return {str(team.get("label")) for team in tournament.get("teams", []) if not team.get("guest")}


def test_reviewed_safe_candidate_applies_at_its_discovery_revision(tmp_path: Path) -> None:
    root = _promote_replacement_ranking_fixture(tmp_path)
    result = search_participant_swaps(_service(root), season=SEASON, tournament_ids=SCOPE)
    safe = [item for item in result["candidates"] if item["safe_to_apply"]]
    assert safe, "fixture must offer at least one safe swap"
    candidate = safe[0]

    before_a = _roster(root, candidate["tournament_a_id"])
    before_b = _roster(root, candidate["tournament_b_id"])
    applied = _apply(root, candidate, result["canonical_state_revision"])

    assert applied["dry_run"] is False
    assert applied["canonical_state_revision"] != result["canonical_state_revision"]
    after_a = _roster(root, candidate["tournament_a_id"])
    after_b = _roster(root, candidate["tournament_b_id"])
    assert after_a == (before_a - {candidate["team_a_label"]}) | {candidate["team_b_label"]}
    assert after_b == (before_b - {candidate["team_b_label"]}) | {candidate["team_a_label"]}


def test_apply_is_refused_when_canonical_state_moved_after_review(tmp_path: Path) -> None:
    root = _promote_replacement_ranking_fixture(tmp_path)
    result = search_participant_swaps(_service(root), season=SEASON, tournament_ids=SCOPE)
    reviewed = next(item for item in result["candidates"] if item["safe_to_apply"])
    reviewed_revision = result["canonical_state_revision"]

    # Another operator's commit moves the stored revision without touching any
    # roster, so the reviewed candidate still passes every domain gate and only
    # the revision compare-and-swap can refuse it.
    snapshot = _service(root).load(SEASON)
    decisions = copy.deepcopy(snapshot.decisions)
    decisions[CANONICAL_STATE_REVISION_KEY] = "moved-by-another-operator"
    CanonicalSeasonStore(root).write(snapshot.with_decisions(decisions))
    snapshot_before = (root / SEASON / "schedule.json").read_bytes()
    decisions_before = (root / SEASON / "decisions.json").read_bytes()

    with pytest.raises(CanonicalRevisionConflictError):
        _apply(root, reviewed, reviewed_revision)

    assert (root / SEASON / "schedule.json").read_bytes() == snapshot_before
    assert (root / SEASON / "decisions.json").read_bytes() == decisions_before


def test_truncated_scope_reports_itself_as_incomplete(tmp_path: Path) -> None:
    root = _promote_replacement_ranking_fixture(tmp_path)
    wide = search_participant_swaps(_service(root), season=SEASON, tournament_ids=SCOPE)

    assert wide["candidate_count"] == MAX_EVALUATED_CANDIDATES
    assert wide["truncated"] is True
    assert wide["complete"] is False


def test_small_scope_reports_itself_as_complete(tmp_path: Path) -> None:
    root = _promote_replacement_ranking_fixture(tmp_path)
    narrow = search_participant_swaps(_service(root), season=SEASON, tournament_ids=["t1", "t5"])

    assert narrow["truncated"] is False
    assert narrow["complete"] is True
