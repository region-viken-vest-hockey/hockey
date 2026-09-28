"""Published-season sealing and sealed-lifecycle regression tests.

Covers the core invariant from the published-season lifecycle work:

    published projection + attested additions + recorded mutations
    = current canonical projection

plus the guards that stop a published season from silently returning to
whole-season replanning/regeneration, the emergency reopen escape hatch, and the
publication-boundary auto-seal hook.
"""

from __future__ import annotations

from pathlib import Path
from dataclasses import replace
import copy
import json

import pytest

from tournament_scheduler.infrastructure.canonical_revision_history import (
    revision_snapshot_path,
)

from tournament_scheduler.application.canonical_season_service import CanonicalSeasonService
from tournament_scheduler.canonical_state import canonical_state_revision, schedule_fingerprint
from tournament_scheduler.infrastructure.canonical_season_store import (
    DECISIONS_SCHEMA_VERSION,
    SEASON_STATE_SCHEMA_VERSION,
    CanonicalSeasonSnapshot,
    CanonicalSeasonStore,
    SeasonStateError,
)
from tournament_scheduler.pipeline.export_lifecycle import write_draft_manifest
from tournament_scheduler.pipeline.export_projection_guard import (
    ExportProjectionError,
    assert_export_preserves_canonical_plan,
    diff_tournament_projection,
    tournament_projection,
)
from tournament_scheduler.pipeline.publication_lifecycle import (
    assert_publication_allowed,
    record_publication_seal,
)
from tournament_scheduler.pipeline.state import PipelineState, StageName, StageStatus
from tournament_scheduler.published_baseline import (
    PublishedBaselineError,
    SeasonSealedError,
    effective_projection_from_canonical_schedule,
    is_published_sealed,
    projection_from_canonical_schedule,
)
from tournament_scheduler.published_mutation_history import (
    reconcile_published_baseline,
    replayed_occupancy_overrides,
)
from tournament_scheduler.application.canonical_season.scoped_mutation import (
    ScopedMutationAuthorization,
    _SCOPED_MUTATION_CAPABILITY,
    authorize_bounded_repair,
    authorize_participant_swap,
)
from tournament_scheduler.testing.reviewed_export import write_reviewed_stage4_export

def _problem(plan: dict | None = None) -> dict:
    ages = {
        str(t.get("age_group") or "U10")
        for t in (plan or {}).get("tournaments", [])
        if isinstance(t, dict)
    }
    ages.add("U10")
    return {"ice_time_minutes": {age: 120 for age in ages}}


def _tp(plan: dict) -> dict:
    return tournament_projection(plan, _problem(plan))


def _team(club: str, label: str, age_group: str = "U10") -> dict:
    return {"club": club, "label": label, "age_group": age_group}


def _tournament(tid: str, date: str, start: str, arena: str, host: str, age: str = "U10") -> dict:
    return {
        "id": tid,
        "date": date,
        "start_time": start,
        "arena": arena,
        "host_club": host,
        "age_group": age,
        "teams": [_team(host, f"{host}1", age), _team("Guest", f"G-{tid}", age)],
        "games": [],
    }


def _write_canonical(
    root: Path,
    tournaments: list[dict],
    *,
    history: list[dict] | None = None,
    season: str = "2026-2027",
    problem: dict | None = None,
) -> None:
    now = "2026-09-22T00:00:00+00:00"
    plan = {"start_date": "2026-09-01", "end_date": "2027-04-30", "tournaments": tournaments}
    fingerprint = schedule_fingerprint(plan)
    schedule = {
        "schema_version": SEASON_STATE_SCHEMA_VERSION,
        "season": season,
        "created_at": now,
        "updated_at": now,
        "revision": fingerprint,
        "fingerprint": fingerprint,
        "plan_schema_version": 1,
        "plan": plan,
        "verification_context": {"problem": problem if problem is not None else _problem(plan)},
    }
    decisions = {
        "schema_version": DECISIONS_SCHEMA_VERSION,
        "season": season,
        "created_at": now,
        "updated_at": now,
        "schedule_fingerprint": fingerprint,
        "actor": "tester",
        "decisions": {str(t["id"]): {"status": "pending_review", "placement_locked": False, "participants_locked": False, "approved_fingerprint": None} for t in tournaments},
        "history": list(history or []),
    }
    CanonicalSeasonStore(root).write(
        CanonicalSeasonSnapshot(season=season, schedule=schedule, decisions=decisions)
    )


def _move_event(tournament_id: str, **placement: str) -> dict:
    return {
        "event": "move",
        "tournament_id": tournament_id,
        "at": "2026-09-22T00:00:00+00:00",
        "actor": "tester",
        "details": {"new_placement": dict(placement)},
    }


# ---------------------------------------------------------------------------
# Pure reconciliation invariant
# ---------------------------------------------------------------------------


def test_reconciliation_applies_move_omission_and_materialization() -> None:
    baseline = _tp({"tournaments": [_tournament("rvv-1", "2026-10-11", "10:00", "A", "Alpha")]})
    current = _tp(
        {
            "tournaments": [
                _tournament("rvv-1", "2026-10-11", "12:30", "A", "Alpha"),
                _tournament("rvv-2", "2026-11-15", "10:00", "B", "Beta"),
                _tournament("rvv-3", "2026-12-19", "12:00", "C", "Gamma"),
            ]
        }
    )
    # rvv-2 existed canonically at publication but was omitted from the artifact;
    # rvv-3 was materialized after publication (attested).
    omission = _tp({"tournaments": [_tournament("rvv-2", "2026-11-15", "10:00", "B", "Beta")]})
    materialization = _tp({"tournaments": [_tournament("rvv-3", "2026-12-19", "12:00", "C", "Gamma")]})
    report = reconcile_published_baseline(
        published_projection=baseline,
        current_projection=current,
        history=[_move_event("rvv-1", start_time="12:30")],
        attested_additions={**omission, **materialization},
    )
    assert report["ok"] is True
    assert report["applied_mutation_count"] == 1


def test_reconciliation_fails_closed_on_unexplained_delta() -> None:
    baseline = _tp({"tournaments": [_tournament("rvv-1", "2026-10-11", "10:00", "A", "Alpha")]})
    current = _tp(
        {
            "tournaments": [
                _tournament("rvv-1", "2026-10-11", "10:00", "A", "Alpha"),
                _tournament("rvv-9", "2026-12-19", "12:00", "Z", "Zeta"),
            ]
        }
    )
    report = reconcile_published_baseline(
        published_projection=baseline,
        current_projection=current,
        history=[],
        attested_additions={},
    )
    assert report["ok"] is False
    assert report["unexplained_delta"]["added_tournament_ids"] == ["rvv-9"]


def test_reconciliation_detects_unexplained_placement_change() -> None:
    baseline = _tp({"tournaments": [_tournament("rvv-1", "2026-10-11", "10:00", "A", "Alpha")]})
    current = _tp({"tournaments": [_tournament("rvv-1", "2026-10-11", "13:00", "A", "Alpha")]})
    report = reconcile_published_baseline(
        published_projection=baseline,
        current_projection=current,
        history=[],
        attested_additions={},
    )
    assert report["ok"] is False
    assert report["unexplained_delta"]["placement_changes"][0]["tournament_id"] == "rvv-1"


def test_reconciliation_replays_generic_repair_after_records() -> None:
    baseline = _tp({"tournaments": [_tournament("rvv-1", "2026-10-11", "10:00", "A", "Alpha")]})
    current = _tp({"tournaments": [_tournament("rvv-1", "2026-10-11", "13:00", "A", "Alpha")]})
    report = reconcile_published_baseline(
        published_projection=baseline,
        current_projection=current,
        history=[
            {
                "event": "repair_option_applied",
                "tournament_id": "rvv-1",
                "details": {
                    "option_id": "repair-1",
                    "after_records": {
                        "rvv-1": {
                            "id": "rvv-1",
                            "date": "2026-10-11",
                            "start_time": "13:00",
                            "arena": "A",
                            "host_club": "Alpha",
                            "age_group": "U10",
                            "participants": baseline["rvv-1"]["participants"],
                        }
                    },
                },
            }
        ],
        attested_additions={},
    )
    assert report["ok"] is True


def test_reconciliation_override_chain_survives_repair_carry_forward_and_clear() -> None:
    """A repair's captured duration must not defeat a later recorded clear.

    A bounded repair can persist the overridden occupied duration into its
    ``after_records``. When the operator later releases the override, the clear
    event's recorded default has to win during replay so sealed reconciliation
    converges on the age-group default rather than the stale carried-forward
    duration.
    """

    baseline = _tp({"tournaments": [_tournament("rvv-1", "2026-10-11", "10:00", "A", "Alpha")]})
    report = reconcile_published_baseline(
        published_projection=baseline,
        current_projection=baseline,
        history=[
            {
                "event": "set_ice_time_minutes",
                "tournament_id": "rvv-1",
                "details": {"minutes": 60, "default_minutes": 120},
            },
            {
                "event": "repair_option_applied",
                "tournament_id": "rvv-1",
                "details": {
                    "option_id": "repair-1",
                    "after_records": {
                        "rvv-1": {
                            "id": "rvv-1",
                            "date": "2026-10-11",
                            "start_time": "10:00",
                            "arena": "A",
                            "host_club": "Alpha",
                            "age_group": "U10",
                            "participants": baseline["rvv-1"]["participants"],
                            "duration_minutes": 60,
                        }
                    },
                },
            },
            {
                "event": "clear_ice_time_minutes",
                "tournament_id": "rvv-1",
                "details": {"override_id": "ice-time-override:deadbeef", "default_minutes": 120},
            },
        ],
        attested_additions={},
        occupancy_overrides={},
    )
    assert report["ok"] is True
    assert report["unexplained_delta"]["changed"] is False


def test_reconciliation_fails_closed_on_unknown_history_event() -> None:
    baseline = _tp({"tournaments": [_tournament("rvv-1", "2026-10-11", "10:00", "A", "Alpha")]})
    report = reconcile_published_baseline(
        published_projection=baseline,
        current_projection=baseline,
        history=[{"event": "mystery_schedule_mutation"}],
        attested_additions={},
    )
    assert report["ok"] is False
    assert "unknown canonical history event" in report["unexplained_delta"]["replay_error"]


def test_reconciliation_ignores_export_freshness_history() -> None:
    """Recording a fresh export is decision-only and never a schedule mutation."""

    baseline = _tp({"tournaments": [_tournament("rvv-1", "2026-10-11", "10:00", "A", "Alpha")]})
    report = reconcile_published_baseline(
        published_projection=baseline,
        current_projection=baseline,
        history=[
            {
                "event": "mark_export_fresh",
                "tournament_id": "",
                "actor": "tester",
                "details": {
                    "canonical_state_revision": "rev-1",
                    "export_dir": "export/2026-09-28T0851",
                },
            }
        ],
        attested_additions={},
    )
    assert report["ok"] is True
    assert report["applied_mutation_count"] == 0


def test_reconciliation_replays_typed_cancellation_guest_and_duration_mutations() -> None:
    plan = {
        "tournaments": [
            _tournament("rvv-1", "2026-10-11", "10:00", "A", "Alpha"),
            _tournament("rvv-2", "2026-11-15", "10:00", "B", "Beta"),
        ]
    }
    baseline = _tp(plan)

    # A batch cancellation is a typed, replayed schedule mutation.
    cancelled = copy.deepcopy(plan)
    cancelled["tournaments"][0]["cancelled"] = True
    cancelled["tournaments"][0]["cancellation_reason"] = "hall closed"
    report = reconcile_published_baseline(
        published_projection=baseline,
        current_projection=tournament_projection(cancelled, _problem(cancelled)),
        history=[
            {
                "event": "batch_maintenance",
                "details": {"cancellations": [{"tournament_id": "rvv-1", "reason": "hall closed"}]},
            }
        ],
        attested_additions={},
    )
    assert report["ok"] is True

    # A guest reservation is replayed from its typed slot history.
    reserved = copy.deepcopy(plan)
    reserved["tournaments"][1]["guest_slots"] = [{"id": "guest:rvv-2:1", "status": "open"}]
    report = reconcile_published_baseline(
        published_projection=baseline,
        current_projection=tournament_projection(reserved, _problem(reserved)),
        history=[
            {
                "event": "reserve_guest_slot",
                "tournament_id": "rvv-2",
                "details": {"slots": [{"id": "guest:rvv-2:1", "status": "open"}], "displaced_teams": []},
            }
        ],
        attested_additions={},
    )
    assert report["ok"] is True

    # A semantic ice-time migration is a typed, replayed occupancy change.
    duration_baseline = tournament_projection(plan, {"ice_time_minutes": {"U10": 120}})
    duration_current = tournament_projection(plan, {"ice_time_minutes": {"U10": 150}})
    report = reconcile_published_baseline(
        published_projection=duration_baseline,
        current_projection=duration_current,
        history=[
            {
                "event": "reconcile_config",
                "details": {
                    "semantic_migrations": [
                        {
                            "field": "ice_time_minutes",
                            "age_group_changes": {"U10": {"old_value": 120, "migrated_value": 150}},
                        }
                    ]
                },
            }
        ],
        attested_additions={},
    )
    assert report["ok"] is True

    # Unrelated rows may not change without their own recorded mutation.
    unrelated = copy.deepcopy(cancelled)
    unrelated["tournaments"][1]["start_time"] = "13:00"
    report = reconcile_published_baseline(
        published_projection=baseline,
        current_projection=tournament_projection(unrelated, _problem(unrelated)),
        history=[
            {
                "event": "batch_maintenance",
                "details": {"cancellations": [{"tournament_id": "rvv-1", "reason": "hall closed"}]},
            }
        ],
        attested_additions={},
    )
    assert report["ok"] is False


