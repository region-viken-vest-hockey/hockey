"""Bounded Pareto-convergence driver over an unpromoted reviewed candidate.

This is the outer loop the repository was missing: an audited
``REVIEW_REQUIRED`` candidate is no longer automatically a human escalation.
While repository-owned findings still map to a supported repair/search
direction, the driver keeps generating independently verified candidates,
folding them into the bounded non-dominated frontier and re-exporting each
accepted mutation, until the controller reports PASS, operator-required,
bounded-search-exhausted or Pareto-stable.

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
    ArchiveEntry,
    ConvergenceController,
    ConvergenceState,
    FindingDirection,
    ParetoArchive,
    archive_entry_from_option,
    classify_finding,
    classify_findings,
    describe_terminal,
)

DEFAULT_DIMENSIONS = ("participants", "host")


def _short_hash(value: str) -> str:
    return hashlib.sha1(value.encode("utf-8")).hexdigest()[:12]


def _ref_for(direction: FindingDirection, option: Mapping[str, Any]) -> str:
    return f"pareto:{direction.direction}:{_short_hash(str(option.get('option_id') or ''))}"


def _load_state(work_dir: Any, run_id: str | None, frontier_limit: int) -> tuple[Any, ParetoArchive, ConvergenceState]:
    from .stage3_session_store import Stage3SessionStore

    store = Stage3SessionStore(work_dir)
    session = store.load(expected_run_id=run_id or None)
    archive = ParetoArchive.from_list(session.pareto_archive, max_size=frontier_limit)
    state = ConvergenceState.from_dict(session.convergence)
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
    dry_run: bool = False,
    allow_search: bool = True,
    reconcile_budget: bool = True,
    force_finding_id: str | None = None,
    run_id: str | None = None,
    finding_provider: Callable[..., list[dict[str, Any]]] | None = None,
    option_provider: Callable[..., dict[str, Any]] | None = None,
    apply_provider: Callable[..., dict[str, Any]] | None = None,
    body_provider: Callable[..., dict[str, Any]] | None = None,
    log_fn: Optional[Callable[[str], None]] = None,
) -> dict[str, Any]:
    """Run the bounded convergence loop over one reviewed unpromoted candidate.

    Returns a truthful terminal report: ``terminal_reason`` distinguishes PASS,
    operator-required, bounded-search-exhausted, bounded-budget and
    Pareto-stable, and ``terminal_detail`` never claims global optimality.
    ``frontier`` is the bounded non-dominated set the harness may select from.
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

    store, archive, state = _load_state(work_dir, run_id, frontier_limit)
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

    while not state.is_terminal() and state.epoch < max_epochs:
        from .candidate_refinement import load_finalized_candidate

        _session, _checkpoint, candidate = load_finalized_candidate(work_dir, run_id=run_id)
        findings = finding_provider(candidate, problem)
        directions = classify_findings(findings)
        direction = controller.next_direction(directions, force_finding_id=pending_force)
        pending_force = None
        coverage_directions: list[str] = []
        if direction is None:
            outcome = controller.record_epoch(
                direction=None, generated=[], findings=directions, search_incomplete_directions=[]
            )
            epochs.append(_epoch_dict(outcome, None))
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
        coverage_directions = [
            d.direction for d in directions if d.actionable and d.search_incomplete
        ]
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
                search_incomplete_directions=coverage_directions,
            )
            epochs.append(_epoch_dict(outcome, None))
            if not dry_run:
                _persist(store, run_id, archive, state)
            continue

        entries, bodies = _measure_frontier(
            candidate, problem, direction, measured, body_provider, tuple(dimensions)
        )
        chosen = measured[0]
        result = apply_provider(
            work_dir,
            problem=problem,
            option_id=str(chosen.get("option_id") or ""),
            finding_id=direction.finding_id,
            dimensions=tuple(dimensions),
            dry_run=dry_run,
            export=export and not dry_run,
            run_id=run_id,
        )
        committed_ref = _ref_for(direction, chosen)
        if dry_run:
            # No mutation and no persistence: return the planned epoch only.
            archive.consider(next((e for e in entries if e.candidate_ref == committed_ref), entries[0]))
            return _report(
                reason="dry_run",
                detail="Preview only; no candidate was mutated.",
                state=state,
                archive=archive,
                epochs=[*epochs, {"direction": direction.direction, "planned_option": chosen.get("option_id")}],
                extra={"planned_entries": [e.to_dict() for e in entries]},
            )
        if not result.get("ok"):
            log(
                f"convergence epoch {state.epoch + 1}: applying {chosen.get('option_id')} "
                f"rejected ({result.get('reason')})"
            )
            outcome = controller.record_epoch(
                direction=direction,
                generated=[],
                findings=directions,
                search_incomplete_directions=coverage_directions,
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
        fresh_incomplete = [d.direction for d in fresh if d.actionable and d.search_incomplete]
        outcome = controller.record_epoch(
            direction=direction,
            generated=entries,
            findings=fresh,
            search_incomplete_directions=fresh_incomplete,
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
        reason, detail = _stalled_reason(state)
        state.terminal_reason, state.terminal_detail = reason, detail
        _persist(store, run_id, archive, state)

    return _report(
        reason=state.terminal_reason,
        detail=state.terminal_detail or describe_terminal(state.terminal_reason),
        state=state,
        archive=archive,
        epochs=epochs,
        extra={"committed_epochs": committed},
    )


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


def _stalled_reason(state: ConvergenceState) -> tuple[str, str]:
    from .pareto_convergence import TERMINAL_BOUNDED_BUDGET

    return TERMINAL_BOUNDED_BUDGET, describe_terminal(TERMINAL_BOUNDED_BUDGET)


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
        "globally_optimal": False,
        "convergence": state.to_dict(),
        "frontier": archive.to_list(),
        "frontier_refs": archive.refs(),
        "epochs": epochs,
    }
    if extra:
        payload.update(dict(extra))
    # A fresh semantic audit is required whenever the reviewed export was
    # superseded by an accepted mutation. A pure PASS with no mutation keeps
    # the caller's existing audit authority.
    payload["audit_required"] = bool(payload.get("committed_epochs"))
    return payload


__all__ = ["run_bounded_convergence", "DEFAULT_DIMENSIONS"]
