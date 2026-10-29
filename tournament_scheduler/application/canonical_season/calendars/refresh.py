"""Promoted-season calendar evidence refresh orchestration."""

from __future__ import annotations

import copy
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from tournament_scheduler.booking.calendar_bookings import booking_assessment
from tournament_scheduler.booking.calendar_bookings import booking_status_report as _booking_status_report
from tournament_scheduler.booking.refresh_consequences import refresh_tournament_consequences
from tournament_scheduler.season.state import (
    canonical_state_revision,
    schedule_fingerprint,
)
from tournament_scheduler.infrastructure.canonical_calendar_snapshot_archive import (
    calendar_snapshot_content,
)
from tournament_scheduler.pipeline.fingerprints import stable_payload_sha256
from tournament_scheduler.infrastructure.canonical_season_store import (
    SeasonStateError,
)

from ..shared import (
    _operator_identity,
    _now_iso,
    _append_decision_history,
    _resolve_plan_problem,
    _parse_iso_date_for_move,
)

from .common import _AUTO_REFRESH_RECONCILE_CLUBS, _calendar_problem_payload
from .fingerprints import calendar_evidence_fingerprint, stage2_evidence_fingerprint
from .scoped_refresh import (
    classify_refresh_reconciliation,
    merge_refresh_problem,
    merge_refresh_source_evidence,
    prepare_refresh_config,
    refresh_impact_summary,
    resolve_target_club,
)
from .source_policy import _source_policy_from_evidence


