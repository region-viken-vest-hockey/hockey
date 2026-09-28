"""Tournament-scoped publication eligibility (issue #541).

Publication eligibility belongs to each active tournament, not to season-wide
planning completeness. These tests pin the deterministic publication-scope
result: an incremental republish whose exact last-publication delta is
eligible must not be blocked by unrelated full-season planning debt, while an
unaccepted interval, an unexplained removal, a projection inconsistency or an
unauthorized hosting-responsibility transfer must still be held or blocked.
"""

from __future__ import annotations

from pathlib import Path

from tournament_scheduler.canonical_state import schedule_fingerprint
from tournament_scheduler.calendar_bookings import new_manual_assertion_record
from tournament_scheduler.infrastructure.canonical_season_store import (
    DECISIONS_SCHEMA_VERSION,
    SEASON_STATE_SCHEMA_VERSION,
    CanonicalSeasonSnapshot,
    CanonicalSeasonStore,
)
from tournament_scheduler.pipeline.publication_scope import (
    STATUS_BLOCKED,
    STATUS_ELIGIBLE,
    STATUS_HELD,
    STATUS_NOT_CHECKABLE,
    evaluate_publication_scope,
)
from tournament_scheduler.published_baseline import build_baseline_record

_SEASON = "2026-2027"


def _team(club: str, label: str) -> dict:
    return {"club": club, "label": label, "age_group": "U10"}


def _tournament(
    tournament_id: str,
    *,
    date: str,
    start_time: str,
    host_club: str,
    arena: str = "Hall",
    cancelled: bool = False,
) -> dict:
    return {
        "id": tournament_id,
        "date": date,
        "start_time": start_time,
        "arena": arena,
        "host_club": host_club,
        "age_group": "U10",
        "cancelled": cancelled,
        "teams": [_team(host_club, f"{host_club}-1"), _team("Guest", f"G-{tournament_id}")],
        "games": [],
    }


def _problem(plan: dict) -> dict:
    teams: list[dict] = []
    seen: set[tuple] = set()
    for tournament in plan.get("tournaments") or []:
        for team in tournament.get("teams") or []:
            key = (team.get("club"), team.get("label"), team.get("age_group"))
            if key in seen:
                continue
            seen.add(key)
            teams.append(dict(team))
    return {"teams": teams, "ice_time_minutes": {"U10": 120}}


def _write_season(
    root: Path,
    *,
    plan: dict,
    published_plan: dict,
    decisions_extra: dict | None = None,
) -> dict:
    problem = _problem(plan)
    schedule = {
        "schema_version": SEASON_STATE_SCHEMA_VERSION,
        "season": _SEASON,
        "created_at": "2026-09-01T00:00:00+00:00",
        "updated_at": "2026-09-01T00:00:00+00:00",
        "revision": schedule_fingerprint(plan),
        "fingerprint": schedule_fingerprint(plan),
        "plan_schema_version": 1,
        "plan": plan,
        "verification_context": {"problem": problem},
    }
    baseline_projection = _projection(published_plan)
    baseline = build_baseline_record(
        season=_SEASON,
        publication_id="pub-1",
        canonical_revision="rev-published",
        published_at="2026-09-01T00:00:00+00:00",
        projection=baseline_projection,
    )
    decisions = {
        "schema_version": DECISIONS_SCHEMA_VERSION,
        "season": _SEASON,
        "created_at": "2026-09-01T00:00:00+00:00",
        "updated_at": "2026-09-01T00:00:00+00:00",
        "schedule_fingerprint": schedule["fingerprint"],
        "actor": "tester",
        "decisions": {
            str(t["id"]): {
                "status": "pending_review",
                "placement_locked": False,
                "participants_locked": False,
                "approved_fingerprint": None,
            }
            for t in plan.get("tournaments") or []
        },
        "history": [],
        "season_lifecycle": {"state": "published_sealed", "published_baseline": baseline},
    }
    decisions.update(decisions_extra or {})
    CanonicalSeasonStore(root).write(
        CanonicalSeasonSnapshot(season=_SEASON, schedule=schedule, decisions=decisions)
    )
    return problem


