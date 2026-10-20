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


def test_rotation_preview_reports_booked_consequences_per_affected_team(tmp_path: Path) -> None:
    _work_dir, root = _promote(tmp_path)
    service = CanonicalSeasonService(root=root)

    preview = preview_canonical_operation(
        service,
        operation="batch",
        season=SEASON,
        operations=_rotation_operations(),
        scope=[DONOR, RECIPIENT],
        request_id="rotate-y1-w1",
        actor="tester",
    )

    consequences = preview["consequences"]
    assert consequences["basis"] == "booked_reality"
    assert isinstance(consequences["provisional"], bool)
    assert consequences["affected_teams"], "rotation must report the teams it touches"
    for row in consequences["affected_teams"]:
        for side in ("before", "after"):
            counts = row[side]
            assert counts["booked"] == counts["before_christmas"] + counts["after_christmas"] + counts["unsplit"]
        assert row["delta"]["booked"] == row["after"]["booked"] - row["before"]["booked"]


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
        expected_token=preview["reviewed_token"],
    )

    assert applied["committed"] is True
    assert applied["revision"] == preview["candidate_fingerprint"]
    assert applied["reviewed_token"] == preview["reviewed_token"]
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


def _rotation_preview(service: CanonicalSeasonService, **overrides) -> dict:
    arguments = {
        "operation": "batch",
        "season": SEASON,
        "operations": _rotation_operations(),
        "scope": [DONOR, RECIPIENT],
        "request_id": "rotate-y1-w1",
        "actor": "tester",
    }
    arguments.update(overrides)
    return preview_canonical_operation(service, **arguments)


@pytest.mark.parametrize(
    "changed",
    [
        {"operations": [{"op": "move", "tournament_id": DONOR, "date": "2026-09-13"}]},
        {"scope": [DONOR, RECIPIENT, "u10-c-20261018"]},
        {"request_id": "rotate-y1-w1-amended"},
        {"allow_manual_placement": True},
    ],
    ids=["different_operations", "different_scope", "different_request_id", "different_gate_inputs"],
)
def test_apply_refuses_a_different_scenario_at_the_same_revision(tmp_path: Path, changed: dict) -> None:
    _work_dir, root = _promote(tmp_path)
    service = CanonicalSeasonService(root=root)
    reviewed = _revision(root)
    preview = _rotation_preview(service, expected_revision=reviewed)
    before = (_schedule_bytes(root), _decisions_bytes(root))

    arguments = {
        "season": SEASON,
        "operations": _rotation_operations(),
        "scope": [DONOR, RECIPIENT],
        "request_id": "rotate-y1-w1",
        "expected_revision": reviewed,
        "expected_token": preview["reviewed_token"],
        **changed,
    }
    with pytest.raises(SeasonStateError, match="no longer matches the preview"):
        batch_maintenance(service, **arguments)
    assert (_schedule_bytes(root), _decisions_bytes(root)) == before


def test_preview_reports_booked_fairness_for_every_impacted_team(tmp_path: Path) -> None:
    _work_dir, root = _promote(tmp_path)
    preview = _rotation_preview(CanonicalSeasonService(root=root))

    fairness = preview["details"]["booked_fairness"]
    assert fairness["basis"] == "booked_reality"
    labels = {row["team"] for row in fairness["impacted_teams"]}
    assert labels == {"K1", "X1", "Y1", "Z1", "W1", "V1"}
    for row in fairness["impacted_teams"]:
        assert isinstance(row["before_booked"], int)
        assert isinstance(row["after_booked"], int)
        assert row["delta"] == row["after_booked"] - row["before_booked"]
    assert fairness["lowest_booked_per_team_by_age_group"]


CANCEL_AND_SLOT_REPLACEMENT = [
    {"op": "cancel", "tournament_id": "u10-c-20261018", "reason": "cancelled by host"},
    {"op": "move", "tournament_id": DONOR, "date": "2026-10-18"},
]
CANCEL_SCOPE = [DONOR, "u10-c-20261018"]
CANCELLED_TEAMS = {"C1", "C2", "D1", "D2"}