def test_reconciliation_recognizes_ice_time_override_events_as_decision_only() -> None:
    """Host-confirmed ice-time overrides advance the revision but are not schedule drift.

    The override narrows the rendered occupied interval through the projected
    verification problem and never edits plan placement. Reconciliation applies
    the current active override set to the replayed schedule-only projection and
    reports the before/after duration with provenance, so it converges on the
    effective current projection instead of silently ignoring the change or
    failing closed on it. A release event restores the recorded default, and a
    genuine unrelated schedule drift is still detected.
    """

    plan = {
        "tournaments": [
            _tournament("rvv-1", "2026-10-11", "10:00", "A", "Alpha"),
            _tournament("rvv-2", "2026-11-15", "10:00", "B", "Beta"),
        ]
    }
    configured = _problem(plan)
    schedule_only = tournament_projection(plan, configured)
    override_event = {
        "event": "set_ice_time_minutes",
        "tournament_id": "rvv-1",
        "details": {"minutes": 60, "default_minutes": 120},
    }
    effective = tournament_projection(
        plan, {**configured, "ice_time_minutes_overrides": {"rvv-1": 60}}
    )
    assert effective["rvv-1"]["duration_minutes"] == 60
    assert schedule_only["rvv-1"]["duration_minutes"] == 120

    report = reconcile_published_baseline(
        published_projection=schedule_only,
        current_projection=effective,
        history=[override_event],
        attested_additions={},
        occupancy_overrides={"rvv-1": 60},
    )
    assert report["ok"] is True
    assert report["applied_mutation_count"] == 0
    assert report["decision_backed_occupancy_changes"] == [
        {"tournament_id": "rvv-1", "before_minutes": 120, "after_minutes": 60}
    ]

    # A release recorded with the restored default replays back to the default.
    released = reconcile_published_baseline(
        published_projection=schedule_only,
        current_projection=schedule_only,
        history=[
            override_event,
            {
                "event": "clear_ice_time_minutes",
                "tournament_id": "rvv-1",
                "details": {"override_id": "ice-time-override:deadbeef", "default_minutes": 120},
            },
        ],
        attested_additions={},
        occupancy_overrides={},
    )
    assert released["ok"] is True

    # An unrelated schedule drift is still unexplained and fails closed.
    drifted = copy.deepcopy(plan)
    drifted["tournaments"][0]["start_time"] = "13:00"
    drifted_override = {
        **configured,
        "ice_time_minutes_overrides": {"rvv-1": 60},
    }
    drift_report = reconcile_published_baseline(
        published_projection=schedule_only,
        current_projection=tournament_projection(drifted, drifted_override),
        history=[override_event],
        attested_additions={},
        occupancy_overrides={"rvv-1": 60},
    )
    assert drift_report["ok"] is False
    assert drift_report["unexplained_delta"]["placement_changes"][0]["tournament_id"] == "rvv-1"


def test_full_operational_projection_detects_age_duration_cancellation_and_guest_drift() -> None:
    plan = {"tournaments": [_tournament("rvv-1", "2026-10-11", "10:00", "A", "Alpha")]}
    baseline = tournament_projection(plan, {"ice_time_minutes": {"U10": 120, "U11": 120}})

    age_changed = copy.deepcopy(plan)
    age_changed["tournaments"][0]["age_group"] = "U11"
    delta = diff_tournament_projection(
        baseline,
        tournament_projection(age_changed, {"ice_time_minutes": {"U10": 120, "U11": 120}}),
    )
    assert delta["changed"] is True
    assert delta["field_changes"][0]["fields"]["age_group"] == {"before": "U10", "after": "U11"}

    longer = tournament_projection(plan, {"ice_time_minutes": {"U10": 150}})
    delta = diff_tournament_projection(baseline, longer)
    assert delta["duration_changes"][0]["fields"]["duration_minutes"] == {"before": 120, "after": 150}

    cancelled = copy.deepcopy(plan)
    cancelled["tournaments"][0]["cancelled"] = True
    cancelled["tournaments"][0]["cancellation_reason"] = "hall closed"
    delta = diff_tournament_projection(baseline, tournament_projection(cancelled, _problem(cancelled)))
    assert delta["cancellation_changes"][0]["fields"]["cancelled"] == {"before": False, "after": True}

    guest = copy.deepcopy(plan)
    guest["tournaments"][0]["guest_slots"] = [{"id": "guest:rvv-1:1", "status": "open"}]
    delta = diff_tournament_projection(baseline, tournament_projection(guest, _problem(guest)))
    assert delta["guest_slot_changes"][0]["tournament_id"] == "rvv-1"

    evidence_only = copy.deepcopy(plan)
    evidence_only["tournaments"][0]["approval_status"] = "approved"
    assert diff_tournament_projection(baseline, tournament_projection(evidence_only, _problem(evidence_only)))["changed"] is False

    with pytest.raises(ExportProjectionError) as excinfo:
        assert_export_preserves_canonical_plan(
            canonical_plan=plan,
            proposed_plan=plan,
            canonical_problem={"ice_time_minutes": {"U10": 120}},
            proposed_problem={"ice_time_minutes": {"U10": 150}},
        )
    assert excinfo.value.report["canonical_delta"]["duration_changes"]


def test_projection_diff_fails_closed_on_legacy_or_incomplete_schema() -> None:
    current = _tp({"tournaments": [_tournament("rvv-1", "2026-10-11", "10:00", "A", "Alpha")]})
    legacy = {
        "rvv-1": {
            "id": "rvv-1",
            "date": "2026-10-11",
            "start_time": "10:00",
            "arena": "A",
            "host_club": "Alpha",
            "age_group": "U10",
            "participants": current["rvv-1"]["participants"],
        }
    }
    delta = diff_tournament_projection(legacy, current)
    assert delta["changed"] is True
    assert delta["schema_errors"][0]["reason"] == "unsupported_projection_schema"


# ---------------------------------------------------------------------------
# Lifecycle sealing and guards
# ---------------------------------------------------------------------------


def _tournaments_abc() -> list[dict]:
    return [
        _tournament("rvv-1", "2026-10-11", "10:00", "A", "Alpha"),
        _tournament("rvv-2", "2026-11-15", "10:00", "B", "Beta"),
        _tournament("rvv-3", "2026-12-19", "12:00", "C", "Gamma"),
    ]


def _seal_abc(root: Path, *, materialize_three: bool = True) -> dict:
    published = _tp({"tournaments": _tournaments_abc()[:1]})
    publication_canonical = _tp({"tournaments": _tournaments_abc()[:2]})
    return CanonicalSeasonService(root=root).seal_published_season(
        season="2026-2027",
        publication_id="2026-09-21T0908",
        canonical_revision="rev-published",
        published_at="2026-09-21T09:14:53+00:00",
        published_projection=published,
        publication_canonical_projection=publication_canonical,
        materializations=(
            [{"tournament_id": "rvv-3", "provenance": "materialized by durable repository history"}]
            if materialize_three
            else []
        ),
        actor="tester",
    )


_REPAIRABLE_FINDING = "unplaced_placement:U10:2026-10-10:1"
_REPAIRABLE_EXISTING_ID = "rvv-existing"
_REPAIRABLE_EXTRA_FINDING = "unplaced_placement:U10:2026-10-17:1"


def _write_sealed_repairable_season(
    root: Path,
    *,
    season: str = "2026-2027",
    extra_obligations: int = 0,
) -> None:
    """Canonical season with one scheduled tournament and unplaced obligations.

    The named obligation materializes through the bounded ``unplaced_placement``
    repair, giving a sealed season a real one-tournament bounded repair to
    exercise the service-owned authorization end to end. Extra obligations are
    unrelated plan-owned facts a bounded repair must preserve.
    """

    from datetime import date as _date

    from tournament_scheduler.planning_contract import build_planning_problem

    teams = [
        {"club": club, "label": f"{club} {index}", "age_group": "U10"}
        for club in ("Nordby", "Sorby")
        for index in (1, 2)
    ]
    config = {
        "teams": teams,
        "age_groups": ["U10"],
        "parallel_games": {"U10": 2},
        "round_length_minutes": {"U10": 30},
        "ice_time_minutes": {"U10": 120},
        "rounds_per_tournament": {"U10": 3},
    }
    problem = build_planning_problem(config, None, _date(2026, 10, 1), _date(2026, 10, 31))
    problem["clubs"] = {club: f"{club} Arena" for club in ("Nordby", "Sorby")}
    problem["club_calendar_status"] = {club: "known" for club in ("Nordby", "Sorby")}
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
    existing = {
        "id": _REPAIRABLE_EXISTING_ID,
        "date": "2026-10-03",
        "arena": "Nordby Arena",
        "age_group": "U10",
        "host_club": "Nordby",
        "teams": [dict(team) for team in teams],
        "games": games,
        "start_time": "10:00",
    }
    obligation = {
        "id": _REPAIRABLE_FINDING,
        "age_group": "U10",
        "date": "2026-10-10",
        "period": "before_christmas",
        "responsible_host": "Sorby",
        "participant_teams": [dict(team) for team in teams],
        "participant_team_count": len(teams),
        "required_duration_minutes": 90,
        "category": "manual_tournament_placement",
        "search_attempted": True,
        "bounded_repair_exhausted": True,
        "reason": "no_participant_host_slot",
        "source_tournament_id": "rvv-9001",
    }
    unresolved = [obligation]
    for index in range(extra_obligations):
        extra = dict(obligation)
        extra["id"] = f"unplaced_placement:U10:2026-10-{17 + index:02d}:1"
        extra["date"] = f"2026-10-{17 + index:02d}"
        extra["source_tournament_id"] = f"rvv-900{2 + index}"
        unresolved.append(extra)
    plan = {
        "schema_version": 1,
        "start_date": "2026-10-01",
        "end_date": "2026-10-31",
        "tournaments": [existing],
        "unresolved_tournament_placements": unresolved,
    }
    now = "2026-09-22T00:00:00+00:00"
    fingerprint = schedule_fingerprint(plan)
    schedule = {
        "schema_version": SEASON_STATE_SCHEMA_VERSION,
        "season": season,
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
        "season": season,
        "created_at": now,
        "updated_at": now,
        "schedule_fingerprint": fingerprint,
        "actor": "tester",
        "decisions": {
            _REPAIRABLE_EXISTING_ID: {
                "status": "pending_review",
                "placement_locked": False,
                "participants_locked": False,
                "approved_fingerprint": None,
            }
        },
        "history": [],
    }
    CanonicalSeasonStore(root).write(
        CanonicalSeasonSnapshot(season=season, schedule=schedule, decisions=decisions)
    )
    service = CanonicalSeasonService(root=root)
    snapshot = service.load(season)
    projection = tournament_projection(snapshot.schedule["plan"], problem)
    service.seal_published_season(
        season=season,
        publication_id="2026-09-21T0908",
        canonical_revision="rev-published",
        published_at="2026-09-21T09:14:53+00:00",
        published_projection=projection,
        publication_canonical_projection=projection,
        materializations=[],
        actor="tester",
    )


