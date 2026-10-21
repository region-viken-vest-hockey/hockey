"""Move discovery is read-only, scoped and unranked, and reports the shared move preview verdict."""

from __future__ import annotations

from pathlib import Path

import pytest

from tournament_scheduler.application.canonical_season import move_search
from tournament_scheduler.application.canonical_season.consequence_preview import preview_canonical_operation
from tournament_scheduler.application.canonical_season.move_search import search_tournament_moves
from tournament_scheduler.application.canonical_season_service import CanonicalSeasonService
from tournament_scheduler.infrastructure.canonical_season_store import SeasonStateError

from tests.test_consequence_preview import SEASON, _state_bytes, _tournament
from tests.test_swap_candidates import _tournaments, _write


def _service(root: Path) -> CanonicalSeasonService:
    return CanonicalSeasonService(root=root)


def test_discovery_proposes_same_host_free_ice_dates_through_the_shared_move_preview(tmp_path: Path) -> None:
    root = tmp_path / "season"
    _write(root, _tournaments())
    result = search_tournament_moves(_service(root), season=SEASON, tournament_ids=["u10-a", "u10-b"])

    assert result["candidate_count"] == len(result["candidates"]) > 0
    assert result["empty_reason"] is None
    assert result["truncated"] is False
    assert result["complete"] is True
    assert result["ranking_policy"].startswith("none:")

    order = [(c["tournament_id"], c["date"], c["start_time"], c["arena"]) for c in result["candidates"]]
    assert order == sorted(order)

    current = {"u10-a": "2026-10-03", "u10-b": "2026-10-10"}
    for candidate in result["candidates"]:
        assert candidate["date"] != current[candidate["tournament_id"]]
        assert candidate["host_club"] == candidate["arena"].removesuffix(" Arena")
        direct = preview_canonical_operation(
            _service(root),
            operation="move",
            season=SEASON,
            expected_revision=result["canonical_state_revision"],
            tournament_id=candidate["tournament_id"],
            date=candidate["date"],
        )
        assert candidate["verdict"] == direct["verdict"]
        assert candidate["candidate_fingerprint"] == direct["candidate_fingerprint"]
        assert candidate["safe_to_apply"] == direct["verdict"]["applicable"]


def test_discovery_writes_nothing_and_keeps_the_revision(tmp_path: Path) -> None:
    root = tmp_path / "season"
    _write(root, _tournaments())
    before = _state_bytes(root)

    result = search_tournament_moves(_service(root), season=SEASON, tournament_ids=["u10-a", "u10-b"])

    assert _state_bytes(root) == before
    assert all(c["canonical_preview"]["expected_revision"] == result["canonical_state_revision"] for c in result["candidates"])


def test_placement_locked_tournaments_are_excluded_but_participant_locked_ones_are_not(tmp_path: Path) -> None:
    root = tmp_path / "season"
    overrides = {
        "u10-a": {"status": "approved", "placement_locked": True, "participants_locked": False, "approved_fingerprint": None},
        "u10-b": {"status": "approved", "placement_locked": False, "participants_locked": True, "approved_fingerprint": None},
    }
    _write(root, _tournaments(), decisions_overrides=overrides)

    result = search_tournament_moves(_service(root), season=SEASON, tournament_ids=["u10-a", "u10-b", "u10-c"])

    assert result["excluded_tournaments"] == [{"tournament_id": "u10-a", "reason": "placement_locked"}]
    assert {c["tournament_id"] for c in result["candidates"]} >= {"u10-b", "u10-c"}
    assert "u10-a" not in {c["tournament_id"] for c in result["candidates"]}


def test_cancelled_tournaments_are_excluded(tmp_path: Path) -> None:
    root = tmp_path / "season"
    tournaments = _tournaments()
    tournaments[1]["cancelled"] = True
    _write(root, tournaments)

    result = search_tournament_moves(_service(root), season=SEASON, tournament_ids=["u10-a", "u10-b"])

    assert result["excluded_tournaments"] == [{"tournament_id": "u10-b", "reason": "cancelled"}]
    assert {c["tournament_id"] for c in result["candidates"]} == {"u10-a"}