def test_cancel_and_slot_replacement_preview_names_every_impacted_team(tmp_path: Path) -> None:
    _work_dir, root = _promote(tmp_path)
    service = CanonicalSeasonService(root=root)
    before = (_schedule_bytes(root), _decisions_bytes(root))

    blocked = preview_canonical_operation(
        service,
        operation="batch",
        season=SEASON,
        operations=CANCEL_AND_SLOT_REPLACEMENT,
        scope=CANCEL_SCOPE,
        request_id="cancel-replace",
        actor="tester",
    )

    assert blocked["verdict"]["status"] == STATUS_BLOCKED
    assert blocked["verdict"]["checks"]["consequences_acceptable"] is False
    named = {entry["team"] for entry in blocked["verdict"]["review"]["consequences"]}
    assert named == CANCELLED_TEAMS
    assert blocked["reviewed_token"]
    assert (_schedule_bytes(root), _decisions_bytes(root)) == before


def test_batch_preview_exposes_every_material_regression_per_team(tmp_path: Path) -> None:
    _work_dir, root = _promote(tmp_path)
    service = CanonicalSeasonService(root=root)

    blocked = preview_canonical_operation(
        service,
        operation="batch",
        season=SEASON,
        operations=CANCEL_AND_SLOT_REPLACEMENT,
        scope=CANCEL_SCOPE,
        request_id="cancel-replace",
        actor="tester",
    )

    regressions = blocked["consequences"]["material_regressions"]
    assert {entry["team"] for entry in regressions} == CANCELLED_TEAMS
    assert regressions == blocked["verdict"]["review"]["consequences"]
    for entry in regressions:
        assert entry["code"]
        assert entry["club"]
    assert blocked["consequences"]["basis"] == "booked_reality"


def test_cancel_and_slot_replacement_applies_only_the_reviewed_consequences(tmp_path: Path) -> None:
    _work_dir, root = _promote(tmp_path)
    service = CanonicalSeasonService(root=root)
    reviewed = _revision(root)
    review = preview_canonical_operation(
        service,
        operation="batch",
        season=SEASON,
        operations=CANCEL_AND_SLOT_REPLACEMENT,
        scope=CANCEL_SCOPE,
        request_id="cancel-replace",
        actor="tester",
    )["verdict"]["review"]

    accepted = preview_canonical_operation(
        service,
        operation="batch",
        season=SEASON,
        operations=CANCEL_AND_SLOT_REPLACEMENT,
        scope=CANCEL_SCOPE,
        request_id="cancel-replace",
        actor="tester",
        accept_reviewed_consequences=review["token"],
        accept_regression_reason="host cancelled; slot reassigned",
        expected_revision=reviewed,
    )
    assert accepted["verdict"]["status"] == STATUS_SAFE_TO_APPLY

    applied = batch_maintenance(
        service,
        season=SEASON,
        operations=CANCEL_AND_SLOT_REPLACEMENT,
        scope=CANCEL_SCOPE,
        request_id="cancel-replace",
        actor="tester",
        accept_reviewed_consequences=review["token"],
        accept_regression_reason="host cancelled; slot reassigned",
        expected_revision=reviewed,
        expected_token=accepted["reviewed_token"],
    )
    assert applied["committed"] is True
    tournaments = _tournaments_by_id(root)
    assert tournaments["u10-c-20261018"].get("cancelled") is True
    assert tournaments[DONOR]["date"] == "2026-10-18"