def test_seal_records_baseline_and_state(tmp_path: Path) -> None:
    root = tmp_path / "season"
    _write_canonical(root, _tournaments_abc())
    report = _seal_abc(root)
    assert report["state"] == "published_sealed"
    assert report["reconciliation"]["publication_omissions"] == ["rvv-2"]
    assert report["reconciliation"]["materializations"] == ["rvv-3"]

    snapshot = CanonicalSeasonStore(root).load("2026-2027")
    assert is_published_sealed(snapshot.decisions)
    lifecycle = snapshot.decisions["season_lifecycle"]
    assert lifecycle["published_baseline"]["tournament_count"] == 1
    assert len(lifecycle["publication_history"]) == 1
    # The immutable baseline remains the actual published artifact (1 row).
    assert len(lifecycle["published_baseline"]["tournaments"]) == 1

    status = CanonicalSeasonService(root=root).season_lifecycle_report("2026-2027")
    assert status["state"] == "published_sealed"
    assert status["reconciliation"]["ok"] is True


def test_sealed_reconciliation_accepts_a_recorded_export_freshness_event(
    tmp_path: Path,
) -> None:
    """A successful export on a sealed season must not break reconciliation."""

    root = tmp_path / "season"
    _write_canonical(root, _tournaments_abc())
    _seal_abc(root)
    service = CanonicalSeasonService(root=root)
    snapshot = service.load("2026-2027")
    revision = canonical_state_revision(snapshot.schedule, snapshot.decisions)

    result = service.mark_export_fresh(
        season="2026-2027",
        expected_revision=revision,
        export_dir="export/2026-09-28T0851",
        actor="tester",
    )
    assert result["cleared"] is True

    report = service.verify_sealed_reconciliation("2026-2027")
    assert report["ok"] is True, report


def test_legacy_published_baseline_is_backfilled_from_publication_revision(tmp_path: Path) -> None:
    root = tmp_path / "season"
    _write_canonical(root, _tournaments_abc())
    _seal_abc(root)

    # Simulate a baseline recorded before the versioned operational projection:
    # strip the operational fields the legacy record never carried.
    store = CanonicalSeasonStore(root)
    snapshot = store.load("2026-2027")
    decisions = copy.deepcopy(snapshot.decisions)
    baseline = decisions["season_lifecycle"]["published_baseline"]
    legacy_fields = (
        "projection_schema",
        "projection_schema_version",
        "duration_minutes",
        "end_time",
        "cancelled",
        "cancellation_reason",
        "guest_slots",
    )
    for entry in baseline["tournaments"]:
        for field in legacy_fields:
            entry.pop(field, None)
    for key in ("publication_omissions", "materializations"):
        for entry in (baseline.get("migration") or {}).get(key) or []:
            for field in legacy_fields:
                entry.pop(field, None)

    revision = str(baseline["canonical_revision"])
    publication_tournaments = _tournaments_abc()[:2]
    snapshot_path = revision_snapshot_path("2026-2027", revision, season_root=root)
    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    snapshot_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "season": "2026-2027",
                "revision": revision,
                "plan": {"tournaments": publication_tournaments},
                "verification_context": {"problem": _problem({"tournaments": publication_tournaments})},
            }
        ),
        encoding="utf-8",
    )
    store.write(
        CanonicalSeasonSnapshot(
            season="2026-2027",
            schedule=snapshot.schedule,
            decisions=decisions,
        )
    )

    report = CanonicalSeasonService(root=root).season_lifecycle_report("2026-2027")
    assert report["reconciliation"]["ok"] is True, report["reconciliation"]["unexplained_delta"]

    # The immutable baseline record itself is never rewritten by the read.
    reloaded = store.load("2026-2027")
    assert reloaded.decisions["season_lifecycle"]["published_baseline"]["tournaments"][0].get(
        "projection_schema"
    ) is None


def test_seal_fails_closed_on_unsupported_materialization(tmp_path: Path) -> None:
    root = tmp_path / "season"
    _write_canonical(root, _tournaments_abc())
    published = _tp({"tournaments": _tournaments_abc()[:1]})
    publication_canonical = _tp({"tournaments": _tournaments_abc()[:2]})
    with pytest.raises(PublishedBaselineError):
        CanonicalSeasonService(root=root).seal_published_season(
            season="2026-2027",
            publication_id="2026-09-21T0908",
            canonical_revision="rev-published",
            published_at="2026-09-21T09:14:53+00:00",
            published_projection=published,
            publication_canonical_projection=publication_canonical,
            # rvv-3 is not attested -> unexplained added tournament.
            materializations=[],
        )


def test_sealed_season_guards_global_regeneration(tmp_path: Path) -> None:
    from tournament_scheduler.canonical_replan import replan_around_baseline
    from tournament_scheduler.season_state import apply_candidate, normalize_placements

    root = tmp_path / "season"
    _write_canonical(root, _tournaments_abc())
    _seal_abc(root)

    with pytest.raises(SeasonSealedError):
        apply_candidate(season="2026-2027", candidate={"tournaments": []}, root=root)
    with pytest.raises(SeasonSealedError):
        apply_candidate(
            season="2026-2027",
            candidate={"tournaments": []},
            root=root,
            operation="targeted_mutation",
        )
    with pytest.raises(SeasonSealedError):
        normalize_placements(season="2026-2027", root=root, dry_run=False)
    with pytest.raises(SeasonSealedError):
        replan_around_baseline(
            season="2026-2027",
            config={},
            scraping_result=None,
            start_date=__import__("datetime").date(2026, 9, 1),
            end_date=__import__("datetime").date(2027, 4, 30),
            root=root,
        )

    # Targeted, decision-only maintenance still works.
    from tournament_scheduler.season_state import approve_tournament

    approve_tournament(season="2026-2027", tournament_id="rvv-1", root=root, actor="tester")
    status = CanonicalSeasonService(root=root).season_lifecycle_report("2026-2027")
    assert status["reconciliation"]["ok"] is True


def test_sealed_service_refuses_caller_constructed_wide_scope(tmp_path: Path) -> None:
    """A caller-built scope or stolen capability token is never authorization."""

    from tournament_scheduler.season_state import apply_candidate as api_apply_candidate

    root = tmp_path / "season"
    _write_canonical(root, _tournaments_abc())
    _seal_abc(root)
    service = CanonicalSeasonService(root=root)
    snapshot = service.load("2026-2027")
    candidate = copy.deepcopy(snapshot.schedule["plan"])
    for tournament in candidate["tournaments"]:
        tournament["start_time"] = "23:00"
    wide_ids = tuple(sorted(str(t["id"]) for t in candidate["tournaments"]))
    # A fully-formed object carrying the module capability token, declaring
    # every tournament and replayable history -- still not authorization.
    forged = ScopedMutationAuthorization(
        operation="bounded_repair",
        expected_canonical_revision=canonical_state_revision(
            snapshot.schedule, snapshot.decisions
        ),
        parameters={"option_id": "forged-option"},
        affected_tournament_ids=wide_ids,
        before_records={tournament_id: None for tournament_id in wide_ids},
        after_records={tournament_id: None for tournament_id in wide_ids},
        permitted_history_event="repair_option_applied",
        _capability=_SCOPED_MUTATION_CAPABILITY,
    )
    forged_history = {
        "event": "repair_option_applied",
        "tournament_id": wide_ids[0],
        "details": {"after_records": forged.after_records},
    }

    with pytest.raises(SeasonStateError):
        service.apply_candidate(
            season="2026-2027",
            candidate=candidate,
            operation="global_regeneration",
            _scoped_authorization=forged,
            _history_event=forged_history,
        )
    # A plain non-authorization object on a sealed season falls through to the
    # global-regeneration refusal, even with a plausible shape.
    with pytest.raises(SeasonSealedError):
        service.apply_candidate(
            season="2026-2027",
            candidate=candidate,
            operation="global_regeneration",
            _scoped_authorization=dict(forged.__dict__),
            _history_event=forged_history,
        )
    with pytest.raises(SeasonSealedError):
        api_apply_candidate(
            season="2026-2027",
            candidate=candidate,
            root=root,
            operation="targeted_mutation",
        )
    # Nothing was written.
    assert service.load("2026-2027").schedule["plan"] == snapshot.schedule["plan"]


def test_sealed_bounded_repair_is_reproduced_and_replays(tmp_path: Path) -> None:
    from tournament_scheduler.season_maintenance import apply_repair, repair_options

    root = tmp_path / "season"
    _write_sealed_repairable_season(root)
    service = CanonicalSeasonService(root=root)
    before = service.load("2026-2027").schedule["plan"]["tournaments"]

    report = repair_options("2026-2027", _REPAIRABLE_FINDING, root=root)
    assert report["option_count"] >= 1
    result = apply_repair(
        "2026-2027",
        report["options"][0]["option_id"],
        report["revision"],
        root=root,
        finding_id=_REPAIRABLE_FINDING,
    )

    assert result["ok"] is True, result
    assert service.season_lifecycle_report("2026-2027")["reconciliation"]["ok"] is True
    latest = service.load("2026-2027")
    assert latest.decisions["history"][-1]["event"] == "repair_option_applied"
    # The pre-existing tournament is untouched by the bounded repair.
    assert [t for t in latest.schedule["plan"]["tournaments"] if t["id"] == _REPAIRABLE_EXISTING_ID] == [
        t for t in before if t["id"] == _REPAIRABLE_EXISTING_ID
    ]


def _minted_repair_authorization(root: Path):
    """Return a real sealed bounded-repair candidate plus its authorization."""

    from tournament_scheduler.season_maintenance import (
        apply_repair_to_plan,
        load_context,
        repair_options,
    )

    service = CanonicalSeasonService(root=root)
    snapshot = service.load("2026-2027")
    _schedule, _decisions, _plan, problem = load_context("2026-2027", root=str(root))
    report = repair_options("2026-2027", _REPAIRABLE_FINDING, root=root)
    option_id = report["options"][0]["option_id"]
    reproduction = apply_repair_to_plan(
        snapshot.schedule["plan"], problem, option_id, finding_id=_REPAIRABLE_FINDING
    )
    assert reproduction["ok"] is True
    candidate = reproduction["candidate"]
    authorization = authorize_bounded_repair(
        schedule=snapshot.schedule,
        decisions=snapshot.decisions,
        candidate=candidate,
        option_id=option_id,
        finding_id=_REPAIRABLE_FINDING,
    )
    return service, snapshot, problem, candidate, authorization


def test_sealed_bounded_repair_rejects_widened_candidate_and_stale_revision(
    tmp_path: Path,
) -> None:
    root = tmp_path / "season"
    _write_sealed_repairable_season(root)
    service, _snapshot, problem, candidate, authorization = _minted_repair_authorization(root)
    assert authorization.operation == "bounded_repair"
    history = {
        "event": "repair_option_applied",
        "tournament_id": authorization.affected_tournament_ids[0],
        "details": {},
    }

    widened = copy.deepcopy(candidate)
    for tournament in widened["tournaments"]:
        if tournament["id"] == _REPAIRABLE_EXISTING_ID:
            tournament["start_time"] = "23:00"
    with pytest.raises(SeasonStateError, match="does not match the reproduced operation"):
        service.apply_candidate(
            season="2026-2027",
            candidate=widened,
            problem=problem,
            operation="targeted_repair",
            _scoped_authorization=authorization,
            _history_event=history,
        )

    service.approve_tournament(
        season="2026-2027", tournament_id=_REPAIRABLE_EXISTING_ID, actor="tester"
    )
    with pytest.raises(SeasonStateError, match="expected canonical revision"):
        service.apply_candidate(
            season="2026-2027",
            candidate=candidate,
            problem=problem,
            operation="targeted_repair",
            _scoped_authorization=authorization,
            _history_event=history,
        )


def test_sealed_repair_rejects_unrelated_obligation_deletion(tmp_path: Path) -> None:
    root = tmp_path / "season"
    _write_sealed_repairable_season(root, extra_obligations=1)
    service, _snapshot, problem, candidate, authorization = _minted_repair_authorization(root)

    forged = copy.deepcopy(candidate)
    remaining = [
        obligation
        for obligation in forged.get("unresolved_tournament_placements") or []
        if str(obligation.get("id") or "") != _REPAIRABLE_EXTRA_FINDING
    ]
    assert len(remaining) == len(forged["unresolved_tournament_placements"]) - 1
    forged["unresolved_tournament_placements"] = remaining

    with pytest.raises(SeasonStateError, match="does not match the reproduced operation"):
        service.apply_candidate(
            season="2026-2027",
            candidate=forged,
            problem=problem,
            operation="targeted_repair",
            _scoped_authorization=authorization,
            _history_event={
                "event": "repair_option_applied",
                "tournament_id": authorization.affected_tournament_ids[0],
                "details": {},
            },
        )


