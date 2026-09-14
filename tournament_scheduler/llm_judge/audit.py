"""Headless (no interactive harness) execution of the semantic safety-net
audit (issue #325 Phase 1).

When an interactive harness (Claude Code/Pi) is orchestrating the run, it
performs the audit itself in-session by reading
``operator audit-context`` output and reasoning adversarially, then submits
its verdict via ``operator audit-submit`` — see
``tournament_scheduler.llm_judge.harness.is_harness_active``. This module is
only used for the headless fallback (cron/CI): it builds a prompt from the
assembled audit context, calls a real :class:`LLMJudge` backend, and parses
the response into the same structured result shape an interactive harness
would submit.

A response that can't be parsed/validated never becomes ``PASS`` — it comes
back as ``INCOMPLETE``, which the publish gate treats as blocking, exactly
like a ``FAIL`` (issue #325: "a partial or failed audit must never silently
become PASS").
"""

from __future__ import annotations

import json
from typing import Any

from ..pipeline.audit_result import build_audit_id, now_iso, validate_audit_result

_CHECKLIST_LINES_TEMPLATE = "  {item_id}. {question}"


def build_audit_prompt(context: dict[str, Any]) -> str:
    """Render the audit checklist plus assembled context into a judge prompt."""
    checklist_lines = [
        _CHECKLIST_LINES_TEMPLATE.format(**item) for item in context.get("checklist") or []
    ]
    lines = [
        "You are performing an independent, adversarial semantic safety-net audit of a "
        "youth hockey tournament season-plan export, immediately before publication.",
        "",
        "Your job is not to rubber-stamp the deterministic verifier. Simulate a careful "
        "human reviewer: reconstruct important facts from the evidence below, cross-check "
        "outputs against each other, look for contradictions, counterexamples and suspicious "
        "outliers across the whole season, and report possible missing planner rules with "
        "concrete examples.",
        "",
        "Do not assume the schedule is correct merely because deterministic verification "
        "passed. The scheduler and its deterministic verifier may share a logic defect, or "
        "may simply be missing a rule. Explain anything that does not make operational sense.",
        "",
        "Hard policy: a tournament with more than "
        f"{context.get('hard_max_club_teams_per_tournament', 3)} teams from the same club is "
        "an invalid plan, not a quality concern. If "
        "`plan_audit_summary.same_club_per_tournament_summary.tournaments_over_hard_max` is "
        "greater than zero, or you independently find such a tournament, the overall status "
        "must be `FAIL` -- never `REVIEW_REQUIRED` or `PASS` -- regardless of any other score "
        "or `production_ready` label.",
        "",
        "Soft policy (issue #327): a team's participation count coming in under its nominal "
        "target is not automatically a defect. Check each shortfall's `category` in "
        "`plan_audit_summary.unresolved_participation_shortfalls` against "
        "`plan_audit_summary.club_participation_fairness` -- a "
        "`participation_under_target_club_share_ok` shortfall means the team's club already "
        "received its fair proportional share of participation slots with sibling teams "
        "rotated evenly (spread <=1), and is not by itself grounds for `REVIEW_REQUIRED`. Still "
        "scrutinize a club materially below its `target_share` without such evidence, or any "
        "shortfall where `sibling_spread` is greater than 1 -- those indicate a genuine "
        "fairness gap worth flagging.",
        "",
        "Audit mission:",
        json.dumps(context.get("audit_mission"), ensure_ascii=False, indent=2),
        "",
        f"Run id: {context.get('run_id')}",
        f"Export fingerprint: {context.get('export_fingerprint')}",
        f"Export directory: {context.get('export_dir')}",
        "",
        "Deterministic verification result (already computed, do not recompute — cross-check "
        "and look beyond it):",
        json.dumps(context.get("deterministic_verify_result"), ensure_ascii=False, indent=2),
        "",
        "Publication readiness (already computed):",
        json.dumps(context.get("publication_readiness"), ensure_ascii=False, indent=2),
        "",
        "Checklist evidence guide. Use this to avoid overlooking persisted evidence, but do "
        "not treat the summaries as proof that the schedule is correct. If a referenced summary "
        "contains a count, contradiction, outlier or examples, reason from it; if evidence is "
        "missing where a human reviewer would need it, report that as an audit finding:",
        json.dumps(context.get("checklist_evidence_guide"), ensure_ascii=False, indent=2),
        "",
        "Persisted source/calendar evidence (do not perform fresh live scraping):",
        json.dumps(context.get("calendar_evidence_summary"), ensure_ascii=False, indent=2),
        "",
        "Selected-plan audit summary (use this for tournament duration, host participation, "
        "per-team daily participation and same-club participant-count cross-checks):",
        json.dumps(context.get("plan_audit_summary"), ensure_ascii=False, indent=2),
        "",
        "Export output files (cross-check export-format consistency, checklist item 8):",
        json.dumps(context.get("output_files"), ensure_ascii=False, indent=2),
        "",
        "Export consistency summary:",
        json.dumps(context.get("export_consistency_summary"), ensure_ascii=False, indent=2),
        "",
        "Operator checklist — answer every item; item 9 is open-ended and the most important:",
        *checklist_lines,
        "",
        "Respond with a single JSON object with this exact shape:",
        json.dumps(
            {
                "status": "PASS | REVIEW_REQUIRED | FAIL",
                "checklist_findings": [
                    {
                        "item_id": "int 1-9",
                        "question": "string",
                        "finding": "string",
                        "severity": "info | minor | major | critical",
                        "confidence": "low | medium | high",
                        "evidence": ["string", "..."],
                        "could_not_establish": "bool",
                    }
                ],
                "potential_missing_rule": [
                    {
                        "description": "string",
                        "severity": "info | minor | major | critical",
                        "confidence": "low | medium | high",
                        "evidence": ["string", "..."],
                    }
                ],
                "could_not_independently_establish": ["string", "..."],
            },
            indent=2,
        ),
        "",
        "Respond with only the JSON object, no other text.",
    ]
    return "\n".join(lines)


