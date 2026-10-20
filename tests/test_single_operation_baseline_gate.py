"""Single-operation writers gate on the same baseline classification as batch.

Each scoped operation (replace, swap, move, remove) is verified against the
pre-operation canonical state. The preview must report the verdict its writer
applies, so every case checks both the preview and the real write.

A test-only debt model is layered over the real verifier to create hard
findings with known identities and severities. The accepted-exception step is
replaced for tests that need an accepted finding, because real acceptance
requires exact booking evidence that these fixtures do not carry.

Fixtures:
- ``pipeline``: a promoted two-tournament season where swap and move are
  clean. Replacement is consequence-blocked here for reasons unrelated to hard
  findings, so it is only used where the classification must block.
- ``clubs``: a five-club season where removing D1 from both tournaments is
  clean.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any, Callable

import pytest

from tournament_scheduler.application.canonical_season.consequence_preview import (
    STATUS_BLOCKED,
    STATUS_SAFE_TO_APPLY,
    preview_canonical_operation,
)
from tournament_scheduler.application.canonical_season.store_facade import promote_from_stage3
from tournament_scheduler.application.canonical_season_service import CanonicalSeasonService
from tournament_scheduler.infrastructure.canonical_season_store import (
    DECISIONS_SCHEMA_VERSION,
    SEASON_STATE_SCHEMA_VERSION,
    CanonicalSeasonSnapshot,
    CanonicalSeasonStore,
    SeasonStateError,
)
from tournament_scheduler.pipeline.state import PipelineState, StageName, StageStatus
from tournament_scheduler.planning import verification
from tournament_scheduler.planning.contract import build_planning_problem
from tournament_scheduler.season.state import canonical_state_revision, schedule_fingerprint
from tournament_scheduler.testing.reviewed_export import write_reviewed_stage4_export

SEASON = "2026-2027"
TOURNAMENT_A = "u10-a-20260912"
TOURNAMENT_B = "u10-b-20260920"

Violation = dict[str, Any]
DebtModel = Callable[[dict[str, Any]], list[Violation]]


def _pipeline_season(tmp_path: Path) -> Path:
    work_dir = tmp_path / ".pipeline"
    root = tmp_path / "season"
    state = PipelineState(work_dir)

    def four_team_games(labels: list[str]) -> list[dict]:
        return [
            {"home": labels[0], "away": labels[1], "parallel_slot": 0, "round_number": 1},
            {"home": labels[2], "away": labels[3], "parallel_slot": 1, "round_number": 1},
            {"home": labels[0], "away": labels[2], "parallel_slot": 0, "round_number": 2},
            {"home": labels[1], "away": labels[3], "parallel_slot": 1, "round_number": 2},
            {"home": labels[0], "away": labels[3], "parallel_slot": 0, "round_number": 3},
            {"home": labels[1], "away": labels[2], "parallel_slot": 1, "round_number": 3},
        ]

    def team(label: str) -> dict:
        return {"club": label[0], "label": label, "age_group": "U10"}

    candidate = {
        "schema_version": 1,
        "start_date": "2026-09-01",
        "end_date": "2027-04-30",
        "tournaments": [
            {
                "id": TOURNAMENT_A,
                "date": "2026-09-12",
                "arena": "Arena A",
                "age_group": "U10",
                "host_club": "A",
                "teams": [team(label) for label in ["A1", "B1", "C1", "D1"]],
                "games": four_team_games(["A1", "B1", "C1", "D1"]),
                "start_time": "10:00",
            },
            {
                "id": TOURNAMENT_B,
                "date": "2026-09-20",
                "arena": "Arena B",
                "age_group": "U10",
                "host_club": "B",
                "teams": [team(label) for label in ["B2", "E1", "F1", "G1"]],
                "games": four_team_games(["B2", "E1", "F1", "G1"]),
                "start_time": "12:00",
            },
        ],
    }
    state.write_stage(StageName.PLANNING, {"plan": candidate}, status=StageStatus.DONE)
    write_reviewed_stage4_export(state)
    promote_from_stage3(work_dir=work_dir, root=root, actor="tester")
    return root


def _clubs_season(root: Path) -> None:
    clubs = ("A", "B", "C", "D", "E")
    teams = [{"club": club, "label": f"{club}1", "age_group": "U10"} for club in clubs]
    config = {
        "teams": teams,
        "age_groups": ["U10"],
        "parallel_games": {"U10": 4},
        "round_length_minutes": {"U10": 30},
        "ice_time_minutes": {"U10": 200},
        "rounds_per_tournament": {"U10": 5},
    }
    problem = build_planning_problem(config, None, date(2026, 10, 1), date(2026, 10, 31))
    problem["clubs"] = {club: f"{club} Arena" for club in clubs}
    problem["club_calendar_status"] = {club: "known" for club in clubs}

    def tournament(tournament_id: str, tournament_date: str, host: str) -> dict:
        return {
            "id": tournament_id,
            "date": tournament_date,
            "arena": f"{host} Arena",
            "age_group": "U10",
            "host_club": host,
            "teams": [dict(team) for team in teams],
            "games": [],
            "start_time": "10:00",
        }

    plan = {
        "schema_version": 1,
        "start_date": "2026-10-01",
        "end_date": "2026-10-31",
        "tournaments": [
            tournament(TOURNAMENT_A, "2026-10-03", "A"),
            tournament(TOURNAMENT_B, "2026-10-10", "B"),
        ],
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
        "verification_context": {"problem": problem},
    }
    decisions = {
        "schema_version": DECISIONS_SCHEMA_VERSION,
        "season": SEASON,
        "created_at": now,
        "updated_at": now,
        "schedule_fingerprint": fingerprint,
        "actor": "tester",
        "decisions": {
            item["id"]: {
                "status": "pending_review",
                "placement_locked": False,
                "participants_locked": False,
                "approved_fingerprint": None,
            }
            for item in plan["tournaments"]
        },
        "history": [],
    }
    CanonicalSeasonStore(root).write(
        CanonicalSeasonSnapshot(season=SEASON, schedule=schedule, decisions=decisions)
    )


# Operation arguments shared by preview and writer.
OPERATIONS: dict[str, dict[str, Any]] = {
    "remove": {
        "method": "remove_participant",
        "season": "clubs",
        "arguments": {
            "tournament_ids": [TOURNAMENT_A, TOURNAMENT_B],
            "remove_team_label": "D1",
            "reconcile_withdrawal": True,
        },
    },
    "replace": {
        "method": "replace_participant",
        "season": "pipeline",
        "arguments": {
            "tournament_id": TOURNAMENT_A,
            "remove_team_label": "D1",
            "add_team_label": "E1",
        },
    },
    "swap": {
        "method": "swap_participants",
        "season": "pipeline",
        "arguments": {
            "tournament_a_id": TOURNAMENT_A,
            "team_a_label": "D1",
            "tournament_b_id": TOURNAMENT_B,
            "team_b_label": "E1",
        },
    },
    "move": {
        "method": "move_tournament",
        "season": "pipeline",
        "arguments": {"tournament_id": TOURNAMENT_A, "date": "2026-09-19"},
    },
}


def _season_for(tmp_path: Path, operation: str) -> Path:
    if OPERATIONS[operation]["season"] == "pipeline":
        return _pipeline_season(tmp_path)
    root = tmp_path / "season"
    _clubs_season(root)
    return root


@pytest.fixture
def debt(monkeypatch: pytest.MonkeyPatch) -> Callable[[DebtModel | None], None]:
    """Install a hard-finding model on top of the real verifier.

    The model maps a tournament to the violations it carries. Violations marked
    ``accepted`` move to the accepted-exception list, as an exact accepted
    booking interval would.
    """

    real_verify = verification._verify_candidate
    model: dict[str, Any] = {"debt": None}

    def verify(candidate, problem=None):
        result = dict(real_verify(candidate, problem))
        violations = [dict(item) for item in result.get("violations") or []]
        for tournament in candidate.get("tournaments") or []:
            if model["debt"] is not None:
                violations.extend(model["debt"](tournament))
        result["violations"] = violations
        return result

    def reclassify(problem, candidate, violations):
        accepted = [dict(item) for item in violations if item.get("accepted")]
        remaining = [dict(item) for item in violations if not item.get("accepted")]
        return accepted, remaining

    monkeypatch.setattr(verification, "_verify_candidate", verify)
    monkeypatch.setattr(verification, "_reclassify_accepted_booking_floor", reclassify)

    def install(debt_model: DebtModel | None) -> None:
        model["debt"] = debt_model

    return install


def _labels(tournament: dict) -> set[str]:
    return {str(team.get("label")) for team in tournament.get("teams") or []}


def _static_debt(tournament: dict) -> list[Violation]:
    # Pre-existing debt on the tournament no operation touches for the removal
    # case; it is present in the baseline and in every candidate.
    if tournament["id"] != TOURNAMENT_B:
        return []
    return [{"code": "test_static_debt", "tournament_id": TOURNAMENT_B, "message": "pre-existing debt"}]


def _accepted_static_debt(tournament: dict) -> list[Violation]:
    return [{**item, "accepted": True} for item in _static_debt(tournament)]


def _removable_debt(tournament: dict) -> list[Violation]:
    # Present while D1 is on u10-a, so removing D1 resolves it.
    if tournament["id"] != TOURNAMENT_A or "D1" not in _labels(tournament):
        return []
    return [{"code": "test_removable_debt", "tournament_id": TOURNAMENT_A, "message": "D1 debt"}]


def _incoming_debt(tournament: dict) -> list[Violation]:
    # A new hard finding that appears once E1 joins u10-a.
    if tournament["id"] != TOURNAMENT_A or "E1" not in _labels(tournament):
        return []
    return [{"code": "test_incoming_rule", "tournament_id": TOURNAMENT_A, "message": "E1 not allowed here"}]


def _floor_by_team_count(tournament: dict) -> list[Violation]:
    # Shortfall shrinks as teams leave u10-a, so removing D1 improves it but
    # leaves the finding present.
    if tournament["id"] != TOURNAMENT_A:
        return []
    shortfall = 10 * len(tournament.get("teams") or []) - 20
    if shortfall <= 0:
        return []
    return [{"code": "test_floor", "tournament_id": TOURNAMENT_A, "deviation": shortfall, "message": "floor shortfall"}]


def _floor_by_incoming(tournament: dict) -> list[Violation]:
    # Shortfall grows when E1 joins u10-a, so the same finding worsens.
    if tournament["id"] != TOURNAMENT_A:
        return []
    shortfall = 30 if "E1" in _labels(tournament) else 10
    return [{"code": "test_floor", "tournament_id": TOURNAMENT_A, "deviation": shortfall, "message": "floor shortfall"}]


def _preview(service: CanonicalSeasonService, operation: str) -> dict:
    spec = OPERATIONS[operation]
    return preview_canonical_operation(
        service,
        operation=operation,
        season=SEASON,
        actor="tester",
        request_id=f"gate-{operation}",
        **spec["arguments"],
    )


def _apply(service: CanonicalSeasonService, operation: str) -> None:
    spec = OPERATIONS[operation]
    getattr(service, spec["method"])(
        season=SEASON,
        actor="tester",
        request_id=f"gate-{operation}",
        **spec["arguments"],
    )


def _assert_preview_matches_writer(root: Path, operation: str) -> dict:
    """The preview verdict and the writer outcome must agree on the same revision."""

    service = CanonicalSeasonService(root=root)
    preview = _preview(service, operation)
    if preview["verdict"]["applicable"]:
        _apply(service, operation)
    else:
        with pytest.raises(SeasonStateError):
            _apply(service, operation)
    return preview


@pytest.mark.parametrize("operation", ["swap", "move", "remove"])
def test_clean_state_is_safe_to_apply_and_the_writer_applies(tmp_path: Path, debt, operation: str) -> None:
    debt(None)
    root = _season_for(tmp_path, operation)

    preview = _assert_preview_matches_writer(root, operation)

    assert preview["verdict"]["status"] == STATUS_SAFE_TO_APPLY
    findings = preview["findings"]
    assert findings["classified"] is True
    assert findings["basis"] == "baseline_verification"
    assert findings["acceptable"] is True
    assert findings["blocking"] == []


@pytest.mark.parametrize("operation", list(OPERATIONS))
def test_unchanged_unaccepted_debt_blocks_every_single_operation(tmp_path: Path, debt, operation: str) -> None:
    # Existing unaccepted debt is not grandfathered: it stays blocking, as in batch.
    debt(_static_debt)
    root = _season_for(tmp_path, operation)
    service = CanonicalSeasonService(root=root)

    preview = _preview(service, operation)

    assert preview["verdict"]["status"] == STATUS_BLOCKED
    unchanged = preview["findings"]["buckets"]["unchanged_unaccepted"]
    assert [item["code"] for item in unchanged] == ["test_static_debt"]
    assert preview["findings"]["acceptable"] is False
    _assert_preview_matches_writer(root, operation)


@pytest.mark.parametrize("operation", ["swap", "move", "remove"])
def test_unchanged_accepted_debt_does_not_block_and_stays_visible(tmp_path: Path, debt, operation: str) -> None:
    debt(_accepted_static_debt)
    root = _season_for(tmp_path, operation)
    service = CanonicalSeasonService(root=root)

    preview = _preview(service, operation)

    assert preview["verdict"]["status"] == STATUS_SAFE_TO_APPLY
    findings = preview["findings"]
    assert [item["code"] for item in findings["buckets"]["unchanged_accepted"]] == ["test_static_debt"]
    assert findings["buckets"]["unchanged_unaccepted"] == []
    assert findings["acceptable"] is True
    _assert_preview_matches_writer(root, operation)


def test_resolved_debt_is_safe_and_reported_as_resolved(tmp_path: Path, debt) -> None:
    # Removing D1 from u10-a resolves the debt that depends on it.
    debt(_removable_debt)
    root = _season_for(tmp_path, "remove")
    service = CanonicalSeasonService(root=root)

    preview = _preview(service, "remove")

    assert preview["verdict"]["status"] == STATUS_SAFE_TO_APPLY
    assert [item["code"] for item in preview["findings"]["buckets"]["resolved"]] == ["test_removable_debt"]
    _assert_preview_matches_writer(root, "remove")


@pytest.mark.parametrize("operation", ["replace", "swap"])
def test_introduced_debt_blocks_on_the_classification_and_the_writer_refuses(
    tmp_path: Path, debt, operation: str
) -> None:
    # Bringing E1 into u10-a creates a hard finding that did not exist before.
    debt(_incoming_debt)
    root = _season_for(tmp_path, operation)
    service = CanonicalSeasonService(root=root)

    preview = _preview(service, operation)

    assert preview["verdict"]["status"] == STATUS_BLOCKED
    assert "schedule_acceptable" in preview["verdict"]["blockers"]
    assert [item["code"] for item in preview["findings"]["buckets"]["introduced"]] == ["test_incoming_rule"]
    _assert_preview_matches_writer(root, operation)


@pytest.mark.parametrize("operation", ["replace", "swap"])
def test_worsened_debt_blocks_on_the_classification_and_the_writer_refuses(
    tmp_path: Path, debt, operation: str
) -> None:
    # The existing floor finding grows when E1 joins u10-a.
    debt(_floor_by_incoming)
    root = _season_for(tmp_path, operation)
    service = CanonicalSeasonService(root=root)

    preview = _preview(service, operation)

    assert preview["verdict"]["status"] == STATUS_BLOCKED
    assert "schedule_acceptable" in preview["verdict"]["blockers"]
    assert [item["code"] for item in preview["findings"]["buckets"]["worsened"]] == ["test_floor"]
    _assert_preview_matches_writer(root, operation)


def test_improved_but_unresolved_debt_still_blocks(tmp_path: Path, debt) -> None:
    # Removing D1 shrinks the floor shortfall, but the finding remains.
    debt(_floor_by_team_count)
    root = _season_for(tmp_path, "remove")
    service = CanonicalSeasonService(root=root)

    preview = _preview(service, "remove")

    assert preview["verdict"]["status"] == STATUS_BLOCKED
    assert [item["code"] for item in preview["findings"]["buckets"]["improved"]] == ["test_floor"]
    assert preview["findings"]["buckets"]["resolved"] == []
    _assert_preview_matches_writer(root, "remove")


def test_preview_never_writes_canonical_state(tmp_path: Path, debt) -> None:
    debt(_static_debt)
    root = _pipeline_season(tmp_path)
    service = CanonicalSeasonService(root=root)
    snapshot = service.load(SEASON)
    before = canonical_state_revision(snapshot.schedule, snapshot.decisions)

    for operation in ("swap", "move", "replace"):
        _preview(service, operation)

    after = service.load(SEASON)
    assert canonical_state_revision(after.schedule, after.decisions) == before
