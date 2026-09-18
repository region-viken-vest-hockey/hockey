"""Bounded Pareto-convergence driver over an unpromoted reviewed candidate.

This is the outer loop the repository was missing: an audited
``REVIEW_REQUIRED`` candidate is no longer automatically a human escalation.
While repository-owned findings still map to a supported repair/search
direction, the driver keeps generating independently verified candidates and
folding them into the bounded non-dominated frontier, until the controller
reports PASS, operator-required, bounded-search-exhausted or Pareto-stable. A
configured epoch budget may pause the loop instead; the pause is persisted as
resumable state (never a terminal), so a later invocation with a larger budget
continues from the saved epoch.

Internal epochs are the optimizer's planning state, not review handoffs: every
accepted mutation is still independently verified and persisted as a new
candidate revision/frontier entry, but the driver does not materialize a
Stage 4 bundle per epoch. Exactly one timestamped Stage 4 export is produced at
the batch boundary (the single auditable handoff), and the prior semantic audit
is invalidated only by that materialization.

The deterministic providers/verifiers own legality, mutation and measurement;
:class:`~tournament_scheduler.application.pareto_convergence.ConvergenceController`
owns frontier bookkeeping and convergence criteria; this module only composes
them with the existing single-step refinement boundary. It never re-implements
a repair, a verifier or a metric.
"""

from __future__ import annotations

import hashlib
from typing import Any, Callable, Iterable, Mapping, Optional

from .pareto_convergence import (
    DEFAULT_FRONTIER_LIMIT,
    DEFAULT_MAX_EPOCHS,
    DEFAULT_MAX_NO_IMPROVEMENT_EPOCHS,
    PAUSE_BUDGET_EXHAUSTED,
    PAUSE_PAUSED,
    ArchiveEntry,
    ConvergenceController,
    ConvergenceState,
    FindingDirection,
    ParetoArchive,
    archive_entry_from_option,
    classify_finding,
    classify_findings,
    describe_pause,
    describe_terminal,
)

DEFAULT_DIMENSIONS = ("participants", "host")


def _short_hash(value: str) -> str:
    return hashlib.sha1(value.encode("utf-8")).hexdigest()[:12]


def _ref_for(direction: FindingDirection, option: Mapping[str, Any]) -> str:
    return f"pareto:{direction.direction}:{_short_hash(str(option.get('option_id') or ''))}"


def _resolved_coverage(
    report: Mapping[str, Any], direction: FindingDirection
) -> dict[str, dict[str, Any]]:
    """Resolved per-finding coverage the provider attached to its report."""
    finding = report.get("finding") or {}
    coverage = finding.get("search_coverage") if isinstance(finding, Mapping) else None
    if isinstance(coverage, Mapping) and coverage:
        return {direction.finding_id: dict(coverage)}
    return {}


def _exhausted_coverage(
    _resolved: Mapping[str, dict[str, Any]], direction: FindingDirection
) -> dict[str, Any]:
    """Coverage for a direction whose only option proved stale/unapplicable.

    The bounded search ran and produced an option, but it did not reproduce
    against the current candidate, so no usable automatic option exists for
    this candidate. Recorded as bounded exhaustion (never ``proven_infeasible``)
    so the loop does not repeat the same stale apply forever.
    """
    return {
        "status": "bounded_search_exhausted",
        "supported": [],
        "untried": [],
        "attempted": [],
        "search_requested": True,
        "proven_infeasible": False,
        "reason": "stale_or_unapplicable_option",
    }


