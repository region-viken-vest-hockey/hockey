"""Resolve interactive decisions supplied by a shared-host UI/agent."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .interactive_state_io import (
    _clear_shared_host_state,
    _current_run_id,
    _read_shared_host_state,
    _write_shared_host_state,
)

def _resolve_shared_host_decisions(
    state: "Any",
    cfg: "dict[str, Any]",
    scraping: "dict[str, Any]",
    start: "Any",
    end: "Any",
    log_fn: "Any",
    *,
    interactive: bool,
) -> "tuple[int | None, list[dict[str, Any]]]":
    """Resolve every shared/joint-club hosting decision for this run.

    The same :class:`~..application.decisions.DecisionContext` either gets
    auto-answered by a headless judge or pauses (exit code 2) for the
    harness to answer via ``run --interactive --decision-action`` — never
    silently defaulted, matching every other decision point in this module.

    Returns ``(pause_exit_code, shared_host_decisions)``:

    - ``pause_exit_code`` is ``None`` when every joint registration this run
      is resolved (the common case — no joint registrations at all resolves
      immediately with an empty list). Callers thread ``shared_host_decisions``
      into Stage 3's config (it doubles as both the ``chosen_club`` lookup
      and the provenance list — see
      ``stage3_planning._shared_host_choices_from_config``).
    - When not ``None`` (only possible when *interactive* is True and no
      headless judge is configured), the caller must return that exit code
      immediately without running Stage 3 — the pending decision's context
      has already been printed/persisted.

    *interactive* False (the auto-confirmed ``run`` / ``_cmd_run`` path,
    never paused): with no headless judge configured, this matches every
    other decision point's established no-judge legacy fallback —
    deterministic constituent order, unchanged existing behavior.
    """
    import json as _json

    from ...application.decisions import decide, record_llm_decision
    from ...llm_judge import get_judge_if_headless
    from ...pipeline.run_log_paths import resolve_active_run_log_dir
    from ...pipeline.stage3_planning import compute_shared_registration_facts
    from ...shared_host_decision import (
        build_shared_host_decision_context,
        build_shared_host_decision_prompt,
        parse_shared_host_verdict,
        shared_host_decision_record,
    )

    run_id = _current_run_id(state)
    work_dir = str(state.work_dir)
    saved = _read_shared_host_state(state, expected_run_id=run_id)
    decisions: list[dict[str, Any]] = list(saved.get("decisions") or [])
    unresolved: list[dict[str, str]] = list(saved.get("unresolved") or [])
    considered_keys = {(d.get("registration"), d.get("age_group")) for d in decisions}
    considered_keys |= {(u.get("registration"), u.get("age_group")) for u in unresolved}

    try:
        facts_rows = compute_shared_registration_facts(cfg, scraping, start, end)
    except Exception as exc:
        log_fn(f"Delt vertskap: kunne ikke beregne fakta — hopper over: {exc}")
        facts_rows = []
    pending_rows = [
        row for row in facts_rows
        if (row.get("registration"), row.get("age_group")) not in considered_keys
    ]

    if not pending_rows:
        _clear_shared_host_state(state)
        return None, decisions

    try:
        judge = get_judge_if_headless()
    except ValueError:
        judge = None

    if judge is not None:
        for facts in pending_rows:
            registration = str(facts.get("registration", ""))
            age_group = str(facts.get("age_group", ""))
            context = build_shared_host_decision_context(run_id, registration, age_group, facts)
            try:
                raw_verdict = judge.judge(build_shared_host_decision_prompt(context))
            except RuntimeError as exc:
                log_fn(f"Delt vertskap {registration} ({age_group}): dommer-kall feilet — hopper over: {exc}")
                unresolved.append({"registration": registration, "age_group": age_group})
                continue
            action = parse_shared_host_verdict(context, raw_verdict)
            result = decide(context, action)
            try:
                record_llm_decision(work_dir, context, action, result)
            except Exception as exc:
                log_fn(f"Delt vertskap {registration} ({age_group}): record_llm_decision feilet: {exc}")
            if not result.accepted or action.action_id != "assign_shared_host":
                log_fn(
                    f"Delt vertskap {registration} ({age_group}): dommer valgte "
                    f"{action.action_id!r} ({result.rejection_reason or 'ingen automatisk plassering'}) "
                    "— faller tilbake til deterministisk rekkefølge."
                )
                unresolved.append({"registration": registration, "age_group": age_group})
                continue
            chosen_club = str(action.arguments.get("chosen_club", ""))
            decisions.append(
                shared_host_decision_record(
                    registration, age_group, chosen_club, str(action.rationale or ""),
                    decided_by="llm", decided_at=datetime.now(timezone.utc).isoformat(),
                )
            )
            log_fn(
                f"Delt vertskap {registration} ({age_group}): dommer valgte {chosen_club} "
                f"({(action.rationale or '')[:200]})"
            )
        _clear_shared_host_state(state)
        return None, decisions

    if not interactive:
        return None, decisions

    # Interactive harness, no headless judge: pause for exactly one pending
    # decision per invocation, same one-decision-per-call contract as every
    # other Stage 3 interactive decision point in this module.
    facts = pending_rows[0]
    registration = str(facts.get("registration", ""))
    age_group = str(facts.get("age_group", ""))
    context = build_shared_host_decision_context(run_id, registration, age_group, facts)
    _write_shared_host_state(
        state,
        {
            "run_id": run_id,
            "decisions": decisions,
            "unresolved": unresolved,
            "pending": {"registration": registration, "age_group": age_group},
            "last_context": context.to_dict(),
        },
    )
    payload = context.to_dict()
    try:
        log_dir = resolve_active_run_log_dir(work_dir)
        log_dir.mkdir(parents=True, exist_ok=True)
        with open(log_dir / "decision_context.json", "w", encoding="utf-8") as fh:
            _json.dump(payload, fh, indent=2, ensure_ascii=False)
    except Exception:
        pass
    print(_json.dumps(payload, indent=2, ensure_ascii=False))
    return 2, []
