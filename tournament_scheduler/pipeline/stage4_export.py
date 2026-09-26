"""Stage 4 — multi-format export (Excel, iCal, CSV).

Reads the Stage 3 plan checkpoint, reconstructs a :class:`SeasonPlan` from it,
and writes three output files:

- ``<export_dir>/season_plan.xlsx``   — Excel workbook via :class:`SeasonPlanExporter`
- ``<export_dir>/season_plan.ics``    — iCal feed via :class:`ICalExporter`
- ``<export_dir>/season_plan.csv``    — flat game CSV + ``_overview.csv`` via :class:`CsvExporter`
- ``<export_dir>/season_plan.html``   — interactive HTML overview via :class:`~tournament_scheduler.html.html_exporter.HtmlExporter`
- ``<export_dir>/season_plan_report.html``   — companion diagnostics report with fairness / travel / hosting summaries
- ``<export_dir>/manual_schedule.html``   — “Må planlegges manuelt” view listing hall time that must be booked/verified by hand: tournaments that could not be placed without an arena/sequence collision, plus tournaments hosted by clubs whose calendar could not be scraped (provisional start times). Only written when such items exist; they no longer block the export
- ``<export_dir>/season_plan_spond_games.xlsx`` — printable tournament-by-tournament schedule attachment for Spond
- ``<export_dir>/review_packets/`` — per-club approval folders with review workbook, Spond import, schedule attachment, and response template

File paths are written to the Stage 4 checkpoint.

The module is split into focused siblings kept under the repo's
300-line-per-file guideline: ``stage4_export_errors`` (``Stage4Error``),
``stage4_export_timing`` (build-timestamp resolution, export pruning),
``stage4_export_xlsx`` (reproducible XLSX normalization),
``stage4_export_not_started`` (placeholder exports before planning starts),
``stage4_export_manual_schedule`` (the "Må planlegges manuelt" view), and
``stage4_export_verification`` (the hard-verification problem builder).
This file owns the ``run()`` orchestration itself and the CLI entry point.
"""

from __future__ import annotations

import copy
import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from ..arena_conflicts import find_arena_interval_collisions
from ..planning_contract import extract_candidate, verify_candidate
from .export_lifecycle import EXPORT_LIFECYCLE_FILENAME, write_draft_manifest
from .export_projection_guard import assert_export_preserves_canonical_plan, tournament_projection
from .fingerprints import stable_payload_sha256
from ..excel.plan_exporter import SeasonPlanExporter
from ..ical.ical_exporter import ICalExporter
from ..csv.csv_exporter import CsvExporter
from ..html.html_exporter import HtmlExporter
from .stage1_config import load_effective_config
from .state import PipelineState, StageName, StageStatus
from ..serialization.season_plan import season_plan_from_dict
from .stage4_helpers import build_tournament_placement_entries
from .calendar_viewer import generate_html as _generate_calendars_html
from .input_viewer import generate_html as _generate_input_html
from .activity_viewer import write_activity_artifacts_from_payload
from .not_started import NOT_STARTED_MESSAGE
from ..review.review_packet_exporter import ReviewPacketExporter
from ..spond.spond_exporter import SpondExporter
from .stage4_export_errors import Stage4Error
from .stage4_export_timing import (
    DEFAULT_EXPORT_DIR,
    DEFAULT_BASENAME,
    _TIMESTAMP_DIR_RE,
    _prune_old_exports,
    _resolve_build_timestamp,
)
from .stage4_export_xlsx import _normalize_export_workbooks
from .stage4_export_not_started import _write_not_started_exports
from .stage4_export_manual_schedule import (
    MANUAL_SCHEDULE_FILENAME,
    MANUAL_SCHEDULE_CATEGORIES,
    _manual_schedule_html,
)
from .stage4_export_verification import _build_export_verification_problem
from .verification_context import build_verification_context
from .public_export_context import (
    build_public_export_context,
    fingerprint_public_export_context,
)

logger = logging.getLogger(__name__)


def _compute_scrape_age(updated_at: str) -> str:
    """Return a human-readable age for a scrape timestamp, or ``""``."""
    if not updated_at:
        return ""
    from datetime import datetime as _dt, timezone as _tz

    try:
        delta = _dt.now(tz=_tz.utc) - _dt.fromisoformat(updated_at)
    except Exception:  # noqa: BLE001 - presentation metadata is best-effort
        return ""
    if delta.total_seconds() < 3600:
        return f"{int(delta.total_seconds() // 60)}m siden"
    if delta.days < 1:
        return f"{int(delta.total_seconds() // 3600)}t siden"
    return f"{delta.days}d siden"


