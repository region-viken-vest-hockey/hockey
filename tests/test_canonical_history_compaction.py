"""Canonical decision-history growth bound and compaction regression tests.

Issue #464: every targeted ``season move`` embedded a whole-season
``verify_candidate`` result in ``decisions.json.history[].details``, duplicating
the same payload per move and dominating the canonical decisions file. These
tests lock the bounded summary the writer now persists, the opt-in migration
that archives the already-oversized evidence, and the safety invariants
(revision/fingerprint stability, replay identity, and fail-closed archive
validation).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tournament_scheduler.pipeline.state import PipelineState, StageName, StageStatus
from tournament_scheduler.testing.reviewed_export import write_reviewed_stage4_export
from tournament_scheduler.application.canonical_season_service import CanonicalSeasonService
from tournament_scheduler.season_state import load_decisions, move_tournament, promote_from_stage3
from tournament_scheduler.canonical_history_summary import (
    verification_hash,
    verification_summary,
)
from tournament_scheduler.canonical_state import (
    CANONICAL_STATE_REVISION_KEY,
    PARTICIPATION_ACCEPTANCES_KEY,
    compute_canonical_state_revision,
    schedule_fingerprint,
)
from tournament_scheduler.infrastructure.canonical_compaction_backup import (
    backup_dir,
    compaction_backup_id,
    load_compaction_backup,
)
from tournament_scheduler.infrastructure.canonical_evidence_archive import (
    EvidenceArchiveError,
    load_move_evidence,
    moves_dir,
)
from tournament_scheduler.infrastructure.canonical_season_store import SeasonStateError
from tournament_scheduler.published_mutation_history import replay_recorded_mutations

YEAR = "2026-2027"


def _candidate() -> dict[str, Any]:
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


def _large_verification_result(n_tournaments: int = 180) -> dict[str, Any]:
    """A representative whole-season verification result (issue #464 sizes)."""

    return {
        "ok": True,
        "violations": [],
        "waived_violations": [],
        "skipped": [],
        "club_controlled_allocations_used": [],
        "movable_allocations_used": [],
        "calendar_interpretations_used": [],
        "unresolved_hosting_obligations": [],
        "hosting_balance": [{"club": f"Club{i}", "hosted": i} for i in range(n_tournaments)],
        "hosting_balance_imbalances": [{"club": f"Club{i}"} for i in range(12)],
        "manual_calendar_placements": [],
        "manual_external_conflict_placements": [{"tournament_id": f"T{i}"} for i in range(2)],
        "manual_participation_placements": [{"tournament_id": f"T{i}"} for i in range(105)],
        "participation_deviations": [
            {
                "club": f"C{i}",
                "actual": i % 10,
                "target": 10,
                "age_group": "U10",
                "club_pool": {
                    "team_distribution": [
                        {"team": f"C{i}-{j}", "actual": 1, "target": 2} for j in range(4)
                    ]
                },
            }
            for i in range(206)
        ],
        "participation_metrics": {"teams": n_tournaments},
        "participation_club_pools": [
            {
                "club": f"C{i}",
                "age_group": "U10",
                "scope": "season",
                "team_distribution": [
                    {"team": f"C{i}-{j}", "actual": 1, "target": 2} for j in range(4)
                ],
            }
            for i in range(183)
        ],
        "participation_club_pool_shortfalls": [
            {"club": f"C{i}", "age_group": "U10", "classification": "minor_club_pool_shortfall"}
            for i in range(96)
        ],
        "input_constrained_shapes": [],
        "stale_approvals": [],
        "orphaned_approvals": [],
    }


def _summary_size(result: dict[str, Any], *, tournament_id: str | None = None) -> int:
    return len(json.dumps(verification_summary(result, tournament_id=tournament_id), sort_keys=True))


def _move_entry(
    tournament_id: str,
    new_date: str,
    verification_result: dict[str, Any],
    *,
    at: str = "2026-09-25T10:00:00+00:00",
) -> dict[str, Any]:
    return {
        "event": "move",
        "tournament_id": tournament_id,
        "actor": "operator",
        "at": at,
        "tournament_fingerprint": "tf",
        "previous_fingerprint": "pf",
        "schedule_fingerprint": "sf",
        "note": "",
        "details": {
            "old_placement": {"date": "2026-09-12", "arena": "Arena A", "host_club": "A", "start_time": "10:00"},
            "new_placement": {"date": new_date, "arena": "Arena A", "host_club": "A", "start_time": "10:00"},
            "before_fingerprint": "bf",
            "after_fingerprint": "af",
            "before_canonical_revision": "rev-before",
            "verification_result": verification_result,
            "operational_acceptability": {
                "ok": True,
                "regressions": [],
                "before_counts": {"fixed_busy_placement": 0, "manual_calendar_placement": 0, "unresolved_placement_obligation": 0, "host_confirmation_dependency": 0},
                "after_counts": {"fixed_busy_placement": 0, "manual_calendar_placement": 0, "unresolved_placement_obligation": 0, "host_confirmation_dependency": 0},
                "before_profile": {"fixed_busy_placement": [], "manual_calendar_placement": [], "unresolved_placement_obligation": [], "host_confirmation_dependency": []},
                "after_profile": {"fixed_busy_placement": [], "manual_calendar_placement": [], "unresolved_placement_obligation": [], "host_confirmation_dependency": []},
            },
            "run_id": "run-1",
        },
    }


def _write_season(root: Path, *, history: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Write a synthetic canonical season with an already-oversized history."""

    plan = {
        "schema_version": 1,
        "start_date": "2026-09-01",
        "end_date": "2027-04-30",
        "tournaments": [
            {
                "id": "T1",
                "date": "2026-10-10",
                "arena": "Arena A",
                "age_group": "U10",
                "host_club": "A",
                "start_time": "10:00",
                "teams": [
                    {"club": "A", "label": "A1", "age_group": "U10"},
                    {"club": "B", "label": "B1", "age_group": "U10"},
                ],
                "games": [{"home": "A1", "away": "B1", "parallel_slot": 0, "round_number": 1}],
            }
        ],
    }
    fingerprint = schedule_fingerprint(plan)
    schedule = {
        "schema_version": 1,
        "season": YEAR,
        "revision": fingerprint,
        "fingerprint": fingerprint,
        "plan_schema_version": 1,
        "plan": plan,
    }
    decisions = {
        "schema_version": 1,
        "season": YEAR,
        "schedule_fingerprint": fingerprint,
        "decisions": {
            "T1": {
                "status": "pending_review",
                "placement_locked": False,
                "participants_locked": False,
                "approved_fingerprint": None,
            }
        },
        "history": history,
    }
    decisions[CANONICAL_STATE_REVISION_KEY] = compute_canonical_state_revision(schedule, decisions)
    season_dir = root / YEAR
    season_dir.mkdir(parents=True, exist_ok=True)
    (season_dir / "schedule.json").write_text(
        json.dumps(schedule, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (season_dir / "decisions.json").write_text(
        json.dumps(decisions, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return schedule, decisions


# ---------------------------------------------------------------------------
# Bounded summary
# ---------------------------------------------------------------------------


def test_move_persists_bounded_summary_not_full_result(tmp_path: Path) -> None:
    """The writer no longer appends a whole-season verifier payload to a move."""

    work_dir = tmp_path / ".pipeline"
    root = tmp_path / "season"
    state = PipelineState(work_dir)
    state.write_stage(StageName.PLANNING, {"plan": _candidate()}, status=StageStatus.DONE)
    write_reviewed_stage4_export(state)
    promote_from_stage3(work_dir=work_dir, root=root, actor="tester")

    move_tournament(
        season=YEAR,
        tournament_id="u10-a-20260912",
        root=root,
        date="2026-09-13",
        actor="mover",
        note="requested slot",
    )

    decisions = load_decisions(YEAR, root=root)
    move_events = [event for event in decisions.get("history", []) if event.get("event") == "move"]
    assert len(move_events) == 1
    details = move_events[0]["details"]
    # Bounded summary, not the full whole-season result.
    assert "verification_summary" in details
    assert "verification_result" not in details
    summary = details["verification_summary"]
    assert summary["ok"] is True
    assert isinstance(summary["counts"], dict)
    assert len(json.dumps(details, sort_keys=True)) < 5_000
    # The dry-run/CLI response still carries the full result for review.


def test_verification_summary_is_bounded_for_representative_result() -> None:
    result = _large_verification_result()
    full_size = len(json.dumps(result, sort_keys=True))
    assert full_size > 100_000  # sanity: the full result really is large
    summary = verification_summary(result, tournament_id="T1")
    size = _summary_size(result, tournament_id="T1")
    # The summary is counts + a hash + tournament-scoped findings, never rows.
    assert size < 2_000
    assert size < full_size // 50
    assert summary["ok"] is True
    assert summary["counts"]["participation_deviations"] == 206
    assert summary["counts"]["participation_club_pools"] == 183
    assert summary["verification_hash"] == verification_hash(result)
    # No full row payload leaks into the summary.
    encoded = json.dumps(summary, sort_keys=True)
    assert "Club1" not in encoded


def test_verification_summary_scopes_findings_to_tournament() -> None:
    result = _large_verification_result()
    result["manual_external_conflict_placements"] = [
        {"tournament_id": "T1", "message": "conflict"},
        {"tournament_id": "T2", "message": "conflict"},
    ]
    summary = verification_summary(result, tournament_id="T1")
    assert summary["tournament_external_conflicts"] == ["T1"]
    assert summary["counts"]["manual_external_conflict_placements"] == 2


# ---------------------------------------------------------------------------
# Compaction
# ---------------------------------------------------------------------------


def test_compact_history_archives_evidence_and_preserves_identity(tmp_path: Path) -> None:
    result = _large_verification_result()
    root = tmp_path / "season"
    schedule, decisions = _write_season(
        root,
        history=[
            _move_entry("T1", "2026-10-17", result),
            {"event": "approve", "tournament_id": "T1", "actor": "op", "at": "2026-09-25T11:00:00+00:00"},
        ],
    )
    before_revision = compute_canonical_state_revision(schedule, decisions)
    before_fingerprint = schedule_fingerprint(schedule["plan"])
    seed = {
        "T1": {
            "id": "T1",
            "date": "2026-10-10",
            "start_time": "10:00",
            "arena": "Arena A",
            "host_club": "A",
            "age_group": "U10",
            "duration_minutes": 120,
            "participants": [],
        }
    }
    before_replay = replay_recorded_mutations(seed, decisions["history"])[0]
    original_decisions_bytes = (root / YEAR / "decisions.json").read_bytes()

    service = CanonicalSeasonService(root=root)
    report = service.compact_history(season=YEAR)

    assert report["compacted_moves"] == 1
    assert report["already_compacted"] == 0
    assert report["before_revision"] == report["after_revision"] == before_revision
    assert report["before_fingerprint"] == report["after_fingerprint"] == before_fingerprint
    assert report["chars_saved"] > 0

    compacted = service.load(YEAR).decisions
    move_details = compacted["history"][0]["details"]
    assert "verification_result" not in move_details
    assert "verification_summary" in move_details
    assert "evidence_ref" in move_details
    assert move_details["new_placement"]["date"] == "2026-10-17"
    # The narrow transform preserves operational acceptability verbatim; only the
    # oversized verification_result is extracted.
    assert move_details["operational_acceptability"]["before_profile"] == {
        "fixed_busy_placement": [],
        "manual_calendar_placement": [],
        "unresolved_placement_obligation": [],
        "host_confirmation_dependency": [],
    }

    # The archived evidence resolves and matches the referenced hash.
    ref = move_details["evidence_ref"]
    archived = load_move_evidence(YEAR, ref, root=root)
    assert archived["verification_result"]["ok"] is True
    assert archived["verification_result"]["participation_deviations"][0]["actual"] == 0

    # A durable byte-for-byte backup of the complete original is recorded.
    backup_manifest = report["backup"]
    assert backup_manifest["backup_id"] == compaction_backup_id(original_decisions_bytes)
    assert backup_manifest["source_canonical_revision"] == before_revision
    assert backup_manifest["compacted_event_indices"] == [0]
    _backup_manifest, backup_bytes = load_compaction_backup(
        YEAR, backup_manifest["backup_id"], root=root
    )
    assert backup_bytes == original_decisions_bytes

    # Replay-critical fields are preserved in order, so replay is unchanged.
    after_replay = replay_recorded_mutations(seed, compacted["history"])[0]
    assert after_replay == before_replay
    # And the replay actually applied the move (the seed is not a no-op).
    assert after_replay["T1"]["date"] == "2026-10-17"


def test_compact_history_is_idempotent(tmp_path: Path) -> None:
    result = _large_verification_result()
    root = tmp_path / "season"
    _write_season(root, history=[_move_entry("T1", "2026-10-17", result)])

    service = CanonicalSeasonService(root=root)
    first = service.compact_history(season=YEAR)
    assert first["compacted_moves"] == 1
    assert first["backup"] is not None

    second = service.compact_history(season=YEAR)
    assert second["compacted_moves"] == 0
    assert second["already_compacted"] == 1
    assert second["before_revision"] == first["before_revision"]
    assert second["committed"] is False
    assert second["backup"] is None
    # Only one archived evidence file ever exists for the same content.
    assert len(list(moves_dir(YEAR, root=root).glob("*.json"))) == 1
    # Only one compaction backup ever exists (idempotent no-op on re-run).
    assert len(list(backup_dir(YEAR, root=root).iterdir())) == 1


def test_compact_history_fails_closed_on_missing_archive(tmp_path: Path) -> None:
    result = _large_verification_result()
    root = tmp_path / "season"
    _write_season(root, history=[_move_entry("T1", "2026-10-17", result)])

    service = CanonicalSeasonService(root=root)
    service.compact_history(season=YEAR)

    # Corrupt/remove the retained archive; a later compaction must fail closed.
    for evidence_file in moves_dir(YEAR, root=root).glob("*.json"):
        evidence_file.unlink()

    with pytest.raises(EvidenceArchiveError):
        service.compact_history(season=YEAR)


def test_compact_history_dry_run_does_not_write(tmp_path: Path) -> None:
    result = _large_verification_result()
    root = tmp_path / "season"
    schedule, decisions = _write_season(root, history=[_move_entry("T1", "2026-10-17", result)])
    decisions_bytes = (root / YEAR / "decisions.json").read_bytes()

    service = CanonicalSeasonService(root=root)
    report = service.compact_history(season=YEAR, dry_run=True)
    assert report["dry_run"] is True
    assert report["compacted_moves"] == 1
    assert report["backup"]["dry_run"] is True
    assert report["backup"]["backup_id"] == compaction_backup_id(decisions_bytes)
    assert (root / YEAR / "decisions.json").read_bytes() == decisions_bytes
    assert list(moves_dir(YEAR, root=root).glob("*.json")) == []
    assert list(backup_dir(YEAR, root=root).glob("*")) == []


def test_compaction_preserves_stored_revision_field_exactly(tmp_path: Path) -> None:
    """The stored canonical-state revision field is byte-identical after compaction."""

    result = _large_verification_result()
    root = tmp_path / "season"
    _schedule, decisions = _write_season(root, history=[_move_entry("T1", "2026-10-17", result)])
    stored_before = decisions[CANONICAL_STATE_REVISION_KEY]

    service = CanonicalSeasonService(root=root)
    report = service.compact_history(season=YEAR)
    assert report["after_revision"] == report["before_revision"] == stored_before
    assert service.load(YEAR).decisions[CANONICAL_STATE_REVISION_KEY] == stored_before


def test_compact_history_preserves_top_level_decisions_verbatim(tmp_path: Path) -> None:
    """Only ``history`` is transformed; every other top-level field survives."""

    result = _large_verification_result()
    root = tmp_path / "season"
    _schedule, decisions = _write_season(
        root,
        history=[_move_entry("T1", "2026-10-17", result)],
    )
    decisions["extra_operator_field"] = {"nested": [1, 2, {"x": "y"}]}
    (root / YEAR / "decisions.json").write_text(
        json.dumps(decisions, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    service = CanonicalSeasonService(root=root)
    service.compact_history(season=YEAR)

    compacted = service.load(YEAR).decisions
    for key, value in decisions.items():
        if key == "history":
            continue
        assert compacted[key] == value, f"top-level decisions field {key!r} changed"


def test_compact_history_refuses_pending_acceptance_id_migration(tmp_path: Path) -> None:
    """A history-only migration must not silently migrate legacy acceptance ids."""

    result = _large_verification_result()
    root = tmp_path / "season"
    schedule, decisions = _write_season(
        root, history=[_move_entry("T1", "2026-10-17", result)]
    )
    # A legacy participation-acceptance id (omits age_group) that a normal
    # commit would migrate. Compaction must refuse rather than do both at once.
    decisions[PARTICIPATION_ACCEPTANCES_KEY] = [
        {
            "club": "ClubA",
            "label": "TeamA",
            "age_group": "U10",
            "scope": "season",
            "id": "participation_acceptance:ClubA:TeamA:season",
        }
    ]
    decisions[CANONICAL_STATE_REVISION_KEY] = compute_canonical_state_revision(schedule, decisions)
    original = json.dumps(decisions, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    (root / YEAR / "decisions.json").write_text(original, encoding="utf-8")

    service = CanonicalSeasonService(root=root)
    with pytest.raises(SeasonStateError):
        service.compact_history(season=YEAR)

    # Nothing was committed: no backup, no archive, decisions byte-for-byte intact.
    assert (root / YEAR / "decisions.json").read_text(encoding="utf-8") == original
    assert list(moves_dir(YEAR, root=root).glob("*.json")) == []
    assert list(backup_dir(YEAR, root=root).glob("*")) == []


def test_identical_evidence_keeps_event_specific_provenance(tmp_path: Path) -> None:
    """Two events with identical proof share one archive but keep distinct provenance."""

    result = _large_verification_result()
    root = tmp_path / "season"
    entry_a = _move_entry("T1", "2026-10-17", result, at="2026-09-25T10:00:00+00:00")
    entry_b = _move_entry("T2", "2026-10-18", result, at="2026-09-25T11:00:00+00:00")
    entry_b["details"]["before_canonical_revision"] = "rev-before-2"
    _write_season(root, history=[entry_a, entry_b])

    service = CanonicalSeasonService(root=root)
    report = service.compact_history(season=YEAR)
    assert report["compacted_moves"] == 2

    compacted = service.load(YEAR).decisions
    refs = [
        entry["details"]["evidence_ref"]
        for entry in compacted["history"]
        if entry.get("event") == "move"
    ]
    assert len(refs) == 2
    # The shared archive file is addressed by the proof only; each event's ref
    # carries its own provenance.
    assert refs[0]["sha256"] == refs[1]["sha256"]
    assert refs[0]["path"] == refs[1]["path"]
    assert refs[0]["tournament_id"] == "T1"
    assert refs[1]["tournament_id"] == "T2"
    assert refs[0]["event_at"] == "2026-09-25T10:00:00+00:00"
    assert refs[1]["event_at"] == "2026-09-25T11:00:00+00:00"
    assert refs[0]["canonical_revision"] == "rev-before"
    assert refs[1]["canonical_revision"] == "rev-before-2"

    # One shared file holds the hash-bound result; both refs resolve to it.
    assert len(list(moves_dir(YEAR, root=root).glob("*.json"))) == 1
    for ref in refs:
        payload = load_move_evidence(YEAR, ref, root=root)
        assert payload["verification_result"]["ok"] is True


def test_verification_summary_fails_closed_on_malformed_ok() -> None:
    """A missing/non-boolean 'ok' verdict must raise, never be reported successful."""

    with pytest.raises(ValueError):
        verification_summary({"violations": []}, tournament_id="T1")
    with pytest.raises(ValueError):
        verification_summary({"ok": "yes"}, tournament_id="T1")
    with pytest.raises(ValueError):
        verification_summary({"ok": 1}, tournament_id="T1")
    with pytest.raises(ValueError):
        verification_summary({"ok": None}, tournament_id="T1")

    # A real boolean (True or False) is accepted.
    assert verification_summary({"ok": False, "violations": []}, tournament_id="T1")["ok"] is False
    assert verification_summary({"ok": True, "violations": []}, tournament_id="T1")["ok"] is True


def test_compact_history_refuses_unknown_evidence_shape(tmp_path: Path) -> None:
    """A non-object verification_result is refused, never silently stripped."""

    root = tmp_path / "season"
    entry = _move_entry("T1", "2026-10-17", _large_verification_result())
    entry["details"]["verification_result"] = ["not", "an", "object"]
    _write_season(root, history=[entry])

    service = CanonicalSeasonService(root=root)
    with pytest.raises(SeasonStateError):
        service.compact_history(season=YEAR)
    # Nothing was mutated and no backup/archive was written.
    assert list(moves_dir(YEAR, root=root).glob("*.json")) == []
    assert list(backup_dir(YEAR, root=root).glob("*")) == []


def test_compact_history_rejects_verification_result_on_non_move_event(tmp_path: Path) -> None:
    """Only historical move events may carry inline evidence; others are refused."""

    root = tmp_path / "season"
    entry = {
        "event": "participant_replacement",
        "tournament_id": "T1",
        "actor": "operator",
        "at": "2026-09-25T10:00:00+00:00",
        "details": {"verification_result": _large_verification_result()},
    }
    _write_season(root, history=[entry])

    service = CanonicalSeasonService(root=root)
    with pytest.raises(SeasonStateError):
        service.compact_history(season=YEAR)

    # Nothing was transformed, archived or backed up.
    assert list(moves_dir(YEAR, root=root).glob("*.json")) == []
    assert list(backup_dir(YEAR, root=root).glob("*")) == []
    assert service.load(YEAR).decisions["history"][0]["details"]["verification_result"]


def test_compact_history_leaves_non_move_events_untouched(tmp_path: Path) -> None:
    """A non-move event's bounded summary is not treated as compactable evidence."""

    root = tmp_path / "season"
    entry = {
        "event": "participant_replacement",
        "tournament_id": "T1",
        "actor": "operator",
        "at": "2026-09-25T10:00:00+00:00",
        "details": {
            "verification_summary": {
                "schema_version": 1,
                "ok": True,
                "verification_hash": "abc",
                "counts": {},
            }
        },
    }
    _write_season(root, history=[entry])

    service = CanonicalSeasonService(root=root)
    report = service.compact_history(season=YEAR)
    assert report["compacted_moves"] == 0
    assert report["already_compacted"] == 0
    assert report["committed"] is False
    assert service.load(YEAR).decisions["history"][0]["details"]["verification_summary"]


def test_compact_history_refuses_malformed_ok_before_archiving(tmp_path: Path) -> None:
    """A malformed verification result is refused before any evidence is archived."""

    result = _large_verification_result()
    del result["ok"]
    root = tmp_path / "season"
    _write_season(root, history=[_move_entry("T1", "2026-10-17", result)])

    service = CanonicalSeasonService(root=root)
    with pytest.raises(ValueError):
        service.compact_history(season=YEAR)

    # Validation failed before archiving: no evidence and no backup were written.
    assert list(moves_dir(YEAR, root=root).glob("*.json")) == []
    assert list(backup_dir(YEAR, root=root).glob("*")) == []


def test_compact_history_commit_failure_preserves_original(tmp_path: Path) -> None:
    """A failed final swap leaves the original active state intact."""

    result = _large_verification_result()
    root = tmp_path / "season"
    _write_season(root, history=[_move_entry("T1", "2026-10-17", result)])
    original_bytes = (root / YEAR / "decisions.json").read_bytes()

    service = CanonicalSeasonService(root=root)

    def _boom(snapshot):
        raise SeasonStateError("simulated commit failure")

    service._commit_history_only = _boom  # type: ignore[method-assign]

    with pytest.raises(SeasonStateError):
        service.compact_history(season=YEAR)

    # The active decisions file is untouched (atomic swap never happened).
    assert (root / YEAR / "decisions.json").read_bytes() == original_bytes


def test_compact_history_always_archives_full_evidence(tmp_path: Path) -> None:
    """There is no drop-evidence path: every full result is retained in the archive."""

    result = _large_verification_result()
    root = tmp_path / "season"
    _write_season(root, history=[_move_entry("T1", "2026-10-17", result)])

    service = CanonicalSeasonService(root=root)
    report = service.compact_history(season=YEAR)
    assert report["compacted_moves"] == 1
    assert "dropped_moves" not in report

    compacted = service.load(YEAR).decisions
    move_details = compacted["history"][0]["details"]
    assert "verification_result" not in move_details
    assert "evidence_ref" in move_details
    assert "verification_summary" in move_details
    # The full evidence is retained in the archive, never silently dropped.
    ref = move_details["evidence_ref"]
    archived = load_move_evidence(YEAR, ref, root=root)
    assert archived["verification_result"] == result


def test_compaction_on_sealed_season_reports_replay_parity(tmp_path: Path) -> None:
    """Compaction on a published-sealed season verifies replay/reconciliation parity."""

    from tests.test_published_season_sealing import (
        _seal_abc,
        _tournaments_abc,
        _write_canonical,
    )

    root = tmp_path / "season"
    _write_canonical(root, _tournaments_abc())
    _seal_abc(root)
    service = CanonicalSeasonService(root=root)
    service.move_tournament(
        season=YEAR, tournament_id="rvv-1", start_time="11:00", actor="tester"
    )

    # Simulate the historical oversized format: put a full whole-season result
    # back into the move details so compaction has something to extract.
    decisions = service.load(YEAR).decisions
    move = next(entry for entry in decisions["history"] if entry.get("event") == "move")
    move["details"]["verification_result"] = _large_verification_result()
    (root / YEAR / "decisions.json").write_text(
        json.dumps(decisions, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    report = service.compact_history(season=YEAR)
    assert report["compacted_moves"] == 1
    assert report["replay_parity"] is not None
    assert report["replay_parity"]["ok"] is True