def _projection(plan: dict) -> dict:
    from tournament_scheduler.pipeline.export_projection_guard import tournament_projection

    return tournament_projection(plan, _problem(plan))


def _booked_assertion(*, tournament: dict, problem: dict) -> dict:
    return new_manual_assertion_record(
        tournament=tournament,
        booking_status="booked",
        problem=problem,
        actor="tester",
        note="club confirmed by email",
        reference="email-2026-09-20",
        source_scope="tournament",
        stated_interval=None,
        asserted_at="2026-09-20T00:00:00+00:00",
        source_revision="rev-published",
    )


def _scope(root: Path, *, reviewed_plan: dict, problem: dict, decisions_extra: dict | None = None):
    decisions = CanonicalSeasonStore(root).load(_SEASON).decisions
    return evaluate_publication_scope(
        reviewed_plan=reviewed_plan,
        problem=problem,
        decisions=decisions,
        season=_SEASON,
        season_root=root,
        export_fingerprint="fp-current",
        canonical_revision="rev-current",
        full_season_reasons=[{"code": "unresolved_hosting", "count": 9}],
    )


class TestNoPublishedBaseline:
    def test_missing_baseline_fails_closed_to_audit_gate(self, tmp_path):
        plan = {"start_date": "2026-09-01", "end_date": "2027-04-30", "tournaments": [_tournament("t1", date="2026-10-11", start_time="10:00", host_club="A")]}
        problem = _write_season(tmp_path, plan=plan, published_plan=plan)
        decisions = CanonicalSeasonStore(tmp_path).load(_SEASON).decisions
        decisions = {k: v for k, v in decisions.items() if k != "season_lifecycle"}
        result = evaluate_publication_scope(
            reviewed_plan=plan,
            problem=problem,
            decisions=decisions,
            season=_SEASON,
            season_root=tmp_path,
        )
        assert result["status"] == STATUS_NOT_CHECKABLE
        assert any(reason["code"] == "no_published_baseline" for reason in result["reasons"])


class TestEligibleIncrementalCorrection:
    def test_changed_interval_with_accepted_booking_is_eligible(self, tmp_path):
        published = {"start_date": "2026-09-01", "end_date": "2027-04-30", "tournaments": [_tournament("t1", date="2026-10-11", start_time="10:00", host_club="A"), _tournament("t2", date="2026-11-15", start_time="10:00", host_club="B")]}
        current = {"start_date": "2026-09-01", "end_date": "2027-04-30", "tournaments": [_tournament("t1", date="2026-10-11", start_time="10:00", host_club="A"), _tournament("t2", date="2026-12-12", start_time="12:00", host_club="B")]}
        problem = _write_season(tmp_path, plan=current, published_plan=published)
        # t2's new interval has accepted, source-backed booking evidence.
        assertion = _booked_assertion(tournament=current["tournaments"][1], problem=problem)
        store = CanonicalSeasonStore(tmp_path)
        snapshot = store.load(_SEASON)
        snapshot.decisions.setdefault("manual_booking_assertions", []).append(assertion)
        store.write(snapshot)

        result = _scope(tmp_path, reviewed_plan=current, problem=problem)

        assert result["status"] == STATUS_ELIGIBLE, result
        assert result["delta"]["changed_tournament_ids"] == ["t2"]
        assert result["delta"]["interval_changed_tournament_ids"] == ["t2"]
        # Unrelated full-season planning debt is retained as diagnostic, not
        # used as a global blocker.
        assert result["diagnostic"]["full_season_reasons"] == [
            {"code": "unresolved_hosting", "count": 9}
        ]

    def test_roster_only_change_keeps_published_acceptance(self, tmp_path):
        published = {"start_date": "2026-09-01", "end_date": "2027-04-30", "tournaments": [_tournament("t1", date="2026-10-11", start_time="10:00", host_club="A")]}
        current = {
            "start_date": "2026-09-01",
            "end_date": "2027-04-30",
            "tournaments": [
                {
                    **_tournament("t1", date="2026-10-11", start_time="10:00", host_club="A"),
                    "teams": [_team("A", "A-1"), _team("A", "A-2")],
                }
            ],
        }
        problem = _write_season(tmp_path, plan=current, published_plan=published)

        result = _scope(tmp_path, reviewed_plan=current, problem=problem)

        assert result["status"] == STATUS_ELIGIBLE, result
        assert result["delta"]["participant_only_changed_tournament_ids"] == ["t1"]


