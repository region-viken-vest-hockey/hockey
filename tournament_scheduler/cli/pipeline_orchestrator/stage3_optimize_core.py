"""Stage 3 fairness-penalty hints, mid-planning critic hints, CP-SAT shadow run."""

from __future__ import annotations

from typing import Any

from .judgment import _compute_verdict_tone, _extract_plan_obj, _fairness_gate, _plan_attempt_quality, _score_attr

def _fairness_penalty_hints_from_checkpoint(plan_checkpoint: "dict[str, Any]") -> dict[str, float]:
    """Extract planner penalty hints from a Stage 3 checkpoint's fairness data."""
    hints: dict[str, float] = {}
    plan_obj = _extract_plan_obj(plan_checkpoint)
    gate = _fairness_gate(plan_obj)

    try:
        metrics = gate.get("metrics", []) if isinstance(gate, dict) else []
        for metric in metrics or []:
            if not isinstance(metric, dict):
                continue
            key = str(metric.get("key", ""))
            status = str(metric.get("status", "pass")).lower()
            if key and status != "pass":
                hints[f"{key}_score"] = float(metric.get("score", 100) or 0)
    except Exception:
        pass

    for key in ("pairwise_matchup_score", "diversity_score", "month_balance_score"):
        score = _score_attr(plan_obj, key, default=1.0)
        if score < 0.75:
            hints[key] = score * 100.0

    return hints


def _build_mid_planning_critic_hints(
    plan_checkpoint: "dict[str, Any]",
    iteration: int,
    log_fn: "Any",
) -> dict[str, Any]:
    """Inspect a Stage 3 checkpoint and return structured hints for a rerun."""
    from ..plan_critic import generate_critic_summary

    plan_obj = _extract_plan_obj(plan_checkpoint)
    issues: list[str] = []
    if plan_obj:
        try:
            issues = list(generate_critic_summary(plan_obj) or [])
        except Exception as exc:
            log_fn(f"Mid-planning critic {iteration}: generate_critic_summary failed: {exc}")

    penalty_hints = _fairness_penalty_hints_from_checkpoint(plan_checkpoint)
    tone = _compute_verdict_tone(plan_checkpoint)
    quality = _plan_attempt_quality(plan_checkpoint) if plan_checkpoint else {}

    return {
        "source": "mid_planning_critic",
        "iteration": iteration,
        "tone": tone,
        "issues": issues,
        "penalty_hints": penalty_hints,
        "quality": quality,
    }


_CP_SAT_AUTO_SHADOW_BUDGET_SECONDS = 15.0


def _maybe_run_stage3_cp_sat_shadow(
    cfg: "dict[str, Any]",
    problem: "dict[str, Any] | None",
    baseline_candidate: "dict[str, Any] | None",
    log_fn: "Any",
) -> "dict[str, Any] | None":
    """Best-effort automatic CP-SAT shadow evaluation for a Stage 3 candidate
    (issue #288: "CP-SAT shadow evaluation must be reachable automatically
    from `/rvv-miniputt:run`; the operator should not need to know or invoke
    `plan ab --engine cp-sat`").

    Runs the CP-SAT engine through the same :func:`stage3_engine.run_planner`
    boundary the interactive ``optimize_plan(engine="cp_sat")`` path uses, and
    wraps the result with :func:`stage3_shadow.build_shadow_report` so the
    comparison is fingerprinted/reproducible evidence, not just a prose
    summary. Solves with ``decompose_by_half=True`` (issue #298 Phase 2/3):
    a monolithic Oct-Apr quality-mode solve is the shape that produced the
    original UNKNOWN evidence in #298, so the automatic shadow comparison
    must use the smaller per-half models Phase 2 built rather than continue
    attempting the harder combined search under the same 15s budget. Returns
    ``None`` only when shadow evaluation is disabled via
    config (``cp_sat_shadow_enabled: false``) or there is no candidate to
    shadow yet -- every other outcome, including a missing OR-Tools
    dependency or an infeasible/timed-out solve, returns a dict describing
    what happened rather than raising, so this can never interrupt or fail
    the production ``/run`` workflow. Never applies or publishes the shadow
    candidate -- this is comparison evidence for the existing
    apply_candidate/keep_baseline decision only.
    """
    if baseline_candidate is None or not bool(cfg.get("cp_sat_shadow_enabled", True)):
        return None

    from ...stage3_cpsat import CpSatNoCandidate, CpSatUnavailable
    from ...stage3_engine import run_planner
    from ...stage3_shadow import build_shadow_report

    budget = float(cfg.get("cp_sat_shadow_budget_seconds", _CP_SAT_AUTO_SHADOW_BUDGET_SECONDS))
    try:
        shadow_candidate = run_planner(
            engine="cp_sat",
            problem=problem,
            baseline=baseline_candidate,
            request={"solve_budget_seconds": budget, "decompose_by_half": True},
        )
    except CpSatUnavailable as exc:
        log_fn(f"stage3 cp_sat shadow: unavailable ({exc}) -- skipping automatic shadow evaluation")
        return {"attempted": True, "available": False, "engine": "cp_sat", "error": {"type": "CpSatUnavailable", "message": str(exc)}}
    except CpSatNoCandidate as exc:
        log_fn(f"stage3 cp_sat shadow: no feasible candidate within budget ({exc})")
        return {
            "attempted": True,
            "available": True,
            "engine": "cp_sat",
            "error": {
                "type": "CpSatNoCandidate",
                "message": str(exc),
                "status": getattr(exc, "status", None),
                "runtime_seconds": getattr(exc, "runtime_seconds", None),
            },
        }
    except Exception as exc:  # defensive: shadow evaluation must never break /run
        log_fn(f"stage3 cp_sat shadow: unexpected error ({exc}) -- skipping")
        return {"attempted": True, "available": False, "engine": "cp_sat", "error": {"type": type(exc).__name__, "message": str(exc)}}

    try:
        report = build_shadow_report(baseline_candidate, shadow_candidate, problem, engine="cp_sat")
    except Exception as exc:
        log_fn(f"stage3 cp_sat shadow: could not build shadow report ({exc})")
        return {
            "attempted": True,
            "available": True,
            "engine": "cp_sat",
            "candidate_source": shadow_candidate.get("source"),
            "report_error": str(exc),
        }

    log_fn(
        "stage3 cp_sat shadow: evaluated automatically "
        f"(dominates_baseline={report['ab_report'].get('dominates_baseline')})"
    )
    return {
        "attempted": True,
        "available": True,
        "engine": "cp_sat",
        "dominates_baseline": report["ab_report"].get("dominates_baseline"),
        "production_ready": report["ab_report"].get("production_ready"),
        "candidate_source": shadow_candidate.get("source"),
        "fingerprints": report["fingerprints"],
    }
