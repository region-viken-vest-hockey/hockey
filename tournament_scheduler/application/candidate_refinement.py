"""Refine an already-finalized, unpromoted Stage 3/Stage 4 candidate.

An interactive run can review and export a hard-valid Stage 4 candidate and
then discover a localized defect (for example a genuinely unplaced hosting
obligation) during the semantic audit -- before anyone promotes the season.
The historical choices were all wrong for that situation:

- promote merely to unlock season repair/search, which falsely accepts the
  candidate as the operational baseline;
- reset Stage 3, a recovery operation;
- rerun Stages 1-4, which discards the exact reviewed candidate and repeats
  earlier work.

This module owns the missing normal path:

    finalized unpromoted candidate
      -> explicit ``refine_candidate`` session transition
      -> finding-directed repair on the exact reviewed candidate
      -> new candidate revision (fully verified)
      -> re-finalize (new candidate fingerprint)
      -> Stage 4 re-export with provenance to the superseded export

Stage 1/2 checkpoints and fingerprints are read-only inputs: refinement never
rescrapes and never rebuilds the planning baseline. The repair legality and
mutation stay in the deterministic providers (``season_maintenance``); this
module only composes the session lifecycle, the independent verifier and the
export boundary.

Semantic audit is not re-run here: the repository owns audit *context*
assembly, and the semantic verdict belongs to the active harness. The result
reports ``audit_required`` plus the fresh export fingerprint so the caller
re-runs the same audit boundary over the new export.

A candidate commit and a Stage 4 export are separable: internal convergence
epochs commit verified revisions with ``export=False`` (recording
``export_pending`` on the session) and :func:`materialize_review_export` turns
the selected candidate into exactly one review handoff at the batch boundary.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Optional

from .stage3_session import TRANSITION_APPLY_REPAIR
from .stage3_session_store import (
    Stage3SessionStore,
    extract_candidate_body,
    fingerprint_plan,
)

# The canonical maintenance dimension set, reused so an unpromoted refinement
# searches the same dimensions a promoted repair would.
DEFAULT_DIMENSIONS = ("participants", "host")


class RefinementError(RuntimeError):
    """The refinement request cannot be served safely."""


def _promoted_from_matches(
    export_fingerprint: str | None, candidate_fingerprint: str | None, season_root: str | Path
) -> bool:
    """True when this reviewed candidate is already the promoted season state."""
    root = Path(season_root)
    if not root.exists():
        return False
    for schedule_path in sorted(root.glob("*/schedule.json")):
        try:
            schedule = json.loads(schedule_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        promoted = schedule.get("promoted_from") or {}
        if export_fingerprint and promoted.get("stage4_export_fingerprint") == export_fingerprint:
            return True
        if candidate_fingerprint and promoted.get("stage3_fingerprint") == candidate_fingerprint:
            return True
    return False


def reviewed_unpromoted_candidate(
    work_dir: str | Path,
    *,
    season_root: str | Path = "season",
) -> dict[str, Any] | None:
    """Describe a finalized, exported, unpromoted Stage 3 candidate, if one exists.

    This is the lifecycle predicate behind the plain-run guard: a plain
    ``rvv-miniputt run`` that restarts/revalidates Stage 1 would invalidate
    exactly this candidate. It is deliberately conservative -- a candidate
    with no current Stage 4 export is not yet *reviewed*, and one whose
    ``promoted_from`` provenance matches the canonical season is already the
    operational baseline -- so the guard never blocks a genuinely new
    planning run.
    """
    store = Stage3SessionStore(work_dir)
    session = store.load()
    if not session.is_finalized():
        return None

    from ..pipeline.export_lifecycle import SUPERSEDED_STATUS, read_export_manifest
    from ..pipeline.state import PipelineState, StageName

    state = PipelineState(work_dir)
    export_checkpoint = state.read_stage(StageName.EXPORT) or {}
    export_dir = export_checkpoint.get("export_dir")
    export_fingerprint = export_checkpoint.get("export_fingerprint")
    if not export_dir or not export_fingerprint:
        return None

    manifest = read_export_manifest(export_dir) or {}
    lifecycle_status = manifest.get("lifecycle_status")
    if lifecycle_status == SUPERSEDED_STATUS:
        return None
    manifest_fingerprint = manifest.get("export_fingerprint")
    if manifest_fingerprint and str(manifest_fingerprint) != str(export_fingerprint):
        return None

    candidate_fingerprint = session.finalized_fingerprint or session.candidate_fingerprint
    if _promoted_from_matches(
        str(export_fingerprint) if export_fingerprint else None,
        str(candidate_fingerprint) if candidate_fingerprint else None,
        season_root,
    ):
        return None

    return {
        "run_id": session.run_id,
        "candidate_fingerprint": candidate_fingerprint,
        "finalized_revision": session.finalized_revision,
        "export_dir": str(export_dir),
        "export_id": manifest.get("export_id") or Path(export_dir).name,
        "export_fingerprint": export_fingerprint,
        "lifecycle_status": lifecycle_status,
        "published": lifecycle_status == "published",
    }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _active_run_id(work_dir: str | Path) -> str:
    try:
        from ..pipeline.run_manifest import RunManifest

        return str(RunManifest(work_dir).read().get("run_id") or "")
    except Exception:
        return ""


def refinement_findings(
    plan: Mapping[str, Any], problem: Mapping[str, Any]
) -> list[dict[str, Any]]:
    """Stable actionable findings over the unpromoted candidate."""
    from ..season_maintenance import findings_for_plan

    return findings_for_plan(plan, problem)


def refinement_options(
    plan: Mapping[str, Any],
    problem: Mapping[str, Any],
    finding_id: str,
    *,
    allow_search: bool = False,
    dimensions: Iterable[str] = DEFAULT_DIMENSIONS,
) -> dict[str, Any]:
    """Deterministic repair options for one finding on the unpromoted candidate."""
    from ..season_maintenance import repair_options_for_plan

    return repair_options_for_plan(
        plan, problem, finding_id, allow_search=allow_search, dimensions=dimensions
    )


def load_finalized_candidate(
    work_dir: str | Path, *, run_id: str | None = None
) -> tuple[Any, dict[str, Any], dict[str, Any]]:
    """Return ``(session, checkpoint, candidate_plan)`` for the reviewed candidate.

    The Stage 3 checkpoint on disk is preferred (it is what Stage 4 actually
    exported) but only when it still matches the finalized fingerprint. A
    mismatch is an explicit stale-handoff error, never a silent fallback to a
    different candidate.
    """
    store = Stage3SessionStore(work_dir)
    resolved_run_id = run_id or _active_run_id(work_dir)
    session = store.load(expected_run_id=resolved_run_id or None)
    if not session.is_finalized():
        raise RefinementError(
            "Refinement requires a finalized unpromoted Stage 3 candidate "
            f"(session status is {session.status!r})"
        )
    if not session.finalized_fingerprint:
        raise RefinementError("The finalized session records no candidate fingerprint")

    from ..pipeline.state import PipelineState, StageName

    state = PipelineState(work_dir)
    disk = state.read_stage(StageName.PLANNING) or {}
    if disk and fingerprint_plan(disk) == session.finalized_fingerprint:
        checkpoint = dict(disk)
    elif session.candidate is not None and fingerprint_plan(session.candidate) == session.finalized_fingerprint:
        checkpoint = dict(session.candidate)
    else:
        raise RefinementError(
            "The Stage 3 checkpoint no longer matches the reviewed candidate; "
            "refusing to refine a candidate that was not reviewed"
        )
    candidate = extract_candidate_body(checkpoint)
    if candidate is None:
        raise RefinementError("The reviewed Stage 3 checkpoint carries no candidate plan")
    return session, checkpoint, dict(candidate)


def _prior_export_provenance(work_dir: str | Path) -> dict[str, Any]:
    """Describe the reviewed export the refinement will supersede."""
    from ..pipeline.export_lifecycle import read_export_manifest
    from ..pipeline.state import PipelineState, StageName

    state = PipelineState(work_dir)
    export_checkpoint = state.read_stage(StageName.EXPORT) or {}
    export_dir = export_checkpoint.get("export_dir")
    manifest = read_export_manifest(export_dir) if export_dir else None
    return {
        "export_dir": str(export_dir) if export_dir else None,
        "export_id": (manifest or {}).get("export_id") or (Path(export_dir).name if export_dir else None),
        "export_fingerprint": export_checkpoint.get("export_fingerprint")
        or (manifest or {}).get("export_fingerprint"),
        "lifecycle_status": (manifest or {}).get("lifecycle_status"),
    }


def _resolve_export_root(prior_export_dir: str | None, export_dir: str | None) -> str:
    if export_dir:
        return str(export_dir)
    if prior_export_dir:
        parent = Path(prior_export_dir).parent
        if prior_export_dir != str(parent):
            return str(parent)
        return prior_export_dir
    return "export"


def refine_finalized_candidate(
    work_dir: str | Path,
    *,
    problem: Mapping[str, Any],
    option_id: str,
    finding_id: str | None = None,
    dimensions: Iterable[str] = DEFAULT_DIMENSIONS,
    dry_run: bool = False,
    export: bool = True,
    export_dir: str | None = None,
    timestamped_export: bool = True,
    strict: bool = True,
    actor: str | None = None,
    rationale: str = "",
    log_fn: Optional[Callable[[str], None]] = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    """Apply one verified repair to the reviewed candidate and re-export it.

    The returned mapping always carries ``ok``; a rejected option leaves both
    the session and the Stage 3/Stage 4 checkpoints completely unchanged.
    """
    log = log_fn or (lambda _message: None)
    session, checkpoint, candidate = load_finalized_candidate(work_dir, run_id=run_id)
    before_revision = session.candidate_revision
    before_fingerprint = session.finalized_fingerprint or session.candidate_fingerprint

    prior_export = _prior_export_provenance(work_dir)
    if prior_export.get("lifecycle_status") == "published":
        raise RefinementError(
            "The reviewed export is published; use the publication/rollback boundary "
            "instead of refining over a live public projection"
        )

    from ..season_maintenance import apply_repair_to_plan, baseline_for_plan

    applied = apply_repair_to_plan(
        candidate,
        dict(problem),
        option_id,
        finding_id=finding_id,
        dimensions=dimensions,
        baseline=baseline_for_plan(candidate),
    )
    if not applied.get("ok"):
        return {
            "ok": False,
            "reason": str(applied.get("reason") or "refinement_rejected"),
            "option_id": option_id,
            "finding": applied.get("finding"),
            "verification": applied.get("verification"),
            "candidate_fingerprint_before": before_fingerprint,
            "candidate_fingerprint_after": before_fingerprint,
            "session_revision_unchanged": before_revision,
        }

    result_candidate = dict(applied["candidate"])
    # The same responsibility-preserving guard the interactive Stage 3 boundary
    # applies: a repair must never move hosting burden onto a club the fairness
    # model did not assign it to. Skipped only when no planning problem exists
    # (self-consistency-only verification).
    if problem:
        from ..hosting_responsibility import unexplained_responsibility_transfers

        transfers = unexplained_responsibility_transfers(
            candidate, result_candidate, dict(problem)
        )
        if transfers:
            return {
                "ok": False,
                "reason": "unexplained_hosting_responsibility_transfer",
                "option_id": option_id,
                "finding": applied.get("finding"),
                "responsibility_transfers": transfers,
                "candidate_fingerprint_before": before_fingerprint,
                "candidate_fingerprint_after": before_fingerprint,
                "session_revision_unchanged": before_revision,
            }

    delta = dict(applied.get("delta") or {})
    after_fingerprint = fingerprint_plan(result_candidate)
    if dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "option_id": option_id,
            "finding": applied.get("finding"),
            "family": applied.get("family"),
            "candidate_fingerprint_before": before_fingerprint,
            "candidate_fingerprint_after": after_fingerprint,
            "delta": delta,
            "verification": applied.get("verification"),
            "session_revision_unchanged": before_revision,
        }

    return commit_refined_candidate(
        work_dir,
        session,
        checkpoint=checkpoint,
        candidate=result_candidate,
        before_fingerprint=before_fingerprint,
        after_fingerprint=after_fingerprint,
        source="refinement_repair",
        transition=TRANSITION_APPLY_REPAIR,
        action_id="apply_repair",
        detail={"option_id": option_id, "finding": applied.get("finding")},
        result_extra={
            "option_id": option_id,
            "finding": applied.get("finding"),
            "family": applied.get("family"),
            "delta": delta,
            "verification": applied.get("verification"),
        },
        problem=problem,
        export=export,
        export_dir=export_dir,
        timestamped_export=timestamped_export,
        strict=strict,
        actor=actor,
        rationale=rationale,
        log_fn=log,
    )


def commit_refined_candidate(
    work_dir: str | Path,
    session: Any,
    *,
    checkpoint: Mapping[str, Any],
    candidate: Mapping[str, Any],
    before_fingerprint: str,
    after_fingerprint: str,
    source: str,
    transition: str,
    action_id: str,
    detail: Mapping[str, Any],
    result_extra: Mapping[str, Any],
    problem: Mapping[str, Any],
    export: bool,
    export_dir: str | None,
    timestamped_export: bool,
    strict: bool,
    actor: str | None,
    rationale: str,
    log_fn: Callable[[str], None],
) -> dict[str, Any]:
    """Commit one candidate revision over the reviewed candidate and re-export.

    Shared by option-based refinement and frontier adoption so a candidate is
    always advanced the same way (explicit reopen -> new revision ->
    re-finalize -> Stage 4 with provenance) and a partial export can never
    silently leave the reviewed candidate replaced.
    """
    before_revision = session.candidate_revision
    prior_export = _prior_export_provenance(work_dir)
    new_checkpoint = dict(checkpoint)
    new_checkpoint["plan"] = dict(candidate)
    new_checkpoint["source"] = source
    new_checkpoint["refinement"] = {
        "from_candidate_fingerprint": before_fingerprint,
        "to_candidate_fingerprint": after_fingerprint,
        "option_id": detail.get("option_id"),
        "finding": detail.get("finding"),
        "actor": actor,
        "at": _now(),
    }

    # Explicit reopen of the reviewed candidate, then one revision-bound
    # mutation and re-finalization. Stage 1/2 evidence is never touched. The
    # candidate is committed before the regeneration attempt so a partial
    # export can never leave the reviewed candidate silently replaced.
    reopened = session.begin_refinement(
        export_provenance=prior_export,
        rationale=rationale or f"refine reviewed candidate ({source})",
        at=_now(),
    )

    from ..pipeline.state import PipelineState, StageName, StageStatus

    state = PipelineState(work_dir)
    state.write_stage(StageName.PLANNING, new_checkpoint, status=StageStatus.DONE)

    session.advance_candidate(
        new_checkpoint,
        fingerprint=after_fingerprint,
        source=source,
        transition=transition,
        action_id=action_id,
        rationale=rationale or f"refine reviewed candidate ({source})",
        at=_now(),
        extra={"detail": dict(detail)},
    )
    session.finalize(
        transition=transition,
        action_id=action_id,
        rationale=rationale or "refinement re-finalized",
        at=_now(),
    )
    # A commit without an export advances the candidate but leaves the reviewed
    # export behind: the lifecycle now owes exactly one Stage 4 handoff. A
    # commit *with* an export clears/keeps that flag through
    # :func:`_materialize_candidate_export`, which owns the successful
    # materialization boundary.
    if not export:
        session.export_pending = True
    Stage3SessionStore(work_dir).save(session)

    result: dict[str, Any] = {
        "ok": True,
        "dry_run": False,
        "session_revision_before": before_revision,
        "session_revision_after": session.candidate_revision,
        "candidate_fingerprint_before": before_fingerprint,
        "candidate_fingerprint_after": after_fingerprint,
        "refinement": reopened,
        "audit_required": True,
        **dict(result_extra),
    }

    if not export:
        result["export_required"] = True
        return result

    return _materialize_candidate_export(
        work_dir,
        session,
        new_checkpoint,
        problem=problem,
        prior_export=prior_export,
        export_dir=export_dir,
        timestamped_export=timestamped_export,
        strict=strict,
        log_fn=log_fn,
        result=result,
    )


def _materialize_candidate_export(
    work_dir: str | Path,
    session: Any,
    checkpoint: Mapping[str, Any],
    *,
    problem: Mapping[str, Any],
    prior_export: Mapping[str, Any],
    export_dir: str | None,
    timestamped_export: bool,
    strict: bool,
    log_fn: Callable[[str], None],
    result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Materialize the current candidate as exactly one Stage 4 review export.

    Shared by option-based refinement (which exports inline) and batched
    convergence (which commits many internal revisions and exports once at the
    batch boundary). The verified candidate is already persisted before this
    runs, so a failed export never loses it; the session keeps
    ``export_pending`` set so a later invocation knows one handoff is still
    owed, and the prior reviewed export is never superseded by a failed
    generation.
    """
    from ..pipeline.state import PipelineState

    payload: dict[str, Any] = dict(result or {})
    state = PipelineState(work_dir)
    try:
        export_result = _reexport_refined_candidate(
            state,
            checkpoint,
            problem=dict(problem),
            prior_export=prior_export,
            export_dir=export_dir,
            timestamped_export=timestamped_export,
            strict=strict,
            log_fn=log_fn,
        )
    except Exception as exc:  # noqa: BLE001 - the committed candidate survives
        session.export_pending = True
        Stage3SessionStore(work_dir).save(session)
        payload.update(
            {
                "ok": False,
                "reason": "export_failed",
                "candidate_committed": True,
                "export_error": str(exc),
                "export_required": True,
            }
        )
        return payload

    if export_result.get("errors"):
        session.export_pending = True
        Stage3SessionStore(work_dir).save(session)
        payload.update(
            {
                "ok": False,
                "reason": "export_failed",
                "candidate_committed": True,
                "export_errors": list(export_result.get("errors") or []),
                "export_required": True,
            }
        )
        return payload

    session.export_pending = False
    session.refinement = {
        **(session.refinement or {}),
        "result_export": {
            "export_dir": export_result.get("export_dir"),
            "export_fingerprint": export_result.get("export_fingerprint"),
            "candidate_fingerprint": fingerprint_plan(checkpoint),
        },
    }
    Stage3SessionStore(work_dir).save(session)
    payload.update(
        {
            "ok": True,
            "export": export_result,
            "export_dir": export_result.get("export_dir"),
            "export_fingerprint": export_result.get("export_fingerprint"),
            "export_required": False,
        }
    )
    # A successfully re-exported candidate is a new reviewed revision: the
    # persisted audit/convergence workflow must re-enter ``audit_required`` for
    # the new export fingerprint so the run cannot complete on the superseded
    # audit. Best-effort: workflow state never blocks an already-committed
    # candidate/export.
    try:
        from .audit_lifecycle import mark_audit_required

        mark_audit_required(work_dir, run_id=session.run_id or None)
    except Exception:
        pass
    return payload


