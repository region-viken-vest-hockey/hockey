"""Swap discovery is read-only, scoped and unranked, and reports the shared preview verdict."""

from __future__ import annotations

from pathlib import Path

import pytest

from tournament_scheduler.application.canonical_season import swap_search
from tournament_scheduler.application.canonical_season.consequence_preview import (
    PREVIEW_CONTRACT,
    STATUS_STALE_REVISION,
    preview_canonical_operation,
)
from tournament_scheduler.application.canonical_season.swap_search import search_participant_swaps
from tournament_scheduler.application.canonical_season_service import CanonicalSeasonService
from tournament_scheduler.infrastructure.canonical_season_store import (
    DECISIONS_SCHEMA_VERSION,
    SEASON_STATE_SCHEMA_VERSION,
    CanonicalSeasonSnapshot,
    CanonicalSeasonStore,
    SeasonStateError,
)
from tournament_scheduler.season.state import canonical_state_revision, schedule_fingerprint

from tests.test_consequence_preview import CLUBS, SEASON, _problem, _state_bytes, _tournament


def _team(club: str, age_group: str = "U10") -> dict:
    return {"club": club, "label": f"{club} 1", "age_group": age_group}


def _write(root: Path, tournaments: list[dict], decisions_overrides: dict | None = None) -> None:
    plan = {
        "schema_version": 1,
        "start_date": "2026-10-01",
        "end_date": "2026-10-31",
        "tournaments": tournaments,
    }
    now = "2026-09-22T00:00:00+00:00"
    fingerprint = schedule_fingerprint(plan)
    schedule = {
        "schema_version": SEASON_STATE_SCHEMA_VERSION,
        "season": SEASON,
        "created_at": now,
        "updated_at": now,
        "revision": fingerprint,
        "fingerprint": fingerprint,
        "plan_schema_version": 1,
        "plan": plan,
        "verification_context": {"problem": _problem()},
    }
    records = {
        tournament["id"]: {
            "status": "pending_review",
            "placement_locked": False,
            "participants_locked": False,
            "approved_fingerprint": None,
        }
        for tournament in tournaments
    }
    records.update(decisions_overrides or {})
    decisions = {
        "schema_version": DECISIONS_SCHEMA_VERSION,
        "season": SEASON,
        "created_at": now,
        "updated_at": now,
        "schedule_fingerprint": fingerprint,
        "actor": "tester",
        "decisions": records,
        "history": [],
    }
    CanonicalSeasonStore(root).write(
        CanonicalSeasonSnapshot(season=SEASON, schedule=schedule, decisions=decisions)
    )


def _tournaments() -> list[dict]:
    # Different rosters so that some same-age swaps are legal candidates and
    # others are refused by the shared gates, while the sets still overlap.
    first = _tournament("u10-a", "2026-10-03", "Alfa")
    first["teams"] = [_team(club) for club in CLUBS[:3]]
    second = _tournament("u10-b", "2026-10-10", "Bravo")
    second["teams"] = [_team(club) for club in CLUBS[2:]]
    third = _tournament("u10-c", "2026-10-17", "Charlie")
    third["teams"] = [_team(club) for club in CLUBS[1:4]]
    return [first, second, third]


def _service(root: Path) -> CanonicalSeasonService:
    return CanonicalSeasonService(root=root)


def test_discovery_evaluates_same_age_pairs_through_the_shared_preview(tmp_path: Path) -> None:
    root = tmp_path / "season"
    _write(root, _tournaments())
    result = search_participant_swaps(_service(root), season=SEASON, tournament_ids=["u10-a", "u10-b", "u10-c"])

    assert result["candidate_count"] == len(result["candidates"]) > 0
    assert result["empty_reason"] is None
    assert result["truncated"] is False
    assert result["ranking_policy"].startswith("none:")

    order = [
        (c["tournament_a_id"], c["tournament_b_id"], c["team_a_label"], c["team_b_label"])
        for c in result["candidates"]
    ]
    assert order == sorted(order)
    assert {c["verdict"]["status"] for c in result["candidates"]} >= {"blocked"}

    for candidate in result["candidates"]:
        direct = preview_canonical_operation(
            _service(root),
            operation="swap",
            season=SEASON,
            expected_revision=result["canonical_state_revision"],
            tournament_a_id=candidate["tournament_a_id"],
            team_a_label=candidate["team_a_label"],
            tournament_b_id=candidate["tournament_b_id"],
            team_b_label=candidate["team_b_label"],
        )
        direct.pop("details")
        assert candidate["canonical_preview"] == direct
        assert candidate["verdict"] == direct["verdict"]
        assert candidate["safe_to_apply"] is direct["verdict"]["applicable"]
        assert candidate["blockers"] == direct["verdict"]["blockers"]
        assert candidate["canonical_preview"]["contract"] == PREVIEW_CONTRACT
        assert candidate["canonical_preview"]["writes_canonical_state"] is False
        assert candidate["canonical_preview"]["expected_revision"] == result["canonical_state_revision"]


