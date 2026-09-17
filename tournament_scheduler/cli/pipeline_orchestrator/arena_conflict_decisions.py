"""Resolve internal arena/time double-booking decisions supplied by a
harness/agent or a headless judge (mirrors ``shared_host_decisions.py``)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping

from ...host_team_missing_repair import candidate_fingerprint

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


def _candidate_fingerprint(candidate: Mapping[str, Any]) -> str:
    """Fingerprint the exact candidate checkpoint an arena context came from."""
    return candidate_fingerprint(candidate)


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
                "candidate_fingerprint": _candidate_fingerprint(candidate),
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


def _collision_facts_with_keys(
    candidate: "dict[str, Any]", ice_time_for_age_group: "dict[str, int]"
) -> "list[dict[str, Any]]":
    """Collision facts tagged with their stable ``collision_key``."""
    from ...arena_conflict_decision import collision_key

    facts_rows = _collision_facts(candidate, ice_time_for_age_group)
    for facts in facts_rows:
        facts["_key"] = collision_key(facts)
    return facts_rows


def _apply_recorded_arena_decisions(
    candidate: "dict[str, Any]",
    decisions: "list[dict[str, Any]]",
    ice_time_for_age_group: "dict[str, int]",
) -> "set[Any]":
    """Apply every already-recorded decision to *candidate* by stable key.

    Returns the set of stable keys that were successfully applied. A record
    whose key matches but whose roles cannot be mapped cleanly (for example
    both sides share the same ``age_group``/``host_club`` label) is left
    unapplied so a still-real collision stays visible instead of being
    hidden by its old record.
    """
    from ...arena_conflict_decision import side_label

    facts_rows = _collision_facts_with_keys(candidate, ice_time_for_age_group)
    applied: "set[Any]" = set()
    for record in decisions:
        key = record.get("key")
        match = next((f for f in facts_rows if f["_key"] == key), None)
        if match is None:
            continue
        by_label = {side_label(s): s["tournament_id"] for s in match["sides"]}
        keep = by_label.get(record.get("keep_side", ""))
        manual_id = by_label.get(record.get("manual_side", ""))
        if not keep or not manual_id or keep == manual_id:
            continue
        _apply_arena_conflict_decision(candidate, keep, manual_id, str(record.get("rationale", "")))
        applied.add(key)
    return applied


def _pending_arena_collisions(
    candidate: "dict[str, Any]",
    decisions: "list[dict[str, Any]]",
    unresolved: "list[dict[str, Any]]",
    ice_time_for_age_group: "dict[str, int]",
) -> "list[dict[str, Any]]":
    """Return the still-unresolved collision facts after applying recordings.

    A manual placement can remove several interval pairs involving the same
    tournament, so this recomputes on the already-demoted candidate rather
    than asking about stale pairs that no longer exist.
    """
    applied = _apply_recorded_arena_decisions(candidate, decisions, ice_time_for_age_group)
    suppressed = applied | {entry.get("key") for entry in unresolved}
    return [
        facts
        for facts in _collision_facts_with_keys(candidate, ice_time_for_age_group)
        if facts["_key"] not in suppressed
    ]


def _arena_conflict_context(run_id: str, facts: "dict[str, Any]") -> "tuple[Any, dict[str, Any]]":
    """Build the pending :class:`DecisionContext` and marker for one collision."""
    from ...arena_conflict_decision import build_arena_conflict_decision_context

    clean_facts = {key: value for key, value in facts.items() if key != "_key"}
    return build_arena_conflict_decision_context(run_id, clean_facts), {"key": facts["_key"]}


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

    # Apply every already-resolved collision to *this* candidate and recompute
    # the still-real ones by stable key (Stage 3 rebuilds tournaments with
    # fresh ids every invocation).
    pending_rows = _pending_arena_collisions(candidate, decisions, unresolved, ice_time_for_age_group)
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
                clean_facts,
                keep,
                manual_id,
                str(action.rationale or ""),
                decided_by="llm",
                decided_at=datetime.now(timezone.utc).isoformat(),
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
