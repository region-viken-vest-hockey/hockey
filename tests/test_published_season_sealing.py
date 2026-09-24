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
    is_published_sealed,
)
from tournament_scheduler.published_mutation_history import (
    reconcile_published_baseline,
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
        "verification_context": {"problem": _problem(plan)},
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
