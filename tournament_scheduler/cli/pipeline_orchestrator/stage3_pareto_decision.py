"""Emit the Stage 3 pareto-candidate decision (headless judge or interactive harness)."""

from __future__ import annotations

from typing import Any

from ...pipeline.run_log_paths import resolve_active_run_log_dir
from .interactive_state_io import (
    _MAX_INTERACTIVE_STAGE3_ATTEMPTS,
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

    from ...application.decisions import DecisionContext
    from ...pipeline.fingerprints import stable_payload_sha256
    from ...stage3_decision import STAGE3_DECISION_ACTIONS, _OPTIMIZE_PLAN_SCHEMAS

    run_id = _current_run_id(state)
    interactive_state = _read_stage3_interactive_state(state, expected_run_id=run_id)
    attempts_used = int(interactive_state.get("attempts_used", 0)) + 1

    if not portfolio:
        # Nothing non-dominated came back (e.g. every epoch converged to
        # the same point) -- fall back to keep/abort rather than offer a
        # choice that doesn't exist. Still independently verify the current
        # best plan so a hard-failing baseline can't be finalized via
        # keep_baseline just because the search produced no candidates.
        problem = _mid_planning_decision_problem(cfg, scraping, start, end)
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

    available = list(STAGE3_DECISION_ACTIONS)
    if attempts_used >= _MAX_INTERACTIVE_STAGE3_ATTEMPTS:
        available.remove("optimize_plan")

    problem = _mid_planning_decision_problem(cfg, scraping, start, end)
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
    interactive_state["attempts_used"] = attempts_used
    interactive_state["pending_candidates"] = entries
    interactive_state.pop("pending_candidate", None)
    interactive_state["pending_attempt"] = attempts_used
    interactive_state["last_context"] = context.to_dict()
    _write_stage3_interactive_state(state, interactive_state)

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
