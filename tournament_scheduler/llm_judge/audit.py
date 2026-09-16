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

# The judge backends exposed by ``llm_judge`` take a single prompt string, so
# interactive tool-call retrieval is not available. Instead the harness runs a
# deliberately bounded expansion: if the model asks for evidence, its queries
# are resolved through the canonical repository query API and appended to one
# more prompt, at most this many rounds. A backend that did support tool
# callbacks could loop on the same API instead.
MAX_AUDIT_EVIDENCE_ROUNDS = 2
_EVIDENCE_QUERIES_PER_ROUND = 6

_CHECKLIST_LINES_TEMPLATE = "  {item_id}. {question}"


def build_audit_prompt(context: dict[str, Any], *, evidence_appendix: str | None = None) -> str:
    """Render the audit checklist plus bounded context into a judge prompt."""
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
        "Hard policy: pause/bye teams are invalid in every age group. If "
        "`plan_audit_summary.tournament_utilisation_summary.tournaments_with_byes_or_invalid_no_bye_roster` "
        "is greater than zero, or you independently find any odd-sized/byed tournament, the overall "
        "status must be `FAIL` -- never `REVIEW_REQUIRED` or `PASS`. Repeated even-roster but "
        "low-utilisation tournaments should still be reported as planning-quality findings.",
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
        "Queryable evidence index (bounded). This is the discoverable map of the detailed "
        "evidence that exists. It deliberately shows counts, distributions and top examples "
        "rather than the whole evidence universe; use `evidence_queries` below to pull exact "
        "records for a suspicious area instead of guessing at artifact paths:",
        json.dumps(context.get("evidence_index"), ensure_ascii=False, indent=2),
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
                "evidence_queries": [
                    {
                        "category": "string (from available_selectors.categories)",
                        "item": "int 1-9 (optional)",
                        "tournament": "durable tournament id (optional)",
                        "club": "string (optional)",
                        "age_group": "string (optional)",
                        "unresolved": "bool (optional)",
                        "why": "string: what you are trying to establish",
                    }
                ],
            },
            indent=2,
        ),
        "",
        "If you need exact supporting evidence, populate `evidence_queries` with at most "
        f"{_EVIDENCE_QUERIES_PER_ROUND} bounded selectors; the harness will return the matching "
        f"records and ask you to conclude, for at most {MAX_AUDIT_EVIDENCE_ROUNDS} rounds. You may "
        "also answer immediately with `evidence_queries: []`.",
        "",
        "Respond with only the JSON object, no other text.",
    ]
    if evidence_appendix:
        lines.extend(
            [
                "",
                "Requested detailed evidence (resolved through the canonical repository query API). "
                "Use it to finalize your verdict; request more only if a specific question remains open:",
                evidence_appendix,
                "",
                "Now respond with the final JSON object only.",
            ]
        )
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


def _incomplete_result(
    context: dict[str, Any], *, backend: str, reason: str, metrics: dict[str, Any] | None = None
) -> dict[str, Any]:
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
        "audit_metrics": metrics or {},
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


def _resolve_evidence_queries(evidence_index: dict[str, Any], queries: Any) -> tuple[str, list[dict[str, Any]]]:
    """Resolve a model's requested evidence selectors through the canonical API.

    Returns ``(appendix_text, metrics_rows)``. Unknown selectors are ignored
    rather than erroring, and the whole batch is bounded by
    ``_EVIDENCE_QUERIES_PER_ROUND`` so a model cannot request the entire
    evidence universe in one shot.
    """
    from ..pipeline.audit_evidence import (
        EVIDENCE_EXPANSION_MAX_BYTES,
        EVIDENCE_EXPANSION_QUERY_LIMIT,
        query_evidence,
    )

    appendix: list[str] = []
    metrics_rows: list[dict[str, Any]] = []
    total_bytes = 0
    if not isinstance(queries, list):
        return "", metrics_rows
    for query in queries[:_EVIDENCE_QUERIES_PER_ROUND]:
        if not isinstance(query, dict):
            continue
        try:
            item = int(query["item"]) if query.get("item") is not None else None
        except (TypeError, ValueError):
            item = None
        response = query_evidence(
            evidence_index,
            item=item,
            tournament=query.get("tournament"),
            club=query.get("club"),
            age_group=query.get("age_group"),
            category=query.get("category"),
            unresolved=bool(query.get("unresolved")),
            limit=EVIDENCE_EXPANSION_QUERY_LIMIT,
        )
        serialized = json.dumps(response, ensure_ascii=False, default=str)
        size = len(serialized.encode("utf-8"))
        if total_bytes + size > EVIDENCE_EXPANSION_MAX_BYTES:
            metrics_rows.append(
                {
                    "category": query.get("category"),
                    "item": item,
                    "tournament": query.get("tournament"),
                    "club": query.get("club"),
                    "age_group": query.get("age_group"),
                    "unresolved": bool(query.get("unresolved")),
                    "matched_record_count": response.get("matched_record_count"),
                    "returned_record_count": 0,
                    "serialized_bytes": 0,
                    "truncated": True,
                    "skipped_due_to_round_budget": True,
                }
            )
            continue
        total_bytes += size
        metrics_rows.append(
            {
                "category": query.get("category"),
                "item": item,
                "tournament": query.get("tournament"),
                "club": query.get("club"),
                "age_group": query.get("age_group"),
                "unresolved": bool(query.get("unresolved")),
                "matched_record_count": response.get("matched_record_count"),
                "returned_record_count": response.get("returned_record_count"),
                "serialized_bytes": size,
                "truncated": response.get("truncated"),
            }
        )
        appendix.append(serialized)
    return "\n".join(appendix), metrics_rows