def _load_state(work_dir: Any, run_id: str | None, frontier_limit: int) -> tuple[Any, ParetoArchive, ConvergenceState]:
    from .stage3_session_store import Stage3SessionStore

    store = Stage3SessionStore(work_dir)
    session = store.load(expected_run_id=run_id or None)
    archive = ParetoArchive.from_list(session.pareto_archive, max_size=frontier_limit)
    state = ConvergenceState.from_dict(session.convergence)
    if state.terminal_reason:
        # A terminal dominates any (stale) pause reason: phrase the report from
        # the terminal, never from a superseded budget stop.
        state.pause_reason = ""
        state.pause_detail = ""
    current = session.finalized_fingerprint or session.candidate_fingerprint
    off_frontier = bool(current) and current not in archive.fingerprints()
    if off_frontier:
        # The current baseline was not produced by this convergence frontier
        # (for example the harness applied a manual ``stage3 refine``): all
        # candidate-scoped search evidence is stale. Controller-round fairness
        # history is deliberately retained.
        state.search_coverage = {}
        state.explored_findings = []
    if state.terminal_reason and off_frontier:
        # A terminal convergence describes the candidate it stopped on. If the
        # current candidate is no longer on the retained frontier, the old
        # terminal is stale: resume exploration instead of refusing to look at
        # the changed candidate.
        state.terminal_reason = ""
        state.terminal_detail = ""
        state.pause_reason = ""
        state.pause_detail = ""
        state.no_improvement_epochs = 0
        # A manually changed baseline starts a fresh epoch budget; the
        # previous count described the candidate the loop stopped on.
        state.epoch = 0
    return store, archive, state


def _persist(
    store: Any, run_id: str | None, archive: ParetoArchive, state: ConvergenceState
) -> None:
    session = store.load(expected_run_id=run_id or None)
    if run_id:
        session.run_id = run_id
    session.pareto_archive = archive.to_list()
    session.convergence = state.to_dict()
    store.save(session)


def _retain_frontier_bodies(
    store: Any,
    run_id: str | None,
    entries: Iterable[ArchiveEntry],
    bodies: Mapping[str, Mapping[str, Any]],
) -> None:
    """Retain verified frontier candidate bodies so any frontier ref stays selectable.

    The archive holds identity + objective evidence; the bounded verified
    attempt portfolio holds the actual candidate body. Keeping both means a
    later worse attempt cannot make an earlier non-dominated candidate
    unavailable, without inventing a second candidate authority.
    """
    for entry in entries:
        body = bodies.get(entry.candidate_ref)
        if not body:
            continue
        record = {
            "candidate_ref": entry.candidate_ref,
            "candidate_revision": entry.candidate_revision,
            "candidate_fingerprint": entry.candidate_fingerprint,
            "source": str((entry.source or {}).get("family") or "pareto_convergence"),
            "search_arguments": dict(entry.source or {}),
            "hard_verification_ok": True,
            "candidate": {"plan": dict(body)},
            "objective_vector": dict(entry.objective_vector),
            "metrics": dict(entry.metrics),
            "direction": entry.direction,
            "frontier": True,
        }
        try:
            from .stage3_session_store import stage3_checkpoint_facts_fingerprint

            # The state object is not available here; the caller passes a
            # work_dir-backed store whose session already exists, so read the
            # facts identity through the store's own checkpoint projection.
            record["facts_fingerprint"] = stage3_checkpoint_facts_fingerprint(
                _state_for_store(store)
            )
        except Exception:
            # Adoption re-validates and refuses an attempt without provenance;
            # never fabricate a facts identity.
            pass
        store.retain_candidate_attempt(record, run_id=run_id)


def _state_for_store(store: Any) -> Any:
    from ..pipeline.state import PipelineState

    return PipelineState(store.work_dir)