def _export_approvals(effective_config: dict[str, Any], plan_dict: dict[str, Any]) -> dict[str, Any]:
    """Operator-confirmed placements for the plan's canonical season, if any.

    An explicit approval/placement lock is confirmation: that placement is a
    real booking even when a scraped calendar disagrees, so normalization must
    never demote it. Best-effort -- a missing canonical season just means no
    approval overlay.
    """
    try:
        from ..canonical_baseline import resolve_canonical_state

        def _as_date(value: Any):
            if hasattr(value, "isoformat") and not isinstance(value, str):
                return value
            try:
                return datetime.strptime(str(value), "%Y-%m-%d").date()
            except (TypeError, ValueError):
                return None

        start = _as_date(plan_dict.get("start_date") or effective_config.get("start_date"))
        end = _as_date(plan_dict.get("end_date") or effective_config.get("end_date"))
        if start is None or end is None:
            return {}
        resolved = resolve_canonical_state(effective_config, start, end)
        if not resolved:
            return {}
        return dict(resolved.get("decisions", {}).get("decisions", {}) or {})
    except Exception:
        return {}

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def run(
    plan_checkpoint: dict[str, Any],
    state: PipelineState,
    *,
    export_dir: str | os.PathLike[str] = DEFAULT_EXPORT_DIR,
    basename: str = DEFAULT_BASENAME,
    strict: bool = True,
    timestamped_export: bool = True,
    build_timestamp: str | int | float | datetime | None = None,
    verification_problem: dict[str, Any] | None = None,
    effective_config_override: dict[str, Any] | None = None,
    use_pipeline_metadata: bool = True,
    public_export_context: dict[str, Any] | None = None,
    supersedes: dict[str, Any] | None = None,
    allow_placement_normalization: bool = True,
    canonical_schedule_plan: dict[str, Any] | None = None,
    published_export_guard: dict[str, Any] | None = None,
    published_canonical_plan: dict[str, Any] | None = None,
    published_canonical_problem: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Export the Stage 3 plan to Excel, iCal, and CSV.

    Parameters
    ----------
    plan_checkpoint:
        Stage 3 checkpoint data (must contain a ``plan`` key).
    state:
        :class:`PipelineState` managing the work directory.
    export_dir:
        Directory where output files are written (created if needed).
    basename:
        Base filename without extension (default ``season_plan``).
    strict:
        If ``True``, raise :class:`Stage4Error` on any export failure.
    supersedes:
        Provenance back to the reviewed export this generation refines. It is
        recorded in the new export's lifecycle manifest and returned in the
        checkpoint; the caller owns marking the prior export superseded.
    allow_placement_normalization:
        Whether Stage 4 may demote fixed-conflict placements before export.
        Published canonical-season exports pass ``False`` because they must be
        schedule-preserving projections of canonical state.
    canonical_schedule_plan:
        When supplied, the proposed export projection is checked against this
        canonical plan by stable tournament id before any artifacts are written.
    published_canonical_plan:
        The canonical plan at the publication revision recorded by
        ``published_export_guard``. It is used to recover stable tournament
        identity from legacy published artifacts that predate
        ``schedule_projection``; without it such a baseline fails closed instead
        of binding rows by position. ``published_canonical_problem`` supplies
        the historical ice-time contract used to backfill the versioned
        occupied-interval facts without rewriting the immutable artifact.

    Returns
    -------
    dict
        Checkpoint data with output file paths.
    """
    def _progress(message: str) -> None:
        print(f"[progress] {message}", file=sys.stdout, flush=True)

    state.write_stage(StageName.EXPORT, {}, status=StageStatus.RUNNING)
    _progress("Klarmaker eksport: laster plan og forbereder filer")

    plan_dict = plan_checkpoint.get("plan", {})
    if not plan_dict:
        reason = "Ingen plan funnet i Stage 3 checkpoint — kjør Stage 3 først."
        state.write_stage(StageName.EXPORT, {}, status=StageStatus.FAILED)
        if strict:
            raise Stage4Error(reason)
        return {}

    effective_config: dict[str, Any] = dict(effective_config_override or {})
    if not effective_config and use_pipeline_metadata:
        try:
            effective_config = load_effective_config(state)
        except Exception:
            effective_config = {}

    try:
        from .run_manifest import RunManifest

        source_run_id = RunManifest(state.work_dir).read().get("run_id")
    except Exception:
        source_run_id = None

    # Hard verification boundary (issue #309): whatever candidate is about to
    # be materialized into every export format must independently re-verify
    # clean *here*, at the one chokepoint every caller of this function goes
    # through -- the interactive pipeline's own pre-export gate
    # (`cli.pipeline_orchestrator.verification._assert_hard_verification_before_export`)
    # only covers the guarded `run`/`run --interactive` CLI paths, not a
    # direct `python3 -m tournament_scheduler.pipeline.stage4_export`
    # invocation or a future caller that resumes/regenerates an export from
    # an on-disk checkpoint that was mutated (e.g. by the Stage 3 v2
    # optimizer) after it was last verified. Self-consistency checks
    # (duplicate participation, duplicate team in a tournament, age-group
    # mismatch) need no `problem` and always run. The problem-dependent hard
    # checks (arena interval conflicts, banned/locked dates, excluded host
    # clubs, capacity, window bounds) previously only ran inside the
    # evidence bundle's *post-export* re-verification
    # (`cli.pipeline_orchestrator.verification._write_run_evidence_bundle`) --
    # too late to block a bad artifact from reaching disk. Reconstructing
    # the same `planning_problem` Stage 3 used and passing it here closes
    # that bypass: any caller that reaches this `run()`, guarded CLI path or
    # not, now gets the full verifier at the point that actually matters.
    export_problem = verification_problem
    if export_problem is None and use_pipeline_metadata:
        export_problem = _build_export_verification_problem(effective_config, state)

    # Placement-state normalization: whatever reaches the exporters must be a
    # real schedule. A tournament whose concrete placement provably collides
    # with a trusted/fixed external booking, or a legacy placeholder from an
    # exhausted slot search, is moved out of `plan.tournaments` into a durable
    # `unresolved_tournament_placements` obligation before verification and
    # every export format. Operator-approved placements are confirmation and
    # are never demoted.
    normalization_report: dict[str, Any] = {}
    if allow_placement_normalization and isinstance(plan_dict, dict) and plan_dict.get("tournaments"):
        from ..placement_normalization import normalize_unplaced_placements

        plan_dict = copy.deepcopy(plan_dict)
        plan_checkpoint = {**plan_checkpoint, "plan": plan_dict}
        normalization_report = normalize_unplaced_placements(
            plan_dict,
            export_problem,
            approvals=_export_approvals(effective_config, plan_dict),
        )

    export_projection_guard: dict[str, Any] | None = None
    if canonical_schedule_plan is not None:
        export_projection_guard = assert_export_preserves_canonical_plan(
            canonical_plan=canonical_schedule_plan,
            proposed_plan=plan_dict,
            canonical_problem=export_problem,
            proposed_problem=export_problem,
            season=(plan_checkpoint.get("canonical_state") or {}).get("season"),
            canonical_revision=(plan_checkpoint.get("canonical_state") or {}).get("revision"),
            published_export=published_export_guard,
            published_canonical_plan=published_canonical_plan,
            published_canonical_problem=published_canonical_problem,
        )

    try:
        export_candidate = extract_candidate(plan_checkpoint)
    except ValueError:
        export_candidate = dict(plan_dict)
    export_verify_result = verify_candidate(export_candidate, export_problem)
    if normalization_report.get("changed"):
        from ..plan_derived_state import reconcile_plan_derived_state

        reconcile_plan_derived_state(plan_dict, export_verify_result, problem=export_problem)
    export_fingerprint = stable_payload_sha256(export_candidate.get("tournaments", []))
    # Provenance-bound verification context: persist the exact
    # problem this candidate was verified against, plus its fingerprint and
    # the source run id, so `season promote` can prove it is re-verifying the
    # same reviewed handoff instead of rebuilding a problem from whatever
    # Stage 1/2 files happen to be present later.
    verification_context = build_verification_context(
        run_id=source_run_id,
        candidate=export_candidate,
        problem=export_problem,
        verify_result=export_verify_result,
    )
    if not export_verify_result.get("ok", True):
        violations = export_verify_result.get("violations", [])
        violation_summary = "; ".join(
            f"{v.get('code')}: {v.get('message')}" for v in violations
        )
        reason = (
            "Refusing to export: candidate fails hard verification immediately "
            f"before serialization ({len(violations)} violation(s)): {violation_summary}"
        )
        state.write_stage(
            StageName.EXPORT,
            {
                "generated_at": _resolve_build_timestamp(build_timestamp).isoformat(),
                "output_files": {},
                "errors": [reason],
                "verify_result": export_verify_result,
                "export_fingerprint": export_fingerprint,
                "verification_context": verification_context,
            },
            status=StageStatus.FAILED,
        )
        _progress("Eksport avbrutt: kandidaten feiler hard verifisering")
        raise Stage4Error(reason)

    if normalization_report.get("changed") and use_pipeline_metadata:
        # Keep the workspace's selected Stage 3 candidate consistent with the
        # plan that was actually verified and exported. Placement
        # normalization is a deterministic domain-state correction, not a
        # re-plan, and promotion proves the promoted plan against the reviewed
        # export fingerprint; persisting the corrected candidate is what stops
        # a legitimately normalized handoff from looking like a changed
        # candidate at promotion time.
        try:
            state.write_stage(
                StageName.PLANNING,
                {**dict(plan_checkpoint), "plan": plan_dict},
                status=StageStatus.DONE,
            )
        except Exception as exc:  # noqa: BLE001 - export must not fail on this best-effort sync
            logger.warning("Could not persist the normalized planning checkpoint: %s", exc)

    # Canonical exports must carry the strict, fail-closed operational
    # projection. A non-canonical Stage 4 export is only a working manifest, so
    # its projection may leave an unknown duration at zero rather than refuse.
    schedule_projection = tournament_projection(
        plan_dict,
        export_problem,
        strict=canonical_schedule_plan is not None,
    )

    plan = season_plan_from_dict(plan_dict)
    export_path = Path(export_dir)
    export_path.mkdir(parents=True, exist_ok=True)
    canonical_build_timestamp = _resolve_build_timestamp(build_timestamp)

    # Store the primary export path (may be flat or timestamped)
    primary_export_path = export_path
    already_timestamped = bool(_TIMESTAMP_DIR_RE.match(export_path.name))
    if timestamped_export and not already_timestamped:
        ts_dir = canonical_build_timestamp.strftime("%Y-%m-%dT%H%M")
        primary_export_path = export_path / ts_dir
        primary_export_path.mkdir(parents=True, exist_ok=True)

    errors: list[str] = []
    output_files: dict[str, str] = {}
    generated_at = canonical_build_timestamp.isoformat()
    input_path = str(effective_config.get("input_path") or "input.xlsx")
    raw_canonical_state = plan_checkpoint.get("canonical_state")
    canonical_state = raw_canonical_state if isinstance(raw_canonical_state, dict) else {}
    canonical_season = canonical_state.get("season")
    canonical_revision = canonical_state.get("revision") or canonical_state.get("fingerprint")

    # Operator approval/lock status for this canonical season.
    # Exported plans are projections of canonical schedule + decision state,
    # so the export surfaces which placements the operator already approved
    # (and which approvals have gone stale) instead of making clubs re-review
    # them.  Best-effort: a missing/unreadable canonical season just means no
    # approval overlay, never an export failure.
    approval_status: dict[str, Any] | None = None
    booking_status: dict[str, Any] | None = None
    try:
        from ..canonical_baseline import resolve_canonical_state
        from ..season_state import approval_report, booking_status_report

        plan_start = getattr(plan, "start_date", None)
        plan_end = getattr(plan, "end_date", None)
        if plan_start is not None and plan_end is not None:
            resolved = resolve_canonical_state(effective_config, plan_start, plan_end)
            if resolved:
                approval_status = approval_report(resolved["season"], root=resolved["root"])
                booking_status = booking_status_report(season=resolved["season"], root=resolved["root"])
    except Exception:
        approval_status = None
        booking_status = None

    if plan_dict.get("placeholder") == "not_started" or (plan_checkpoint.get("not_started") and not plan.tournaments):
        message = str(plan_dict.get("message") or NOT_STARTED_MESSAGE)
        _progress("Genererer tomme ikke-begynt-filer")
        output_files = _write_not_started_exports(primary_export_path, basename, message)
        _normalize_export_workbooks(primary_export_path, canonical_build_timestamp)
        lifecycle_manifest = None
        pruned_exports: list[str] = []
        if _TIMESTAMP_DIR_RE.match(primary_export_path.name):
            lifecycle_manifest = write_draft_manifest(
                primary_export_path,
                export_id=primary_export_path.name,
                generated_at=generated_at,
                export_fingerprint=export_fingerprint,
                source_run_id=source_run_id,
                canonical_season=canonical_season,
                canonical_revision=canonical_revision,
                supersedes=supersedes,
                schedule_projection=schedule_projection,
            )
            output_files["export_manifest"] = str(primary_export_path / EXPORT_LIFECYCLE_FILENAME)
            pruned_exports = _prune_old_exports(primary_export_path.parent)
        checkpoint = {
            "generated_at": generated_at,
            "input_path": input_path,
            "export_dir": str(primary_export_path),
            "output_files": output_files,
            "errors": [],
            "not_started": True,
            "message": message,
            "export_fingerprint": export_fingerprint,
            "verification_context": verification_context,
            "public_export_context": public_export_context,
            "reviewed_plan": dict(plan_dict),
            "canonical_season": canonical_season,
            "canonical_revision": canonical_revision,
            "export_lifecycle": lifecycle_manifest,
            "supersedes": supersedes,
            "pruned_exports": pruned_exports,
            "export_projection_guard": export_projection_guard,
        }
        state.write_stage(StageName.EXPORT, checkpoint, status=StageStatus.DONE)
        _progress("Eksport ferdig")
        return checkpoint

    round_length_for_age_group: dict[str, int] = dict(effective_config.get("round_length_minutes", {}))
    ice_time_for_age_group: dict[str, int] = dict(effective_config.get("ice_time_minutes") or effective_config.get("round_length_minutes", {}))
    # Feasible-round-adapted occupancy (issue #473): the nominal per-age-group
    # round count, used by `occupancy.py` to reduce the booked window when a
    # tournament's actual round count falls short of the configured format.
    rounds_per_tournament_for_age_group: dict[str, int] = dict(effective_config.get("rounds_per_tournament", {}))
    if not rounds_per_tournament_for_age_group and isinstance(export_problem, dict):
        for age_group, rounds in (export_problem.get("rounds_per_tournament") or {}).items():
            try:
                rounds_per_tournament_for_age_group[str(age_group)] = int(rounds)
            except (TypeError, ValueError):
                continue
    # The projection's occupied interval is derived from the export problem's
    # ice-time contract. When the pipeline config does not carry ice times (for
    # example a canonical `season export` with an explicit verification
    # problem), the printed end times must still agree with that contract
    # rather than rendering empty and failing artifact parity.
    if not ice_time_for_age_group and isinstance(export_problem, dict):
        for age_group, minutes in (export_problem.get("ice_time_minutes") or {}).items():
            try:
                ice_time_for_age_group[str(age_group)] = int(minutes)
            except (TypeError, ValueError):
                continue
    # issue #314: a fresh recomputation is authoritative even when it comes
    # back empty. A truthiness fallback here (`derived_collisions or
    # stored`) cannot tell "not recomputed" from "recomputed and zero", so a
    # freshly verified `[]` used to silently resurrect a stale collision
    # list carried over from an earlier candidate/baseline.
    stored_collisions = find_arena_interval_collisions(
        plan.tournaments,
        ice_time_for_age_group,
        rounds_per_tournament=rounds_per_tournament_for_age_group,
        round_length_minutes=round_length_for_age_group,
    )
    # Enrich planner-stored collision dicts with the host club from the plan
    # (they may omit it) so the manual view can name who must act.
    host_by_tournament_id = {
        tournament.id: tournament.host_club for tournament in plan.tournaments if tournament.host_club
    }
    collision_entries: list[dict[str, str]] = []
    for collision in stored_collisions:
        item = dict(collision)
        if not item.get("host_club"):
            item["host_club"] = host_by_tournament_id.get(str(item.get("tournament_id", "")))
        item["type"] = item.get("type", "Arena-/tidskollisjon")
        item["category"] = item.get("category", "arena_collision")
        collision_entries.append(item)
    # A `manual_booking_reason` does not by itself mean the calendar could not
    # be verified. When a structured unresolved placement
    # or external-conflict record already owns the intervention, do not also
    # emit a spurious "calendar not verified" finding for the same
    # tournament -- one intervention, one category.
    tournament_id_by_age_and_date = {
        (tournament.age_group, tournament.date.isoformat()): tournament.id
        for tournament in plan.tournaments
        if tournament.manual_booking_reason
    }
    tournament_by_id = {tournament.id: tournament for tournament in plan.tournaments}
    structured_manual_tournament_ids = {
        tournament_id_by_age_and_date.get(
            (str(item.get("age_group") or ""), str(item.get("date") or ""))
        )
        for item in getattr(plan, "unresolved_tournament_placements", None) or []
    }
    structured_manual_tournament_ids.discard(None)
    structured_manual_tournament_ids.update(
        str(item.get("tournament_id") or "")
        for item in getattr(plan, "unresolved_external_conflicts", None) or []
    )
    # Clubs whose calendar source could not be scraped still receive their
    # proportional share of home tournaments; those tournaments are marked on
    # the plan (Tournament.manual_booking_reason) because the auto-assigned
    # start time cannot be validated against the real hall calendar — the istid
    # must be booked/verified by hand. Surface them in the same manual view.
    manual_host_entries: list[dict[str, str]] = []
    for tournament in plan.tournaments:
        if not tournament.manual_booking_reason:
            continue
        if tournament.id in structured_manual_tournament_ids:
            continue
        interval = tournament.start_time or tournament.date.isoformat()
        manual_host_entries.append(
            {
                "type": "Kalender utilgjengelig — istid må bookes manuelt",
                "category": "manual_calendar_verification",
                "date": tournament.date.isoformat(),
                "arena": tournament.arena,
                "host_club": tournament.host_club or "",
                "age_group": tournament.age_group,
                "tournament_id": tournament.id,
                "interval": interval,
                "conflicting_tournament_id": "",
                "conflicting_age_group": "",
                "conflicting_interval": "",
                "message": tournament.manual_booking_reason,
            }
        )
    # issue #266 P0: a club x age-group hosting obligation the planner
    # could not resolve after exhausting every legitimate availability tier
    # -- no tournament exists to attach this to (that's the point: nothing
    # was placed), so most tournament-shaped fields are left blank. The
    # club must make room for a slot; do not treat another club's hosting
    # as satisfying this obligation.
    unresolved_hosting_entries: list[dict[str, str]] = []
    for item in getattr(plan, "unresolved_hosting_obligations", None) or []:
        club = str(item.get("club", "") or "")
        age_group = str(item.get("age_group", "") or "")
        reason = str(item.get("reason", "") or "")
        # issue #329: both the same-age (host swap on a tournament the club
        # already participates in) and cross-age (repurpose a surplus
        # hosting slot) repairs were already tried before this obligation
        # was accepted as unresolved -- surface that, plus the registered
        # team count, so the row is actionable rather than a bare "manual
        # placement required" with no context.
        registered_team_count = item.get("registered_team_count")
        team_count_clause = (
            f"Registered teams: {registered_team_count}. " if registered_team_count is not None else ""
        )
        same_age_tried = len(item.get("same_age_reallocation_candidates") or [])
        cross_age_tried = len(item.get("candidate_reallocation_slots") or [])
        repair_clause = (
            f"Same-age host-swap candidates considered and rejected: {same_age_tried}. "
            f"Cross-age reallocation candidates considered and rejected: {cross_age_tried}. "
        )
        unresolved_hosting_entries.append(
            {
                "type": "MANUAL PLACEMENT REQUIRED — manglende vertskap",
                "category": item.get("category", "manual_hosting_obligation"),
                "date": "",
                "arena": "",
                "host_club": club,
                "age_group": age_group,
                "tournament_id": "",
                "interval": "",
                "conflicting_tournament_id": "",
                "conflicting_age_group": "",
                "conflicting_interval": "",
                "message": (
                    "MANUAL PLACEMENT REQUIRED. "
                    f"Club: {club}. Age group: {age_group}. {team_count_clause}"
                    f"Reason: {reason or 'required hosting obligation has no verified feasible automatic slot'}. "
                    f"{repair_clause}"
                    f"Action: {club}/RVV must provide or confirm a suitable {age_group} tournament slot."
                ),
            }
        )
    tournament_placement_entries = build_tournament_placement_entries(plan)
    # An unresolved placement is the concrete intervention behind a manual
    # placeholder tournament that also carries a calendar/conflict
    # finding. Link the placement record to that tournament id so the manual
    # view groups the findings into one operator work item instead of
    # rendering the same intervention several times.
    for entry in tournament_placement_entries:
        if not entry.get("tournament_id"):
            entry["tournament_id"] = tournament_id_by_age_and_date.get(
                (str(entry.get("age_group") or ""), str(entry.get("date") or "")), ""
            )
        placement_tournament = tournament_by_id.get(entry["tournament_id"])
        if placement_tournament is None:
            continue
        if not entry.get("host_club"):
            entry["host_club"] = placement_tournament.host_club or ""
        if not entry.get("arena"):
            entry["arena"] = placement_tournament.arena
        if not entry.get("interval"):
            entry["interval"] = placement_tournament.start_time or placement_tournament.date.isoformat()
    # Render the canonical conflict-aware candidate weekends
    # (``candidate_weekends``) for each unresolved placement. This consumes
    # the same evidence-only provider the Stage 3 decision context exposes; it
    # never generates a placement or changes schedule state.
    candidate_weekends_by_tournament: dict[str, dict] = {}
    if export_problem:
        try:
            from ..host_placement_repair import collect_candidate_weekend_evidence

            for bundle in collect_candidate_weekend_evidence(plan_dict, export_problem):
                key = str(bundle.get("tournament_id") or "") or str(bundle.get("finding_id") or "")
                if key:
                    candidate_weekends_by_tournament[key] = bundle
        except Exception:  # noqa: BLE001 - evidence is best-effort, never blocks export
            candidate_weekends_by_tournament = {}
    # Genuine external calendar conflicts the planner/optimizer couldn't route
    # around (see planning_contract.verify_candidate's manual_external_conflict_placements).
    external_conflict_entries: list[dict[str, str]] = []
    for item in getattr(plan, "unresolved_external_conflicts", None) or []:
        conflict_host_club = str(item.get("host_club", "") or "")
        conflict_reason = str(item.get("reason", "") or "")
        conflict_tournament = tournament_by_id.get(str(item.get("tournament_id", "") or ""))
        conflict_arena = conflict_tournament.arena if conflict_tournament else ""
        conflict_interval = (
            (conflict_tournament.start_time or conflict_tournament.date.isoformat())
            if conflict_tournament
            else ""
        )
        # Every manual row needs a concrete operator action, not just a
        # description of the conflict.
        action = (
            f"Handling: {conflict_host_club or 'RVV'} må bekrefte eller flytte "
            "istiden manuelt for å løse den eksterne kalenderkonflikten."
        )
        external_conflict_entries.append(
            {
                "type": "MANUAL PLACEMENT REQUIRED — ekstern kalenderkonflikt",
                "category": item.get("category", "manual_external_conflict"),
                "date": str(item.get("date", "") or ""),
                "arena": conflict_arena,
                "host_club": conflict_host_club,
                "age_group": str(item.get("age_group", "") or ""),
                "tournament_id": str(item.get("tournament_id", "") or ""),
                "interval": conflict_interval,
                "conflicting_tournament_id": "",
                "conflicting_age_group": "",
                "conflicting_interval": "",
                "message": f"{conflict_reason} {action}".strip(),
            }
        )
    # Participation-target deviations are planning-quality signals, not booking work,
    # so they stay out of the table above but are still rendered in their own page
    # section (`_participation_section_html`) since `publication_readiness` requires it.
    candidate_entries = (
        collision_entries + manual_host_entries + unresolved_hosting_entries
        + external_conflict_entries + tournament_placement_entries
    )
    manual_entries = [entry for entry in candidate_entries if entry.get("category") in MANUAL_SCHEDULE_CATEGORIES]
    participation_entries = list(getattr(plan, "unresolved_participation_shortfalls", None) or [])
    # Explicit operator waivers this plan relies on -- rendered as their own
    # section so a waived exception stays visibly distinct from a clean pass.
    waiver_entries = list(getattr(plan, "operator_waivers", None) or [])
    # Fresh recomputation always wins, including the empty case -- a stale
    # stored collision list must not survive a verified zero-collision plan.
    plan.arena_day_collisions = collision_entries
    if collision_entries:
        first = collision_entries[0]
        detail = first.get("message") if isinstance(first, dict) else str(first)
        logger.warning(
            "%d arena-/dagskollisjon(er) krever manuell oppfølging (eksport fortsetter): %s",
            len(collision_entries),
            detail,
        )
    if manual_host_entries:
        logger.warning(
            "%d turnering(er) hos klubb(er) uten tilgjengelig kalender er merket for manuell istidsbooking: %s",
            len(manual_host_entries),
            ", ".join(sorted({str(e.get("host_club", "")) for e in manual_host_entries})),
        )
    configured_age_groups = list(dict.fromkeys(effective_config.get("age_groups", [])))
    if not configured_age_groups and not effective_config.get("age_groups_from_input", False):
        configured_age_groups = sorted({t.age_group for t in plan.tournaments})

    # --- Excel ---
    try:
        _progress("Eksporterer Excel-arbeidsbok")
        excel_path = str(primary_export_path / f"{basename}.xlsx")
        rules_report = plan_checkpoint.get("rules_report")
        SeasonPlanExporter().export(
            plan,
            excel_path,
            rules_report=rules_report,
            round_length_for_age_group=round_length_for_age_group,
            ice_time_for_age_group=ice_time_for_age_group,
            rounds_per_tournament_for_age_group=rounds_per_tournament_for_age_group,
            decision_status={"approval": approval_status, "booking": booking_status},
            season_metadata={
                "season": canonical_season,
                "canonical_revision": canonical_revision,
                "export_fingerprint": export_fingerprint,
                "generated_at": generated_at,
            },
        )
        output_files["excel"] = excel_path
    except Exception as exc:  # noqa: BLE001
        errors.append(f"Excel-eksport feilet: {exc}")

    # --- iCal ---
    try:
        _progress("Eksporterer iCal-feed")
        ical_path = str(primary_export_path / f"{basename}.ics")
        ICalExporter(
            round_length_for_age_group=round_length_for_age_group,
            ice_time_for_age_group=ice_time_for_age_group,
            rounds_per_tournament_for_age_group=rounds_per_tournament_for_age_group,
        ).export_tournament_summary(plan, ical_path)
        output_files["ical"] = ical_path
    except Exception as exc:  # noqa: BLE001
        errors.append(f"iCal-eksport feilet: {exc}")

    # --- CSV ---
    try:
        _progress("Eksporterer CSV-filer")
        csv_path = str(primary_export_path / f"{basename}.csv")
        games_path, overview_path = CsvExporter().export(plan, csv_path)
        output_files["csv_games"] = games_path
        output_files["csv_overview"] = overview_path
    except Exception as exc:  # noqa: BLE001
        errors.append(f"CSV-eksport feilet: {exc}")

    # --- HTML export + manual-schedule view ---
    # Shared metadata is prepared up front so both the season-plan/report pages and
    # the manual-schedule view (written when collisions or calendar-less hosts
    # remain) can use it.
    html_path = str(primary_export_path / f"{basename}.html")
    pipeline_meta: dict[str, Any] = {
        "generated_at": generated_at,
        "input_path": input_path,
        "input_file": Path(input_path).name,
        "canonical_season": canonical_season,
        "canonical_revision": canonical_revision,
        "export_fingerprint": export_fingerprint,
        "approval_status": approval_status,
        "booking_status": booking_status,
    }
    # Immutable public/source presentation context. The normal pipeline captures
    # it from the live workspace once, here; a canonical `season export` passes
    # the context promoted with the season so this never re-reads mutable
    # `.pipeline` scrape state. It is provenance-bound to the reviewed handoff
    # by `fingerprint_public_export_context`.
    if public_export_context is None and use_pipeline_metadata:
        _progress("Samler pipeline-metadata for rapporten")
        try:
            public_export_context = build_public_export_context(
                state, effective_config, generated_at=generated_at
            )
        except Exception as exc:  # noqa: BLE001 - never block export on presentation context
            logger.warning("Kunne ikke bygge public export context: %s", exc)
            public_export_context = None
    export_context = public_export_context if isinstance(public_export_context, dict) else {}
    public_export_context_fingerprint = fingerprint_public_export_context(export_context)
    scrape_context = export_context.get("scrape") if isinstance(export_context.get("scrape"), dict) else {}
    if scrape_context:
        pipeline_meta["source_count"] = int(scrape_context.get("source_count") or 0)
        pipeline_meta["total_events"] = int(scrape_context.get("total_events") or 0)
        pipeline_meta["blocked"] = list(scrape_context.get("blocked") or [])
        updated = str(scrape_context.get("updated_at") or "")
        if updated:
            pipeline_meta["scrape_updated_at"] = updated
            scrape_age = _compute_scrape_age(updated)
            if scrape_age:
                pipeline_meta["scrape_age"] = scrape_age
    configured_start = effective_config.get("start_date")
    configured_end = effective_config.get("end_date")
    if configured_start or configured_end:
        pipeline_meta["date_range"] = f"{configured_start or ''} &ndash; {configured_end or ''}"
    context_age_groups = export_context.get("age_groups")
    if context_age_groups or configured_age_groups:
        pipeline_meta["age_groups"] = list(context_age_groups or configured_age_groups)

    _calendars_path: str | None = None
    _input_html_path: str | None = None
    # --- Input viewer (input.html) — public overview of registered clubs/teams ---
    # Generated before the calendar viewer so calendars.html's navbar can link to it.
    # The context's input snapshot is only present when Stage 1 recorded a real
    # input workbook (the whitelisted "Lag" sheet), so skipping Stage 1 never
    # accidentally picks up an unrelated input.xlsx from cwd.
    input_context = export_context.get("input") if isinstance(export_context.get("input"), dict) else None
    if input_context is not None:
        if input_context.get("file_name"):
            pipeline_meta["input_file"] = str(input_context["file_name"])
        try:
            _progress("Genererer oversikt over påmeldte lag")
            _generate_input_html(
                teams=list(input_context.get("teams") or []),
                export_dir=str(primary_export_path),
            )
            _input_html_path = str(primary_export_path / "input.html")
            output_files["input_html"] = _input_html_path
        except Exception as exc:  # noqa: BLE001
            errors.append(f"Input-visning feilet: {exc}")

        activities_payload = export_context.get("activities")
        if isinstance(activities_payload, dict) and activities_payload:
            try:
                _progress("Genererer aktivitetskalender")
                output_files.update(
                    write_activity_artifacts_from_payload(
                        activities_payload, export_dir=str(primary_export_path)
                    )
                )
            except Exception as exc:  # noqa: BLE001
                errors.append(f"Aktivitetskalender feilet: {exc}")
    # --- Calendar viewer (calendars.html) ---
    # Generate before HtmlExporter so calendars_path can be passed in and the navbar can link to it.
    # Only generate when the snapshot actually has events/sources — without it the file would be
    # empty and the navbar link would be broken.
    calendar_context = export_context.get("calendar") if isinstance(export_context.get("calendar"), dict) else None
    if calendar_context and (
        int(calendar_context.get("total_events") or 0) > 0
        or int(calendar_context.get("source_count") or 0) > 0
    ):
        try:
            _progress("Genererer kalenderoversikt")
            _generate_calendars_html(
                data=calendar_context,
                confidence=export_context.get("scrape_confidence"),
                export_dir=str(primary_export_path),
            )
            _calendars_path = str(primary_export_path / "calendars.html")
            output_files["calendars_html"] = _calendars_path
        except Exception as exc:  # noqa: BLE001
            errors.append(f"Kalendervisning feilet: {exc}")

    # --- Main HTML pages (season_plan.html + season_plan_report.html) ---
    _manual_schedule_path: str | None = None
    try:
        _progress("Genererer HTML-rapport")
        HtmlExporter().export(
            plan,
            html_path,
            output_files=output_files,
            pipeline_meta=pipeline_meta,
            ice_time_for_age_group=ice_time_for_age_group,
            round_length_for_age_group=round_length_for_age_group,
            rounds_per_tournament_for_age_group=rounds_per_tournament_for_age_group,
            age_groups=configured_age_groups,
            calendars_path=_calendars_path,
            input_html_path=_input_html_path,
        )
        output_files["html"] = html_path
        output_files["html_report"] = str(Path(html_path).with_name(f"{Path(html_path).stem}_report{Path(html_path).suffix}"))
    except Exception as exc:  # noqa: BLE001
        errors.append(f"HTML-eksport feilet: {exc}")

    # --- Manual-schedule view (manual_schedule.html) ---
    # Anything that cannot be treated as auto-confirmed hall time goes here:
    # arena/sequence collisions that could not be placed, plus tournaments
    # hosted by clubs whose calendar could not be scraped (provisional istid).
    if (manual_entries or participation_entries or waiver_entries) and not errors:
        try:
            _progress("Genererer manuell-oppfølgingsvisning")
            _manual_path = primary_export_path / MANUAL_SCHEDULE_FILENAME
            manual_html = _manual_schedule_html(
                plan,
                manual_entries=manual_entries,
                candidate_weekends_by_tournament=candidate_weekends_by_tournament,
                participation_entries=participation_entries,
                waiver_entries=waiver_entries,
                generated_at=generated_at,
                input_path=input_path,
                date_range=str(pipeline_meta.get("date_range", "")),
                source_count=int(pipeline_meta.get("source_count", 0)),
                event_count=int(pipeline_meta.get("total_events", 0)),
                blocked=pipeline_meta.get("blocked", []),
                scrape_age=str(pipeline_meta.get("scrape_age", "")),
                calendars_href="calendars.html" if (_calendars_path and os.path.exists(_calendars_path)) else "",
                season_plan_href="season_plan.html",
                report_href="season_plan_report.html",
                input_href="input.html" if (_input_html_path and os.path.exists(_input_html_path)) else "",
            )
            _manual_path.write_text(manual_html, encoding="utf-8")
            _manual_schedule_path = str(_manual_path)
            output_files["manual_schedule"] = _manual_schedule_path
        except Exception as exc:  # noqa: BLE001
            errors.append(f"Manuell-oppfølgingsvisning feilet: {exc}")

    # Re-export the season_plan/report pages when the manual view was written so
    # their navbars pick up the manual-schedule link (the manual page links back).
    if _manual_schedule_path and not errors:
        try:
            _progress("Genererer HTML-rapport med manuell-lenke")
            HtmlExporter().export(
                plan,
                html_path,
                    output_files=output_files,
                pipeline_meta=pipeline_meta,
                ice_time_for_age_group=ice_time_for_age_group,
                round_length_for_age_group=round_length_for_age_group,
                rounds_per_tournament_for_age_group=rounds_per_tournament_for_age_group,
                age_groups=configured_age_groups,
                calendars_path=_calendars_path,
                input_html_path=_input_html_path,
                manual_schedule_path=_manual_schedule_path,
            )
            output_files["html"] = html_path
            output_files["html_report"] = str(Path(html_path).with_name(f"{Path(html_path).stem}_report{Path(html_path).suffix}"))
        except Exception as exc:  # noqa: BLE001
            errors.append(f"HTML-eksport (manuell-oppfølgingsvisning) feilet: {exc}")

    # --- Spond ---
    try:
        _progress("Genererer Spond-eksport")
        spond_path = str(primary_export_path / f"{basename}_spond.xlsx")
        schedule_path = str(primary_export_path / f"{basename}_spond_games.xlsx")
        exporter = SpondExporter()
        exporter.export(
            plan,
            spond_path,
            round_length_for_age_group=round_length_for_age_group,
            ice_time_for_age_group=ice_time_for_age_group,
            rounds_per_tournament_for_age_group=rounds_per_tournament_for_age_group,
        )
        exporter.export_schedule_attachment(
            plan,
            schedule_path,
            round_length_for_age_group=round_length_for_age_group,
            ice_time_for_age_group=ice_time_for_age_group,
            rounds_per_tournament_for_age_group=rounds_per_tournament_for_age_group,
        )
        output_files["spond"] = spond_path
        output_files["spond_games"] = schedule_path
    except Exception as exc:  # noqa: BLE001
        errors.append(f"Spond-eksport feilet: {exc}")

    # --- Per-club review packets ---
    try:
        _progress("Genererer klubbreview-pakker")
        review_dir = primary_export_path / "review_packets"
        clubs = sorted({team.club for tournament in plan.tournaments for team in tournament.teams})
        ReviewPacketExporter().export(
            plan,
            review_dir,
            clubs=clubs,
            round_length_for_age_group=round_length_for_age_group,
            ice_time_for_age_group=ice_time_for_age_group,
            rounds_per_tournament_for_age_group=rounds_per_tournament_for_age_group,
        )
        output_files["review_packets"] = str(review_dir)
    except Exception as exc:  # noqa: BLE001
        errors.append(f"Review-pakker feilet: {exc}")

    try:
        _normalize_export_workbooks(primary_export_path, canonical_build_timestamp)
    except Exception as exc:  # noqa: BLE001
        errors.append(f"Normalisering av Excel-filer feilet: {exc}")

    # Artifact-parity preflight: verify the actual generated XLSX/HTML bytes
    # against each other and the frozen canonical projection before the export
    # is considered complete. A FAIL or NOT_CHECKABLE result blocks the export
    # (and therefore publication); it is never a silent pass. The export
    # manifest is deliberately not consulted here -- it is written below from
    # the same facts, so reading it would only add a second authority.
    #
    # The standard season-plan workbook+page are always expected together, so a
    # missing half (an exporter that returned without writing, or a file that
    # disappeared) is still verified and persisted as ``NOT_CHECKABLE`` instead
    # of silently skipping parity. A non-standard/legacy bundle keeps the old
    # "only when both halves exist" behavior.
    export_parity: dict[str, Any] | None = None
    expected_season_pair = basename == DEFAULT_BASENAME
    pair_present = (primary_export_path / f"{basename}.xlsx").exists() and (
        primary_export_path / f"{basename}.html"
    ).exists()
    if (not errors and pair_present) or expected_season_pair:
        try:
            from .export_parity import verify_export_parity, write_parity_report

            export_parity = verify_export_parity(
                primary_export_path,
                basename=basename,
                required_canonical_revision=str(canonical_revision or ""),
                manifest={},
                expected_projection=schedule_projection,
                checked_at=generated_at,
            )
            write_parity_report(primary_export_path, export_parity)
            if export_parity["status"] == "FAIL":
                details = [
                    str(reason.get("message") or reason)
                    for reason in export_parity.get("reasons", [])
                ]
                errors.append(
                    f"XLSX/HTML-artefaktparitet: {export_parity['status']} — "
                    + "; ".join(details[:5])
                )
            elif export_parity["status"] == "NOT_CHECKABLE":
                # Not a silent pass: the status is persisted and the publication
                # preflight re-verifies it and blocks a non-PASS pair. A missing
                # half of the expected season pair is an export failure so a
                # canonical export cannot complete with an unverifiable pair; a
                # legacy/non-canonical export shape is still produced for review.
                missing_half = any(
                    reason.get("code") in {"artifact_missing", "artifacts_missing"}
                    for reason in export_parity.get("reasons", [])
                )
                details = [
                    str(reason.get("message") or reason)
                    for reason in export_parity.get("reasons", [])
                ]
                if expected_season_pair and missing_half:
                    errors.append(
                        f"XLSX/HTML-artefaktparitet: {export_parity['status']} — "
                        + "; ".join(details[:5])
                    )
                else:
                    logger.warning(
                        "XLSX/HTML-artefaktparitet kunne ikke verifiseres: %s",
                        "; ".join(details),
                    )
        except Exception as exc:  # noqa: BLE001 - parity must fail closed
            errors.append(f"Kunne ikke verifisere XLSX/HTML-artefaktparitet: {exc}")

    lifecycle_manifest = None
    pruned_exports: list[str] = []
    if not errors and _TIMESTAMP_DIR_RE.match(primary_export_path.name):
        try:
            lifecycle_manifest = write_draft_manifest(
                primary_export_path,
                export_id=primary_export_path.name,
                generated_at=generated_at,
                export_fingerprint=export_fingerprint,
                source_run_id=source_run_id,
                canonical_season=canonical_season,
                canonical_revision=canonical_revision,
                supersedes=supersedes,
                schedule_projection=schedule_projection,
            )
            output_files["export_manifest"] = str(primary_export_path / EXPORT_LIFECYCLE_FILENAME)
            pruned_exports = _prune_old_exports(primary_export_path.parent)
            if pruned_exports:
                _progress(f"Fjernet {len(pruned_exports)} eldre eksport(er): {', '.join(pruned_exports)}")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"Opprydding/metadata for gamle eksporter feilet: {exc}")

    checkpoint: dict[str, Any] = {
        "generated_at": generated_at,
        "input_path": input_path,
        "export_dir": str(primary_export_path),
        "output_files": output_files,
        "errors": errors,
        "arena_day_collisions": list(plan.arena_day_collisions or []),
        "manual_booking_count": len(manual_host_entries),
        "pruned_exports": pruned_exports,
        "verify_result": export_verify_result,
        "export_fingerprint": export_fingerprint,
        "verification_context": verification_context,
        "public_export_context": export_context,
        "public_export_context_fingerprint": public_export_context_fingerprint,
        "reviewed_plan": dict(plan_dict),
        "canonical_season": canonical_season,
        "canonical_revision": canonical_revision,
        "approval_status": approval_status,
        "booking_status": booking_status,
        "export_lifecycle": lifecycle_manifest,
        "supersedes": supersedes,
        "export_projection_guard": export_projection_guard,
        "export_parity": export_parity,
    }
    if normalization_report:
        checkpoint["placement_normalization"] = normalization_report
    if errors and strict:
        state.write_stage(StageName.EXPORT, checkpoint, status=StageStatus.FAILED)
        _progress("Eksport feilet")
        raise Stage4Error("\n".join(errors))

    status = StageStatus.DONE if not errors else StageStatus.FAILED
    state.write_stage(StageName.EXPORT, checkpoint, status=status)
    _progress("Eksport ferdig")
    return checkpoint


# ---------------------------------------------------------------------------
# Deserialisation
# ---------------------------------------------------------------------------


# CLI entry point — supports: python3 -m tournament_scheduler.pipeline.stage4_export
# ---------------------------------------------------------------------------

if __name__ == "__main__":  # pragma: no cover
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="Stage 4: multi-format export")
    parser.add_argument("--work-dir", default=".pipeline", help="Pipeline work directory")
    parser.add_argument("--export-dir", default="export", help="Directory for output files")
    parser.add_argument(
        "--timestamped-export",
        dest="timestamped_export",
        action="store_true",
        help="Write exports into a timestamped subfolder of --export-dir",
    )
    parser.add_argument(
        "--no-timestamped-export",
        dest="timestamped_export",
        action="store_false",
        help="Write exports flat into --export-dir",
    )
    parser.add_argument(
        "--build-timestamp",
        default=None,
        help="Canonical content timestamp (ISO-8601 or epoch seconds) for reproducible exports",
    )
    parser.set_defaults(timestamped_export=True)
    cli_args = parser.parse_args()

    from .run_log_paths import append_stage_log_line  # noqa: E402
    from .state import PipelineState, StageName  # noqa: E402

    _state = PipelineState(cli_args.work_dir)
    _plan_ckpt = _state.read_stage(StageName.PLANNING)
    if not _plan_ckpt:
        print("Stage 3 checkpoint not found — run Stage 3 first.", file=sys.stderr)
        sys.exit(1)

    try:
        _result = run(
            _plan_ckpt,
            _state,
            export_dir=cli_args.export_dir,
            timestamped_export=cli_args.timestamped_export,
            build_timestamp=cli_args.build_timestamp,
        )
        files = _result.get("output_files", {})
        print(f"Stage 4 OK — {len(files)} filer eksportert: {', '.join(files.values())}")
        # Resolved after run() so it lands in the export folder run() just used,
        # not the pre-export --export-dir/logs fallback.
        append_stage_log_line(
            _state,
            f"Stage 4 OK: {len(files)} files exported",
            preferred_export_dir=cli_args.export_dir,
        )
        sys.exit(0)
    except Stage4Error as _e:
        append_stage_log_line(_state, f"Stage 4 FAILED: {_e}", preferred_export_dir=cli_args.export_dir)
        print(str(_e), file=sys.stderr)
        sys.exit(1)
