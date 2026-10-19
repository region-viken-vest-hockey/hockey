"""Versioned batch scenarios: revision-bound dry-run, exact apply and fail-closed refusals."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.test_canonical_batch_maintenance import (
    _add_unavailable,
    _decisions_bytes,
    _promote,
    _schedule_bytes,
    _tournaments_by_id,
)
from tournament_scheduler.application.canonical_season.batch import batch_maintenance
from tournament_scheduler.application.canonical_season.consequence_preview import (
    STATUS_BLOCKED,
    STATUS_SAFE_TO_APPLY,
    STATUS_STALE_REVISION,
    preview_canonical_operation,
)
from tournament_scheduler.application.canonical_season.scenario import SCENARIO_SCHEMA, parse_scenario
from tournament_scheduler.application.canonical_season_service import CanonicalSeasonService
from tournament_scheduler.infrastructure.canonical_season_store import SeasonStateError
from tournament_scheduler.season.state import canonical_state_revision

SEASON = "2026-2027"
DONOR = "u10-a-20260912"
RECIPIENT = "u10-b-20260920"


def _rotation_operations() -> list[dict]:
    """Donor/recipient rotation: Y1 leaves tournament A for B while W1 moves from B to A."""

    return [
        {
            "op": "swap_participants",
            "tournament_a": DONOR,
            "team_a": "Y1",
            "tournament_b": RECIPIENT,
            "team_b": "W1",
        }
    ]


def _revision(root: Path) -> str:
    snapshot = CanonicalSeasonService(root=root).load(SEASON)
    return canonical_state_revision(snapshot.schedule, snapshot.decisions)


def test_rotation_preview_is_safe_and_writes_nothing(tmp_path: Path) -> None:
    _work_dir, root = _promote(tmp_path)
    service = CanonicalSeasonService(root=root)
    before = (_schedule_bytes(root), _decisions_bytes(root))

    preview = preview_canonical_operation(
        service,
        operation="batch",
        season=SEASON,
        operations=_rotation_operations(),
        scope=[DONOR, RECIPIENT],
        request_id="rotate-y1-w1",
        actor="tester",
    )

    assert preview["verdict"]["status"] == STATUS_SAFE_TO_APPLY
    assert preview["changed_tournament_ids"] == [DONOR, RECIPIENT]
    assert preview["candidate_fingerprint"]
    assert preview["writes_canonical_state"] is False
    assert (_schedule_bytes(root), _decisions_bytes(root)) == before


def test_applied_rotation_matches_the_previewed_candidate(tmp_path: Path) -> None:
    _work_dir, root = _promote(tmp_path)
    service = CanonicalSeasonService(root=root)
    reviewed = _revision(root)
    preview = preview_canonical_operation(
        service,
        operation="batch",
        season=SEASON,
        operations=_rotation_operations(),
        scope=[DONOR, RECIPIENT],
        request_id="rotate-y1-w1",
        expected_revision=reviewed,
    )

    applied = batch_maintenance(
        service,
        season=SEASON,
        operations=_rotation_operations(),
        scope=[DONOR, RECIPIENT],
        request_id="rotate-y1-w1",
        actor="tester",
        expected_revision=reviewed,
    )

    assert applied["committed"] is True
    assert applied["revision"] == preview["candidate_fingerprint"]
    rosters = {
        tournament_id: {team["label"] for team in tournament["teams"]}
        for tournament_id, tournament in _tournaments_by_id(root).items()
    }
    assert "Y1" not in rosters[DONOR] and "W1" in rosters[DONOR]
    assert "W1" not in rosters[RECIPIENT] and "Y1" in rosters[RECIPIENT]


def test_out_of_scope_rotation_is_blocked_before_any_write(tmp_path: Path) -> None:
    _work_dir, root = _promote(tmp_path)
    service = CanonicalSeasonService(root=root)
    before = (_schedule_bytes(root), _decisions_bytes(root))

    preview = preview_canonical_operation(
        service,
        operation="batch",
        season=SEASON,
        operations=_rotation_operations(),
        scope=[DONOR],
        request_id="rotate-out-of-scope",
    )

    assert preview["verdict"]["status"] == STATUS_BLOCKED
    assert preview["verdict"]["checks"]["scope_respected"] is False
    assert any("outside the declared scope" in reason for reason in preview["verdict"]["blockers"])
    assert (_schedule_bytes(root), _decisions_bytes(root)) == before


def test_stale_expected_revision_refuses_apply_without_writes(tmp_path: Path) -> None:
    _work_dir, root = _promote(tmp_path)
    service = CanonicalSeasonService(root=root)
    reviewed = _revision(root)
    _add_unavailable(root, "concurrent-constraint", "2026-09-12")
    before = (_schedule_bytes(root), _decisions_bytes(root))

    with pytest.raises(SeasonStateError, match="no longer current"):
        batch_maintenance(
            service,
            season=SEASON,
            operations=_rotation_operations(),
            scope=[DONOR, RECIPIENT],
            request_id="rotate-stale",
            expected_revision=reviewed,
        )

    stale = preview_canonical_operation(
        service,
        operation="batch",
        season=SEASON,
        operations=_rotation_operations(),
        scope=[DONOR, RECIPIENT],
        request_id="rotate-stale",
        expected_revision=reviewed,
    )
    assert stale["verdict"]["status"] == STATUS_STALE_REVISION
    assert (_schedule_bytes(root), _decisions_bytes(root)) == before


def test_commit_is_compare_and_swap_against_the_reviewed_revision(tmp_path: Path) -> None:
    _work_dir, root = _promote(tmp_path)
    service = CanonicalSeasonService(root=root)
    original_commit = service._commit

    def commit_after_concurrent_write(snapshot, **kwargs):
        # Another writer lands between the batch's read and its commit.
        _add_unavailable(root, "racing-constraint", "2026-09-12")
        return original_commit(snapshot, **kwargs)

    service._commit = commit_after_concurrent_write  # type: ignore[method-assign]
    before_schedule = _schedule_bytes(root)

    with pytest.raises(SeasonStateError, match="changed while the batch was being applied"):
        batch_maintenance(
            service,
            season=SEASON,
            operations=_rotation_operations(),
            scope=[DONOR, RECIPIENT],
            request_id="rotate-race",
            expected_revision=_revision(root),
        )
    assert _schedule_bytes(root) == before_schedule


def test_scenario_schema_accepts_versioned_and_legacy_documents() -> None:
    operations = _rotation_operations()

    versioned = parse_scenario(
        {"schema": SCENARIO_SCHEMA, "scope": [DONOR, RECIPIENT], "operations": operations}
    )
    assert versioned == {"operations": operations, "scope": [DONOR, RECIPIENT]}

    legacy = parse_scenario(operations)
    assert legacy == {"operations": operations, "scope": None}


@pytest.mark.parametrize(
    "document, message",
    [
        ({"schema": "canonical-scenario/2", "operations": _rotation_operations()}, "unsupported schema"),
        ({"schema": SCENARIO_SCHEMA, "operations": _rotation_operations(), "owner": "x"}, "unknown keys"),
        ({"schema": SCENARIO_SCHEMA, "operations": []}, "non-empty list"),
        ({"schema": SCENARIO_SCHEMA, "operations": _rotation_operations(), "scope": "u10-a"}, "list of tournament ids"),
        ("not a scenario", "expected a JSON list"),
    ],
)
def test_scenario_schema_refuses_unknown_or_malformed_documents(document, message: str) -> None:
    with pytest.raises(SeasonStateError, match=message):
        parse_scenario(document)


def test_cli_preview_reports_stale_scenario_with_exit_three(tmp_path: Path) -> None:
    from tournament_scheduler.cli.rvv_cli import main as cli_main

    _work_dir, root = _promote(tmp_path)
    scenario = tmp_path / "rotation.json"
    scenario.write_text(
        json.dumps({"schema": SCENARIO_SCHEMA, "scope": [DONOR, RECIPIENT], "operations": _rotation_operations()})
    )
    base = [
        "season", "preview", "--season", SEASON, "--root", str(root), "--operation", "batch",
        "--operations-file", str(scenario), "--request-id", "cli-rotate",
        "--fail-on-blocked", "--json",
    ]

    assert cli_main([*base]) == 0
    assert cli_main([*base, "--expected-revision", "not-current"]) == 3