def run_bounded_convergence(
    work_dir: Any,
    *,
    problem: Mapping[str, Any],
    dimensions: Iterable[str] = DEFAULT_DIMENSIONS,
    max_epochs: int = DEFAULT_MAX_EPOCHS,
    max_no_improvement_epochs: int = DEFAULT_MAX_NO_IMPROVEMENT_EPOCHS,
    frontier_limit: int = DEFAULT_FRONTIER_LIMIT,
    export: bool = True,
    export_dir: str | None = None,
    timestamped_export: bool = True,
    strict: bool = True,
    dry_run: bool = False,
    allow_search: bool = True,
    reconcile_budget: bool = True,
    force_finding_id: str | None = None,
    preferred_option_id: str | None = None,
    audit_payload: Mapping[str, Any] | None = None,
    run_id: str | None = None,
    finding_provider: Callable[..., list[dict[str, Any]]] | None = None,
    option_provider: Callable[..., dict[str, Any]] | None = None,
    apply_provider: Callable[..., dict[str, Any]] | None = None,
    body_provider: Callable[..., dict[str, Any]] | None = None,
    materialize_provider: Callable[..., dict[str, Any]] | None = None,
    log_fn: Optional[Callable[[str], None]] = None,
) -> dict[str, Any]:
    """Run the bounded convergence loop over one reviewed unpromoted candidate.

    Returns a truthful terminal report: ``terminal_reason`` distinguishes PASS,
    operator-required, bounded-search-exhausted and Pareto-stable; a resumable
    budget stop is reported separately as ``pause_reason`` (``budget_exhausted``
    or ``paused``) with ``paused``/``resumable`` true and an empty terminal, and
    ``terminal_detail``/``pause_detail`` never claim global optimality.
    ``frontier`` is the bounded non-dominated set the harness may select from.

    Export lifecycle: internal epochs commit verified candidate revisions with
    no Stage 4 bundle. At the batch boundary the driver materializes exactly one
    Stage 4 review export when ``export`` is requested (and, on resume, when a
    previous batch left one owed). ``export_materialized``/``audit_required``
    mean a fresh handoff exists; ``export_required`` means the verified
    candidate is persisted but not yet materialized as an auditable export.
    """
    log = log_fn or (lambda _message: None)
    if finding_provider is None or option_provider is None or apply_provider is None:
        from .candidate_refinement import (
            refinement_findings,
            refinement_options,
            refine_finalized_candidate,
        )

        finding_provider = finding_provider or refinement_findings
        option_provider = option_provider or refinement_options
        apply_provider = apply_provider or refine_finalized_candidate
    if body_provider is None:
        from ..season_maintenance import apply_repair_to_plan

        body_provider = apply_repair_to_plan
    if materialize_provider is None:
        from .candidate_refinement import materialize_review_export

        materialize_provider = materialize_review_export

    store, archive, state = _load_state(work_dir, run_id, frontier_limit)
    # A fresh REVIEW_REQUIRED audit verdict demands re-evaluation of the current
    # candidate: a convergence terminal recorded before this verdict described
    # an earlier candidate/audit and must not silently satisfy the new audit
    # cycle (including a stale ``operator_required`` question the fresh audit no
    # longer raises). The re-explored cycle gets a fresh bounded budget so the
    # audit cannot be ignored because an earlier cycle spent the epoch budget.
    if (
        audit_payload is not None
        and str(audit_payload.get("status") or "") == "REVIEW_REQUIRED"
        and state.terminal_reason
    ):
        state.terminal_reason = ""
        state.terminal_detail = ""
        state.pause_reason = ""
        state.pause_detail = ""
        state.epoch = 0
    controller = ConvergenceController(
        state,
        archive,
        max_epochs=max_epochs,
        max_no_improvement_epochs=max_no_improvement_epochs,
        frontier_limit=frontier_limit,
    )
    epochs: list[dict[str, Any]] = []
    committed = 0
    pending_force = force_finding_id
    pending_preferred_option = preferred_option_id
    audit_decision: dict[str, Any] | None = None

    while not state.is_terminal() and state.epoch < max_epochs:
        from .candidate_refinement import load_finalized_candidate

        _session, _checkpoint, candidate = load_finalized_candidate(work_dir, run_id=run_id)
        findings = finding_provider(candidate, problem)
        directions = classify_findings(findings)
        if audit_payload is not None:
            from .audit_convergence import audit_convergence_decision

            # Re-evaluate against the current candidate's repository findings:
            # a direction the audit flagged may only become actionable after an
            # earlier commit changed the candidate.
            audit_decision = audit_convergence_decision(
                audit_payload,
                repository_directions=sorted(
                    {d.direction for d in directions} | set(state.explored_directions)
                ),
            )
            # An audit finding the repository cannot act on is exactly the
            # question to ask: do not search blindly on its behalf. If nothing
            # automatic remains at all, stop immediately; otherwise carry the
            # questions through the automatic refinement and surface them at
            # the terminal.
            if audit_decision["operator_required"] and not any(d.actionable for d in directions):
                state.terminal_reason = "operator_required"
                state.terminal_detail = _operator_detail(audit_decision)
                if not dry_run:
                    _persist(store, run_id, archive, state)
                report = _report(
                    reason=state.terminal_reason,
                    detail=state.terminal_detail,
                    state=state,
                    archive=archive,
                    epochs=epochs,
                    extra={"committed_epochs": committed, "audit_decision": audit_decision},
                )
                _materialize_batch_boundary(
                    work_dir,
                    report,
                    problem=problem,
                    export=export,
                    committed=committed,
                    dry_run=dry_run,
                    export_dir=export_dir,
                    timestamped_export=timestamped_export,
                    strict=strict,
                    materialize_provider=materialize_provider,
                    run_id=run_id,
                    log_fn=log,
                )
                _finalize_workflow(work_dir, report, run_id)
                return report
        direction = controller.next_direction(directions, force_finding_id=pending_force)
        pending_force = None
        if direction is None:
            outcome = controller.record_epoch(
                direction=None,
                generated=[],
                findings=directions,
                search_incomplete_directions=[],
                exploration_exhausted=True,
            )
            epochs.append(_epoch_dict(outcome, None))
            if not dry_run:
                _persist(store, run_id, archive, state)
            break

        report = option_provider(
            candidate,
            problem,
            direction.finding_id,
            allow_search=allow_search,
            dimensions=tuple(dimensions),
        )
        resolved = classify_finding(report.get("finding") or {}) if report.get("finding") else direction
        if resolved.finding_id:
            directions = [
                resolved if d.finding_id == direction.finding_id else d for d in directions
            ]
        resolved_coverage = _resolved_coverage(report, direction)
        measured = [
            option
            for option in (report.get("options") or [])
            if option.get("objectives") and option.get("non_dominated")
        ]
        if not measured:
            log(f"convergence epoch {state.epoch + 1}: no non-dominated option for {direction.finding_id}")
            outcome = controller.record_epoch(
                direction=direction,
                generated=[],
                findings=directions,
                resolved_coverage=resolved_coverage,
            )
            epochs.append(_epoch_dict(outcome, None))
            if not dry_run:
                _persist(store, run_id, archive, state)
            continue

        entries, bodies = _measure_frontier(
            candidate, problem, direction, measured, body_provider, tuple(dimensions)
        )
        # Never commit (and therefore never re-export) a candidate the bounded
        # frontier already dominates: retain it as evidence for the epoch but
        # keep exploring another direction.
        committable = [entry for entry in entries if not archive.dominated_by(entry)]
        if not committable:
            log(
                f"convergence epoch {state.epoch + 1}: every option for "
                f"{direction.finding_id} is dominated by the retained frontier"
            )
            outcome = controller.record_epoch(
                direction=direction,
                generated=entries,
                findings=directions,
                resolved_coverage=resolved_coverage,
            )
            epochs.append(_epoch_dict(outcome, None))
            if not dry_run:
                _persist(store, run_id, archive, state)
            continue
        chosen_entry = committable[0]
        if pending_preferred_option:
            preferred = next(
                (
                    entry
                    for entry in committable
                    if str((entry.source or {}).get("option_id") or "")
                    == pending_preferred_option
                ),
                None,
            )
            if preferred is not None:
                chosen_entry = preferred
        pending_preferred_option = None
        chosen = next(
            option
            for option in measured
            if _ref_for(direction, option) == chosen_entry.candidate_ref
        )
        if dry_run:
            # No mutation and no persistence: return the planned epoch only.
            for entry in entries:
                archive.consider(entry)
            report = _report(
                reason="dry_run",
                detail="Preview only; no candidate was mutated.",
                state=state,
                archive=archive,
                epochs=[
                    *epochs,
                    {"direction": direction.direction, "planned_option": chosen.get("option_id")},
                ],
                extra={"planned_entries": [e.to_dict() for e in entries]},
            )
            _materialize_batch_boundary(
                work_dir,
                report,
                problem=problem,
                export=export,
                committed=committed,
                dry_run=dry_run,
                export_dir=export_dir,
                timestamped_export=timestamped_export,
                strict=strict,
                materialize_provider=materialize_provider,
                run_id=run_id,
                log_fn=log,
            )
            _finalize_workflow(work_dir, report, run_id)
            return report
        result = apply_provider(
            work_dir,
            problem=problem,
            option_id=str(chosen.get("option_id") or ""),
            finding_id=direction.finding_id,
            dimensions=tuple(dimensions),
            dry_run=False,
            # An internal convergence epoch is a verified planning state, not a
            # review handoff: it persists the candidate revision without
            # materializing a Stage 4 bundle. The batch boundary owns the single
            # export below.
            export=False,
            run_id=run_id,
        )
        committed_ref = _ref_for(direction, chosen)
        if not result.get("ok"):
            log(
                f"convergence epoch {state.epoch + 1}: applying {chosen.get('option_id')} "
                f"rejected ({result.get('reason')})"
            )
            outcome = controller.record_epoch(
                direction=direction,
                generated=[],
                findings=directions,
                resolved_coverage={
                    direction.finding_id: _exhausted_coverage(resolved_coverage, direction)
                },
            )
            epochs.append(_epoch_dict(outcome, result))
            _persist(store, run_id, archive, state)
            continue

        committed += 1
        committed_fingerprint = str(result.get("candidate_fingerprint_after") or "")
        for entry in entries:
            if entry.candidate_ref == committed_ref:
                entry.export_fingerprint = str(result.get("export_fingerprint") or "")
        session, _checkpoint, next_candidate = load_finalized_candidate(work_dir, run_id=run_id)
        fresh = classify_findings(finding_provider(next_candidate, problem))
        outcome = controller.record_epoch(
            direction=direction,
            generated=entries,
            findings=fresh,
            candidate_changed=True,
        )
        epochs.append(_epoch_dict(outcome, result))
        _retain_frontier_bodies(store, run_id, entries, bodies)
        _persist(store, run_id, archive, state)
        log(
            f"convergence epoch {outcome.epoch}: committed {committed_fingerprint[:12]} "
            f"({direction.direction}); frontier={len(outcome.frontier_refs)}"
        )
        if not reconcile_budget:
            break

    if not state.is_terminal():
        reason, detail = _pause_reason(state, max_epochs)
        state.pause_reason, state.pause_detail = reason, detail
        _persist(store, run_id, archive, state)

    # Uncovered material audit findings are asked as questions even when the
    # automatic repair/search work converged; the human decides them.
    if audit_decision and audit_decision.get("operator_required"):
        if state.terminal_reason != "operator_required":
            state.terminal_reason = "operator_required"
            state.terminal_detail = _operator_detail(audit_decision)
            _persist(store, run_id, archive, state)

    report = _report(
        reason=state.terminal_reason,
        detail=state.terminal_detail or describe_terminal(state.terminal_reason),
        state=state,
        archive=archive,
        epochs=epochs,
        extra={"committed_epochs": committed, "audit_decision": audit_decision},
    )
    _materialize_batch_boundary(
        work_dir,
        report,
        problem=problem,
        export=export,
        committed=committed,
        dry_run=dry_run,
        export_dir=export_dir,
        timestamped_export=timestamped_export,
        strict=strict,
        materialize_provider=materialize_provider,
        run_id=run_id,
        log_fn=log,
    )
    _finalize_workflow(work_dir, report, run_id)
    return report