def _extract_json_object(raw: str) -> dict[str, Any] | None:
    """Best-effort extraction of a JSON object from *raw* model output.

    Tries a straight parse first, then falls back to the first top-level
    ``{...}`` span, tolerating models that wrap JSON in prose or code
    fences. Returns ``None`` (never a partial guess) when nothing parses.
    """
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.lower().startswith("json"):
            raw = raw[4:]
        raw = raw.strip()
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        pass
    start = raw.find("{")
    end = raw.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        parsed = json.loads(raw[start : end + 1])
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        return None


def _incomplete_result(context: dict[str, Any], *, backend: str, reason: str) -> dict[str, Any]:
    generated_at = now_iso()
    run_id = str(context.get("run_id") or "")
    export_fingerprint = str(context.get("export_fingerprint") or "")
    return {
        "schema_version": 1,
        "audit_id": build_audit_id(export_fingerprint=export_fingerprint, run_id=run_id, generated_at=generated_at),
        "generated_at": generated_at,
        "run_id": run_id,
        "export_fingerprint": export_fingerprint,
        "source_fingerprints": context.get("source_fingerprints") or {},
        "prompt_version": context.get("audit_prompt_version"),
        "runbook_version": context.get("runbook_version"),
        "backend": backend,
        "execution_mode": "headless",
        "status": "INCOMPLETE",
        "checklist_findings": [
            {
                "item_id": item["item_id"],
                "question": item["question"],
                "finding": reason,
                "severity": "critical",
                "confidence": "high",
                "evidence": [],
                "could_not_establish": True,
            }
            for item in context.get("checklist") or []
        ],
        "potential_missing_rule": [],
        "could_not_independently_establish": [reason],
        "raw_response_ref": None,
    }


def run_headless_audit(context: dict[str, Any], backend: str) -> dict[str, Any]:
    """Run the headless audit path: build prompt, call *backend*, parse/validate.

    Never returns ``status: PASS`` unless the backend's response parsed
    cleanly and passed schema validation — any failure (backend error,
    unparseable response, invalid schema) returns an ``INCOMPLETE`` result
    instead, which the publish gate treats as blocking.
    """
    from . import create_judge

    prompt = build_audit_prompt(context)
    try:
        judge = create_judge(backend)
        raw = judge.judge(prompt)
    except Exception as exc:  # noqa: BLE001 — surface as INCOMPLETE, never crash the audit
        return _incomplete_result(context, backend=backend, reason=f"Judge backend call failed: {exc}")

    parsed = _extract_json_object(raw)
    if parsed is None:
        return _incomplete_result(
            context, backend=backend, reason=f"Could not parse judge response as JSON: {raw[:500]!r}"
        )

    generated_at = now_iso()
    run_id = str(context.get("run_id") or "")
    export_fingerprint = str(context.get("export_fingerprint") or "")
    result = {
        "schema_version": 1,
        "audit_id": build_audit_id(export_fingerprint=export_fingerprint, run_id=run_id, generated_at=generated_at),
        "generated_at": generated_at,
        "run_id": run_id,
        "export_fingerprint": export_fingerprint,
        "source_fingerprints": context.get("source_fingerprints") or {},
        "prompt_version": context.get("audit_prompt_version"),
        "runbook_version": context.get("runbook_version"),
        "backend": backend,
        "execution_mode": "headless",
        "status": parsed.get("status"),
        "checklist_findings": parsed.get("checklist_findings") or [],
        "potential_missing_rule": parsed.get("potential_missing_rule") or [],
        "could_not_independently_establish": parsed.get("could_not_independently_establish") or [],
        "raw_response_ref": None,
    }

    errors = validate_audit_result(result)
    if errors:
        return _incomplete_result(
            context, backend=backend, reason=f"Judge response failed schema validation: {'; '.join(errors)}"
        )
    return result