def test_no_verified_free_ice_is_reported_as_no_available_ice(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "season"
    _write(root, _tournaments())
    monkeypatch.setattr(move_search, "_free_ice_dates", lambda problem, tournament: [])

    result = search_tournament_moves(_service(root), season=SEASON, tournament_ids=["u10-a"])

    assert result["candidates"] == []
    assert result["empty_reason"] == "no_available_ice"


def test_tournament_without_a_configured_duration_is_excluded_not_searched(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "season"
    _write(root, _tournaments())
    monkeypatch.setattr(move_search, "_duration_minutes", lambda problem, age_group: 0)

    result = search_tournament_moves(_service(root), season=SEASON, tournament_ids=["u10-a", "u10-b"])

    assert result["candidates"] == []
    assert result["excluded_tournaments"] == [
        {"tournament_id": "u10-a", "reason": "no_duration_configured"},
        {"tournament_id": "u10-b", "reason": "no_duration_configured"},
    ]
    assert result["empty_reason"] == "no_eligible_tournaments"


def test_evaluated_candidates_are_capped_and_truncation_is_reported(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "season"
    _write(root, _tournaments())
    monkeypatch.setattr(move_search, "MAX_EVALUATED_CANDIDATES", 2)

    result = search_tournament_moves(_service(root), season=SEASON, tournament_ids=["u10-a", "u10-b"])

    assert result["candidate_count"] == 2
    assert result["truncated"] is True
    assert result["complete"] is False


def test_scope_must_name_one_to_ten_distinct_tournaments(tmp_path: Path) -> None:
    root = tmp_path / "season"
    _write(root, _tournaments())
    with pytest.raises(ValueError):
        search_tournament_moves(_service(root), season=SEASON, tournament_ids=[])
    with pytest.raises(ValueError):
        search_tournament_moves(_service(root), season=SEASON, tournament_ids=[f"x{i}" for i in range(11)])


def test_unknown_scoped_tournament_is_refused(tmp_path: Path) -> None:
    root = tmp_path / "season"
    _write(root, _tournaments())
    with pytest.raises(SeasonStateError):
        search_tournament_moves(_service(root), season=SEASON, tournament_ids=["u10-a", "missing"])


def test_cli_move_candidates_reports_json_without_writing(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from tournament_scheduler.cli.rvv_cli import main as cli_main

    root = tmp_path / "season"
    _write(root, _tournaments())
    before = _state_bytes(root)

    assert (
        cli_main(
            ["season", "move-candidates", "--season", SEASON, "--root", str(root),
             "--tournament-ids", "u10-a", "--json"]
        )
        == 0
    )
    assert _state_bytes(root) == before
    assert '"ranking_policy"' in capsys.readouterr().out


def _full_roster_tournaments() -> list[dict]:
    # Full five-club rosters: a three-team fixture cannot pass the shared gates
    # for any move, so it cannot exercise a real apply.
    return [
        _tournament("u10-a", "2026-10-03", "Alfa"),
        _tournament("u10-b", "2026-10-10", "Bravo"),
        _tournament("u10-c", "2026-10-17", "Charlie"),
    ]


def test_applying_a_candidate_at_the_reviewed_revision_commits(tmp_path: Path) -> None:
    root = tmp_path / "season"
    _write(root, _full_roster_tournaments())
    result = search_tournament_moves(_service(root), season=SEASON, tournament_ids=["u10-a"])
    safe = [c for c in result["candidates"] if c["safe_to_apply"]]
    assert safe, "fixture must offer at least one safe candidate"
    candidate = safe[0]

    committed = _service(root).move_tournament(
        season=SEASON,
        tournament_id=candidate["tournament_id"],
        date=candidate["date"],
        expected_revision=result["canonical_state_revision"],
    )

    assert committed["revision"] != result["canonical_state_revision"]
    moved = next(t for t in committed["plan"]["tournaments"] if t["id"] == candidate["tournament_id"])
    assert moved["date"] == candidate["date"]


def test_applying_a_candidate_against_a_stale_reviewed_revision_is_refused(tmp_path: Path) -> None:
    root = tmp_path / "season"
    _write(root, _full_roster_tournaments())
    result = search_tournament_moves(_service(root), season=SEASON, tournament_ids=["u10-a"])
    safe = [c for c in result["candidates"] if c["safe_to_apply"]]
    assert safe, "fixture must offer at least one safe candidate"
    candidate = safe[0]
    reviewed = result["canonical_state_revision"]

    # A real, safe move of another tournament commits and moves the revision on.
    other = search_tournament_moves(_service(root), season=SEASON, tournament_ids=["u10-c"])
    other_safe = [c for c in other["candidates"] if c["safe_to_apply"]]
    assert other_safe, "fixture must offer a second safe candidate"
    _service(root).move_tournament(
        season=SEASON,
        tournament_id="u10-c",
        date=other_safe[0]["date"],
    )

    # The candidate must still pass hard verification in the new state, so the
    # only reason left to refuse it is the stale reviewed revision.
    current = search_tournament_moves(_service(root), season=SEASON, tournament_ids=["u10-a"])
    still_valid = [c for c in current["candidates"] if c["safe_to_apply"]]
    assert still_valid, "fixture must offer a candidate that is valid after the change"

    with pytest.raises(SeasonStateError, match="revision"):
        _service(root).move_tournament(
            season=SEASON,
            tournament_id="u10-a",
            date=still_valid[0]["date"],
            expected_revision=reviewed,
        )