def test_sealed_roster_authorization_rejects_unrelated_metadata_change(tmp_path: Path) -> None:
    from tournament_scheduler.application.canonical_season.roster import _apply_swap_to_plan

    root = tmp_path / "season"
    _write_canonical(root, _tournaments_abc())
    _seal_abc(root)
    service = CanonicalSeasonService(root=root)
    snapshot = service.load("2026-2027")
    problem = snapshot.schedule["verification_context"]["problem"]
    plan = copy.deepcopy(snapshot.schedule["plan"])
    _apply_swap_to_plan(
        plan,
        tournament_a_id="rvv-1",
        team_a_label="G-rvv-1",
        tournament_b_id="rvv-2",
        team_b_label="G-rvv-2",
        problem=problem,
    )
    authorization = authorize_participant_swap(
        schedule=snapshot.schedule,
        decisions=snapshot.decisions,
        candidate=plan,
        tournament_a_id="rvv-1",
        team_a_label="G-rvv-1",
        tournament_b_id="rvv-2",
        team_b_label="G-rvv-2",
    )

    forged = copy.deepcopy(plan)
    for tournament in forged["tournaments"]:
        if tournament["id"] == "rvv-3":
            tournament["requires_host_confirmation"] = True
            tournament["host_confirmation_reason"] = "forged"
            tournament["placement_status"] = "provisional"
    with pytest.raises(SeasonStateError, match="does not match the reproduced operation"):
        service.apply_candidate(
            season="2026-2027",
            candidate=forged,
            problem=problem,
            operation="targeted_mutation",
            _scoped_authorization=authorization,
            _history_event={"event": "participant_swap", "tournament_id": "rvv-1", "details": {}},
        )


def test_sealed_repair_rejects_unrelated_game_change(tmp_path: Path) -> None:
    root = tmp_path / "season"
    _write_sealed_repairable_season(root)
    service, _snapshot, problem, candidate, authorization = _minted_repair_authorization(root)

    forged = copy.deepcopy(candidate)
    for tournament in forged["tournaments"]:
        if tournament["id"] == _REPAIRABLE_EXISTING_ID:
            tournament["games"] = [
                {"home": "Nordby 1", "away": "Sorby 1", "round_number": 1, "parallel_slot": 0}
            ]
    with pytest.raises(SeasonStateError, match="does not match the reproduced operation"):
        service.apply_candidate(
            season="2026-2027",
            candidate=forged,
            problem=problem,
            operation="targeted_repair",
            _scoped_authorization=authorization,
            _history_event={
                "event": "repair_option_applied",
                "tournament_id": authorization.affected_tournament_ids[0],
                "details": {},
            },
        )


def test_sealed_scoped_mutation_binds_permitted_history_event(tmp_path: Path) -> None:
    root = tmp_path / "season"
    _write_sealed_repairable_season(root)
    service, _snapshot, problem, candidate, authorization = _minted_repair_authorization(root)

    with pytest.raises(SeasonStateError, match="permitted durable history event"):
        service.apply_candidate(
            season="2026-2027",
            candidate=candidate,
            problem=problem,
            operation="targeted_repair",
            _scoped_authorization=authorization,
            _history_event={"event": "move", "tournament_id": "rvv-existing", "details": {}},
        )

    forged = replace(authorization, permitted_history_event="move")
    with pytest.raises(SeasonStateError, match="permitted durable history event"):
        service.apply_candidate(
            season="2026-2027",
            candidate=candidate,
            problem=problem,
            operation="targeted_repair",
            _scoped_authorization=forged,
            _history_event={"event": "move", "tournament_id": "rvv-existing", "details": {}},
        )


def test_sealed_scoped_mutation_rejects_forged_projection_records(tmp_path: Path) -> None:
    root = tmp_path / "season"
    _write_sealed_repairable_season(root)
    service, _snapshot, problem, candidate, authorization = _minted_repair_authorization(root)
    history = {
        "event": "repair_option_applied",
        "tournament_id": authorization.affected_tournament_ids[0],
        "details": {},
    }

    empty = replace(authorization, before_records={}, after_records={})
    with pytest.raises(SeasonStateError, match="before-state"):
        service.apply_candidate(
            season="2026-2027",
            candidate=candidate,
            problem=problem,
            operation="targeted_repair",
            _scoped_authorization=empty,
            _history_event=history,
        )

    forged_after = replace(
        authorization,
        after_records={tid: None for tid in authorization.affected_tournament_ids},
    )
    with pytest.raises(SeasonStateError, match="after-state"):
        service.apply_candidate(
            season="2026-2027",
            candidate=candidate,
            problem=problem,
            operation="targeted_repair",
            _scoped_authorization=forged_after,
            _history_event=history,
        )


def test_sealed_scoped_history_stamps_recomputed_projection_records(tmp_path: Path) -> None:
    root = tmp_path / "season"
    _write_sealed_repairable_season(root)
    service, _snapshot, problem, candidate, authorization = _minted_repair_authorization(root)

    service.apply_candidate(
        season="2026-2027",
        candidate=candidate,
        problem=problem,
        operation="targeted_repair",
        _scoped_authorization=authorization,
        _history_event={
            "event": "repair_option_applied",
            "tournament_id": authorization.affected_tournament_ids[0],
            "details": {"before_records": {}, "after_records": {}},
        },
    )

    details = service.load("2026-2027").decisions["history"][-1]["details"]
    assert details["before_records"] == authorization.before_records
    assert details["after_records"] == authorization.after_records
    assert details["before_records"]
    assert details["after_records"]


def test_sealed_scoped_apply_uses_authoritative_problem_for_verification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import tournament_scheduler.application.canonical_season.candidates as candidate_module

    seen: list[dict | None] = []
    original = candidate_module.verify_candidate

    def spy(candidate, problem=None):
        if isinstance(problem, dict):
            seen.append(copy.deepcopy(problem))
        else:
            seen.append(problem)
        return original(candidate, problem)

    monkeypatch.setattr(candidate_module, "verify_candidate", spy)

    for name, supplied_problem in (("none", None), ("altered", {"ice_time_minutes": {"U10": 1}})):
        root = tmp_path / name
        _write_sealed_repairable_season(root)
        service, _snapshot, _problem_arg, candidate, authorization = _minted_repair_authorization(root)
        service.apply_candidate(
            season="2026-2027",
            candidate=candidate,
            problem=supplied_problem,
            operation="targeted_repair",
            _scoped_authorization=authorization,
            _history_event={
                "event": "repair_option_applied",
                "tournament_id": authorization.affected_tournament_ids[0],
                "details": {},
            },
        )

    scoped_problems = [item for item in seen if isinstance(item, dict) and item.get("rounds_per_tournament")]
    assert scoped_problems
    assert all(item["rounds_per_tournament"].get("U10") == 3 for item in scoped_problems)
    assert not any(item == {"ice_time_minutes": {"U10": 1}} for item in seen)


def test_sealed_move_reconciles_immediately(tmp_path: Path) -> None:
    root = tmp_path / "season"
    _write_canonical(root, _tournaments_abc())
    _seal_abc(root)
    service = CanonicalSeasonService(root=root)
    service.move_tournament(
        season="2026-2027",
        tournament_id="rvv-1",
        start_time="11:00",
        actor="tester",
    )
    assert service.season_lifecycle_report("2026-2027")["reconciliation"]["ok"] is True


def test_sealed_confirm_calendar_booking_reconciles_shorter_authoritative_interval(
    tmp_path: Path,
) -> None:
    """An event-authoritative interval below the planning minimum reconciles on a sealed season.

    The calendar confirmation changes the booked interval (start time plus an
    evidence-backed occupancy override). That occupancy change must be recorded
    through the canonical override-decision chain so the sealed-season replay
    can prove its provenance instead of refusing the otherwise-correct match.
    """

    from tournament_scheduler.calendar_bookings import iter_events

    root = tmp_path / "season"
    tournaments = [_tournament("rvv-1", "2026-10-11", "10:00", "Alpha Arena", "Alpha")]
    problem = _problem({"tournaments": tournaments})
    problem["clubs"] = {"Alpha": "Alpha Arena"}
    problem["club_calendar_status"] = {"Alpha": "known"}
    # 80 minutes is below the 120-minute governing floor for U10: the calendar
    # records what is actually booked, not what the planning model prefers.
    problem["club_busy_intervals"] = {
        "Alpha": [
            {
                "date": "2026-10-11",
                "start": "10:00",
                "end": "11:20",
                "availability": "fixed_busy",
                "calendar_event": "Serierunde U10",
            }
        ]
    }
    _write_canonical(root, tournaments, problem=problem)
    service = CanonicalSeasonService(root=root)
    snapshot = service.load("2026-2027")
    projection = tournament_projection(snapshot.schedule["plan"], problem)
    service.seal_published_season(
        season="2026-2027",
        publication_id="2026-09-21T0908",
        canonical_revision="rev-published",
        published_at="2026-09-21T09:14:53+00:00",
        published_projection=projection,
        publication_canonical_projection=projection,
        materializations=[],
        actor="tester",
    )

    event = next(iter(iter_events(problem)))
    result = service.confirm_calendar_booking(
        season="2026-2027",
        event_fingerprint=event["fingerprint"],
        tournament_id="rvv-1",
        note="host confirmed the actual booking",
        actor="tester",
    )

    assert result["interval_alignment"]["accepted_calendar_interval"]["start_time"] == "10:00"
    assert result["ice_time_override"]["minutes"] == 80
    assert result["ice_time_override"]["minimum_minutes"] == 120
    assert [w["code"] for w in result["booking_feasibility_warnings"]] == [
        "ice_time_governing_minimum"
    ]

    loaded = service.load("2026-2027")
    tournament = next(t for t in loaded.schedule["plan"]["tournaments"] if t["id"] == "rvv-1")
    assert tournament["start_time"] == "10:00"
    assert service.season_lifecycle_report("2026-2027")["reconciliation"]["ok"] is True


def test_sealed_confirm_calendar_booking_batch_replays_mixed_override_history(
    tmp_path: Path,
) -> None:
    """Production-shaped Frisk Asker batch: correct matches are not refused.

    Two host-calendar matches are bound on a sealed season. One tournament has
    no prior override (the zero-override shape); the other carries a
    set -> supersede -> clear chain before its match. The calendar description
    is authoritative for the booked interval, so every occupancy change must be
    recorded through the canonical override-decision chain and the active map
    must equal that replayed chain instead of failing as phantom drift.
    """

    from tournament_scheduler.calendar_bookings import iter_events
    from tournament_scheduler.canonical_ice_time_overrides import active_overrides

    root = tmp_path / "season"
    tournaments = [
        _tournament("rvv-0001", "2026-10-11", "10:00", "Alpha Arena", "Alpha"),
        _tournament("rvv-0002", "2026-10-18", "10:00", "Alpha Arena", "Alpha"),
    ]
    problem = _problem({"tournaments": tournaments})
    problem["clubs"] = {"Alpha": "Alpha Arena"}
    problem["club_calendar_status"] = {"Alpha": "known"}
    problem["club_busy_intervals"] = {
        "Alpha": [
            {
                "date": "2026-10-11",
                "start": "10:00",
                "end": "11:20",
                "availability": "fixed_busy",
                "calendar_event": "JU10 Serierunde 11",
            },
            {
                "date": "2026-10-18",
                "start": "10:00",
                "end": "11:20",
                "availability": "fixed_busy",
                "calendar_event": "JU10 Serierunde 18",
            },
        ]
    }
    _write_canonical(root, tournaments, problem=problem)
    service = CanonicalSeasonService(root=root)
    snapshot = service.load("2026-2027")
    projection = tournament_projection(snapshot.schedule["plan"], problem)
    service.seal_published_season(
        season="2026-2027",
        publication_id="2026-09-21T0908",
        canonical_revision="rev-published",
        published_at="2026-09-21T09:14:53+00:00",
        published_projection=projection,
        publication_canonical_projection=projection,
        materializations=[],
        actor="tester",
    )

    # The rvv-0002 shape: set, supersede and clear before the match is bound.
    service.set_ice_time_minutes(
        season="2026-2027", tournament_id="rvv-0002", minutes=130,
        request_id="host:first", note="host window", actor="tester",
    )
    service.set_ice_time_minutes(
        season="2026-2027", tournament_id="rvv-0002", minutes=140,
        request_id="host:second", note="host revised window", actor="tester",
    )
    assert service.clear_ice_time_minutes(
        season="2026-2027", tournament_id="rvv-0002", note="window reverted", actor="tester",
    )["changed"] is True
    assert service.season_lifecycle_report("2026-2027")["reconciliation"]["ok"] is True

    events = {str(event["calendar_event"]): event for event in iter_events(problem)}
    results = {
        tournament_id: service.confirm_calendar_booking(
            season="2026-2027",
            event_fingerprint=events[event_name]["fingerprint"],
            tournament_id=tournament_id,
            note="host confirmed the actual booking",
            actor="tester",
        )
        for tournament_id, event_name in (
            ("rvv-0001", "JU10 Serierunde 11"),
            ("rvv-0002", "JU10 Serierunde 18"),
        )
    }

    for result in results.values():
        assert result["ice_time_override"]["minutes"] == 80
        assert result["interval_alignment"]["changed"] is True
    decisions = service.load("2026-2027").decisions
    active = active_overrides(decisions)
    assert active == replayed_occupancy_overrides(decisions["history"])
    assert active == {"rvv-0001": 80, "rvv-0002": 80}
    assert service.season_lifecycle_report("2026-2027")["reconciliation"]["ok"] is True