def _measure_frontier(
    candidate: Mapping[str, Any],
    problem: Mapping[str, Any],
    direction: FindingDirection,
    measured: list[Mapping[str, Any]],
    body_provider: Callable[..., dict[str, Any]],
    dimensions: tuple[str, ...],
) -> tuple[list[ArchiveEntry], dict[str, dict[str, Any]]]:
    """Materialize candidate bodies and archive entries for measured options."""
    from .stage3_session_store import fingerprint_plan

    entries: list[ArchiveEntry] = []
    bodies: dict[str, dict[str, Any]] = {}
    for option in measured:
        body_result = body_provider(
            candidate,
            problem,
            str(option.get("option_id") or ""),
            finding_id=direction.finding_id,
            dimensions=dimensions,
        )
        if not body_result.get("ok"):
            continue
        body = body_result.get("candidate")
        if not isinstance(body, Mapping):
            continue
        fingerprint = fingerprint_plan(body)
        ref = _ref_for(direction, option)
        entries.append(
            archive_entry_from_option(
                option,
                direction=direction.direction,
                candidate_ref=ref,
                candidate_fingerprint=fingerprint,
                evidence={
                    "family": option.get("family"),
                    "finding_id": direction.finding_id,
                    "quality_vs_current": option.get("quality_vs_current"),
                },
            )
        )
        bodies[ref] = dict(body)
    return entries, bodies