class TestHeldAffectedTournaments:
    def test_changed_interval_without_booking_is_held(self, tmp_path):
        published = {"start_date": "2026-09-01", "end_date": "2027-04-30", "tournaments": [_tournament("t1", date="2026-10-11", start_time="10:00", host_club="A")]}
        current = {"start_date": "2026-09-01", "end_date": "2027-04-30", "tournaments": [_tournament("t1", date="2026-12-12", start_time="12:00", host_club="A")]}
        problem = _write_season(tmp_path, plan=current, published_plan=published)

        result = _scope(tmp_path, reviewed_plan=current, problem=problem)

        assert result["status"] == STATUS_HELD, result
        assert result["held"][0]["tournament_id"] == "t1"
        assert result["held"][0]["code"] == "changed_interval_without_accepted_booking"

    def test_reactivated_cancelled_tournament_requires_booking(self, tmp_path):
        published = {
            "start_date": "2026-09-01",
            "end_date": "2027-04-30",
            "tournaments": [
                _tournament("t1", date="2026-10-11", start_time="10:00", host_club="A"),
                _tournament("t2", date="2026-11-15", start_time="10:00", host_club="B", cancelled=True),
            ],
        }
        # Reactivating a previously published cancellation changes only the
        # ``cancelled`` flag; the interval is unchanged, so it must still prove
        # an accepted booking for the exact interval.
        current = {
            "start_date": "2026-09-01",
            "end_date": "2027-04-30",
            "tournaments": [
                _tournament("t1", date="2026-10-11", start_time="10:00", host_club="A"),
                _tournament("t2", date="2026-11-15", start_time="10:00", host_club="B", cancelled=False),
            ],
        }
        problem = _write_season(tmp_path, plan=current, published_plan=published)

        result = _scope(tmp_path, reviewed_plan=current, problem=problem)

        assert result["status"] == STATUS_HELD, result
        assert result["delta"]["interval_changed_tournament_ids"] == ["t2"]
        assert result["held"][0]["tournament_id"] == "t2"
        assert result["held"][0]["code"] == "changed_interval_without_accepted_booking"

    def test_active_cancellation_keeps_published_acceptance(self, tmp_path):
        published = {
            "start_date": "2026-09-01",
            "end_date": "2027-04-30",
            "tournaments": [
                _tournament("t1", date="2026-10-11", start_time="10:00", host_club="A"),
                _tournament("t2", date="2026-11-15", start_time="10:00", host_club="B"),
            ],
        }
        current = {
            "start_date": "2026-09-01",
            "end_date": "2027-04-30",
            "tournaments": [
                _tournament("t1", date="2026-10-11", start_time="10:00", host_club="A"),
                _tournament("t2", date="2026-11-15", start_time="10:00", host_club="B", cancelled=True),
            ],
        }
        problem = _write_season(tmp_path, plan=current, published_plan=published)

        result = _scope(tmp_path, reviewed_plan=current, problem=problem)

        assert result["status"] == STATUS_ELIGIBLE, result

    def test_missing_hosting_evidence_is_not_checkable(self, tmp_path):
        plan = {"start_date": "2026-09-01", "end_date": "2027-04-30", "tournaments": [_tournament("t1", date="2026-10-11", start_time="10:00", host_club="A")]}
        _write_season(tmp_path, plan=plan, published_plan=plan)
        decisions = CanonicalSeasonStore(tmp_path).load(_SEASON).decisions

        # A valid occupancy contract but no registered teams: the hosting
        # responsibility check cannot be evaluated, so the assessment fails
        # closed to the full audit gate instead of sliding through to ELIGIBLE.
        result = evaluate_publication_scope(
            reviewed_plan=plan,
            problem={"ice_time_minutes": {"U10": 120}},
            decisions=decisions,
            season=_SEASON,
            season_root=tmp_path,
        )

        assert result["status"] == STATUS_NOT_CHECKABLE, result
        assert any(
            reason["code"] == "hosting_evidence_unavailable" for reason in result["reasons"]
        )

    def test_added_tournament_without_booking_is_held(self, tmp_path):
        published = {"start_date": "2026-09-01", "end_date": "2027-04-30", "tournaments": [_tournament("t1", date="2026-10-11", start_time="10:00", host_club="A")]}
        current = {"start_date": "2026-09-01", "end_date": "2027-04-30", "tournaments": [_tournament("t1", date="2026-10-11", start_time="10:00", host_club="A"), _tournament("t2", date="2026-11-15", start_time="10:00", host_club="B")]}
        problem = _write_season(tmp_path, plan=current, published_plan=published)

        result = _scope(tmp_path, reviewed_plan=current, problem=problem)

        assert result["status"] == STATUS_HELD, result
        codes = {entry["code"] for entry in result["held"]}
        assert "added_tournament_without_accepted_booking" in codes

    def test_unexplained_removal_is_held(self, tmp_path):
        published = {"start_date": "2026-09-01", "end_date": "2027-04-30", "tournaments": [_tournament("t1", date="2026-10-11", start_time="10:00", host_club="A"), _tournament("t2", date="2026-11-15", start_time="10:00", host_club="B")]}
        current = {"start_date": "2026-09-01", "end_date": "2027-04-30", "tournaments": [_tournament("t1", date="2026-10-11", start_time="10:00", host_club="A")]}
        problem = _write_season(tmp_path, plan=current, published_plan=published)

        result = _scope(tmp_path, reviewed_plan=current, problem=problem)

        assert result["status"] == STATUS_HELD, result
        assert any(entry["code"] == "unexplained_public_entry_removal" for entry in result["held"])