def test_sealed_ice_time_override_reconciles_and_keeps_maintenance_open(tmp_path: Path) -> None:
    """A recorded per-tournament override must not block later sealed-season maintenance.

    Regression for the published-baseline replay engine: the override advances the
    canonical revision (and changes the rendered interval), but it is a
    decision-only overlay in the reconciliation projection. An unrecognized
    history event made every later real ``move``/``batch`` on the sealed season
    refuse under the whole-season reconciliation precondition.
    """

    root = tmp_path / "season"
    tournaments = [
        _tournament("rvv-1", "2026-10-11", "10:00", "A", "Alpha", "U12"),
        _tournament("rvv-2", "2026-11-15", "10:00", "B", "Beta", "U12"),
    ]
    _write_canonical(root, tournaments)
    service = CanonicalSeasonService(root=root)
    snapshot = service.load("2026-2027")
    projection = tournament_projection(snapshot.schedule["plan"], _problem(snapshot.schedule["plan"]))
    service.seal_published_season(
        season="2026-2027",
        publication_id="2026-09-21T0908",
        canonical_revision="rev-published",
        published_at="2026-09-21T09:14:53+00:00",
        published_projection=projection,
        publication_canonical_projection=projection,
        materializations=[],
        actor="tester",
    )

    service.set_ice_time_minutes(
        season="2026-2027",
        tournament_id="rvv-1",
        minutes=60,
        request_id="host-confirmation:demo",
        note="host confirmed a shorter real window",
        actor="tester",
    )
    assert service.season_lifecycle_report("2026-2027")["reconciliation"]["ok"] is True

    # The sealed-season reconciliation precondition on a real move must pass.
    service.move_tournament(
        season="2026-2027",
        tournament_id="rvv-1",
        start_time="13:00",
        actor="tester",
    )
    assert service.season_lifecycle_report("2026-2027")["reconciliation"]["ok"] is True

    # Releasing the override restores the age default and still reconciles.
    service.clear_ice_time_minutes(
        season="2026-2027", tournament_id="rvv-1", actor="tester", note="window reverted"
    )
    assert service.season_lifecycle_report("2026-2027")["reconciliation"]["ok"] is True


def test_sealed_roster_swap_reconciles_immediately(tmp_path: Path) -> None:
    root = tmp_path / "season"
    _write_canonical(root, _tournaments_abc())
    _seal_abc(root)
    service = CanonicalSeasonService(root=root)
    before = service.load("2026-2027").schedule["plan"]

    service.swap_participants(
        season="2026-2027",
        tournament_a_id="rvv-1",
        team_a_label="G-rvv-1",
        tournament_b_id="rvv-2",
        team_b_label="G-rvv-2",
        actor="tester",
    )

    latest = service.load("2026-2027")
    assert service.season_lifecycle_report("2026-2027")["reconciliation"]["ok"] is True
    assert latest.schedule["plan"]["tournaments"][2] == before["tournaments"][2]
    assert latest.decisions["history"][-1]["event"] == "participant_swap"


def test_sealed_scoped_batch_reconciles_immediately(tmp_path: Path) -> None:
    root = tmp_path / "season"
    _write_canonical(root, _tournaments_abc())
    _seal_abc(root)
    service = CanonicalSeasonService(root=root)
    service.batch_maintenance(
        season="2026-2027",
        operations=[{"op": "move", "tournament_id": "rvv-1", "start_time": "11:00"}],
        scope=["rvv-1"],
        request_id="request-1",
        actor="tester",
    )
    assert service.season_lifecycle_report("2026-2027")["reconciliation"]["ok"] is True
    assert service.load("2026-2027").decisions["history"][-1]["event"] == "batch_maintenance"


def test_normalize_placements_dry_run_is_diagnostic_on_sealed_season(tmp_path: Path) -> None:
    from tournament_scheduler.season_state import normalize_placements

    root = tmp_path / "season"
    _write_canonical(root, _tournaments_abc())
    _seal_abc(root)
    schedule, _decisions = normalize_placements(season="2026-2027", root=root, dry_run=True)
    assert schedule is not None


def test_reopen_planning_requires_confirmation_and_reason(tmp_path: Path) -> None:
    from tournament_scheduler.season_state import apply_candidate, reopen_planning

    root = tmp_path / "season"
    _write_canonical(root, _tournaments_abc())
    _seal_abc(root)

    with pytest.raises(Exception):
        reopen_planning(season="2026-2027", root=root, reason="", confirm_break_published_baseline=True)
    with pytest.raises(Exception):
        reopen_planning(season="2026-2027", root=root, reason="restructure", confirm_break_published_baseline=False)

    report = reopen_planning(
        season="2026-2027",
        root=root,
        reason="fundamental restructuring",
        confirm_break_published_baseline=True,
        actor="operator",
    )
    assert report["state"] == "promoted"
    snapshot = CanonicalSeasonStore(root).load("2026-2027")
    assert not is_published_sealed(snapshot.decisions)
    # The prior baseline remains auditable.
    assert snapshot.decisions["season_lifecycle"]["reopen_history"][-1]["reason"] == "fundamental restructuring"
    # Planning regeneration is possible again.
    apply_candidate(season="2026-2027", candidate={"tournaments": []}, root=root)


# ---------------------------------------------------------------------------
# Publication-boundary auto-seal
# ---------------------------------------------------------------------------


def _first_publication_export(
    tmp_path: Path,
    root: Path,
    *,
    drift: bool = False,
    projection: dict | None = None,
    canonical_revision: str | None = None,
    season: str = "2026-2027",
) -> Path:
    export_dir = tmp_path / "export" / "2026-09-28T0908"
    export_dir.mkdir(parents=True)
    exported_projection = projection if projection is not None else _tp({"tournaments": _tournaments_abc()})
    if drift:
        exported_projection = _tp(
            {"tournaments": [_tournament("rvv-1", "2026-10-11", "23:00", "A", "Alpha")]}
        )
    if canonical_revision is None:
        snapshot = CanonicalSeasonStore(root).load(season)
        canonical_revision = canonical_state_revision(snapshot.schedule, snapshot.decisions)
    write_draft_manifest(
        export_dir,
        export_id="2026-09-28T0908",
        generated_at="2026-09-28T09:08:01+00:00",
        export_fingerprint="fp",
        source_run_id="run",
        canonical_season=season,
        canonical_revision=canonical_revision,
        schedule_projection=exported_projection,
    )
    return export_dir


def test_record_publication_seal_seals_season(tmp_path: Path) -> None:
    root = tmp_path / "season"
    _write_canonical(root, _tournaments_abc())
    export_dir = _first_publication_export(tmp_path, root)

    report = record_publication_seal(export_dir, repo_dir=tmp_path)
    assert report is not None
    assert report["state"] == "published_sealed"
    snapshot = CanonicalSeasonStore(root).load("2026-2027")
    assert is_published_sealed(snapshot.decisions)


def test_publication_refuses_manifest_season_without_canonical_state(tmp_path: Path) -> None:
    export_dir = tmp_path / "export" / "2026-09-28T0908"
    export_dir.mkdir(parents=True)
    write_draft_manifest(
        export_dir,
        export_id="2026-09-28T0908",
        generated_at="2026-09-28T09:08:01+00:00",
        export_fingerprint="fp",
        source_run_id="run",
        canonical_season="2026-2027",
        canonical_revision="rev-missing",
        schedule_projection=_tp({"tournaments": _tournaments_abc()}),
    )

    with pytest.raises(RuntimeError, match="cannot be loaded"):
        assert_publication_allowed(export_dir, repo_dir=tmp_path)


def test_publication_refuses_missing_or_malformed_schedule_projection(tmp_path: Path) -> None:
    root = tmp_path / "season"
    _write_canonical(root, _tournaments_abc())
    snapshot = CanonicalSeasonStore(root).load("2026-2027")
    current_revision = canonical_state_revision(snapshot.schedule, snapshot.decisions)
    export_dir = tmp_path / "export" / "2026-09-28T0908"
    export_dir.mkdir(parents=True)
    write_draft_manifest(
        export_dir,
        export_id="2026-09-28T0908",
        generated_at="2026-09-28T09:08:01+00:00",
        export_fingerprint="fp",
        source_run_id="run",
        canonical_season="2026-2027",
        canonical_revision=current_revision,
        schedule_projection={},
    )
    with pytest.raises(RuntimeError, match="schedule_projection"):
        assert_publication_allowed(export_dir, repo_dir=tmp_path)

    write_draft_manifest(
        export_dir,
        export_id="2026-09-28T0908",
        generated_at="2026-09-28T09:08:01+00:00",
        export_fingerprint="fp",
        source_run_id="run",
        canonical_season="2026-2027",
        canonical_revision=current_revision,
        schedule_projection={"rvv-1": {"id": "rvv-1"}},
    )
    with pytest.raises(RuntimeError, match="malformed"):
        assert_publication_allowed(export_dir, repo_dir=tmp_path)


def test_publication_refuses_stale_canonical_revision_before_push(tmp_path: Path) -> None:
    root = tmp_path / "season"
    _write_canonical(root, _tournaments_abc())
    export_dir = _first_publication_export(tmp_path, root, canonical_revision="stale-revision")

    with pytest.raises(RuntimeError, match="current canonical revision"):
        assert_publication_allowed(export_dir, repo_dir=tmp_path)


