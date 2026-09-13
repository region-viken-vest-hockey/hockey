"""Stage 4 hard-verification gate: independent re-verification of a plan
immediately before it is materialized as a production export."""

from __future__ import annotations

from typing import Any

from rich.console import Console


def _baseline_hard_violations_for_plan(
    plan: "dict[str, Any] | None", problem: "dict[str, Any] | None"
) -> "list[str]":
    """Independently verify *plan* against the final hard verifier and
    return its violations as ``"code: message"`` strings (empty when *plan*
    passes or can't be extracted/verified).

    Used to populate ``DecisionContext.baseline_hard_violations`` for the
    Stage 3 interactive/Pareto contexts that construct a
    :class:`~..application.decisions.DecisionContext` directly rather than
    via :func:`..stage3_decision.build_stage3_decision_context` (which
    derives it from an A/B report's ``old`` verification instead) -- a
    ``keep_baseline`` decision must not finalize a plan that already fails
    hard verification (issue #264 real-run finding).
    """
    if plan is None:
        return []
    try:
        from ...final_verification import verify_final_candidate
        from ...planning_contract import extract_candidate

        candidate = extract_candidate(plan)
    except (ValueError, KeyError):
        return []
    try:
        result = verify_final_candidate(candidate, problem)
    except Exception:
        return []
    if result.get("ok", True):
        return []
    return [f"{v.get('code')}: {v.get('message')}" for v in (result.get("violations") or [])]


def _assert_hard_verification_before_export(
    plan: "dict[str, Any] | None",
    problem: "dict[str, Any] | None",
    strict: bool,
    console: "Console",
    log_fn: "Any",
) -> bool:
    """Hard-verifier gate immediately before Stage 4 materializes a
    production export (issue #264 real-run finding).

    A `2026-09-07T0525` production export shipped a `keep_baseline` decision
    whose `final_verify_result.ok` was `False`. Blocking that at decision
    time (``_BASELINE_HARD_VIOLATION_BLOCKED_ACTIONS`` in
    ``application.decisions``) is necessary but not sufficient on its own --
    a resumed ``--resume-from 4`` run reaches Stage 4 directly without
    re-emitting a decision, so this independently re-verifies *plan* right
    before export regardless of how it got here.

    External calendar conflicts and participation-target mismatches remain
    non-blocking structural findings. The strict final verifier surfaces them
    through ``publication_readiness`` while this gate continues to block only
    actual hard violations (for example duplicate participation, malformed
    round-robin games, undersized production tournaments, or arena overlap).

    Returns True when export should proceed. In strict mode (the default) a
    hard-failing plan blocks export outright; ``--non-strict`` logs a
    warning and continues, matching every other pipeline gate's non-strict
    posture (e.g. :func:`_run_approval_gate`).
    """
    violations = _baseline_hard_violations_for_plan(plan, problem)
    if not violations:
        return True
    console.print(
        f"  [red]✗[/red] Planen feiler hard verifisering ({len(violations)} brudd) — "
        "kan ikke materialiseres som produksjonseksport."
    )
    for violation in violations:
        console.print(f"    • {violation}")
    log_fn(f"Stage 4 hard-verification gate FAILED: {'; '.join(violations)}")
    if strict:
        return False
    console.print("  [yellow]⚠[/yellow] Fortsetter pga --non-strict")
    return True