def test_discovery_writes_nothing_and_keeps_the_revision(tmp_path: Path) -> None:
    root = tmp_path / "season"
    _write(root, _tournaments())
    before = _state_bytes(root)
    revision = canonical_state_revision(*_snapshot_parts(root))

    result = search_participant_swaps(_service(root), season=SEASON, tournament_ids=["u10-a", "u10-b"])

    assert result["canonical_state_revision"] == revision
    assert _state_bytes(root) == before
    assert "never" in result["apply_command"]


def test_candidate_is_refused_once_the_reviewed_revision_is_stale(tmp_path: Path) -> None:
    root = tmp_path / "season"
    _write(root, _tournaments())
    result = search_participant_swaps(_service(root), season=SEASON, tournament_ids=["u10-a", "u10-b"])
    candidate = result["candidates"][0]

    stale = preview_canonical_operation(
        _service(root),
        operation="swap",
        season=SEASON,
        expected_revision="0" * 64,
        tournament_a_id=candidate["tournament_a_id"],
        team_a_label=candidate["team_a_label"],
        tournament_b_id=candidate["tournament_b_id"],
        team_b_label=candidate["team_b_label"],
    )
    assert stale["verdict"]["status"] == STATUS_STALE_REVISION
    assert stale["verdict"]["applicable"] is False


def test_scope_must_name_two_to_ten_distinct_tournaments(tmp_path: Path) -> None:
    root = tmp_path / "season"
    _write(root, _tournaments())

    with pytest.raises(ValueError):
        search_participant_swaps(_service(root), season=SEASON, tournament_ids=["u10-a"])
    with pytest.raises(ValueError):
        search_participant_swaps(_service(root), season=SEASON, tournament_ids=["u10-a", "u10-a"])
    with pytest.raises(ValueError):
        search_participant_swaps(_service(root), season=SEASON, tournament_ids=[f"t{i}" for i in range(11)])


def test_unknown_scoped_tournament_is_refused(tmp_path: Path) -> None:
    root = tmp_path / "season"
    _write(root, _tournaments())

    with pytest.raises(SeasonStateError, match="Unknown tournament id"):
        search_participant_swaps(_service(root), season=SEASON, tournament_ids=["u10-a", "missing"])


def test_cancelled_and_participant_locked_tournaments_are_excluded(tmp_path: Path) -> None:
    root = tmp_path / "season"
    tournaments = _tournaments()
    tournaments[2]["cancelled"] = True
    _write(root, tournaments)

    result = search_participant_swaps(_service(root), season=SEASON, tournament_ids=["u10-a", "u10-b", "u10-c"])
    assert result["excluded_tournaments"] == [{"tournament_id": "u10-c", "reason": "cancelled"}]
    assert all("u10-c" not in (c["tournament_a_id"], c["tournament_b_id"]) for c in result["candidates"])


def test_participant_lock_excludes_the_locked_tournament(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "season"
    _write(root, _tournaments())
    real = swap_search.resolve_approval

    def locked_b(record, tournament):
        resolved = real(record, tournament)
        if tournament["id"] == "u10-b":
            resolved = {**resolved, "participants_locked": True}
        return resolved

    monkeypatch.setattr(swap_search, "resolve_approval", locked_b)
    result = search_participant_swaps(_service(root), season=SEASON, tournament_ids=["u10-a", "u10-b"])

    assert result["excluded_tournaments"] == [{"tournament_id": "u10-b", "reason": "participants_locked"}]
    assert result["candidates"] == []
    assert result["empty_reason"] == "no_eligible_pairs"


def test_different_age_groups_are_never_paired(tmp_path: Path) -> None:
    root = tmp_path / "season"
    tournaments = _tournaments()
    tournaments[1]["age_group"] = "U12"
    for team in tournaments[1]["teams"]:
        team["age_group"] = "U12"
    _write(root, tournaments)

    result = search_participant_swaps(_service(root), season=SEASON, tournament_ids=["u10-a", "u10-b"])
    assert result["candidates"] == []
    assert result["empty_reason"] == "no_eligible_pairs"


def test_cli_swap_candidates_reports_json_without_writing(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from tournament_scheduler.cli.rvv_cli import main as cli_main

    root = tmp_path / "season"
    _write(root, _tournaments())
    before = _state_bytes(root)

    assert (
        cli_main(
            ["season", "swap-candidates", "--season", SEASON, "--root", str(root),
             "--tournament-ids", "u10-a", "u10-b", "--json"]
        )
        == 0
    )
    assert _state_bytes(root) == before
    assert '"ranking_policy"' in capsys.readouterr().out


def _snapshot_parts(root: Path):
    snapshot = _service(root).load(SEASON)
    return snapshot.schedule, snapshot.decisions
