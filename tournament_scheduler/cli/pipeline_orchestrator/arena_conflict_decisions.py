"""Resolve internal arena/time double-booking decisions supplied by a
harness/agent or a headless judge (mirrors ``shared_host_decisions.py``)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .interactive_state_io import (
    _clear_arena_conflict_state,
    _current_run_id,
    _read_arena_conflict_state,
    _write_arena_conflict_state,
)


def _candidate_dict(plan: "dict[str, Any]") -> "dict[str, Any] | None":
    """Return the mutable candidate dict (``{"tournaments": [...], ...}``)
    inside *plan*, whatever checkpoint shape it was loaded in (mirrors
    ``planning_contract.extract_candidate`` but returns the *original*
    nested dict, not a copy, so mutations are visible to the caller)."""
    if not isinstance(plan, dict):
        return None
    if "tournaments" in plan:
        return plan
    inner = plan.get("plan")
    if isinstance(inner, dict) and "tournaments" in inner:
        return inner
    return None


def _collision_facts(candidate: "dict[str, Any]", ice_time_for_age_group: "dict[str, int]") -> "list[dict[str, Any]]":
    """Return structured facts for every current arena/time collision pair
    in *candidate*'s tournaments (excludes tournaments already demoted to
    manual placement -- their start_time is cleared, so they're already
    outside ``tournament_interval``'s collision detection)."""
    from ...arena_conflicts import arena_interval_collision_pairs, tournament_intervals
    from ...serialization.season_plan import tournament_from_dict

    tournaments = [t for t in candidate.get("tournaments", []) if isinstance(t, dict) and not t.get("cancelled")]
    tournament_objs = [tournament_from_dict(t) for t in tournaments]
    intervals = tournament_intervals(tournament_objs, ice_time_for_age_group)
    team_counts = {t.get("id", ""): len(t.get("teams") or []) for t in tournaments}

    facts: list[dict[str, Any]] = []
    for current, other in arena_interval_collision_pairs(intervals):
        facts.append(
            {
                "arena": current.arena,
                "date": current.date,
                "overlap": f"{current.interval_label} / {other.interval_label}",
                "sides": [
                    {
                        "tournament_id": current.tournament_id,
                        "age_group": current.age_group,
                        "host_club": current.host_club or "",
                        "interval": current.interval_label,
                        "team_count": team_counts.get(current.tournament_id, 0),
                    },
                    {
                        "tournament_id": other.tournament_id,
                        "age_group": other.age_group,
                        "host_club": other.host_club or "",
                        "interval": other.interval_label,
                        "team_count": team_counts.get(other.tournament_id, 0),
                    },
                ],
            }
        )
    return facts


def _apply_arena_conflict_decision(
    candidate: "dict[str, Any]", keep_tournament_id: str, manual_tournament_id: str, rationale: str
) -> None:
    """Demote the losing tournament to manual placement in *candidate*:
    clear its start time (so it no longer occupies an arena interval, the
    same way any other not-yet-scheduled tournament is represented) and
    flag it via ``manual_booking_reason``, then record it under
    ``manual_arena_conflict_placements`` for export/review visibility
    (mirrors ``unresolved_external_conflicts``)."""
    for tournament in candidate.get("tournaments", []):
        if not isinstance(tournament, dict) or tournament.get("id") != manual_tournament_id:
            continue
        tournament["start_time"] = None
        tournament["manual_booking_reason"] = (
            f"Arena/time collision with tournament {keep_tournament_id} at "
            f"{tournament.get('arena', '?')} on {tournament.get('date', '?')}; needs manual "
            "re-scheduling to a different slot."
        )
        placements = candidate.setdefault("manual_arena_conflict_placements", [])
        placements.append(
            {
                "tournament_id": manual_tournament_id,
                "kept_tournament_id": keep_tournament_id,
                "arena": tournament.get("arena", ""),
                "age_group": tournament.get("age_group", ""),
                "date": tournament.get("date", ""),
                "reason": rationale or "arena/time collision resolved in favor of the other tournament",
            }
        )
        return


