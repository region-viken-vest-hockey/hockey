"""Git-backed canonical season state for promoted RVV schedules.

The pipeline checkpoints under ``.pipeline`` are transient run state.  This
module owns the durable per-season boundary used after an operator deliberately
promotes a verified candidate: ``season/<season-id>/schedule.json`` for schedule
facts and ``season/<season-id>/decisions.json`` for current human workflow state.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tournament_scheduler.canonical_baseline import approval_fingerprint, resolve_approval
from tournament_scheduler.pipeline.fingerprints import stable_payload_sha256
from tournament_scheduler.pipeline.stage4_export_verification import _build_export_verification_problem
from tournament_scheduler.pipeline.state import PipelineState, StageName
from tournament_scheduler.planning_contract import extract_candidate, verify_candidate
from tournament_scheduler.serialization.season_plan import SEASON_PLAN_SCHEMA_VERSION

SEASON_STATE_SCHEMA_VERSION = 1
DECISIONS_SCHEMA_VERSION = 1
DEFAULT_SEASON_ROOT = Path("season")

# Statuses an approval lifecycle can be in.  ``stale_approval`` means a
# previously approved tournament changed without an explicit unapprove, so
# the stored fingerprint no longer proves the current placement.
APPROVED_STATUS = "approved"
STALE_APPROVAL_STATUS = "stale_approval"
PENDING_REVIEW_STATUS = "pending_review"


class SeasonStateError(RuntimeError):
    """Raised when canonical season state cannot be read or written safely."""


def _json_bytes(payload: dict[str, Any]) -> bytes:
    return (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = _json_bytes(payload)
    with tempfile.NamedTemporaryFile("wb", delete=False, dir=path.parent, prefix=f".{path.name}.", suffix=".tmp") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
        tmp_name = handle.name
    os.replace(tmp_name, path)


def _write_season_state_atomic(
    season_directory: Path,
    schedule_payload: dict[str, Any],
    decisions_payload: dict[str, Any],
    *,
    require_absent: bool,
) -> None:
    """Install both canonical season-state files as one atomic boundary.

    The directory is staged and swapped, so a failure never leaves only
    ``schedule.json`` or only ``decisions.json`` behind.  ``require_absent``
    refuses to replace existing canonical state (used by deliberate
    promotion); mutation callers replace it and rely on the swap for rollback.
    """

    parent = season_directory.parent
    parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{season_directory.name}.", suffix=".tmp", dir=parent))
    backup = parent / f".{season_directory.name}.backup"
    try:
        (staging / "schedule.json").write_bytes(_json_bytes(schedule_payload))
        (staging / "decisions.json").write_bytes(_json_bytes(decisions_payload))
        for staged_file in (staging / "schedule.json", staging / "decisions.json"):
            with staged_file.open("rb") as handle:
                os.fsync(handle.fileno())
        if season_directory.exists():
            if require_absent:
                raise SeasonStateError(
                    f"Canonical season state already exists for {season_directory.name}; "
                    "use --force only for deliberate replacement"
                )
            if backup.exists():
                shutil.rmtree(backup)
            os.replace(season_directory, backup)
            try:
                os.replace(staging, season_directory)
            except Exception:
                os.replace(backup, season_directory)
                raise
            shutil.rmtree(backup, ignore_errors=True)
        else:
            os.replace(staging, season_directory)
    finally:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)


def load_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise SeasonStateError(f"Canonical season file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise SeasonStateError(f"Invalid JSON in canonical season file {path}: {exc}") from exc


def season_id_from_plan(plan_dict: dict[str, Any]) -> str:
    start = str(plan_dict.get("start_date") or "")
    end = str(plan_dict.get("end_date") or "")
    if len(start) >= 4 and len(end) >= 4:
        return f"{start[:4]}-{end[:4]}"
    dates = sorted(str(t.get("date")) for t in plan_dict.get("tournaments", []) if t.get("date"))
    if dates:
        first_year = int(dates[0][:4])
        last_year = int(dates[-1][:4])
        return f"{first_year}-{last_year}"
    raise SeasonStateError("Cannot infer season id; pass --season explicitly")


def season_dir(season: str, *, root: str | os.PathLike[str] = DEFAULT_SEASON_ROOT) -> Path:
    return Path(root) / season


def schedule_path(season: str, *, root: str | os.PathLike[str] = DEFAULT_SEASON_ROOT) -> Path:
    return season_dir(season, root=root) / "schedule.json"


def decisions_path(season: str, *, root: str | os.PathLike[str] = DEFAULT_SEASON_ROOT) -> Path:
    return season_dir(season, root=root) / "decisions.json"


def schedule_fingerprint(plan_dict: dict[str, Any]) -> str:
    return stable_payload_sha256(plan_dict.get("tournaments", []))


def load_schedule(season: str, *, root: str | os.PathLike[str] = DEFAULT_SEASON_ROOT) -> dict[str, Any]:
    payload = load_json(schedule_path(season, root=root))
    version = int(payload.get("schema_version", 0) or 0)
    if version != SEASON_STATE_SCHEMA_VERSION:
        raise SeasonStateError(f"Unsupported schedule schema_version: {version!r}")
    if not isinstance(payload.get("plan"), dict):
        raise SeasonStateError("Canonical schedule is missing its plan payload")
    return payload


def load_decisions(season: str, *, root: str | os.PathLike[str] = DEFAULT_SEASON_ROOT) -> dict[str, Any]:
    payload = load_json(decisions_path(season, root=root))
    version = int(payload.get("schema_version", 0) or 0)
    if version != DECISIONS_SCHEMA_VERSION:
        raise SeasonStateError(f"Unsupported decisions schema_version: {version!r}")
    if not isinstance(payload.get("decisions"), dict):
        raise SeasonStateError("Canonical decisions file is missing its decisions object")
    return payload


def planning_checkpoint_from_schedule(schedule: dict[str, Any]) -> dict[str, Any]:
    """Return a Stage-4-compatible planning checkpoint from canonical state."""

    return {
        "plan": dict(schedule["plan"]),
        "canonical_state": {
            "season": schedule.get("season"),
            "revision": schedule.get("revision"),
            "fingerprint": schedule.get("fingerprint"),
            "promoted_from": schedule.get("promoted_from", {}),
        },
    }


def _initial_decisions(plan_dict: dict[str, Any]) -> dict[str, Any]:
    records: dict[str, Any] = {}
    for tournament in plan_dict.get("tournaments", []):
        tournament_id = str(tournament.get("id") or "")
        if not tournament_id:
            continue
        records[tournament_id] = {
            "status": PENDING_REVIEW_STATUS,
            "placement_locked": False,
            "participants_locked": False,
            "approved_fingerprint": None,
            "approved_at": None,
            "approved_by": None,
            "note": "",
        }
    return records


def _append_decision_history(
    decisions: dict[str, Any],
    *,
    event: str,
    tournament_id: str,
    actor: str | None,
    now: str,
    tournament_fingerprint: str | None = None,
    previous_fingerprint: str | None = None,
    note: str = "",
) -> None:
    """Append a durable approval-lifecycle audit entry to decisions.json.

    The event log is deliberately tamper-visible and compact: it records who
    did what to which tournament and which protected-fields fingerprint was
    involved, never private reasoning.
    """
    history = decisions.setdefault("history", [])
    history.append(
        {
            "event": event,
            "tournament_id": tournament_id,
            "actor": actor or os.environ.get("RVV_OPERATOR") or os.environ.get("USER") or "operator",
            "at": now,
            "tournament_fingerprint": tournament_fingerprint,
            "previous_fingerprint": previous_fingerprint,
            "schedule_fingerprint": decisions.get("schedule_fingerprint"),
            "note": note or "",
        }
    )


def promote_from_stage3(
    *,
    work_dir: str | os.PathLike[str] = ".pipeline",
    season: str | None = None,
    root: str | os.PathLike[str] = DEFAULT_SEASON_ROOT,
    actor: str | None = None,
    force: bool = False,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Promote the current verified Stage 3 candidate into canonical season state."""

    state = PipelineState(work_dir)
    checkpoint = state.read_stage(StageName.PLANNING)
    if not checkpoint:
        raise SeasonStateError("No Stage 3 planning checkpoint found to promote")
    candidate = extract_candidate(checkpoint)
    problem = _build_export_verification_problem({}, state)
    result = verify_candidate(candidate, problem)
    if not result.get("ok", True):
        messages = "; ".join(str(v.get("message") or v.get("code")) for v in result.get("violations", []))
        raise SeasonStateError(f"Refusing promotion: selected candidate fails hard verification: {messages}")

    plan_dict = dict(candidate)
    plan_dict["schema_version"] = SEASON_PLAN_SCHEMA_VERSION
    resolved_season = season or season_id_from_plan(plan_dict)
    sched_path = schedule_path(resolved_season, root=root)
    dec_path = decisions_path(resolved_season, root=root)
    season_directory = season_dir(resolved_season, root=root)
    if (sched_path.exists() or dec_path.exists()) and not force:
        raise SeasonStateError(
            f"Canonical season state already exists for {resolved_season}; use --force only for deliberate replacement"
        )

    now = datetime.now(tz=timezone.utc).isoformat()
    fingerprint = schedule_fingerprint(plan_dict)
    source_export = state.read_stage(StageName.EXPORT)
    schedule_payload = {
        "schema_version": SEASON_STATE_SCHEMA_VERSION,
        "season": resolved_season,
        "created_at": now,
        "updated_at": now,
        "revision": fingerprint,
        "fingerprint": fingerprint,
        "plan_schema_version": SEASON_PLAN_SCHEMA_VERSION,
        "plan": plan_dict,
        "promoted_from": {
            "work_dir": str(work_dir),
            "stage3_fingerprint": fingerprint,
            "stage4_export_fingerprint": source_export.get("export_fingerprint"),
            "stage4_export_dir": source_export.get("export_dir"),
        },
    }
    decisions_payload = {
        "schema_version": DECISIONS_SCHEMA_VERSION,
        "season": resolved_season,
        "created_at": now,
        "updated_at": now,
        "schedule_fingerprint": fingerprint,
        "actor": actor or os.environ.get("RVV_OPERATOR") or os.environ.get("USER") or "operator",
        "decisions": _initial_decisions(plan_dict),
    }

    # Install both files as one durable boundary; a failed promotion must not
    # leave only schedule.json or only decisions.json behind.
    _write_season_state_atomic(
        season_directory, schedule_payload, decisions_payload, require_absent=not force
    )
    return schedule_payload, decisions_payload