def materialize_review_export(
    work_dir: str | Path,
    *,
    problem: Mapping[str, Any],
    export_dir: str | None = None,
    timestamped_export: bool = True,
    strict: bool = True,
    log_fn: Callable[[str], None] | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    """Materialize exactly one Stage 4 review export for the current candidate.

    This is the batch/audit boundary: internal convergence revisions advance
    the verified candidate without exporting, and this function turns the
    selected candidate fingerprint into the single review handoff that the next
    semantic audit binds to. It never mutates the candidate, so a failed export
    leaves the verified revision persisted and reports ``export_required``.
    """
    log = log_fn or (lambda _message: None)
    store = Stage3SessionStore(work_dir)
    session = store.load(expected_run_id=run_id or None)
    if not session.is_finalized():
        return {
            "ok": False,
            "reason": "candidate_not_finalized",
            "export_required": True,
        }
    candidate_fingerprint = session.finalized_fingerprint or session.candidate_fingerprint
    checkpoint = _current_candidate_checkpoint(work_dir, session, candidate_fingerprint)
    if checkpoint is None:
        return {
            "ok": False,
            "reason": "candidate_checkpoint_mismatch",
            "export_required": True,
        }
    prior_export = _prior_export_provenance(work_dir)
    if prior_export.get("lifecycle_status") == "published":
        return {
            "ok": False,
            "reason": "reviewed_export_published",
            "candidate_committed": True,
            "export_required": True,
        }
    return _materialize_candidate_export(
        work_dir,
        session,
        checkpoint,
        problem=problem,
        prior_export=prior_export,
        export_dir=export_dir,
        timestamped_export=timestamped_export,
        strict=strict,
        log_fn=log,
    )


def export_is_pending(work_dir: str | Path, *, run_id: str | None = None) -> bool:
    """True when the finalized candidate is not yet the current review export.

    The canonical, process-boundary-safe trigger for the batched convergence
    materialization boundary: a candidate committed without an export leaves
    ``export_pending`` set, so a later invocation (even with zero new commits)
    knows exactly one handoff is still owed instead of either skipping it or
    re-materializing an already-exported candidate.

    The flag is reconciled against the live Stage 4 export: a crash after Stage
    4 committed the new export but before the session cleared the flag must not
    make the next invocation write a duplicate bundle.
    """
    session = Stage3SessionStore(work_dir).load(expected_run_id=run_id or None)
    if not session.export_pending:
        return False
    candidate_fingerprint = session.finalized_fingerprint or session.candidate_fingerprint
    checkpoint = _current_candidate_checkpoint(work_dir, session, candidate_fingerprint)
    if checkpoint is not None and _candidate_matches_current_export(work_dir, checkpoint):
        return False
    return True


def _candidate_matches_current_export(work_dir: str | Path, checkpoint: Mapping[str, Any]) -> bool:
    """True when the current Stage 4 export already materializes this candidate.

    Uses the same canonical candidate-payload comparison the promotion boundary
    uses (the export's content fingerprint is the hash of the candidate's
    tournament payload), so the two boundaries can never disagree about whether
    a candidate is the reviewed export.
    """
    from ..pipeline.fingerprints import stable_payload_sha256
    from ..pipeline.state import PipelineState, StageName

    export_fingerprint = str(
        (PipelineState(work_dir).read_stage(StageName.EXPORT) or {}).get("export_fingerprint") or ""
    )
    if not export_fingerprint:
        return False
    body = extract_candidate_body(checkpoint)
    if body is None:
        return False
    return stable_payload_sha256(body.get("tournaments", [])) == export_fingerprint


def _current_candidate_checkpoint(
    work_dir: str | Path, session: Any, candidate_fingerprint: str
) -> dict[str, Any] | None:
    """Return the checkpoint for the session's current finalized candidate.

    The Stage 3 checkpoint on disk is preferred (it is what Stage 4 exported)
    but only when it still matches the finalized fingerprint; otherwise the
    session's own candidate is used. Never falls back to a different candidate.
    """
    from ..pipeline.state import PipelineState, StageName

    disk = PipelineState(work_dir).read_stage(StageName.PLANNING) or {}
    if disk and fingerprint_plan(disk) == candidate_fingerprint:
        return dict(disk)
    if session.candidate is not None and fingerprint_plan(session.candidate) == candidate_fingerprint:
        # Stage 4 expects a Stage 3 checkpoint envelope, not a bare candidate
        # body, so the session fallback is wrapped the same way
        # ``commit_refined_candidate`` writes it.
        return {
            "plan": dict(session.candidate),
            "source": session.candidate_source or "convergence_repair",
        }
    return None


def _reexport_refined_candidate(
    state: Any,
    checkpoint: Mapping[str, Any],
    *,
    problem: Mapping[str, Any],
    prior_export: Mapping[str, Any],
    export_dir: str | None,
    timestamped_export: bool,
    strict: bool,
    log_fn: Callable[[str], None],
) -> dict[str, Any]:
    """Re-run Stage 4 over the refined candidate and link the new export back.

    The prior reviewed export's artifacts are never rewritten: its lifecycle
    manifest is marked superseded (and thereby protected from draft retention)
    and points at the replacement export.
    """
    from ..pipeline.export_lifecycle import mark_export_superseded
    from ..pipeline.stage4_export import run as stage4_run

    prior_dir = prior_export.get("export_dir")
    root = _resolve_export_root(prior_dir, export_dir)
    result = stage4_run(
        dict(checkpoint),
        state,
        export_dir=root,
        strict=strict,
        timestamped_export=timestamped_export,
        verification_problem=dict(problem),
        supersedes={
            "export_dir": prior_dir,
            "export_id": prior_export.get("export_id"),
            "export_fingerprint": prior_export.get("export_fingerprint"),
        }
        if prior_dir
        else None,
    )
    new_dir = result.get("export_dir")
    new_manifest = result.get("export_lifecycle") or {}
    if result.get("errors"):
        # A failed regeneration must not supersede the still-reviewed export.
        log_fn(
            "refinement: Stage 4 reported errors; prior reviewed export left current"
        )
        return result
    if prior_dir and new_dir and str(prior_dir) != str(new_dir):
        try:
            mark_export_superseded(
                prior_dir,
                superseded_by={
                    "export_dir": new_dir,
                    "export_id": new_manifest.get("export_id") or Path(new_dir).name,
                    "export_fingerprint": result.get("export_fingerprint"),
                    "candidate_fingerprint": fingerprint_plan(checkpoint),
                },
            )
        except Exception as exc:  # noqa: BLE001 - provenance is best-effort
            log_fn(f"refinement: could not mark prior export superseded: {exc}")
    return result