def _resolve_arena_conflict_decisions(
    state: "Any",
    plan: "dict[str, Any] | None",
    ice_time_for_age_group: "dict[str, int]",
    log_fn: "Any",
    *,
    interactive: bool,
) -> "int | None":
    """Resolve every current arena/time collision in *plan* in place.

    Same contract as ``shared_host_decisions._resolve_shared_host_decisions``:
    a headless judge answers every pending collision automatically; an
    interactive harness with no headless judge pauses (exit code 2) for one
    collision at a time via ``run --interactive --decision-action``, applying
    already-answered collisions (keyed by the stable
    ``arena_conflict_decision.collision_key``, not by ephemeral tournament
    ids) before pausing on the next unresolved one.

    Returns ``None`` when every current collision is resolved (the common
    case -- no collisions at all resolves immediately). Otherwise returns
    the pause exit code the caller must return immediately without
    proceeding to the Stage 3 optimize/apply decision.
    """
    import json as _json

    from ...application.decisions import decide, record_llm_decision
    from ...arena_conflict_decision import (
        arena_conflict_decision_record,
        build_arena_conflict_decision_context,
        build_arena_conflict_decision_prompt,
        collision_key,
        parse_arena_conflict_verdict,
    )
    from ...llm_judge import get_judge_if_headless
    from ...pipeline.run_log_paths import resolve_active_run_log_dir

    candidate = _candidate_dict(plan) if plan is not None else None
    if candidate is None:
        return None

    run_id = _current_run_id(state)
    work_dir = str(state.work_dir)
    saved = _read_arena_conflict_state(state, expected_run_id=run_id)
    decisions = list(saved.get("decisions") or [])
    unresolved = list(saved.get("unresolved") or [])
    unresolved_keys = {u.get("key") for u in unresolved}
    applied_decision_keys: set[Any] = set()

    # Apply every already-resolved collision to *this* candidate first --
    # Stage 3 rebuilds tournaments with fresh ids/collisions every
    # invocation, so a decision made against an earlier attempt's plan has
    # to be re-applied by matching the stable key, not the (now different)
    # tournament ids it originally recorded.
    facts_rows = _collision_facts(candidate, ice_time_for_age_group)
    for facts in facts_rows:
        facts["_key"] = collision_key(facts)

    from ...arena_conflict_decision import side_label

    for record in decisions:
        key = record.get("key")
        match = next((f for f in facts_rows if f["_key"] == key), None)
        if match is None:
            continue
        by_label = {side_label(s): s["tournament_id"] for s in match["sides"]}
        keep = by_label.get(record.get("keep_side", ""))
        manual_id = by_label.get(record.get("manual_side", ""))
        if not keep or not manual_id or keep == manual_id:
            # Stable key matched but the recorded roles don't map cleanly
            # onto this rebuild's two sides (e.g. both sides share the same
            # age_group/host_club label) -- leave as a hard collision rather
            # than guess which one the operator meant. Importantly, do not
            # mark this historical key as applied: a still-real collision must
            # be surfaced again rather than hidden by its old record.
            continue
        _apply_arena_conflict_decision(candidate, keep, manual_id, str(record.get("rationale", "")))
        applied_decision_keys.add(key)

    # Recompute after applying previous demotions. One manual placement can
    # remove several interval pairs involving the same tournament; do not ask
    # the harness to decide stale pairs that no longer exist in the candidate
    # that Stage 4 will verify.
    facts_rows = _collision_facts(candidate, ice_time_for_age_group)
    for facts in facts_rows:
        facts["_key"] = collision_key(facts)
    suppressed_keys = applied_decision_keys | unresolved_keys
    pending_rows = [f for f in facts_rows if f["_key"] not in suppressed_keys]
    if not pending_rows:
        _clear_arena_conflict_state(state)
        return None

    try:
        judge = get_judge_if_headless()
    except ValueError:
        judge = None

    if judge is not None:
        for facts in pending_rows:
            clean_facts = {k: v for k, v in facts.items() if k != "_key"}
            context = build_arena_conflict_decision_context(run_id, clean_facts)
            try:
                raw_verdict = judge.judge(build_arena_conflict_decision_prompt(context))
            except RuntimeError as exc:
                log_fn(f"Arena-kollisjon {facts['arena']} {facts['date']}: dommer-kall feilet — hopper over: {exc}")
                unresolved.append({"key": facts["_key"]})
                continue
            action = parse_arena_conflict_verdict(context, raw_verdict)
            result = decide(context, action)
            try:
                record_llm_decision(work_dir, context, action, result)
            except Exception as exc:
                log_fn(f"Arena-kollisjon {facts['arena']} {facts['date']}: record_llm_decision feilet: {exc}")
            if not result.accepted or action.action_id != "resolve_arena_conflict":
                log_fn(
                    f"Arena-kollisjon {facts['arena']} {facts['date']}: dommer valgte "
                    f"{action.action_id!r} ({result.rejection_reason or 'ingen automatisk løsning'}) "
                    "— forblir en hard hindring inntil operatøren avgjør."
                )
                unresolved.append({"key": facts["_key"]})
                continue
            keep = str(action.arguments.get("keep_tournament_id", ""))
            side_ids = {s["tournament_id"] for s in facts["sides"]}
            if keep not in side_ids:
                unresolved.append({"key": facts["_key"]})
                continue
            manual_id = next(iter(side_ids - {keep}))
            record = arena_conflict_decision_record(
                clean_facts, keep, manual_id, str(action.rationale or ""),
                decided_by="llm", decided_at=datetime.now(timezone.utc).isoformat(),
            )
            decisions.append(record)
            _apply_arena_conflict_decision(candidate, keep, manual_id, str(action.rationale or ""))
            log_fn(f"Arena-kollisjon {facts['arena']} {facts['date']}: dommer beholdt {keep}")
        _write_arena_conflict_state(state, {"run_id": run_id, "decisions": decisions, "unresolved": unresolved})
        return None

    if not interactive:
        return None

    facts = pending_rows[0]
    clean_facts = {k: v for k, v in facts.items() if k != "_key"}
    context = build_arena_conflict_decision_context(run_id, clean_facts)
    _write_arena_conflict_state(
        state,
        {
            "run_id": run_id,
            "decisions": decisions,
            "unresolved": unresolved,
            "pending": {"key": facts["_key"]},
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
    return 2