def refresh_calendars(
    service,
    *,
    season: str,
    input_path: str | os.PathLike[str] = "input.xlsx",
    work_dir: str | os.PathLike[str] | None = None,
    actor: str | None = None,
    note: str = "",
    dry_run: bool = False,
    club: str | None = None,
    allow_missing_sources: bool = False,
    allow_source_policy_change: bool = False,
) -> dict[str, Any]:
    """Refresh promoted-season calendar evidence without changing the schedule.

    The promoted plan/decision state remains the operational truth.  Only
    the verification-context calendar facts are rebuilt from a fresh Stage 2
    scrape, preserving the previous evidence fingerprint in history and
    advancing the canonical-state revision on commit.

    The source configuration that produced the promoted evidence is persisted as
    a source-policy snapshot. A refresh whose current ``input.xlsx`` source
    configuration (URL, parser kind, trust/classification, coverage) differs
    from the promoted policy is refused unless ``allow_source_policy_change`` is
    set, so today's workbook configuration can never silently rewrite the
    meaning of already-promoted evidence. The complete previous calendar payload
    and source policy are archived before replacement, so an audit can
    reconstruct what evidence a refresh replaced even on the first refresh of a
    season promoted before ``calendar_evidence`` existed.
    """

    from tournament_scheduler.pipeline import stage1_config, stage2_scraping
    from tournament_scheduler.pipeline.state import PipelineState
    from tournament_scheduler.planning.contract import build_planning_problem, verify_candidate
    from tournament_scheduler.season.maintenance import list_findings

    snapshot = service.load(season)
    schedule = copy.deepcopy(snapshot.schedule)
    decisions = copy.deepcopy(snapshot.decisions)
    plan = copy.deepcopy(schedule.get("plan") or {})
    context = copy.deepcopy(schedule.get("verification_context") or {})
    problem = copy.deepcopy(context.get("problem") or {})
    if not isinstance(problem, dict) or not problem:
        raise SeasonStateError("Canonical season carries no verification-context problem to refresh")

    start = _parse_iso_date_for_move(str(problem.get("start_date") or plan.get("start_date")), "start_date")
    end = _parse_iso_date_for_move(str(problem.get("end_date") or plan.get("end_date")), "end_date")
    before_calendar = _calendar_problem_payload(problem)
    before_schedule_fingerprint = schedule_fingerprint(plan)
    before_revision = canonical_state_revision(schedule, decisions)
    fetched_at = _now_iso()
    target_club = resolve_target_club(club)
    current_evidence = context.get("calendar_evidence")
    if not isinstance(current_evidence, Mapping):
        current_evidence = None
    promoted_policy = _source_policy_from_evidence(current_evidence)
    promoted_policy_fingerprint = stable_payload_sha256(promoted_policy) if promoted_policy is not None else None
    before_fingerprint = calendar_evidence_fingerprint(problem, promoted_policy)

    def _scrape_in(workspace: Path) -> dict[str, Any]:
        state = PipelineState(workspace)
        stage1_config.run(input_path, state, strict=True)
        config = stage1_config.load_effective_config(state, input_path=input_path)
        config["start_date"] = start.isoformat()
        config["end_date"] = end.isoformat()
        # Keep the promoted planning contract's non-source facts authoritative
        # for problem reconstruction; the current workbook supplies sources.
        for key in (
            "teams",
            "age_groups",
            "parallel_games",
            "rounds_per_tournament",
            "round_length_minutes",
            "ice_time_minutes",
            "participation_targets_by_age_group",
        ):
            if key in problem:
                config[key] = copy.deepcopy(problem[key])
        current_policy, source_policy_changes = prepare_refresh_config(
            config, promoted_policy, target_club
        )
        if source_policy_changes and not allow_source_policy_change:
            return {
                "refused": True,
                "source_policy": current_policy,
                "source_policy_changes": source_policy_changes,
            }
        scrape = stage2_scraping.run(
            config,
            state,
            datetime.combine(start, datetime.min.time()),
            datetime.combine(end, datetime.min.time()),
            strict=not allow_missing_sources,
            allow_missing_sources=allow_missing_sources,
            force_refresh=True,
        )
        rebuilt = build_planning_problem(
            config,
            scrape,
            start,
            end,
            waivers=problem.get("operator_waivers") or [],
            canonical_baseline=problem.get("canonical_baseline") if isinstance(problem.get("canonical_baseline"), dict) else None,
        )
        return {
            "scrape": scrape,
            "rebuilt_problem": rebuilt,
            "source_policy": current_policy,
            "source_policy_changes": source_policy_changes,
        }

    if work_dir is None:
        with tempfile.TemporaryDirectory(prefix="rvv-calendar-refresh-") as tmp:
            scrape_result = _scrape_in(Path(tmp))
    else:
        workspace = Path(work_dir)
        workspace.mkdir(parents=True, exist_ok=True)
        scrape_result = _scrape_in(workspace)

    if scrape_result.get("refused"):
        result = {
            "season": season,
            "dry_run": bool(dry_run),
            "target_club": target_club,
            "refused": True,
            "schedule_fingerprint": before_schedule_fingerprint,
            "previous_calendar_fingerprint": before_fingerprint,
            "previous_canonical_state_revision": before_revision,
            "source_policy_fingerprint": stable_payload_sha256(scrape_result["source_policy"]),
            "previous_source_policy_fingerprint": promoted_policy_fingerprint,
            "source_policy_changes": scrape_result["source_policy_changes"],
            "refusal_reasons": ["source configuration changed since promotion"],
        }
        if not dry_run:
            raise SeasonStateError(
                "Refusing calendar evidence refresh: the configured source policy changed since "
                "promotion; pass --accept-source-policy-change to advance the source policy explicitly"
            )
        return result

    scrape = scrape_result["scrape"]
    rebuilt_problem = scrape_result["rebuilt_problem"]
    current_policy = scrape_result["source_policy"]
    source_policy_changes = scrape_result["source_policy_changes"]
    new_problem = merge_refresh_problem(problem, rebuilt_problem, target_club)
    after_calendar = _calendar_problem_payload(new_problem)
    after_fingerprint = calendar_evidence_fingerprint(new_problem, current_policy)
    source_summaries, blocked_sources, empty_sources = merge_refresh_source_evidence(
        scrape=scrape,
        current_evidence=current_evidence,
        promoted_policy=promoted_policy,
        target_club=target_club,
        fetched_at=fetched_at,
    )

    previous_snapshot = {
        "schema_version": 1,
        "captured_at": fetched_at,
        "calendar_fingerprint": before_fingerprint,
        "calendar_payload": before_calendar,
        "source_policy": promoted_policy,
        "source_policy_fingerprint": promoted_policy_fingerprint,
        "prior_calendar_evidence": copy.deepcopy(current_evidence),
    }
    snapshot_ref, snapshot_bytes = calendar_snapshot_content(previous_snapshot)

    evidence_record = {
        "schema_version": 1,
        "refreshed_at": fetched_at,
        "refreshed_by": _operator_identity(actor),
        "note": note or "",
        "input_path": str(input_path),
        "dry_run": bool(dry_run),
        "refresh_scope": {"club": target_club},
        "previous_calendar_fingerprint": before_fingerprint,
        "calendar_fingerprint": after_fingerprint,
        "stage2_fingerprint": stage2_evidence_fingerprint(scrape),
        "source_count": len(source_summaries),
        "blocked_sources": blocked_sources,
        "empty_sources": empty_sources,
        "sources": source_summaries,
        "source_policy": current_policy,
        "source_policy_fingerprint": stable_payload_sha256(current_policy),
        "previous_source_policy_fingerprint": promoted_policy_fingerprint,
        "source_policy_changes": source_policy_changes,
        "previous_snapshot": {
            "sha256": snapshot_ref["sha256"],
            "path": snapshot_ref["path"],
            "calendar_fingerprint": before_fingerprint,
            "source_policy_fingerprint": promoted_policy_fingerprint,
            "captured_at": fetched_at,
            "had_prior_calendar_evidence": current_evidence is not None,
            "had_prior_source_policy": promoted_policy is not None,
        },
    }

    preview_context = copy.deepcopy(context)
    preview_context["problem"] = new_problem
    preview_context["problem_fingerprint"] = stable_payload_sha256(new_problem)
    preview_context.setdefault("calendar_evidence_history", [])
    if current_evidence is not None:
        preview_context["calendar_evidence_history"].append(copy.deepcopy(current_evidence))
    preview_context["calendar_evidence"] = evidence_record
    preview_schedule = copy.deepcopy(schedule)
    preview_schedule["verification_context"] = preview_context
    preview_schedule["updated_at"] = fetched_at

    before_resolved_problem = _resolve_plan_problem(schedule, None, decisions)
    verification_before = verify_candidate(plan, before_resolved_problem)
    resolved_problem = _resolve_plan_problem(preview_schedule, None, decisions)
    verification = verify_candidate(plan, resolved_problem)
    findings_before = list_findings(season, root=service.store.root)

    reconcile_scope = (target_club,) if target_club else _AUTO_REFRESH_RECONCILE_CLUBS
    planned_tournament_reconciliation, reconcile_clubs_present = (
        classify_refresh_reconciliation(
            clubs=reconcile_scope,
            plan=plan,
            resolved_problem=resolved_problem,
            decisions=decisions,
            actor=actor,
            note=note,
            now=fetched_at,
            source_revision=before_revision,
        )
    )
    # Persist classification, but return the revision-bound assessment separately
    # because embedding it would change the revision to which it binds itself.
    evidence_record["planned_tournament_reconciliation"] = copy.deepcopy(
        planned_tournament_reconciliation
    )

    result = {
        "season": season,
        "dry_run": bool(dry_run),
        "target_club": target_club,
        "changed": before_fingerprint != after_fingerprint,
        "schedule_fingerprint": before_schedule_fingerprint,
        "previous_calendar_fingerprint": before_fingerprint,
        "calendar_fingerprint": after_fingerprint,
        "source_count": len(source_summaries),
        "blocked_sources": blocked_sources,
        "empty_sources": empty_sources,
        "sources": source_summaries,
        "source_policy_fingerprint": evidence_record["source_policy_fingerprint"],
        "previous_source_policy_fingerprint": promoted_policy_fingerprint,
        "source_policy_changes": source_policy_changes,
        "previous_snapshot": evidence_record["previous_snapshot"],
        **refresh_impact_summary(
            problem=problem,
            new_problem=new_problem,
            target_club=target_club,
            before_fingerprint=before_fingerprint,
            after_fingerprint=after_fingerprint,
            verification_before=verification_before,
            verification_after=verification,
            dry_run=dry_run,
        ),
        "verification_ok": bool(verification.get("ok")),
        "verification_violations": list(verification.get("violations") or []),
        "manual_external_conflict_placements": list(
            verification.get("manual_external_conflict_placements") or []
        ),
        "planned_tournament_reconciliation": {
            "clubs": planned_tournament_reconciliation["clubs"],
            "assessment": None,
        },
    }
    if dry_run:
        result["canonical_state_revision"] = before_revision
        # Read-only: both projections are computed from in-memory state, so the
        # dry run states each tournament's consequence without committing it.
        result["tournament_consequences"] = refresh_tournament_consequences(
            _booking_status_report(problem=before_resolved_problem, plan=plan, decisions=decisions),
            _booking_status_report(problem=resolved_problem, plan=plan, decisions=decisions),
        )
        if reconcile_clubs_present:
            # Nothing is committed in a dry run, so the current canonical
            # revision remains before_revision for as long as this result is
            # read -- the exact-revision binding booking_assessment() promises
            # is accurate here.
            result["planned_tournament_reconciliation"]["assessment"] = booking_assessment(
                problem=resolved_problem,
                plan=plan,
                decisions=decisions,
                canonical_state_revision=before_revision,
                season=season,
                clubs=reconcile_clubs_present,
            )
        return result

    schedule = preview_schedule
    promoted_from = dict(schedule.get("promoted_from") or {})
    promoted_from["export_stale"] = True
    promoted_from["export_stale_reason"] = "calendar evidence refreshed"
    promoted_from["export_stale_at"] = fetched_at
    schedule["promoted_from"] = promoted_from
    decisions["updated_at"] = fetched_at
    decisions["export_state"] = {
        "status": "stale",
        "stale_reason": "calendar_evidence_refreshed",
        "stale_at": fetched_at,
        "requires_fresh_export": True,
        "requires_fresh_audit": True,
        "calendar_fingerprint": after_fingerprint,
    }
    _append_decision_history(
        decisions,
        event="refresh_calendar_evidence",
        tournament_id="",
        actor=actor,
        now=fetched_at,
        note=note,
        details={
            "previous_calendar_fingerprint": before_fingerprint,
            "calendar_fingerprint": after_fingerprint,
            "stage2_fingerprint": evidence_record["stage2_fingerprint"],
            "source_count": len(source_summaries),
            "blocked_sources": blocked_sources,
            "target_club": target_club,
            "source_policy_fingerprint": evidence_record["source_policy_fingerprint"],
            "source_policy_changes": source_policy_changes,
            "previous_snapshot": evidence_record["previous_snapshot"],
            "planned_tournament_reconciliation": {
                club: {"count": entry["count"], "requires_review_count": entry["requires_review_count"]}
                for club, entry in planned_tournament_reconciliation["clubs"].items()
            },
        },
    )
    committed = service._commit(
        snapshot.with_schedule(schedule).with_decisions(decisions),
        extra_evidence={snapshot_ref["path"]: snapshot_bytes},
    )
    findings_after = list_findings(season, root=service.store.root)
    result["canonical_state_revision"] = canonical_state_revision(committed.schedule, committed.decisions)
    result["previous_canonical_state_revision"] = before_revision
    if reconcile_clubs_present:
        # Compute the crosswalk against the actually committed state so its
        # exact-revision binding matches what a caller reads immediately
        # afterward (e.g. `season status`) -- computing it beforehand would
        # bind it to before_revision, which is stale the instant this refresh
        # commits (#467 P2 review).
        committed_problem = _resolve_plan_problem(committed.schedule, None, committed.decisions)
        result["planned_tournament_reconciliation"]["assessment"] = booking_assessment(
            problem=committed_problem,
            plan=committed.schedule.get("plan") or {},
            decisions=committed.decisions,
            canonical_state_revision=result["canonical_state_revision"],
            season=season,
            clubs=reconcile_clubs_present,
        )
    result["findings_before"] = {
        "finding_count": findings_before.get("finding_count"),
        "baseline_comparison": findings_before.get("baseline_comparison"),
    }
    result["findings_after"] = {
        "finding_count": findings_after.get("finding_count"),
        "baseline_comparison": findings_after.get("baseline_comparison"),
    }
    result["export_state"] = decisions["export_state"]
    return result