def test_cli_apply_requires_the_reviewed_token_from_preview(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from tournament_scheduler.cli.rvv_cli import main as cli_main

    _work_dir, root = _promote(tmp_path)
    scenario = tmp_path / "rotation.json"
    scenario.write_text(
        json.dumps({"schema": SCENARIO_SCHEMA, "scope": [DONOR, RECIPIENT], "operations": _rotation_operations()})
    )
    root_args = ["--root", str(root)]
    batch_args = [
        "season", "batch", "--season", SEASON, "--operations", str(scenario), "--request-id", "cli-rotate", *root_args,
    ]

    assert cli_main(batch_args) == 2
    capsys.readouterr()

    assert cli_main(["season", "preview", "--season", SEASON, "--operation", "batch",
                     "--operations-file", str(scenario), "--request-id", "cli-rotate", *root_args, "--json"]) == 0
    preview = json.loads(capsys.readouterr().out)

    assert cli_main([*batch_args, "--expected-revision", preview["expected_revision"],
                     "--reviewed-token", preview["reviewed_token"]]) == 0
    donor_labels = {team["label"] for team in _tournaments_by_id(root)[DONOR]["teams"]}
    assert "W1" in donor_labels and "Y1" not in donor_labels


def test_moving_a_booked_tournament_off_its_confirmed_slot_removes_it_from_booked_fairness(tmp_path: Path) -> None:
    from tournament_scheduler.application.canonical_season.store_facade import set_manual_booking_assertion

    _work_dir, root = _promote(tmp_path)
    set_manual_booking_assertion(
        season=SEASON,
        root=root,
        tournament_id=DONOR,
        booking_status="booked",
        source_scope="tournament",
        reference="email:confirmed-slot",
        stated_date="2026-09-12",
        stated_start="10:00",
        stated_end="12:00",
        actor="tester",
    )
    service = CanonicalSeasonService(root=root)
    move = [{"op": "move", "tournament_id": DONOR, "date": "2026-09-13"}]

    preview = preview_canonical_operation(
        service,
        operation="batch",
        season=SEASON,
        operations=move,
        scope=[DONOR],
        request_id="move-off-confirmed-slot",
        actor="tester",
    )

    rows = {row["team"]: row for row in preview["details"]["booked_fairness"]["impacted_teams"]}
    donor_teams = {"K1", "X1", "Y1", "Z1"}
    assert donor_teams <= set(rows)
    for team in donor_teams:
        assert rows[team]["before_booked"] == 1, team
        assert rows[team]["after_booked"] == 0, team
        assert rows[team]["delta"] == -1, team
    assert _tournaments_by_id(root)[DONOR]["date"] == "2026-09-12"


def test_impacted_teams_report_before_and_after_christmas_booked_distribution(tmp_path: Path) -> None:
    _work_dir, root = _promote(tmp_path)
    preview = _rotation_preview(CanonicalSeasonService(root=root))

    for row in preview["details"]["booked_fairness"]["impacted_teams"]:
        assert set(row["before_booked_halves"]) == {"before_christmas", "after_christmas", "unsplit"}
        assert set(row["after_booked_halves"]) == {"before_christmas", "after_christmas", "unsplit"}
        assert sum(row["before_booked_halves"].values()) == row["before_booked"]
        assert sum(row["after_booked_halves"].values()) == row["after_booked"]
        assert row["half_delta"] == {
            half: row["after_booked_halves"][half] - row["before_booked_halves"][half]
            for half in row["half_delta"]
        }


def test_transient_hard_dip_is_recovered_only_by_the_final_candidate(tmp_path: Path) -> None:
    from tournament_scheduler.application.canonical_season.store_facade import batch_maintenance as commit_batch

    _work_dir, root = _promote(tmp_path)
    # Moving A onto B's date fails hard verification on its own, but a
    # later move in the same batch leaves a valid final candidate.
    dip = {"op": "move", "tournament_id": DONOR, "date": "2026-09-20"}
    recover = {"op": "move", "tournament_id": DONOR, "date": "2026-09-01"}
    scope = [DONOR]
    service = CanonicalSeasonService(root=root)

    alone = preview_canonical_operation(
        service,
        operation="batch",
        season=SEASON,
        operations=[dip],
        scope=scope,
        request_id="transient-dip-alone",
        actor="tester",
    )
    assert alone["verdict"]["status"] == STATUS_BLOCKED
    assert "final candidate fails hard verification" in alone["verdict"]["blockers"]

    before = (_schedule_bytes(root), _decisions_bytes(root))
    recovered = preview_canonical_operation(
        service,
        operation="batch",
        season=SEASON,
        operations=[dip, recover],
        scope=scope,
        request_id="transient-dip-recovered",
        actor="tester",
    )
    assert recovered["verdict"]["status"] == STATUS_SAFE_TO_APPLY
    assert (_schedule_bytes(root), _decisions_bytes(root)) == before

    result = commit_batch(
        season=SEASON,
        root=root,
        operations=[dip, recover],
        scope=scope,
        request_id="transient-dip-recovered",
        actor="tester",
    )
    assert result["committed"] is True
    assert _tournaments_by_id(root)[DONOR]["date"] == "2026-09-01"