def move_tournament(
    *,
    season: str,
    tournament_id: str,
    root: str | os.PathLike[str] = DEFAULT_SEASON_ROOT,
    date: str | None = None,
    arena: str | None = None,
    host_club: str | None = None,
    start_time: str | None = None,
    problem: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Apply a bounded placement mutation to canonical schedule state.

    Approval/lock decisions are enforced before mutation.  The full serialized
    candidate is re-verified before either canonical file is replaced, using
    *problem* when the caller can reconstruct the planning contract (so
    problem-dependent hard invariants are checked too).  A rejected mutation
    leaves both canonical files byte-unchanged.
    """

    schedule = load_schedule(season, root=root)
    decisions = load_decisions(season, root=root)
    plan = dict(schedule["plan"])
    tournaments = [dict(t) for t in plan.get("tournaments", [])]
    target = next(
        (t for t in tournaments if str(t.get("id")) == tournament_id),
        None,
    )
    if target is None:
        raise SeasonStateError(f"Unknown tournament id in canonical schedule: {tournament_id}")
    record = decisions.get("decisions", {}).get(tournament_id, {})
    resolved = resolve_approval(record, target)
    if resolved["placement_locked"]:
        raise SeasonStateError(
            f"Tournament {tournament_id} has an active placement lock and cannot be moved; "
            "unapprove it explicitly first"
        )

    changed = False
    for tournament in tournaments:
        if str(tournament.get("id")) != tournament_id:
            continue
        for field, value in {
            "date": date,
            "arena": arena,
            "host_club": host_club,
            "start_time": start_time,
        }.items():
            if value is not None and tournament.get(field) != value:
                tournament[field] = value
                changed = True
        break
    if not changed:
        return schedule

    plan["tournaments"] = tournaments
    result = verify_candidate(plan, problem) if problem else verify_candidate(plan)
    if not result.get("ok", True):
        messages = "; ".join(str(v.get("message") or v.get("code")) for v in result.get("violations", []))
        raise SeasonStateError(f"Refusing canonical mutation: candidate fails hard verification: {messages}")

    now = datetime.now(tz=timezone.utc).isoformat()
    fingerprint = schedule_fingerprint(plan)
    updated_schedule = dict(schedule)
    updated_schedule.update(
        {
            "updated_at": now,
            "revision": fingerprint,
            "fingerprint": fingerprint,
            "plan": plan,
        }
    )
    decisions = dict(decisions)
    decisions["schedule_fingerprint"] = fingerprint
    decisions["updated_at"] = now
    # A canonical move invalidates any approval whose protected fingerprint
    # no longer matches the moved tournament (only possible for a tournament
    # approved without a placement lock) -- surface it as stale_approval
    # instead of leaving a record that still claims to be approved.
    decisions["decisions"] = _reconcile_decisions(decisions.get("decisions", {}), plan, now=now)
    _write_season_state_atomic(
        season_dir(season, root=root), updated_schedule, decisions, require_absent=False
    )
    return updated_schedule


def _attributable_blockers(
    verification: dict[str, Any],
    tournament_id: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split a verification result into this tournament's hard/unresolved blockers.

    Only findings that actually concern *tournament_id* block approval; an
    unrelated hard violation elsewhere (another unresolved tournament the
    operator has not reviewed yet) must not prevent incrementally approving
    a tournament that is itself valid.  A violation with no explicit
    ``tournament_id`` counts when its message names this tournament.
    """
    hard: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    for violation in verification.get("violations") or []:
        owner = violation.get("tournament_id")
        if owner is not None:
            if str(owner) == tournament_id:
                hard.append(violation)
            continue
        if tournament_id and tournament_id in str(violation.get("message") or ""):
            hard.append(violation)
    # A known external calendar double-booking is real booking risk, not a
    # soft quality warning: approving it would freeze a placement already
    # proven to conflict with trusted evidence.
    for placement in verification.get("manual_external_conflict_placements") or []:
        if str(placement.get("tournament_id") or "") == tournament_id:
            unresolved.append(
                {
                    "code": "manual_external_conflict_placements",
                    "message": (
                        f"Tournament {tournament_id} has a known external calendar conflict; "
                        "resolve it before approving"
                    ),
                    "tournament_id": tournament_id,
                }
            )
    return hard, unresolved


def approve_tournament(
    *,
    season: str,
    tournament_id: str,
    root: str | os.PathLike[str] = DEFAULT_SEASON_ROOT,
    actor: str | None = None,
    note: str = "",
    placement_locked: bool = True,
    participants_locked: bool = False,
    problem: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Approve/lock one tournament after canonical hard verification.

    Approval is an operator judgment on a *hard-valid* placement, not a
    waiver: the selected canonical plan is re-verified here and a
    tournament-attributable hard violation or known external conflict
    refuses the approval.  The recorded ``approved_fingerprint`` covers the
    normalized protected scheduling state, so any later real change makes
    the approval deterministically stale instead of silently staying valid.
    """

    schedule = load_schedule(season, root=root)
    decisions = load_decisions(season, root=root)
    plan = schedule["plan"]
    tournament = next((t for t in plan.get("tournaments", []) if str(t.get("id")) == tournament_id), None)
    if tournament is None:
        raise SeasonStateError(f"Unknown tournament id in canonical schedule: {tournament_id}")

    verification = verify_candidate(plan, problem) if problem else verify_candidate(plan)
    hard_blockers, unresolved_blockers = _attributable_blockers(verification, tournament_id)
    blockers = hard_blockers + unresolved_blockers
    if blockers:
        messages = "; ".join(
            str(blocker.get("message") or blocker.get("code")) for blocker in blockers
        )
        raise SeasonStateError(
            f"Refusing to approve {tournament_id}: tournament fails canonical verification: {messages}"
        )

    approved_at = datetime.now(tz=timezone.utc).isoformat()
    resolved_actor = actor or os.environ.get("RVV_OPERATOR") or os.environ.get("USER") or "operator"
    tournament_fingerprint = approval_fingerprint(tournament)
    previous = decisions["decisions"].get(tournament_id, {})
    record = dict(previous)
    record.update(
        {
            "status": APPROVED_STATUS,
            "placement_locked": bool(placement_locked),
            "participants_locked": bool(participants_locked),
            "approved_fingerprint": tournament_fingerprint,
            "approved_at": approved_at,
            "approved_by": resolved_actor,
            "note": note,
        }
    )
    # A (re)approval always supersedes earlier invalidation state.
    record.pop("stale_at", None)
    record.pop("stale_reason", None)
    record.pop("unapproved_at", None)
    record.pop("unapproved_by", None)
    decisions["decisions"][tournament_id] = record
    decisions["updated_at"] = approved_at
    _append_decision_history(
        decisions,
        event="approve",
        tournament_id=tournament_id,
        actor=resolved_actor,
        now=approved_at,
        tournament_fingerprint=tournament_fingerprint,
        previous_fingerprint=previous.get("approved_fingerprint"),
        note=note,
    )
    _write_json_atomic(decisions_path(season, root=root), decisions)
    return decisions


def unapprove_tournament(
    *,
    season: str,
    tournament_id: str,
    root: str | os.PathLike[str] = DEFAULT_SEASON_ROOT,
    actor: str | None = None,
    note: str = "",
) -> dict[str, Any]:
    """Explicitly revoke approval and all locks for one canonical tournament.

    This is the only way back to editability for a locked tournament: it
    clears the approval fingerprint and both lock scopes, and records who
    revoked what.  The schedule facts are untouched; a subsequent canonical
    move/apply can then change the tournament normally.
    """
    schedule = load_schedule(season, root=root)
    decisions = load_decisions(season, root=root)
    plan = schedule["plan"]
    tournament = next((t for t in plan.get("tournaments", []) if str(t.get("id")) == tournament_id), None)
    if tournament is None:
        raise SeasonStateError(f"Unknown tournament id in canonical schedule: {tournament_id}")
    existing = decisions["decisions"].get(tournament_id)
    if existing is None:
        raise SeasonStateError(f"Unknown tournament id in decisions state: {tournament_id}")

    now = datetime.now(tz=timezone.utc).isoformat()
    resolved_actor = actor or os.environ.get("RVV_OPERATOR") or os.environ.get("USER") or "operator"
    previous_fingerprint = existing.get("approved_fingerprint")
    record = dict(existing)
    record.update(
        {
            "status": PENDING_REVIEW_STATUS,
            "placement_locked": False,
            "participants_locked": False,
            "approved_fingerprint": None,
            "approved_at": None,
            "approved_by": None,
            "note": note or existing.get("note") or "",
            "unapproved_at": now,
            "unapproved_by": resolved_actor,
        }
    )
    record.pop("stale_at", None)
    record.pop("stale_reason", None)
    decisions["decisions"][tournament_id] = record
    decisions["updated_at"] = now
    _append_decision_history(
        decisions,
        event="unapprove",
        tournament_id=tournament_id,
        actor=resolved_actor,
        now=now,
        previous_fingerprint=previous_fingerprint,
        note=note,
    )
    _write_json_atomic(decisions_path(season, root=root), decisions)
    return decisions


def approval_report(
    season: str,
    *,
    root: str | os.PathLike[str] = DEFAULT_SEASON_ROOT,
) -> dict[str, Any]:
    """Read-only approval/lock status for every canonical tournament.

    Recomputes each stored approval fingerprint against the current
    tournament, so ``stale_approval`` is a deterministic diagnostic of what
    the persisted decision state currently means -- never a cached claim.
    """
    schedule = load_schedule(season, root=root)
    decisions = load_decisions(season, root=root)
    records = decisions.get("decisions", {}) or {}
    tournaments: list[dict[str, Any]] = []
    stale_approvals: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for tournament in schedule["plan"].get("tournaments", []) or []:
        tournament_id = str(tournament.get("id") or "")
        if not tournament_id:
            continue
        seen_ids.add(tournament_id)
        resolved = resolve_approval(records.get(tournament_id), tournament)
        entry = {
            "tournament_id": tournament_id,
            "status": resolved["status"],
            "stale": resolved["stale"],
            "placement_locked": resolved["placement_locked"],
            "participants_locked": resolved["participants_locked"],
            "approved_fingerprint": resolved["approved_fingerprint"],
            "current_fingerprint": resolved["current_fingerprint"],
            "approved_at": resolved["approved_at"],
            "approved_by": resolved["approved_by"],
            "note": resolved["note"],
            "stale_reason": resolved.get("stale_reason"),
        }
        tournaments.append(entry)
        if resolved["stale"]:
            stale_approvals.append(
                {
                    "code": "stale_approval",
                    "tournament_id": tournament_id,
                    "approved_fingerprint": resolved["approved_fingerprint"],
                    "current_fingerprint": resolved["current_fingerprint"],
                    "stale_reason": resolved.get("stale_reason"),
                }
            )
    orphaned = [
        {
            "code": "orphaned_approval",
            "tournament_id": tournament_id,
            "approved_fingerprint": (record or {}).get("approved_fingerprint"),
        }
        for tournament_id, record in records.items()
        if tournament_id not in seen_ids and (record or {}).get("approved_fingerprint")
    ]
    counts = {
        "total": len(tournaments),
        "approved": sum(1 for entry in tournaments if entry["status"] == APPROVED_STATUS),
        "stale": len(stale_approvals),
        "orphaned": len(orphaned),
        "locked": sum(
            1
            for entry in tournaments
            if entry["placement_locked"] or entry["participants_locked"]
        ),
        "pending_review": sum(
            1 for entry in tournaments if entry["status"] == PENDING_REVIEW_STATUS
        ),
    }
    return {
        "season": season,
        "schedule_fingerprint": decisions.get("schedule_fingerprint"),
        "revision": schedule.get("revision"),
        "counts": counts,
        "tournaments": tournaments,
        "stale_approvals": stale_approvals,
        "orphaned_approvals": orphaned,
    }


def _reconcile_decisions(
    existing: dict[str, Any],
    plan_dict: dict[str, Any],
    *,
    now: str,
) -> dict[str, Any]:
    """Carry approval/lock state forward for surviving tournaments only.

    A tournament whose identity survives keeps its record.  A previously
    approved tournament whose facts changed (possible only when it was
    approved without a placement lock) becomes an explicit
    ``stale_approval`` with its old fingerprint retained for audit -- an
    approval fingerprint that no longer matches the schedule is not
    approval, and silently reverting to ``pending_review`` would hide that
    this placement was once trusted.  Removed tournaments drop their
    records; new ids start at ``pending_review``.
    """
    reconciled: dict[str, Any] = {}
    for tournament in plan_dict.get("tournaments", []) or []:
        tournament_id = str(tournament.get("id") or "")
        if not tournament_id:
            continue
        record = dict(existing.get(tournament_id) or {})
        if not record:
            record = {
                "status": PENDING_REVIEW_STATUS,
                "placement_locked": False,
                "participants_locked": False,
                "approved_fingerprint": None,
                "approved_at": None,
                "approved_by": None,
                "note": "",
            }
        elif record.get("status") in (APPROVED_STATUS, STALE_APPROVAL_STATUS) or record.get("approved_fingerprint"):
            approved_fingerprint = record.get("approved_fingerprint")
            fingerprint_matches = bool(approved_fingerprint) and approved_fingerprint == approval_fingerprint(tournament)
            if record.get("status") == STALE_APPROVAL_STATUS or not fingerprint_matches:
                # A changed approval is a first-class stale_approval, not a
                # silent reset to pending_review: the operator must be able
                # to see that this placement was previously approved and is
                # no longer trustworthy until explicitly reapproved.
                record = {
                    "status": STALE_APPROVAL_STATUS,
                    "placement_locked": False,
                    "participants_locked": False,
                    "approved_fingerprint": approved_fingerprint,
                    "approved_at": record.get("approved_at"),
                    "approved_by": record.get("approved_by"),
                    "note": record.get("note") or "",
                    "stale_at": record.get("stale_at") or now,
                    "stale_reason": "approval invalidated by schedule change",
                }
        reconciled[tournament_id] = record
    return reconciled


def apply_candidate(
    *,
    season: str,
    candidate: dict[str, Any],
    root: str | os.PathLike[str] = DEFAULT_SEASON_ROOT,
    problem: dict[str, Any] | None = None,
    actor: str | None = None,
    change_weights: dict[str, float] | None = None,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Apply a verified replan candidate to canonical season state.

    The candidate must be the output of baseline-aware planning: canonical
    placement/participant locks are re-checked here (never trusting the
    caller), the candidate is hard-verified (against *problem* when the
    caller can reconstruct the full planning contract), and surviving
    tournaments keep their durable ids and approval records.  Both canonical
    files are replaced atomically; a rejected candidate leaves them
    byte-unchanged.

    Returns ``(schedule, decisions, change_cost)``.  ``actor`` is accepted
    for symmetry with the other mutation entry points and is not written to
    schedule facts.
    """
    from tournament_scheduler.canonical_baseline import (
        build_canonical_baseline,
        change_cost,
        verify_canonical_locks,
    )

    schedule = load_schedule(season, root=root)
    decisions = load_decisions(season, root=root)
    baseline = build_canonical_baseline(schedule, decisions)
    normalized_candidate = extract_candidate(candidate)

    lock_violations = verify_canonical_locks(baseline, normalized_candidate)
    if lock_violations:
        messages = "; ".join(str(v.get("message")) for v in lock_violations)
        raise SeasonStateError(f"Refusing canonical apply: candidate violates canonical locks: {messages}")

    result = verify_candidate(normalized_candidate, problem) if problem else verify_candidate(normalized_candidate)
    if not result.get("ok", True):
        messages = "; ".join(str(v.get("message") or v.get("code")) for v in result.get("violations", []))
        raise SeasonStateError(f"Refusing canonical apply: candidate fails hard verification: {messages}")

    plan = dict(normalized_candidate)
    plan.pop("source", None)
    plan["schema_version"] = SEASON_PLAN_SCHEMA_VERSION
    plan.setdefault("start_date", schedule["plan"].get("start_date"))
    plan.setdefault("end_date", schedule["plan"].get("end_date"))

    now = datetime.now(tz=timezone.utc).isoformat()
    fingerprint = schedule_fingerprint(plan)
    updated_schedule = {
        **schedule,
        "updated_at": now,
        "revision": fingerprint,
        "fingerprint": fingerprint,
        "plan_schema_version": SEASON_PLAN_SCHEMA_VERSION,
        "plan": plan,
        "applied_from": {
            "previous_revision": schedule.get("revision"),
            "actor": actor or os.environ.get("RVV_OPERATOR") or os.environ.get("USER") or "operator",
        },
    }
    updated_decisions = {
        **decisions,
        "updated_at": now,
        "schedule_fingerprint": fingerprint,
        "decisions": _reconcile_decisions(decisions.get("decisions", {}), plan, now=now),
    }
    cost = change_cost(baseline, plan, weights=change_weights)
    _write_season_state_atomic(
        season_dir(season, root=root), updated_schedule, updated_decisions, require_absent=False
    )
    return updated_schedule, updated_decisions, cost