def test_record_publication_seal_propagates_persistence_failure(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "season"
    _write_canonical(root, _tournaments_abc())
    export_dir = _first_publication_export(tmp_path, root)

    def fail_write(*_args, **_kwargs):
        raise RuntimeError("disk full")

    monkeypatch.setattr(CanonicalSeasonStore, "write", fail_write)
    with pytest.raises(RuntimeError, match="disk full"):
        record_publication_seal(export_dir, repo_dir=tmp_path)


def test_publication_refused_when_export_no_longer_matches_canonical(tmp_path: Path) -> None:
    root = tmp_path / "season"
    _write_canonical(root, _tournaments_abc())
    _seal_abc(root)
    export_dir = _first_publication_export(tmp_path, root, drift=True)
    with pytest.raises(RuntimeError):
        assert_publication_allowed(export_dir, repo_dir=tmp_path)


def test_publication_refuses_age_group_and_duration_drift(tmp_path: Path) -> None:
    root = tmp_path / "season"
    _write_canonical(root, _tournaments_abc())
    _seal_abc(root)
    altered = copy.deepcopy(_tp({"tournaments": _tournaments_abc()}))
    altered["rvv-1"]["age_group"] = "U11"
    altered["rvv-1"]["duration_minutes"] = altered["rvv-1"]["duration_minutes"] + 30

    export_dir = _first_publication_export(tmp_path, root, projection=altered)
    with pytest.raises(RuntimeError, match="no longer matches the current canonical schedule"):
        assert_publication_allowed(export_dir, repo_dir=tmp_path)


def test_publication_refuses_cancellation_and_guest_drift(tmp_path: Path) -> None:
    root = tmp_path / "season"
    _write_canonical(root, _tournaments_abc())
    _seal_abc(root)
    altered = copy.deepcopy(_tp({"tournaments": _tournaments_abc()}))
    altered["rvv-1"]["cancelled"] = True
    altered["rvv-1"]["cancellation_reason"] = "hall closed"
    altered["rvv-2"]["guest_slots"] = [{"id": "guest:rvv-2:1", "status": "open", "external_team": None}]

    export_dir = _first_publication_export(tmp_path, root, projection=altered)
    with pytest.raises(RuntimeError, match="no longer matches the current canonical schedule"):
        assert_publication_allowed(export_dir, repo_dir=tmp_path)


def test_second_publication_appends_history_without_rewriting_first(tmp_path: Path) -> None:
    root = tmp_path / "season"
    _write_canonical(root, _tournaments_abc())
    _seal_abc(root)
    export_dir = _first_publication_export(tmp_path, root)
    record_publication_seal(export_dir, repo_dir=tmp_path)

    snapshot = CanonicalSeasonStore(root).load("2026-2027")
    lifecycle = snapshot.decisions["season_lifecycle"]
    assert lifecycle["state"] == "published_sealed"
    assert len(lifecycle["publication_history"]) == 2
    # The historical Sep-21 baseline is still the first entry, unchanged.
    assert lifecycle["publication_history"][0]["publication_id"] == "2026-09-21T0908"
    assert lifecycle["published_baseline"]["publication_id"] == "2026-09-28T0908"


def test_full_run_refused_for_sealed_season(tmp_path: Path) -> None:
    from datetime import date

    from tournament_scheduler.cli.pipeline_orchestrator.new_run_guard import guard_sealed_season_run

    sealed_root = tmp_path / "season"
    _write_canonical(sealed_root, _tournaments_abc())
    _seal_abc(sealed_root)
    sealed_cfg = {"canonical_season": "2026-2027", "canonical_season_root": str(sealed_root)}
    assert guard_sealed_season_run(sealed_cfg, date(2026, 9, 1), date(2027, 4, 30)) is True

    open_root = tmp_path / "season-open"
    _write_canonical(open_root, _tournaments_abc())
    open_cfg = {"canonical_season": "2026-2027", "canonical_season_root": str(open_root)}
    assert guard_sealed_season_run(open_cfg, date(2026, 9, 1), date(2027, 4, 30)) is False


# ---------------------------------------------------------------------------
# Promote --force guard (integration through the real promotion path)
# ---------------------------------------------------------------------------


def _candidate() -> dict:
    teams = [
        {"club": "A", "label": "A1", "age_group": "U10"},
        {"club": "B", "label": "B1", "age_group": "U10"},
        {"club": "C", "label": "C1", "age_group": "U10"},
        {"club": "D", "label": "D1", "age_group": "U10"},
    ]
    return {
        "schema_version": 1,
        "start_date": "2026-09-01",
        "end_date": "2027-04-30",
        "tournaments": [
            {
                "id": "u10-a-20260912",
                "date": "2026-09-12",
                "arena": "Arena A",
                "age_group": "U10",
                "host_club": "A",
                "teams": teams,
                "games": [
                    {"home": "A1", "away": "B1", "parallel_slot": 0, "round_number": 1},
                    {"home": "C1", "away": "D1", "parallel_slot": 1, "round_number": 1},
                    {"home": "A1", "away": "C1", "parallel_slot": 0, "round_number": 2},
                    {"home": "B1", "away": "D1", "parallel_slot": 1, "round_number": 2},
                    {"home": "A1", "away": "D1", "parallel_slot": 0, "round_number": 3},
                    {"home": "B1", "away": "C1", "parallel_slot": 1, "round_number": 3},
                ],
                "start_time": "10:00",
            }
        ],
    }


def test_promote_force_cannot_replace_sealed_season(tmp_path: Path) -> None:
    from tournament_scheduler.season_state import promote_from_stage3, seal_published_season

    work_dir = tmp_path / ".pipeline"
    root = tmp_path / "season"
    state = PipelineState(work_dir)
    state.write_stage(StageName.PLANNING, {"plan": _candidate()}, status=StageStatus.DONE)
    write_reviewed_stage4_export(state)

    schedule, _decisions = promote_from_stage3(work_dir=work_dir, root=root, actor="tester")
    seal_published_season(
        season=schedule["season"],
        publication_id="2026-09-28T0908",
        canonical_revision=str(schedule.get("revision")),
        published_at="2026-09-28T09:14:53+00:00",
        published_projection=tournament_projection(schedule.get("plan") or {}, _problem(schedule.get("plan") or {})),
        root=root,
        actor="tester",
    )
    with pytest.raises(SeasonSealedError):
        promote_from_stage3(work_dir=work_dir, root=root, actor="tester", force=True)


# ---------------------------------------------------------------------------
# First-class sealed-season republish evidence
# ---------------------------------------------------------------------------


def _full_projection(root: Path) -> dict:
    snapshot = CanonicalSeasonStore(root).load("2026-2027")
    schedule = snapshot.schedule
    context = schedule.get("verification_context") if isinstance(schedule.get("verification_context"), dict) else {}
    return tournament_projection(schedule["plan"], context.get("problem"))


def _seal_full(
    root: Path,
    *,
    publication_id: str,
    canonical_revision: str,
    run_id: str,
    published_at: str = "2026-09-21T09:14:53+00:00",
    projection: dict | None = None,
) -> dict:
    from tournament_scheduler.pipeline.publication_evidence import build_publication_evidence
    from tournament_scheduler.published_baseline import projection_fingerprint

    resolved_projection = projection if projection is not None else _full_projection(root)
    evidence = build_publication_evidence(
        run_id=run_id,
        canonical_revision=canonical_revision,
        projection_fingerprint=projection_fingerprint(resolved_projection),
        bundle_fingerprint=f"bundle-{run_id}",
        pages_branch="gh-pages",
        pages_commit=f"commit-{run_id}",
        published_at=published_at,
    )
    return CanonicalSeasonService(root=root).seal_published_season(
        season="2026-2027",
        publication_id=publication_id,
        canonical_revision=canonical_revision,
        published_at=published_at,
        published_projection=resolved_projection,
        publication_canonical_projection=resolved_projection,
        actor="tester",
        publication_evidence=evidence,
    )


def test_replacement_seal_links_previous_and_records_exact_delta(tmp_path: Path) -> None:
    root = tmp_path / "season"
    _write_canonical(root, _tournaments_abc())
    _seal_full(root, publication_id="2026-09-21T0908", canonical_revision="rev-1", run_id="run-1")

    service = CanonicalSeasonService(root=root)
    service.move_tournament(season="2026-2027", tournament_id="rvv-1", start_time="11:00", actor="tester")

    report = _seal_full(
        root, publication_id="2026-09-28T0908", canonical_revision="rev-2", run_id="run-2"
    )

    assert report["publication_evidence"]["run_id"] == "run-2"
    assert report["previous_publication"]["publication_id"] == "2026-09-21T0908"
    assert report["previous_publication"]["publication_evidence"]["run_id"] == "run-1"
    summary = report["republish_delta"]["summary"]
    assert summary["changed"] == 1
    assert summary["unchanged"] == 2
    assert report["republish_delta"]["delta"]["placement_changes"][0]["tournament_id"] == "rvv-1"

    lifecycle = CanonicalSeasonStore(root).load("2026-2027").decisions["season_lifecycle"]
    # The replaced baseline is preserved byte-for-byte as history, never rewritten.
    assert [entry["publication_id"] for entry in lifecycle["publication_history"]] == [
        "2026-09-21T0908",
        "2026-09-28T0908",
    ]
    assert lifecycle["publication_history"][0].get("previous_publication") is None
    assert lifecycle["publication_history"][0]["tournaments"][0]["start_time"] == "10:00"


def test_replacement_seal_does_not_mutate_canonical_schedule(tmp_path: Path) -> None:
    root = tmp_path / "season"
    _write_canonical(root, _tournaments_abc())
    _seal_full(root, publication_id="2026-09-21T0908", canonical_revision="rev-1", run_id="run-1")
    before = CanonicalSeasonStore(root).load("2026-2027").schedule["plan"]

    _seal_full(root, publication_id="2026-09-28T0908", canonical_revision="rev-2", run_id="run-2")

    after = CanonicalSeasonStore(root).load("2026-2027").schedule["plan"]
    assert after == before


def test_replacement_seal_rejects_mismatched_evidence_fingerprint(tmp_path: Path) -> None:
    from tournament_scheduler.pipeline.publication_evidence import build_publication_evidence

    root = tmp_path / "season"
    _write_canonical(root, _tournaments_abc())
    projection = _full_projection(root)
    evidence = build_publication_evidence(
        run_id="run-1",
        canonical_revision="rev-1",
        projection_fingerprint="not-the-projection",
    )
    with pytest.raises(PublishedBaselineError, match="projection fingerprint"):
        CanonicalSeasonService(root=root).seal_published_season(
            season="2026-2027",
            publication_id="2026-09-21T0908",
            canonical_revision="rev-1",
            published_at="2026-09-21T09:14:53+00:00",
            published_projection=projection,
            publication_canonical_projection=projection,
            publication_evidence=evidence,
        )


def test_record_publication_seal_writes_retained_before_after_evidence(tmp_path: Path) -> None:
    from tournament_scheduler.pipeline.export_lifecycle import promote_export_manifest
    from tournament_scheduler.pipeline.publication_evidence import read_publication_evidence

    root = tmp_path / "season"
    _write_canonical(root, _tournaments_abc())
    snapshot = CanonicalSeasonStore(root).load("2026-2027")
    revision = canonical_state_revision(snapshot.schedule, snapshot.decisions)
    export_dir = tmp_path / "export" / "2026-09-28T0908"
    export_dir.mkdir(parents=True)
    write_draft_manifest(
        export_dir,
        export_id="2026-09-28T0908",
        generated_at="2026-09-28T09:08:01+00:00",
        export_fingerprint="fp",
        source_run_id="run",
        canonical_season="2026-2027",
        canonical_revision=revision,
        schedule_projection=_tp({"tournaments": _tournaments_abc()}),
    )
    promote_export_manifest(
        export_dir,
        expected_export_fingerprint="fp",
        source_run_id="run",
        pages_run_id="run-1",
        pages_bundle_fingerprint="bundle-1",
        pages_commit="commit-1",
        pages_branch="gh-pages",
    )

    report = record_publication_seal(export_dir, repo_dir=tmp_path)
    assert report["publication_evidence"]["run_id"] == "run-1"
    assert report["publication_evidence"]["bundle_fingerprint"] == "bundle-1"
    assert report["evidence_files"]["json"]
    record = read_publication_evidence(tmp_path / "season", "2026-2027", "2026-09-28T0908")
    assert record is not None
    assert record["publication_evidence"]["run_id"] == "run-1"


def _canonical_export_for_current_state(tmp_path: Path, root: Path, publication_id: str):
    snapshot = CanonicalSeasonStore(root).load("2026-2027")
    revision = canonical_state_revision(snapshot.schedule, snapshot.decisions)
    export_dir = tmp_path / "export" / publication_id
    export_dir.mkdir(parents=True)
    write_draft_manifest(
        export_dir,
        export_id=publication_id,
        generated_at="2026-09-28T09:08:01+00:00",
        export_fingerprint="fp",
        source_run_id="run",
        canonical_season="2026-2027",
        canonical_revision=revision,
        schedule_projection=_tp({"tournaments": _tournaments_abc()}),
    )
    return export_dir


def test_publication_guard_refuses_when_previous_bundle_has_no_immutable_reference(
    tmp_path: Path,
) -> None:
    root = tmp_path / "season"
    _write_canonical(root, _tournaments_abc())
    _seal_abc(root)  # legacy seal: no publication_evidence / Pages reference
    export_dir = _canonical_export_for_current_state(tmp_path, root, "2026-09-28T0908")

    with pytest.raises(RuntimeError, match="has no immutable run id"):
        assert_publication_allowed(export_dir, repo_dir=tmp_path)


def _init_local_repo(path: Path) -> None:
    import subprocess

    def git(*args: str) -> None:
        subprocess.run(["git", *args], cwd=path, check=True, capture_output=True, text=True)

    path.mkdir(parents=True, exist_ok=True)
    git("init", "-b", "main")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "Test")
    (path / "README.md").write_text("x\n", encoding="utf-8")
    git("add", "README.md")
    git("commit", "-m", "init")