def _finalize_workflow(work_dir: Any, report: dict[str, Any], run_id: str | None) -> dict[str, Any]:
    """Fold the convergence report into the persisted audit/convergence workflow.

    A committed mutation re-exports a new candidate and therefore re-enters
    ``audit_required``; a terminal report that committed nothing may complete
    the workflow. The projection is attached to the returned report so the
    caller/transport always sees the canonical next transition.
    """
    try:
        from .audit_lifecycle import record_convergence_result, workflow_snapshot

        record_convergence_result(work_dir, report, run_id=run_id)
        view = workflow_snapshot(work_dir, run_id=run_id)
        if view is not None:
            report["workflow"] = view
    except Exception:
        pass
    return report


def _materialize_batch_boundary(
    work_dir: Any,
    report: dict[str, Any],
    *,
    problem: Mapping[str, Any],
    export: bool,
    committed: int,
    dry_run: bool,
    export_dir: str | None,
    timestamped_export: bool,
    strict: bool,
    materialize_provider: Callable[..., dict[str, Any]],
    run_id: str | None,
    log_fn: Callable[[str], None],
) -> dict[str, Any]:
    """Materialize the single Stage 4 review handoff for a convergence batch.

    Internal epochs commit verified candidate revisions without exporting; the
    batch boundary turns the selected candidate into exactly one review export.
    A pending export left by an earlier invocation is materialized even when
    this invocation committed nothing, so a failed or ``--no-export`` batch can
    always be completed later without duplicating an already-materialized
    handoff. The report always distinguishes a real handoff
    (``export_materialized``/``audit_required``) from an owed but unmaterialized
    one (``export_required``).
    """
    from .candidate_refinement import export_is_pending

    if dry_run:
        report.update({"export_materialized": False, "audit_required": False, "export_required": False})
        return report

    pending = export_is_pending(work_dir, run_id=run_id)
    if not export:
        owed = bool(committed) or pending
        report.update(
            {
                "export_materialized": False,
                "audit_required": False,
                "export_required": owed,
            }
        )
        return report
    if not committed and not pending:
        # The current candidate already is the reviewed export: no redundant
        # handoff just because the controller ended another batch.
        report.update(
            {"export_materialized": False, "audit_required": False, "export_required": False}
        )
        return report

    result = materialize_provider(
        work_dir,
        problem=problem,
        export_dir=export_dir,
        timestamped_export=timestamped_export,
        strict=strict,
        log_fn=log_fn,
        run_id=run_id,
    )
    if result.get("ok"):
        report.update(
            {
                "export_materialized": True,
                "audit_required": True,
                "export_required": False,
                "export_dir": result.get("export_dir"),
                "export_fingerprint": result.get("export_fingerprint"),
                "export": result.get("export"),
            }
        )
        return report
    report.update(
        {
            "export_materialized": False,
            "audit_required": False,
            "export_required": True,
            "export_error": result.get("reason") or "export_failed",
        }
    )
    return report


