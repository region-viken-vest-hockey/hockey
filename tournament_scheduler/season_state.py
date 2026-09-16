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

from tournament_scheduler.pipeline.fingerprints import stable_payload_sha256
from tournament_scheduler.pipeline.stage4_export_verification import _build_export_verification_problem
from tournament_scheduler.pipeline.state import PipelineState, StageName
from tournament_scheduler.planning_contract import extract_candidate, verify_candidate
from tournament_scheduler.serialization.season_plan import SEASON_PLAN_SCHEMA_VERSION

SEASON_STATE_SCHEMA_VERSION = 1
DECISIONS_SCHEMA_VERSION = 1
DEFAULT_SEASON_ROOT = Path("season")


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
            "status": "pending_review",
            "placement_locked": False,
            "participants_locked": False,
            "approved_fingerprint": None,
            "approved_at": None,
            "approved_by": None,
            "note": "",
        }
    return records


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
    record = decisions.get("decisions", {}).get(tournament_id, {})
    if record.get("placement_locked"):
        raise SeasonStateError(f"Tournament {tournament_id} has placement_locked=true and cannot be moved")

    plan = dict(schedule["plan"])
    tournaments = [dict(t) for t in plan.get("tournaments", [])]
    changed = False
    found = False
    for tournament in tournaments:
        if str(tournament.get("id")) != tournament_id:
            continue
        found = True
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
    if not found:
        raise SeasonStateError(f"Unknown tournament id in canonical schedule: {tournament_id}")
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
    _write_season_state_atomic(
        season_dir(season, root=root), updated_schedule, decisions, require_absent=False
    )
    return updated_schedule


def approve_tournament(
    *,
    season: str,
    tournament_id: str,
    root: str | os.PathLike[str] = DEFAULT_SEASON_ROOT,
    actor: str | None = None,
    note: str = "",
    placement_locked: bool = True,
    participants_locked: bool = False,
) -> dict[str, Any]:
    """Record current approval/lock state separately from schedule facts."""

    schedule = load_schedule(season, root=root)
    decisions = load_decisions(season, root=root)
    plan = schedule["plan"]
    tournament = next((t for t in plan.get("tournaments", []) if str(t.get("id")) == tournament_id), None)
    if tournament is None:
        raise SeasonStateError(f"Unknown tournament id in canonical schedule: {tournament_id}")
    approved_at = datetime.now(tz=timezone.utc).isoformat()
    tournament_fingerprint = stable_payload_sha256(tournament)
    record = dict(decisions["decisions"].get(tournament_id, {}))
    record.update(
        {
            "status": "approved",
            "placement_locked": bool(placement_locked),
            "participants_locked": bool(participants_locked),
            "approved_fingerprint": tournament_fingerprint,
            "approved_at": approved_at,
            "approved_by": actor or os.environ.get("RVV_OPERATOR") or os.environ.get("USER") or "operator",
            "note": note,
        }
    )
    decisions["decisions"][tournament_id] = record
    decisions["updated_at"] = approved_at
    _write_json_atomic(decisions_path(season, root=root), decisions)
    return decisions