def test_publication_guard_refuses_when_previous_run_snapshot_is_missing(
    tmp_path: Path,
) -> None:
    from tournament_scheduler.pipeline import pages_publish

    local = tmp_path / "local"
    _init_local_repo(local)
    root = local / "season"
    _write_canonical(root, _tournaments_abc())

    # A replaceable baseline whose immutable reference points at a run that was
    # never actually published: the version being replaced would be unrecoverable.
    _seal_full(
        root,
        publication_id="2026-09-21T0908",
        canonical_revision="rev-1",
        run_id="never-published-run",
    )
    export_dir = _canonical_export_for_current_state(tmp_path, root, "2026-09-28T0908")

    # A published branch already exists (an unrelated run), but the run being
    # replaced has no immutable snapshot on it.
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "season_plan.html").write_text("<h1>plan</h1>", encoding="utf-8")
    first = pages_publish.publish(
        export_dir=str(bundle), run_id="unrelated-run", repo_dir=str(local), push=False
    )
    assert first.status == "ok"

    with pytest.raises(RuntimeError, match="is not verifiably retained"):
        assert_publication_allowed(export_dir, repo_dir=local)

    # Once the previous run's immutable snapshot actually exists with the
    # recorded bundle identity, the guard passes.
    result = pages_publish.publish(
        export_dir=str(bundle),
        run_id="never-published-run",
        repo_dir=str(local),
        push=False,
        bundle_fingerprint="bundle-never-published-run",
    )
    assert result.status == "ok"
    report = assert_publication_allowed(export_dir, repo_dir=local)
    assert report["previous_publication"]["run_snapshot_retained"] == "true"


@pytest.mark.parametrize("break_kind", ["missing_meta", "fingerprint_mismatch"])
def test_publication_guard_blocks_on_unverifiable_previous_snapshot(
    tmp_path: Path, break_kind: str
) -> None:
    from tournament_scheduler.pipeline import pages_publish

    local = tmp_path / "local"
    _init_local_repo(local)
    root = local / "season"
    _write_canonical(root, _tournaments_abc())
    _seal_full(
        root,
        publication_id="2026-09-21T0908",
        canonical_revision="rev-1",
        run_id="never-published-run",
    )
    export_dir = _canonical_export_for_current_state(tmp_path, root, "2026-09-28T0908")

    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "season_plan.html").write_text("<h1>plan</h1>", encoding="utf-8")
    if break_kind == "missing_meta":
        # Published without a bundle fingerprint: no _meta.json identity to verify.
        assert pages_publish.publish(
            export_dir=str(bundle), run_id="never-published-run", repo_dir=str(local), push=False
        ).status == "ok"
    else:
        assert pages_publish.publish(
            export_dir=str(bundle),
            run_id="never-published-run",
            repo_dir=str(local),
            push=False,
            bundle_fingerprint="different-bundle",
        ).status == "ok"

    with pytest.raises(RuntimeError, match="is not verifiably retained"):
        assert_publication_allowed(export_dir, repo_dir=local)


def test_publication_evidence_report_shows_active_revision_and_delta(tmp_path: Path) -> None:
    from tournament_scheduler.season_state import publication_evidence_report

    root = tmp_path / "season"
    _write_canonical(root, _tournaments_abc())
    _seal_full(root, publication_id="2026-09-21T0908", canonical_revision="rev-1", run_id="run-1")
    CanonicalSeasonService(root=root).move_tournament(
        season="2026-2027", tournament_id="rvv-1", start_time="11:00", actor="tester"
    )

    report = publication_evidence_report("2026-2027", root=root)
    assert report["active_publication"]["publication_id"] == "2026-09-21T0908"
    assert report["active_publication"]["publication_evidence"]["run_id"] == "run-1"
    assert report["published_to_canonical_delta"]["summary"]["changed"] == 1


def test_publication_guard_refuses_sealed_state_without_a_baseline(tmp_path: Path) -> None:
    root = tmp_path / "season"
    _write_canonical(root, _tournaments_abc())
    store = CanonicalSeasonStore(root)
    snapshot = store.load("2026-2027")
    decisions = copy.deepcopy(snapshot.decisions)
    decisions["season_lifecycle"] = {"state": "published_sealed"}
    store.write(
        CanonicalSeasonSnapshot(season="2026-2027", schedule=snapshot.schedule, decisions=decisions)
    )
    export_dir = _canonical_export_for_current_state(tmp_path, root, "2026-09-28T0908")

    with pytest.raises(RuntimeError, match="has no published baseline"):
        assert_publication_allowed(export_dir, repo_dir=tmp_path)


def test_republish_seal_invokes_no_planning_or_scrape_path(tmp_path: Path, monkeypatch) -> None:
    import tournament_scheduler.pipeline.stage4_export as stage4_export
    import tournament_scheduler.season_state as season_state

    def _boom(*_args, **_kwargs):
        raise AssertionError("republish must not invoke a planning/scrape path")

    monkeypatch.setattr(season_state, "promote_from_stage3", _boom)
    monkeypatch.setattr(stage4_export, "run", _boom)

    root = tmp_path / "season"
    _write_canonical(root, _tournaments_abc())
    export_dir = _canonical_export_for_current_state(tmp_path, root, "2026-09-28T0908")

    report = record_publication_seal(export_dir, repo_dir=tmp_path)
    assert report["state"] == "published_sealed"


def test_replacement_seal_labels_missing_previous_decision_snapshot(tmp_path: Path) -> None:
    """A legacy first publication without a decision snapshot must say so."""

    root = tmp_path / "season"
    _write_canonical(root, _tournaments_abc())
    projection = _full_projection(root)
    CanonicalSeasonService(root=root).seal_published_season(
        season="2026-2027",
        publication_id="2026-09-21T0908",
        canonical_revision="rev-1",
        published_at="2026-09-21T09:14:53+00:00",
        published_projection=projection,
        publication_canonical_projection=projection,
        actor="tester",
        # No publication_evidence -> no decision snapshot on the legacy baseline.
    )

    report = _seal_full(
        root, publication_id="2026-09-28T0908", canonical_revision="rev-2", run_id="run-2"
    )
    changes = report["republish_decision_changes"]
    assert changes["available"] is False
    assert changes["reason"] == "no_previous_decision_snapshot"
    assert changes["changed"] is None


def test_replacement_seal_rejects_conflicting_retained_evidence(tmp_path: Path) -> None:
    from tournament_scheduler.pipeline.publication_evidence import (
        PublicationEvidenceError,
        write_publication_evidence,
    )

    root = tmp_path / "season"
    _write_canonical(root, _tournaments_abc())
    _seal_full(root, publication_id="2026-09-21T0908", canonical_revision="rev-1", run_id="run-1")
    report = _seal_full(
        root, publication_id="2026-09-28T0908", canonical_revision="rev-2", run_id="run-2"
    )
    # A retry for the same publication and identical content is idempotent.
    retry = write_publication_evidence(
        season_root=root,
        season="2026-2027",
        publication_id="2026-09-28T0908",
        evidence=report["publication_evidence"],
        previous_publication=report["previous_publication"],
        republish_delta=report["republish_delta"],
        canonical_revision="rev-2",
        decision_changes=report["republish_decision_changes"],
    )
    retry_again = write_publication_evidence(
        season_root=root,
        season="2026-2027",
        publication_id="2026-09-28T0908",
        evidence=report["publication_evidence"],
        previous_publication=report["previous_publication"],
        republish_delta=report["republish_delta"],
        canonical_revision="rev-2",
        decision_changes=report["republish_decision_changes"],
    )
    assert retry == retry_again
    # A different canonical revision for the same publication id fails closed.
    with pytest.raises(PublicationEvidenceError, match="conflicting"):
        write_publication_evidence(
            season_root=root,
            season="2026-2027",
            publication_id="2026-09-28T0908",
            evidence=report["publication_evidence"],
            previous_publication=report["previous_publication"],
            republish_delta=report["republish_delta"],
            canonical_revision="rev-3",
            decision_changes=report["republish_decision_changes"],
        )


