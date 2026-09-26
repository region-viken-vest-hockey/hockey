"""Publication evidence and republish-delta regression tests.

Covers the deterministic core of first-class sealed-season republish:

* the immutable evidence vocabulary fails closed on a missing revision,
  projection fingerprint or artifact reference;
* the published -> canonical delta is exact by stable tournament id
  (placement, participants, duration, cancellation, guest reservations) and
  fails closed on an incomplete/legacy projection schema;
* approval/booking/protection changes are reported separately from schedule
  mutations;
* the retained before/after evidence record is written and readable.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tournament_scheduler.pipeline.export_projection_guard import tournament_projection
from tournament_scheduler.pipeline.publication_evidence import (
    PublicationEvidenceError,
    build_publication_evidence,
    build_republish_delta,
    decision_snapshot,
    diff_decision_snapshot,
    evidence_directory,
    has_immutable_reference,
    list_publication_evidence,
    previous_publication_link,
    read_publication_evidence,
    validate_publication_evidence,
    write_publication_evidence,
)


def _team(club: str, label: str, age_group: str = "U10") -> dict:
    return {"club": club, "label": label, "age_group": age_group}


def _tournament(
    tid: str, date: str, start: str, arena: str, host: str, age: str = "U10"
) -> dict:
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


def _tp(plan: dict, duration: int = 120) -> dict:
    ages = {str(t.get("age_group") or "U10") for t in plan.get("tournaments", [])}
    return tournament_projection(plan, {"ice_time_minutes": {age: duration for age in ages}})


class TestEvidenceValidation:
    def test_requires_canonical_revision_and_projection(self) -> None:
        with pytest.raises(PublicationEvidenceError, match="canonical_revision"):
            validate_publication_evidence({"projection_fingerprint": "fp", "run_id": "run-1"})
        with pytest.raises(PublicationEvidenceError, match="projection_fingerprint"):
            validate_publication_evidence({"canonical_revision": "rev", "run_id": "run-1"})

    def test_requires_an_artifact_reference(self) -> None:
        with pytest.raises(PublicationEvidenceError, match="artifact reference"):
            build_publication_evidence(
                run_id="",
                canonical_revision="rev",
                projection_fingerprint="fp",
            )

    def test_accepts_an_immutable_reference(self) -> None:
        evidence = build_publication_evidence(
            run_id="run-1",
            canonical_revision="rev",
            projection_fingerprint="fp",
            bundle_fingerprint="bundle",
            pages_branch="gh-pages",
            pages_commit="abc",
        )
        assert evidence["run_id"] == "run-1"
        assert has_immutable_reference(evidence) is True

    def test_private_export_fingerprint_is_not_a_pages_reference(self) -> None:
        evidence = build_publication_evidence(
            run_id="",
            canonical_revision="rev",
            projection_fingerprint="fp",
            export_fingerprint="private",
        )
        # Valid as evidence, but it cannot prove a public rollback target exists.
        assert has_immutable_reference(evidence) is False

    def test_previous_link_carries_the_immutable_reference(self) -> None:
        baseline = {
            "publication_id": "2026-09-21T0908",
            "canonical_revision": "rev-1",
            "published_at": "2026-09-21T09:14:53+00:00",
            "projection_fingerprint": "proj-1",
            "tournament_count": 1,
            "publication_evidence": {"run_id": "run-1", "bundle_fingerprint": "bundle-1"},
        }
        link = previous_publication_link(baseline)
        assert link["publication_id"] == "2026-09-21T0908"
        assert link["publication_evidence"]["run_id"] == "run-1"


class TestRepublishDelta:
    def _baseline(self) -> dict:
        return _tp(
            {
                "tournaments": [
                    _tournament("rvv-1", "2026-10-11", "10:00", "A", "Alpha"),
                    _tournament("rvv-2", "2026-11-15", "10:00", "B", "Beta"),
                ]
            }
        )

    def test_reports_placement_participant_duration_cancellation_guest_and_identity(self) -> None:
        baseline = self._baseline()
        current = _tp(
            {
                "tournaments": [
                    _tournament("rvv-1", "2026-10-11", "12:30", "A", "Alpha"),
                    _tournament("rvv-3", "2026-12-19", "10:00", "C", "Gamma"),
                ]
            }
        )
        current["rvv-1"]["participants"] = sorted(
            baseline["rvv-1"]["participants"] + ["Alpha\u001fAlpha2\u001fU10"]
        )
        current["rvv-1"]["duration_minutes"] = 150
        current["rvv-1"]["end_time"] = "15:00"
        delta = build_republish_delta(baseline, current)
        assert delta["summary"]["removed"] == 1
        assert delta["summary"]["added"] == 1
        assert delta["summary"]["changed"] == 1
        entry = delta["delta"]["field_changes"][0]
        assert entry["tournament_id"] == "rvv-1"
        assert set(entry["fields"]) >= {"start_time", "participants", "duration_minutes", "end_time"}

    def test_unchanged_tournament_reports_no_change(self) -> None:
        baseline = self._baseline()
        delta = build_republish_delta(baseline, baseline)
        assert delta["summary"]["changed"] == 0
        assert delta["summary"]["unchanged"] == 2
        assert delta["delta"]["changed"] is False

    def test_cancellation_and_guest_changes_are_not_silently_equal(self) -> None:
        baseline = self._baseline()
        current = _tp(
            {
                "tournaments": [
                    _tournament("rvv-1", "2026-10-11", "10:00", "A", "Alpha"),
                    _tournament("rvv-2", "2026-11-15", "10:00", "B", "Beta"),
                ]
            }
        )
        current["rvv-1"]["cancelled"] = True
        current["rvv-1"]["cancellation_reason"] = "hall closed"
        current["rvv-2"]["guest_slots"] = [
            {"id": "guest:rvv-2:1", "status": "open", "external_team": None}
        ]
        delta = build_republish_delta(baseline, current)
        assert delta["delta"]["cancellation_changes"]
        assert delta["delta"]["guest_slot_changes"]

    def test_fails_closed_on_legacy_or_incomplete_schema(self) -> None:
        baseline = self._baseline()
        legacy = {"rvv-1": {"id": "rvv-1", "date": "2026-10-11", "participants": []}}
        with pytest.raises(PublicationEvidenceError, match="schema"):
            build_republish_delta(legacy, baseline)


class TestDecisionChanges:
    def test_snapshot_and_diff_report_approvals_bookings_and_protections(self) -> None:
        before_decisions = {
            "decisions": {
                "rvv-1": {"status": "pending_review", "placement_locked": False, "participants_locked": False},
                "rvv-2": {"status": "pending_review", "placement_locked": False, "participants_locked": False},
            },
            "change_protections": [{"id": "change:1", "status": "active"}],
            "request_constraints": [],
            "tournament_booking_evidence": [],
        }
        after_decisions = {
            "decisions": {
                "rvv-1": {"status": "approved", "placement_locked": True, "participants_locked": True},
                "rvv-2": {"status": "pending_review", "placement_locked": False, "participants_locked": False},
            },
            "change_protections": [
                {"id": "change:1", "status": "active"},
                {"id": "change:2", "status": "active"},
            ],
            "request_constraints": [{"id": "request:1", "status": "active"}],
            "tournament_booking_evidence": [
                {
                    "id": "booking_evidence:rvv-1:1",
                    "tournament_id": "rvv-1",
                    "status": "confirmed_booked",
                    "reason": "explicit_association",
                    "event_fingerprint": "event-1",
                    "calendar_fingerprint": "cal-1",
                }
            ],
        }
        before = decision_snapshot(before_decisions)
        after = decision_snapshot(after_decisions)
        changes = diff_decision_snapshot(before, after)
        assert changes["available"] is True
        assert changes["changed"] is True
        assert changes["approval_changes"][0]["tournament_id"] == "rvv-1"
        assert changes["booking_changes"][0]["tournament_id"] == "rvv-1"
        assert changes["change_protections"]["added"] == ["change:2"]
        assert changes["request_constraints"]["added"] == ["request:1"]

    def test_booking_reference_change_keeps_the_same_status_visible(self) -> None:
        before = decision_snapshot(
            {
                "tournament_booking_evidence": [
                    {
                        "id": "booking_evidence:rvv-1:1",
                        "tournament_id": "rvv-1",
                        "status": "confirmed_booked",
                        "event_fingerprint": "event-1",
                    }
                ]
            }
        )
        after = decision_snapshot(
            {
                "tournament_booking_evidence": [
                    {
                        "id": "booking_evidence:rvv-1:2",
                        "tournament_id": "rvv-1",
                        "status": "confirmed_booked",
                        "event_fingerprint": "event-2",
                    }
                ]
            }
        )
        changes = diff_decision_snapshot(before, after)
        assert changes["changed"] is True
        assert changes["booking_changes"][0]["tournament_id"] == "rvv-1"

    def test_missing_previous_snapshot_is_explicitly_unavailable(self) -> None:
        changes = diff_decision_snapshot(None, decision_snapshot({"decisions": {}}))
        assert changes["available"] is False
        assert changes["reason"] == "no_previous_decision_snapshot"
        assert changes["changed"] is None

    def test_identical_snapshots_report_no_change(self) -> None:
        decisions = {
            "decisions": {"rvv-1": {"status": "approved", "placement_locked": True, "participants_locked": False}},
            "change_protections": [],
            "request_constraints": [],
            "tournament_booking_evidence": [],
        }
        changes = diff_decision_snapshot(decision_snapshot(decisions), decision_snapshot(decisions))
        assert changes["available"] is True
        assert changes["changed"] is False


class TestRetainedEvidence:
    def _delta(self) -> dict:
        plan = {"tournaments": [_tournament("rvv-1", "2026-10-11", "10:00", "A", "Alpha")]}
        projection = _tp(plan)
        return build_republish_delta(projection, projection)

    def test_write_read_and_list_round_trip(self, tmp_path: Path) -> None:
        evidence = build_publication_evidence(
            run_id="run-2",
            canonical_revision="rev-2",
            projection_fingerprint="proj-2",
            bundle_fingerprint="bundle-2",
            pages_branch="gh-pages",
            pages_commit="commit-2",
        )
        files = write_publication_evidence(
            season_root=tmp_path / "season",
            season="2026-2027",
            publication_id="2026-09-28T0908",
            evidence=evidence,
            previous_publication={"publication_id": "2026-09-21T0908"},
            republish_delta=self._delta(),
            canonical_revision="rev-2",
        )
        assert Path(files["json"]).exists()
        assert Path(files["markdown"]).exists()
        record = read_publication_evidence(tmp_path / "season", "2026-2027", "2026-09-28T0908")
        assert record is not None
        assert record["publication_evidence"]["run_id"] == "run-2"
        assert record["previous_publication"]["publication_id"] == "2026-09-21T0908"
        listed = list_publication_evidence(tmp_path / "season", "2026-2027")
        assert [entry["publication_id"] for entry in listed] == ["2026-09-28T0908"]
        payload = json.loads(Path(files["json"]).read_text(encoding="utf-8"))
        assert payload["record_fingerprint"]

    def test_retry_is_idempotent_and_conflicting_evidence_is_rejected(self, tmp_path: Path) -> None:
        evidence = build_publication_evidence(
            run_id="run-2",
            canonical_revision="rev-2",
            projection_fingerprint="proj-2",
            bundle_fingerprint="bundle-2",
        )
        kwargs = dict(
            season_root=tmp_path / "season",
            season="2026-2027",
            publication_id="2026-09-28T0908",
            evidence=evidence,
            previous_publication=None,
            republish_delta=self._delta(),
            canonical_revision="rev-2",
        )
        first = write_publication_evidence(**kwargs)
        second = write_publication_evidence(**kwargs)
        assert first == second

        conflicting = dict(kwargs)
        conflicting["canonical_revision"] = "rev-3"
        with pytest.raises(PublicationEvidenceError, match="conflicting"):
            write_publication_evidence(**conflicting)

    def test_retry_repairs_missing_markdown_after_json_succeeded(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        import tournament_scheduler.pipeline.publication_evidence as module

        evidence = build_publication_evidence(
            run_id="run-2",
            canonical_revision="rev-2",
            projection_fingerprint="proj-2",
            bundle_fingerprint="bundle-2",
        )
        kwargs = dict(
            season_root=tmp_path / "season",
            season="2026-2027",
            publication_id="2026-09-28T0908",
            evidence=evidence,
            previous_publication=None,
            republish_delta=self._delta(),
            canonical_revision="rev-2",
        )
        real_write = module._atomic_write_text

        def fail_markdown(path, content):
            if Path(path).name.endswith(".md"):
                raise OSError("disk full on markdown")
            return real_write(path, content)

        monkeypatch.setattr(module, "_atomic_write_text", fail_markdown)
        with pytest.raises(OSError):
            write_publication_evidence(**kwargs)

        json_path = (
            tmp_path
            / "season"
            / "2026-2027"
            / "evidence"
            / "publications"
            / "2026-09-28T0908"
            / module.PUBLICATION_EVIDENCE_JSON
        )
        markdown_path = json_path.with_name(module.PUBLICATION_EVIDENCE_MARKDOWN)
        assert json_path.exists()
        assert not markdown_path.exists()

        # The retry finds the matching JSON, repairs the missing Markdown and
        # must not report success with incomplete evidence.
        monkeypatch.setattr(module, "_atomic_write_text", real_write)
        files = write_publication_evidence(**kwargs)
        assert Path(files["json"]).exists()
        assert Path(files["markdown"]).exists()
        assert markdown_path.read_text(encoding="utf-8") == module._render_markdown(
            read_publication_evidence(tmp_path / "season", "2026-2027", "2026-09-28T0908")
        )

    def test_conflicting_existing_markdown_is_rejected(self, tmp_path: Path) -> None:
        evidence = build_publication_evidence(
            run_id="run-2",
            canonical_revision="rev-2",
            projection_fingerprint="proj-2",
            bundle_fingerprint="bundle-2",
        )
        kwargs = dict(
            season_root=tmp_path / "season",
            season="2026-2027",
            publication_id="2026-09-28T0908",
            evidence=evidence,
            previous_publication=None,
            republish_delta=self._delta(),
            canonical_revision="rev-2",
        )
        files = write_publication_evidence(**kwargs)
        Path(files["markdown"]).write_text("tampered\n", encoding="utf-8")

        with pytest.raises(PublicationEvidenceError, match="conflicting"):
            write_publication_evidence(**kwargs)

    def test_rejects_unsafe_publication_id(self, tmp_path: Path) -> None:
        with pytest.raises(PublicationEvidenceError):
            evidence_directory(tmp_path, "2026-2027", "../escape")