class TestGlobalBlockers:
    def test_unauthorized_hosting_transfer_blocks(self, tmp_path):
        published = {
            "start_date": "2026-09-01",
            "end_date": "2027-04-30",
            "tournaments": [
                _tournament("t1", date="2026-10-11", start_time="10:00", host_club="A"),
                _tournament("t2", date="2026-11-15", start_time="10:00", host_club="B"),
            ],
        }
        # The change moves B's hosting obligation onto A (host changed),
        # without any registration/volume change.
        current = {
            "start_date": "2026-09-01",
            "end_date": "2027-04-30",
            "tournaments": [
                _tournament("t1", date="2026-10-11", start_time="10:00", host_club="A"),
                _tournament("t2", date="2026-11-15", start_time="10:00", host_club="A"),
            ],
        }
        problem = _write_season(tmp_path, plan=current, published_plan=published)

        result = _scope(tmp_path, reviewed_plan=current, problem=problem)

        assert result["status"] == STATUS_BLOCKED, result
        assert any(
            reason["code"] == "unauthorized_hosting_responsibility_transfer"
            for reason in result["reasons"]
        )

    def test_legacy_projection_schema_blocks(self, tmp_path):
        plan = {"start_date": "2026-09-01", "end_date": "2027-04-30", "tournaments": [_tournament("t1", date="2026-10-11", start_time="10:00", host_club="A")]}
        problem = _write_season(tmp_path, plan=plan, published_plan=plan)
        store = CanonicalSeasonStore(tmp_path)
        snapshot = store.load(_SEASON)
        baseline = snapshot.decisions["season_lifecycle"]["published_baseline"]
        # A legacy baseline recorded before the versioned operational
        # projection has no projection schema and must fail closed.
        for entry in baseline["tournaments"]:
            entry.pop("projection_schema", None)
            entry.pop("projection_schema_version", None)
        store.write(snapshot)

        result = _scope(tmp_path, reviewed_plan=plan, problem=problem)

        assert result["status"] == STATUS_BLOCKED, result
        assert any(
            reason["code"] == "projection_schema_error" for reason in result["reasons"]
        )