def _operator_detail(decision: Mapping[str, Any]) -> str:
    questions = decision.get("operator_questions") or []
    if not questions:
        return describe_terminal("operator_required")
    rendered = "; ".join(
        str(item.get("finding") or item.get("question") or "").strip()
        for item in questions
        if (item.get("finding") or item.get("question"))
    )
    return (
        "Automatic refinement stopped: the semantic audit raised findings the "
        f"repository cannot act on automatically and the operator must decide: {rendered}"
    )


def select_frontier_candidate(
    work_dir: Any,
    *,
    candidate_ref: str,
    problem: Mapping[str, Any],
    export: bool = True,
    export_dir: str | None = None,
    timestamped_export: bool = True,
    strict: bool = True,
    actor: str | None = None,
    rationale: str = "",
    run_id: str | None = None,
    log_fn: Optional[Callable[[str], None]] = None,
) -> dict[str, Any]:
    """Adopt one still-valid retained frontier candidate as the current revision.

    The bounded frontier keeps every non-dominated candidate selectable, not
    just the last committed one. Adoption re-validates the retained candidate
    against the current Stage 1/2 facts identity and the current hard verifier
    before committing it as a new revision and re-exporting, so a stale
    candidate is rejected rather than trusted from its original verification.
    """
    from .candidate_refinement import commit_refined_candidate, load_finalized_candidate
    from .stage3_session import TRANSITION_SELECT_CANDIDATE
    from .stage3_session_store import (
        extract_candidate_body,
        fingerprint_plan,
        stage3_checkpoint_facts_fingerprint,
    )

    log = log_fn or (lambda _message: None)
    session, checkpoint, _current = load_finalized_candidate(work_dir, run_id=run_id)
    attempt = session.find_candidate_attempt(candidate_ref)
    if attempt is None:
        archived = any(
            str(entry.get("candidate_ref") or "") == candidate_ref
            for entry in session.pareto_archive
        )
        return {
            "ok": False,
            "reason": (
                "frontier_candidate_body_not_retained"
                if archived
                else "unknown_frontier_candidate_ref"
            ),
            "candidate_ref": candidate_ref,
        }
    expected_facts = str(attempt.get("facts_fingerprint") or "")
    if not expected_facts:
        return {
            "ok": False,
            "reason": "retained_candidate_missing_facts_provenance",
            "candidate_ref": candidate_ref,
        }
    from ..pipeline.state import PipelineState

    if stage3_checkpoint_facts_fingerprint(PipelineState(work_dir)) != expected_facts:
        return {
            "ok": False,
            "reason": "stale_frontier_candidate_facts",
            "candidate_ref": candidate_ref,
        }
    body = extract_candidate_body(attempt.get("candidate"))
    if body is None:
        return {
            "ok": False,
            "reason": "frontier_candidate_body_missing",
            "candidate_ref": candidate_ref,
        }
    from ..planning_contract import verify_candidate

    verification = verify_candidate(dict(body), dict(problem)) if problem else {"ok": True}
    if not verification.get("ok", True):
        return {
            "ok": False,
            "reason": "stale_frontier_candidate_hard_violations",
            "candidate_ref": candidate_ref,
            "verification": verification,
        }
    after_fingerprint = fingerprint_plan(body)
    if after_fingerprint and after_fingerprint == (
        session.finalized_fingerprint or session.candidate_fingerprint
    ):
        return {"ok": True, "already_current": True, "candidate_ref": candidate_ref}
    return commit_refined_candidate(
        work_dir,
        session,
        checkpoint=checkpoint,
        candidate=body,
        before_fingerprint=session.finalized_fingerprint or session.candidate_fingerprint,
        after_fingerprint=after_fingerprint,
        source="frontier_selection",
        transition=TRANSITION_SELECT_CANDIDATE,
        action_id="apply_candidate",
        detail={"candidate_ref": candidate_ref},
        result_extra={"candidate_ref": candidate_ref, "verification": verification},
        problem=problem,
        export=export,
        export_dir=export_dir,
        timestamped_export=timestamped_export,
        strict=strict,
        actor=actor,
        rationale=rationale or f"adopt retained frontier candidate {candidate_ref}",
        log_fn=log,
    )