def _finalize_result(
    context: dict[str, Any],
    *,
    backend: str,
    parsed: dict[str, Any],
    metrics: dict[str, Any],
) -> dict[str, Any]:
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
        "audit_metrics": metrics,
    }

    errors = validate_audit_result(result)
    if errors:
        return _incomplete_result(
            context, backend=backend, reason=f"Judge response failed schema validation: {'; '.join(errors)}", metrics=metrics
        )
    return result


def run_headless_audit(
    context: dict[str, Any],
    backend: str,
    *,
    evidence_index: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run the headless audit path: build prompt, call *backend*, parse/validate.

    Never returns ``status: PASS`` unless the backend's response parsed
    cleanly and passed schema validation — any failure (backend error,
    unparseable response, invalid schema) returns an ``INCOMPLETE`` result
    instead, which the publish gate treats as blocking.

    When *evidence_index* is supplied, the model may request bounded detailed
    evidence and the harness resolves it through the same canonical query API
    the interactive path uses. The configured judge backends are single-turn,
    so this emulates retrieval by appending the results to one more prompt for
    at most ``MAX_AUDIT_EVIDENCE_ROUNDS`` rounds; the model is never handed the
    entire evidence universe.
    """
    from . import create_judge

    overview = context.get("evidence_index") or {}
    metrics: dict[str, Any] = {
        "overview_serialized_bytes": len(json.dumps(context, ensure_ascii=False, default=str).encode("utf-8")),
        "overview_budget_exceeded": bool((context.get("evidence_metrics") or {}).get("overview_budget_exceeded")),
        "evidence_rounds": 0,
        "evidence_queries": [],
        "evidence_bytes_returned": 0,
        "evidence_budget_hit": False,
        "backend_supports_tool_retrieval": False,
    }

    try:
        judge = create_judge(backend)
        raw = judge.judge(build_audit_prompt(context))
    except Exception as exc:  # noqa: BLE001 — surface as INCOMPLETE, never crash the audit
        return _incomplete_result(context, backend=backend, reason=f"Judge backend call failed: {exc}", metrics=metrics)

    parsed = _extract_json_object(raw)
    appendix: str | None = None
    rounds = 0
    while parsed is not None and evidence_index is not None and rounds < MAX_AUDIT_EVIDENCE_ROUNDS:
        queries = parsed.get("evidence_queries")
        if not isinstance(queries, list) or not queries:
            break
        appendix_text, rows = _resolve_evidence_queries(evidence_index, queries)
        rounds += 1
        metrics["evidence_rounds"] = rounds
        metrics["evidence_queries"].extend(rows)
        metrics["evidence_bytes_returned"] += sum(row.get("serialized_bytes") or 0 for row in rows)
        metrics["evidence_budget_hit"] = metrics["evidence_budget_hit"] or any(row.get("truncated") for row in rows)
        if not appendix_text:
            break
        appendix = appendix_text if appendix is None else appendix + "\n" + appendix_text
        try:
            raw = judge.judge(build_audit_prompt(context, evidence_appendix=appendix))
        except Exception as exc:  # noqa: BLE001
            return _incomplete_result(
                context, backend=backend, reason=f"Judge backend call failed: {exc}", metrics=metrics
            )
        parsed = _extract_json_object(raw)

    if parsed is None:
        return _incomplete_result(
            context, backend=backend, reason=f"Could not parse judge response as JSON: {raw[:500]!r}", metrics=metrics
        )
    return _finalize_result(context, backend=backend, parsed=parsed, metrics=metrics)