def test_publication_evidence_write_failure_is_recoverable_on_retry(
    tmp_path: Path, monkeypatch
) -> None:
    """A failed evidence write must not duplicate history and must recover on retry."""

    import tournament_scheduler.pipeline.publication_lifecycle as pub_lifecycle
    from tournament_scheduler.pipeline.export_lifecycle import promote_export_manifest
    from tournament_scheduler.pipeline.publication_evidence import read_publication_evidence

    root = tmp_path / "season"
    _write_canonical(root, _tournaments_abc())
    export_dir = _canonical_export_for_current_state(tmp_path, root, "2026-09-28T0908")
    promote_export_manifest(
        export_dir,
        expected_export_fingerprint="fp",
        source_run_id="run",
        pages_run_id="run-1",
        pages_bundle_fingerprint="bundle-1",
        pages_commit="commit-1",
        pages_branch="gh-pages",
    )

    calls = {"count": 0}
    real_write = pub_lifecycle.write_publication_evidence

    def flaky_write(**kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            raise OSError("disk full")
        return real_write(**kwargs)

    monkeypatch.setattr(pub_lifecycle, "write_publication_evidence", flaky_write)

    with pytest.raises(OSError):
        record_publication_seal(export_dir, repo_dir=tmp_path)

    lifecycle = CanonicalSeasonStore(root).load("2026-2027").decisions["season_lifecycle"]
    assert len(lifecycle["publication_history"]) == 1
    assert read_publication_evidence(root, "2026-2027", "2026-09-28T0908") is None

    report = record_publication_seal(export_dir, repo_dir=tmp_path)
    lifecycle = CanonicalSeasonStore(root).load("2026-2027").decisions["season_lifecycle"]
    assert len(lifecycle["publication_history"]) == 1
    assert read_publication_evidence(root, "2026-2027", "2026-09-28T0908") is not None
    assert report["evidence_files"]["json"]


def test_already_sealed_retry_still_refuses_actual_schedule_drift(tmp_path: Path) -> None:
    """The idempotent-retry revision relaxation must not hide real drift."""

    root = tmp_path / "season"
    _write_canonical(root, _tournaments_abc())
    snapshot = CanonicalSeasonStore(root).load("2026-2027")
    published_revision = canonical_state_revision(snapshot.schedule, snapshot.decisions)
    _seal_full(
        root,
        publication_id="2026-09-28T0908",
        canonical_revision=published_revision,
        run_id="run-1",
    )

    export_dir = tmp_path / "export" / "2026-09-28T0908"
    export_dir.mkdir(parents=True)
    altered = _tp({"tournaments": _tournaments_abc()})
    altered["rvv-1"]["start_time"] = "23:00"
    write_draft_manifest(
        export_dir,
        export_id="2026-09-28T0908",
        generated_at="2026-09-28T09:08:01+00:00",
        export_fingerprint="fp",
        source_run_id="run",
        canonical_season="2026-2027",
        canonical_revision=published_revision,
        schedule_projection=altered,
    )
    with pytest.raises(RuntimeError, match="no longer matches the current canonical schedule"):
        assert_publication_allowed(export_dir, repo_dir=tmp_path)


# ---------------------------------------------------------------------------
# Active per-tournament ice-time overrides at the publication boundary
# ---------------------------------------------------------------------------


def _effective_projection(root: Path) -> dict:
    snapshot = CanonicalSeasonStore(root).load("2026-2027")
    return effective_projection_from_canonical_schedule(snapshot.schedule, snapshot.decisions)


def _write_manifest_with_projection(
    tmp_path: Path,
    root: Path,
    publication_id: str,
    projection: dict,
    *,
    revision: str | None = None,
) -> Path:
    snapshot = CanonicalSeasonStore(root).load("2026-2027")
    if revision is None:
        revision = canonical_state_revision(snapshot.schedule, snapshot.decisions)
    export_dir = tmp_path / "export" / publication_id
    export_dir.mkdir(parents=True)
    write_draft_manifest(
        export_dir,
        export_id=publication_id,
        generated_at="2026-09-28T09:08:01+00:00",
        export_fingerprint="fp",
        source_run_id="run",
        canonical_season="2026-2027",
        canonical_revision=revision,
        schedule_projection=projection,
    )
    return export_dir


def _override_season(tmp_path: Path, tournaments: list[dict], minutes: int) -> Path:
    root = tmp_path / "season"
    _write_canonical(root, tournaments)
    CanonicalSeasonService(root=root).set_ice_time_minutes(
        season="2026-2027",
        tournament_id=tournaments[0]["id"],
        minutes=minutes,
        request_id=f"host-confirmation:{tournaments[0]['id']}",
        note="host confirmed the real occupied window",
        actor="tester",
    )
    return root


def test_publication_freshness_accepts_active_ice_time_override(tmp_path: Path) -> None:
    """The freshness/seal guard must compare against the effective projection.

    A host-confirmed override changes the published occupied interval without
    editing any plan placement field. The export manifest carries the effective
    duration, so the guard must not compare it to the schedule-only projection
    and report the accepted override as stale export drift.
    """

    from tournament_scheduler.pipeline.publication_lifecycle import _publication_context

    root = _override_season(
        tmp_path, [_tournament("rvv-1", "2026-10-11", "10:00", "A", "Alpha", "U12")], 60
    )
    snapshot = CanonicalSeasonStore(root).load("2026-2027")
    schedule_only = projection_from_canonical_schedule(snapshot.schedule)
    effective = _effective_projection(root)

    # Precondition: the two projections genuinely differ for this revision.
    assert schedule_only["rvv-1"]["duration_minutes"] == 120
    assert effective["rvv-1"]["duration_minutes"] == 60
    assert effective["rvv-1"]["end_time"] == "11:00"

    export_dir = _write_manifest_with_projection(tmp_path, root, "2026-09-28T0908", effective)
    context = _publication_context(export_dir, repo_dir=tmp_path)
    assert context is not None
    assert context["published_projection"]["rvv-1"]["duration_minutes"] == 60


def test_publication_freshness_refuses_unrecorded_duration_change(tmp_path: Path) -> None:
    """An altered duration with the current revision is still refused."""

    from tournament_scheduler.pipeline.publication_lifecycle import _publication_context

    root = _override_season(
        tmp_path, [_tournament("rvv-1", "2026-10-11", "10:00", "A", "Alpha", "U12")], 60
    )
    tampered = copy.deepcopy(_effective_projection(root))
    tampered["rvv-1"]["duration_minutes"] = 90
    tampered["rvv-1"]["end_time"] = "11:30"

    export_dir = _write_manifest_with_projection(tmp_path, root, "2026-09-28T0908", tampered)
    with pytest.raises(RuntimeError, match="no longer matches the current canonical schedule"):
        _publication_context(export_dir, repo_dir=tmp_path)


def test_publication_freshness_refuses_stale_revision_with_override(tmp_path: Path) -> None:
    from tournament_scheduler.pipeline.publication_lifecycle import _publication_context

    root = _override_season(
        tmp_path, [_tournament("rvv-1", "2026-10-11", "10:00", "A", "Alpha", "U12")], 60
    )
    export_dir = _write_manifest_with_projection(
        tmp_path, root, "2026-09-28T0908", _effective_projection(root), revision="stale-revision"
    )
    with pytest.raises(RuntimeError, match="current canonical revision"):
        _publication_context(export_dir, repo_dir=tmp_path)


def test_reconciliation_identifies_active_override_with_provenance(tmp_path: Path) -> None:
    root = tmp_path / "season"
    _write_canonical(root, [_tournament("rvv-1", "2026-10-11", "10:00", "A", "Alpha", "U12")])
    _seal_full(root, publication_id="2026-09-21T0908", canonical_revision="rev-1", run_id="run-1")
    service = CanonicalSeasonService(root=root)
    service.set_ice_time_minutes(
        season="2026-2027",
        tournament_id="rvv-1",
        minutes=60,
        request_id="host:rvv-1",
        note="host confirmed",
        actor="tester",
    )

    report = service.verify_sealed_reconciliation("2026-2027")
    assert report["ok"] is True
    changes = report["decision_backed_occupancy_changes"]
    assert [entry["tournament_id"] for entry in changes] == ["rvv-1"]
    assert changes[0]["before_minutes"] == 120
    assert changes[0]["after_minutes"] == 60
    assert report["unexplained_delta"]["changed"] is False


def test_reconciliation_fails_closed_on_unrecorded_override_provenance() -> None:
    """An active override with no recorded set/clear event is not trusted."""

    plan = {"tournaments": [_tournament("rvv-1", "2026-10-11", "10:00", "A", "Alpha")]}
    configured = _problem(plan)
    schedule_only = tournament_projection(plan, configured)
    effective = tournament_projection(
        plan, {**configured, "ice_time_minutes_overrides": {"rvv-1": 60}}
    )

    report = reconcile_published_baseline(
        published_projection=schedule_only,
        current_projection=effective,
        history=[],
        attested_additions={},
        occupancy_overrides={"rvv-1": 60},
    )
    assert report["ok"] is False
    assert "do not match the recorded" in report["unexplained_delta"]["replay_error"]


def test_reconciliation_fails_closed_on_unknown_or_nonpositive_override() -> None:
    plan = {"tournaments": [_tournament("rvv-1", "2026-10-11", "10:00", "A", "Alpha")]}
    configured = _problem(plan)
    schedule_only = tournament_projection(plan, configured)

    unknown = reconcile_published_baseline(
        published_projection=schedule_only,
        current_projection=schedule_only,
        history=[
            {
                "event": "set_ice_time_minutes",
                "tournament_id": "rvv-999",
                "details": {"minutes": 60},
            }
        ],
        attested_additions={},
        occupancy_overrides={"rvv-999": 60},
    )
    assert unknown["ok"] is False
    assert "unknown tournament" in unknown["unexplained_delta"]["replay_error"]

    nonpositive = reconcile_published_baseline(
        published_projection=schedule_only,
        current_projection=schedule_only,
        history=[
            {
                "event": "set_ice_time_minutes",
                "tournament_id": "rvv-1",
                "details": {"minutes": 0},
            }
        ],
        attested_additions={},
        occupancy_overrides={"rvv-1": 0},
    )
    assert nonpositive["ok"] is False
    assert "positive" in nonpositive["unexplained_delta"]["replay_error"]


def test_clearing_override_restores_default_and_reconciles(tmp_path: Path) -> None:
    root = tmp_path / "season"
    _write_canonical(root, [_tournament("rvv-1", "2026-10-11", "10:00", "A", "Alpha", "U12")])
    _seal_full(root, publication_id="2026-09-21T0908", canonical_revision="rev-1", run_id="run-1")
    service = CanonicalSeasonService(root=root)
    service.set_ice_time_minutes(
        season="2026-2027",
        tournament_id="rvv-1",
        minutes=60,
        request_id="host:rvv-1",
        note="host confirmed",
        actor="tester",
    )
    assert service.verify_sealed_reconciliation("2026-2027")["ok"] is True

    service.clear_ice_time_minutes(
        season="2026-2027", tournament_id="rvv-1", actor="tester", note="window reverted"
    )
    report = service.verify_sealed_reconciliation("2026-2027")
    assert report["ok"] is True
    assert report["decision_backed_occupancy_changes"] == []
    assert _effective_projection(root)["rvv-1"]["duration_minutes"] == 120


def test_replacement_seal_records_real_override_interval_and_retains_previous(
    tmp_path: Path,
) -> None:
    root = tmp_path / "season"
    _write_canonical(root, [_tournament("rvv-1", "2026-10-11", "10:00", "A", "Alpha", "U12")])
    _seal_full(root, publication_id="2026-09-21T0908", canonical_revision="rev-1", run_id="run-1")
    service = CanonicalSeasonService(root=root)
    service.set_ice_time_minutes(
        season="2026-2027",
        tournament_id="rvv-1",
        minutes=60,
        request_id="host:rvv-1",
        note="host confirmed",
        actor="tester",
    )

    effective = _effective_projection(root)
    snapshot = service.load("2026-2027")
    revision = canonical_state_revision(snapshot.schedule, snapshot.decisions)
    report = _seal_full(
        root,
        publication_id="2026-09-28T0908",
        canonical_revision=revision,
        run_id="run-2",
        projection=effective,
    )

    delta = report["republish_delta"]
    assert delta["summary"]["changed"] == 1
    assert delta["delta"]["field_changes"][0]["fields"]["duration_minutes"] == {
        "before": 120,
        "after": 60,
    }
    assert delta["delta"]["field_changes"][0]["fields"]["end_time"] == {
        "before": "12:00",
        "after": "11:00",
    }

    lifecycle = CanonicalSeasonStore(root).load("2026-2027").decisions["season_lifecycle"]
    # The historical 2026-09-21 snapshot is preserved byte-for-byte.
    assert lifecycle["publication_history"][0]["publication_id"] == "2026-09-21T0908"
    historical = lifecycle["publication_history"][0]["tournaments"][0]
    assert historical["duration_minutes"] == 120
    # The replacement baseline records the actually published effective interval.
    replacement = lifecycle["published_baseline"]["tournaments"][0]
    assert replacement["duration_minutes"] == 60


def test_publication_guard_allows_republish_with_active_override(tmp_path: Path) -> None:
    """End-to-end publication guard accepts an already-sealed override."""

    from tournament_scheduler.pipeline import pages_publish

    local = tmp_path / "local"
    _init_local_repo(local)
    root = local / "season"
    _write_canonical(root, [_tournament("rvv-1", "2026-10-11", "10:00", "A", "Alpha", "U12")])
    _seal_full(root, publication_id="2026-09-21T0908", canonical_revision="rev-1", run_id="run-1")
    service = CanonicalSeasonService(root=root)
    service.set_ice_time_minutes(
        season="2026-2027",
        tournament_id="rvv-1",
        minutes=60,
        request_id="host:rvv-1",
        note="host confirmed",
        actor="tester",
    )

    export_dir = _write_manifest_with_projection(
        tmp_path, root, "2026-09-28T0908", _effective_projection(root)
    )

    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "season_plan.html").write_text("<h1>plan</h1>", encoding="utf-8")
    assert (
        pages_publish.publish(
            export_dir=str(bundle),
            run_id="run-1",
            repo_dir=str(local),
            push=False,
            bundle_fingerprint="bundle-run-1",
        ).status
        == "ok"
    )

    report = assert_publication_allowed(export_dir, repo_dir=local)
    assert report is not None
    assert report["ok"] is True
    assert len(report["decision_backed_occupancy_changes"]) == 1
    assert report["previous_publication"]["run_snapshot_retained"] == "true"


_PRODUCTION_OVERRIDES: dict[str, tuple[int, int]] = {
    # tournament id -> (default age-group minutes, active override minutes)
    "rvv-0003": (100, 60),
    "rvv-0004": (100, 60),
    "rvv-0104": (155, 120),
    "rvv-0191": (155, 120),
    "rvv-0187": (100, 80),
    "rvv-0134": (155, 120),
    "rvv-0127": (140, 120),
    "rvv-0179": (155, 120),
    "rvv-0181": (140, 120),
    "rvv-0070": (140, 120),
    "rvv-0011": (155, 170),
    "rvv-0028": (100, 110),
    "rvv-0176": (100, 120),
    "rvv-0162": (100, 120),
    "rvv-0023": (100, 80),
    "rvv-0075": (100, 80),
    "rvv-0088": (100, 80),
    "rvv-0063": (100, 80),
}


def test_eighteen_active_overrides_pass_reconciliation_and_freshness(tmp_path: Path) -> None:
    """Production-shaped 18-tournament override set: longer and shorter intervals."""

    from tournament_scheduler.pipeline.publication_lifecycle import _publication_context

    default_to_age = {100: "U12", 140: "U13", 155: "U14"}
    tournaments = [
        _tournament(tid, "2026-10-11", "10:00", f"Arena {index}", f"Host {index}", default_to_age[default])
        for index, (tid, (default, _override)) in enumerate(_PRODUCTION_OVERRIDES.items())
    ]
    problem = {"ice_time_minutes": {age: default for default, age in default_to_age.items()}}

    root = tmp_path / "season"
    _write_canonical(root, tournaments, problem=problem)
    _seal_full(root, publication_id="2026-09-21T0908", canonical_revision="rev-1", run_id="run-1")
    service = CanonicalSeasonService(root=root)
    for tid, (_default, override) in _PRODUCTION_OVERRIDES.items():
        service.set_ice_time_minutes(
            season="2026-2027",
            tournament_id=tid,
            minutes=override,
            request_id=f"host:{tid}",
            note="host confirmed the real occupied window",
            actor="tester",
        )

    report = service.verify_sealed_reconciliation("2026-2027")
    assert report["ok"] is True
    assert len(report["decision_backed_occupancy_changes"]) == 18

    effective = _effective_projection(root)
    for tid, (_default, override) in _PRODUCTION_OVERRIDES.items():
        assert effective[tid]["duration_minutes"] == override

    export_dir = _write_manifest_with_projection(tmp_path, root, "2026-09-28T0908", effective)
    context = _publication_context(export_dir, repo_dir=tmp_path)
    assert context is not None
    assert len(context["published_projection"]) == 18