def _pause_reason(state: ConvergenceState, max_epochs: int) -> tuple[str, str]:
    """Reason for a non-terminal stop: a resumable budget pause.

    An epoch-budget stop is ``budget_exhausted``; any other early stop (for
    example a single-epoch ``reconcile_budget=False`` invocation) is the
    generic resumable ``paused``. Neither is a convergence terminal.
    """
    if state.epoch >= max_epochs:
        return PAUSE_BUDGET_EXHAUSTED, describe_pause(PAUSE_BUDGET_EXHAUSTED)
    return PAUSE_PAUSED, describe_pause(PAUSE_PAUSED)


def _epoch_dict(outcome: Any, apply_result: Mapping[str, Any] | None) -> dict[str, Any]:
    payload = {
        "epoch": outcome.epoch,
        "direction": outcome.direction,
        "accepted_refs": list(outcome.accepted_refs),
        "rejected_refs": list(outcome.rejected_refs),
        "improved": outcome.improved,
        "archive_size": outcome.archive_size,
        "frontier_refs": list(outcome.frontier_refs),
        "terminal_reason": outcome.terminal_reason,
    }
    if apply_result is not None:
        payload["apply_ok"] = bool(apply_result.get("ok"))
        payload["apply_reason"] = apply_result.get("reason")
        payload["candidate_fingerprint"] = apply_result.get("candidate_fingerprint_after")
        payload["export_fingerprint"] = apply_result.get("export_fingerprint")
    return payload


def _report(
    *,
    reason: str,
    detail: str,
    state: ConvergenceState,
    archive: ParetoArchive,
    epochs: list[dict[str, Any]],
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    payload = {
        "ok": True,
        "terminal_reason": reason,
        "terminal_detail": detail,
        "pause_reason": state.pause_reason,
        "pause_detail": state.pause_detail or describe_pause(state.pause_reason),
        "paused": state.is_paused(),
        "resumable": state.is_paused(),
        "globally_optimal": False,
        "convergence": state.to_dict(),
        "frontier": archive.to_list(),
        "frontier_refs": archive.refs(),
        "epochs": epochs,
    }
    if extra:
        payload.update(dict(extra))
    # Whether a fresh semantic audit is required is decided at the batch
    # boundary (``_materialize_batch_boundary``): only a *materialized* Stage 4
    # handoff invalidates the prior audit. A report that only committed
    # internal revisions stays explicitly non-auditable.
    payload.setdefault("audit_required", False)
    payload.setdefault("export_required", False)
    return payload


__all__ = ["run_bounded_convergence", "select_frontier_candidate", "DEFAULT_DIMENSIONS"]
