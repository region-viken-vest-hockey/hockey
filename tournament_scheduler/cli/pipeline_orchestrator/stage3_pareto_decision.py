"""Emit the Stage 3 pareto-candidate decision (headless judge or interactive harness)."""

from __future__ import annotations

from typing import Any

from ...pipeline.run_log_paths import resolve_active_run_log_dir
from .interactive_state_io import (
    _current_run_id,
    _read_stage3_interactive_state,
    _write_stage3_interactive_state,
)
from .verification import _baseline_hard_violations_for_plan, _mid_planning_decision_problem

def _emit_stage3_pareto_decision(
    state: "Any",
    work_dir: str,
    cfg: "dict[str, Any]",
    scraping: "dict[str, Any]",
    start: "Any",
    end: "Any",
    portfolio: "list[dict[str, Any]] | None",
    log_fn: "Any",
    *,
    search_arguments: "dict[str, Any] | None" = None,
) -> int:
    """Build, persist and print the Stage 3 :class:`DecisionContext` for a
    completed multi-objective (Pareto) search attempt (issue #264 P1 /
    issue #265 P1).

    Mirrors :func:`_emit_stage3_interactive_decision`'s attempt-tracking and
    state persistence, but a Pareto attempt can produce several genuinely
    different non-dominated candidates instead of one -- the emitted
    context lists all of them (``candidate_ref``, objective vector, whether
    each dominates the current best) via ``facts``/``action_parameters``,
    and ``pending_candidates`` (plural) replaces the single-optimizer path's
    ``pending_candidate`` in the persisted side-state, so ``apply_candidate``
    can pick any one of them by ``candidate_ref``.

    A Pareto search is only ever reachable via an ``optimize_plan`` decision
    on an *existing* Stage 3 attempt (never the first, auto-baselined one),
    so ``best_plan``/``best_attempt`` are always already present here.
    """
    import json as _json
    from dataclasses import replace as _dc_replace

    from ...application.decisions import DecisionContext
    from ...application.stage3_progress import (
        build_attempt_record,
        build_search_history,
        upsert_attempt,
    )
    from ...application.stage3_session_store import Stage3SessionStore, fingerprint_plan
    from ...pipeline.fingerprints import stable_payload_sha256
    from ...stage3_decision import STAGE3_DECISION_ACTIONS, _OPTIMIZE_PLAN_SCHEMAS

    run_id = _current_run_id(state)
    interactive_state = _read_stage3_interactive_state(state, expected_run_id=run_id)
    attempts_used = int(interactive_state.get("attempts_used", 0)) + 1

    # Continuation evidence is captured against the candidate this search was
    # requested from, before the new candidates are considered.
    store = Stage3SessionStore(state.work_dir)
    session_before = store.load(expected_run_id=run_id)
    prior_fingerprint = session_before.candidate_fingerprint
    prior_revision = session_before.candidate_revision
    prior_hard_violations = session_before.latest_hard_violations()

    if not portfolio:
        # Nothing non-dominated came back (e.g. every epoch converged to
        # the same point) -- fall back to keep/abort rather than offer a
        # choice that doesn't exist. Still independently verify the current
        # best plan so a hard-failing baseline can't be finalized via
        # keep_baseline just because the search produced no candidates.
        problem = _mid_planning_decision_problem(cfg, scraping, start, end, state.work_dir)
        best_plan = interactive_state.get("best_plan")
        context = DecisionContext(
            run_id=run_id,
            capability="stage3_pareto",
            stage="planning",
            objective="The Pareto search produced no non-dominated candidates for this attempt.",
            baseline_hard_violations=tuple(_baseline_hard_violations_for_plan(best_plan, problem)),
            available_actions=("keep_baseline", "request_operator", "abort"),
        )
        interactive_state["run_id"] = run_id
        interactive_state["attempts_used"] = attempts_used
        interactive_state["last_context"] = context.to_dict()
        _write_stage3_interactive_state(state, interactive_state)
        payload = context.to_dict()
        print(_json.dumps(payload, indent=2, ensure_ascii=False))
        return 2

    entries = [{**item, "candidate_ref": f"pareto:{attempts_used}:{index}"} for index, item in enumerate(portfolio)]
    candidate_refs = [entry["candidate_ref"] for entry in entries]

    entry_fingerprints = [fp for fp in (fingerprint_plan(entry["candidate"]) for entry in entries) if fp]
    entry_hard_counts = [
        len((entry.get("verify_result") or {}).get("violations") or []) for entry in entries
    ]
    hard_violation_count = min(entry_hard_counts) if entry_hard_counts else None
    made_progress = bool(entry_fingerprints) and (
        not prior_fingerprint
        or any(fp != prior_fingerprint for fp in entry_fingerprints)
        or (
            prior_hard_violations is not None
            and hard_violation_count is not None
            and hard_violation_count < prior_hard_violations
        )
    )
    search_attempt = build_attempt_record(
        action_id="optimize_plan",
        arguments=search_arguments,
        candidate_revision=prior_revision,
        candidate_fingerprint=prior_fingerprint,
        transition="run_search",
        progress=made_progress,
        hard_violations=hard_violation_count,
    )
    search_history = build_search_history(
        upsert_attempt(session_before.search_attempts, search_attempt)
    )
    circuit_breaker_tripped = bool(search_history.get("circuit_breaker_tripped"))

    available = list(STAGE3_DECISION_ACTIONS)
    if circuit_breaker_tripped:
        available.remove("optimize_plan")

    problem = _mid_planning_decision_problem(cfg, scraping, start, end, state.work_dir)
    baseline_hard_violations = _baseline_hard_violations_for_plan(
        interactive_state.get("best_plan"), problem
    )

    context = DecisionContext(
        run_id=run_id,
        capability="stage3_pareto",
        stage="planning",
        objective=(
            f"Choose one of {len(entries)} non-dominated Stage 3 candidates to apply "
            "(apply_candidate with its candidate_ref), keep the current best (keep_baseline), "
            "request another search epoch with narrowed parameters (optimize_plan), or ask the "
            "operator."
        ),
        baseline_hard_violations=tuple(baseline_hard_violations),
        facts={
            "epoch_count": len(entries),
            "search_history": search_history,
            "candidates": [
                {
                    "candidate_ref": entry["candidate_ref"],
                    "objective_vector": entry["objective_vector"],
                    "dominates_baseline": entry["dominates_baseline"],
                    "verification_ok": entry["verify_result"]["ok"],
                    "weights_used": entry["weights_used"],
                }
                for entry in entries
            ],
        },
        available_actions=tuple(available),
        action_parameters={
            "apply_candidate": {"candidate_ref": {"type": "string", "enum": candidate_refs}},
            **({"optimize_plan": _OPTIMIZE_PLAN_SCHEMAS["pareto"]} if "optimize_plan" in available else {}),
        },
    )

    interactive_state["run_id"] = run_id
    if circuit_breaker_tripped:
        context = _dc_replace(
            context,
            warnings=tuple(context.warnings)
            + (
                "Stage 3 emergency circuit breaker tripped after an implausibly high "
                "number of actions: a technical safety stop for runaway or broken "
                "orchestration, not evidence that the scheduling problem is unsolvable.",
            ),
        )
    interactive_state["attempts_used"] = attempts_used
    interactive_state["pending_candidates"] = entries
    interactive_state.pop("pending_candidate", None)
    interactive_state["pending_attempt"] = attempts_used
    interactive_state["last_context"] = context.to_dict()

    try:
        store.record_emission(
            interactive_state,
            candidate_revision=attempts_used,
            run_id=run_id,
            transition="run_search",
            action_id="optimize_plan",
            search_attempt=search_attempt,
        )
    except Exception as exc:
        log_fn(f"stage3_pareto attempt {attempts_used}: could not persist Stage 3 session: {exc}")

    try:
        from ...pipeline.evidence_bundle import append_stage3_attempt_log_entry

        for entry in entries:
            append_stage3_attempt_log_entry(
                state.work_dir,
                {
                    "attempt": attempts_used,
                    "candidate_ref": entry["candidate_ref"],
                    "candidate_fingerprint": stable_payload_sha256(entry["candidate"].get("tournaments", [])),
                    "candidate_source": entry["candidate"].get("source"),
                    "verify_result": entry["verify_result"],
                    "score_result": entry["score"],
                },
            )
    except Exception as exc:
        log_fn(f"stage3_pareto attempt {attempts_used}: could not append attempt-log entries: {exc}")

    payload = context.to_dict()
    try:
        log_dir = resolve_active_run_log_dir(work_dir)
        log_dir.mkdir(parents=True, exist_ok=True)
        with open(log_dir / "decision_context.json", "w", encoding="utf-8") as fh:
            _json.dump(payload, fh, indent=2, ensure_ascii=False)
    except Exception:
        pass

    print(_json.dumps(payload, indent=2, ensure_ascii=False))
    return 2
