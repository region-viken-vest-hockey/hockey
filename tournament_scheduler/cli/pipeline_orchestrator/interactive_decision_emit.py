"""Compose and emit the interactive Stage 3 decision context."""

from __future__ import annotations

from typing import Any, Mapping

from ...pipeline.run_log_paths import resolve_active_run_log_dir
from .interactive_state_io import (
    _current_run_id,
    _read_stage3_interactive_state,
)
from .judgment import _compute_verdict_tone
from .stage3_optimize_core import _maybe_run_stage3_cp_sat_shadow
from .verification import _baseline_hard_violations_for_plan, _mid_planning_decision_problem

_INTERACTIVE_STAGE_KEYS = {1: "config", 2: "scraping", 3: "planning", 4: "export"}


def _with_current_candidate_adoption(
    context: "Any",
    *,
    plan: dict[str, Any],
    problem: "dict[str, Any] | None",
    has_baseline: bool,
) -> "Any":
    """Keep the exact current hard-valid attempt adoptable from a repair context.

    A local-repair context is emitted for the attempt the session now holds.
    When a previous attempt exists as the session baseline, ``keep_baseline``
    restores that *previous* attempt, so without an explicit adopt-current
    action a hard-valid optimized attempt would have no route to finalization.
    This only exposes the existing ``apply_candidate`` transition (resolved
    against the context's own ``candidate_ref``); it changes no repair
    semantics and never offers to adopt a hard-invalid candidate.
    """
    from dataclasses import replace as _dc_replace

    if not has_baseline or "apply_candidate" in context.available_actions:
        return context
    if _baseline_hard_violations_for_plan(plan, problem):
        return context
    return _dc_replace(
        context, available_actions=tuple(context.available_actions) + ("apply_candidate",)
    )


def _decision_summary_for_checkpoint(
    stage_num: int, checkpoint: dict[str, Any], *, effective_config: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Build the ``checkpoint_summary`` dict ``build_decision_context`` expects,
    directly from a persisted stage checkpoint (issue #260 Phase 5's
    interactive stage mode reads checkpoints after the fact, rather than the
    in-flight local summaries ``_run_stageN`` build for the headless judge).

    Stage 1's ``sources``/``start_date``/``end_date`` are intentionally
    *not* stored in its checkpoint — they live in ``input.xlsx`` and are
    merged in dynamically by ``load_effective_config`` at runtime (see
    ``_run_stage1``) — so callers must pass the already-loaded
    *effective_config* for stage 1 rather than relying on *checkpoint*
    alone, or these facts silently read as empty/"?".
    """
    if stage_num == 1:
        cfg = effective_config or checkpoint
        return {
            "sources": len(cfg.get("sources", [])),
            "start_date": cfg.get("start_date", "?"),
            "end_date": cfg.get("end_date", "?"),
            "age_groups": cfg.get("age_groups", []),
            "clubs": cfg.get("clubs", []),
        }
    if stage_num == 2:
        return {
            "sources_scanned": len(checkpoint.get("sources", [])),
            "blocked": checkpoint.get("blocked", []),
            "source_details": checkpoint.get("sources", []),
        }
    if stage_num == 3:
        plan_obj = checkpoint.get("plan", {})
        tournaments = plan_obj.get("tournaments", []) if isinstance(plan_obj, dict) else []
        return {
            "tournaments_planned": len(tournaments),
            "warnings": checkpoint.get("warnings", []),
            "tone": _compute_verdict_tone(checkpoint),
        }
    # stage 4 / export
    return {
        "files_written": list((checkpoint.get("output_files") or {}).keys()),
        "errors": checkpoint.get("errors", []),
    }


def _emit_interactive_decision_context(
    stage_num: int, state: "Any", work_dir: str, *, input_path: str | None = None
) -> int:
    """Build, persist and print the :class:`DecisionContext` for the stage
    that was just completed, then return the process exit code (always 2 —
    "paused for decision" — distinct from 0/success and 1/hard failure, so a
    caller script can branch on it without parsing output)."""
    import json as _json

    from ...llm_judge.prompts import build_decision_context
    from ...pipeline.state import StageName

    stage_name = list(StageName)[stage_num - 1]
    checkpoint = state.read_stage(stage_name) or {}
    effective_config = None
    if stage_num == 1:
        from ...pipeline.stage1_config import load_effective_config

        effective_config = load_effective_config(state, input_path=input_path)
    summary = _decision_summary_for_checkpoint(stage_num, checkpoint, effective_config=effective_config)
    context = build_decision_context(_INTERACTIVE_STAGE_KEYS[stage_num], summary)
    payload = context.to_dict()

    try:
        from ...pipeline.run_log_paths import resolve_active_run_log_dir

        log_dir = resolve_active_run_log_dir(work_dir)
        log_dir.mkdir(parents=True, exist_ok=True)
        with open(log_dir / "decision_context.json", "w", encoding="utf-8") as fh:
            _json.dump(payload, fh, indent=2, ensure_ascii=False)
    except Exception:
        pass  # best-effort audit copy; stdout below is authoritative

    print(_json.dumps(payload, indent=2, ensure_ascii=False))
    return 2


def _host_team_missing_repair_context(
    plan: "dict[str, Any]",
    problem: "dict[str, Any] | None",
    *,
    run_id: str,
    candidate_ref: str,
    require_options: bool = False,
) -> "Any | None":
    """Repair :class:`DecisionContext` for *plan*, or ``None``.

    Only built when independent verification actually reports
    ``host_team_missing``, so a healthy plan never pays for option
    enumeration. The caller must never hand-edit participant/host values: the
    context exposes exactly the repository-generated option ids and their
    rejection evidence.

    *require_options* additionally suppresses the context when no legal local
    option exists, letting a caller fall back to the ordinary comparison
    context (which can still offer ``keep_baseline``); the first-attempt path
    keeps it ``False`` so the rejection evidence is always surfaced.
    """
    from ...host_team_missing_repair import build_host_team_missing_decision_context
    from ...planning_contract import extract_candidate

    violations = _baseline_hard_violations_for_plan(plan, problem)
    if not any(str(v).startswith("host_team_missing:") for v in violations):
        return None
    context = build_host_team_missing_decision_context(
        extract_candidate(plan),
        problem,
        run_id=run_id,
        candidate_ref=candidate_ref,
    )
    if require_options and not context.facts.get("repair_options"):
        return None
    return context


def _underfilled_roster_repair_context(
    plan: "dict[str, Any]",
    problem: "dict[str, Any] | None",
    *,
    run_id: str,
    candidate_ref: str,
    require_options: bool = False,
) -> "Any | None":
    """Repair context for a locally underfilled tournament roster, or ``None``.

    Companion to :func:`_host_team_missing_repair_context` for the other local
    size/participation defect: a tournament whose participant count is below
    the independently derived ``effective_team_count`` even though the
    canonical registered pool can fill it. The context exposes only
    repository-generated fill/swap option ids plus explicit per-candidate
    rejection evidence; the harness never hand-edits a participant list.
    """
    from ...planning_contract import extract_candidate
    from ...underfilled_roster_repair import build_underfilled_roster_decision_context

    violations = _baseline_hard_violations_for_plan(plan, problem)
    if not any(str(v).startswith("bye_team_not_allowed:") for v in violations):
        return None
    context = build_underfilled_roster_decision_context(
        extract_candidate(plan),
        problem,
        run_id=run_id,
        candidate_ref=candidate_ref,
    )
    if require_options and not context.facts.get("repair_options"):
        return None
    return context


def _placement_preserving_roster_repair_context(
    plan: "dict[str, Any]",
    problem: "dict[str, Any] | None",
    *,
    run_id: str,
    candidate_ref: str,
    require_options: bool = False,
) -> "Any | None":
    """Repair context for a participant conflict on an already-valid placement.

    A tournament whose host/date/arena/start time are already legal but which
    selects a double-booked (or duplicated) participant must be repaired by
    reselecting that participant first -- moving the whole tournament is a
    higher-cost change. This context exposes the repository-generated
    substitution option ids plus explicit rejection evidence so the cheap
    roster repair is always tried before any date/host or solver pass.
    """
    from ...placement_preserving_roster_repair import (
        build_placement_preserving_roster_decision_context,
    )
    from ...planning_contract import extract_candidate

    violations = _baseline_hard_violations_for_plan(plan, problem)
    conflict_codes = (
        "duplicate_participation_same_date:",
        "duplicate_team_in_tournament:",
    )
    if not any(str(v).startswith(conflict_codes) for v in violations):
        return None
    context = build_placement_preserving_roster_decision_context(
        extract_candidate(plan),
        problem,
        run_id=run_id,
        candidate_ref=candidate_ref,
    )
    if require_options and not context.facts.get("repair_options"):
        return None
    return context


def _local_repair_context(
    plan: "dict[str, Any]",
    problem: "dict[str, Any] | None",
    *,
    run_id: str,
    candidate_ref: str,
    require_options: bool = False,
) -> "Any | None":
    """Repository-generated repair context for *plan*, cheapest family first.

    The small local families (underfilled roster, host-team-missing,
    placement-preserving roster, manual placement) are tried first: a
    participant-level defect with an already-valid placement is repaired by
    reselecting the roster before any provider is allowed to move the
    tournament. Only when none of them exposes a legal option does the bounded
    neighborhood search run, so a localized defect that a direct fill/swap/rehost
    already repairs never pays for a solver pass. A family that only has
    rejection evidence must not shadow a different family that does have an
    option.

    When nothing has a legal option, *require_options* suppresses the context
    so a caller can fall back to the ordinary comparison context (which can
    still offer ``keep_baseline``); otherwise the most informative available
    context is returned so its rejection/exhaustion evidence is surfaced.
    """
    cheap = [
        builder(
            plan,
            problem,
            run_id=run_id,
            candidate_ref=candidate_ref,
            require_options=False,
        )
        for builder in (
            _underfilled_roster_repair_context,
            _host_team_missing_repair_context,
            _placement_preserving_roster_repair_context,
            _host_placement_repair_context,
        )
    ]
    cheap = [context for context in cheap if context is not None]
    for context in cheap:
        if context.facts.get("repair_options"):
            return context

    search = _search_neighborhood_repair_context(
        plan,
        problem,
        run_id=run_id,
        candidate_ref=candidate_ref,
        require_options=require_options,
    )
    if search is not None and search.facts.get("repair_options"):
        return search
    if require_options:
        return None
    if cheap:
        return cheap[0]
    return search


def _search_neighborhood_repair_context(
    plan: "dict[str, Any]",
    problem: "dict[str, Any] | None",
    *,
    run_id: str,
    candidate_ref: str,
    require_options: bool = False,
) -> "Any | None":
    """Bounded local-search fallback for a hard finding, or ``None``.

    Only built when independent verification still reports a hard violation
    and no cheaper local family had a legal option. It exposes repository
    verified neighborhood-search results, or the per-seed exhaustion evidence
    when the bounded search found none, so an escalation can distinguish
    "proven infeasible" from "search budget exhausted".
    """
    from ...planning_contract import extract_candidate
    from ...search_neighborhood_repair import (
        build_search_neighborhood_decision_context,
        findings_are_locally_searchable,
    )

    if problem is None:
        return None
    hard_violations = _baseline_hard_violations_for_plan(plan, problem)
    if not hard_violations:
        # Manual placement is soft/unresolved evidence, not a hard violation;
        # the bounded search must not replace the finalizable comparison
        # context for it.
        return None
    codes = [str(violation).split(":", 1)[0] for violation in hard_violations]
    if not findings_are_locally_searchable(codes):
        # Only a bounded participant/host search; a plan whose remaining hard
        # findings are not locally searchable keeps the ordinary context.
        return None
    context = build_search_neighborhood_decision_context(
        extract_candidate(plan),
        problem,
        run_id=run_id,
        candidate_ref=candidate_ref,
    )
    if require_options and not context.facts.get("repair_options"):
        return None
    return context


def _host_placement_repair_context(
    plan: "dict[str, Any]",
    problem: "dict[str, Any] | None",
    *,
    run_id: str,
    candidate_ref: str,
    require_options: bool = False,
) -> "Any | None":
    """Repair context for a tournament left as MANUAL PLACEMENT REQUIRED, or
    ``None`` when no responsibility-preserving placement is available.

    Manual placement is soft/unresolved evidence, not a hard violation, so
    this context is only offered when it actually has a legal move. A plan the
    optimizer cannot improve must still fall through to the ordinary
    comparison context and stay finalizable via ``keep_baseline``.
    """
    from ...host_placement_repair import (
        build_host_placement_decision_context,
        candidate_has_manual_slot_failure,
    )
    from ...planning_contract import extract_candidate

    if problem is None:
        return None
    candidate = extract_candidate(plan)
    if not candidate_has_manual_slot_failure(candidate):
        return None
    context = build_host_placement_decision_context(
        candidate,
        problem,
        run_id=run_id,
        candidate_ref=candidate_ref,
    )
    if require_options and not context.facts.get("repair_options"):
        return None
    if not context.facts.get("repair_options"):
        return None
    return context


def _emit_stage3_interactive_decision(
    state: "Any",
    work_dir: str,
    cfg: "dict[str, Any]",
    scraping: "dict[str, Any]",
    start: "Any",
    end: "Any",
    plan: "dict[str, Any]",
    log_fn: "Any",
    *,
    skip_auto_cp_sat_shadow: bool = False,
    suppress_auto_cp_sat_shadow: bool = False,
    stage3_elapsed_seconds: float = 0.0,
    candidate_transition: str = "create_baseline",
    search_arguments: "Mapping[str, Any] | None" = None,
) -> int:
    """Build, persist and print the Stage 3 :class:`DecisionContext` for the
    attempt that just ran (issue #260 P0).

    Unlike every other stage, which only ever offers the coarse
    proceed/abort-only context from :func:`_emit_interactive_decision_context`,
    Stage 3 gets the same nested ``optimize_plan``/``apply_candidate``/
    ``keep_baseline``/``request_operator`` decision loop the headless
    multi-seed path already drives via ``_decide_plan_adoption`` — the
    interactive harness itself is now the judge for this loop; deterministic
    validation and the loop-safety guards (stale/duplicate action rejection,
    per-search budgets, and a generous emergency circuit breaker) remain repo
    code. A raw attempt count is not a continuation gate: when another search
    is legal the context exposes concise ``search_history`` evidence and leaves
    ``optimize_plan`` available so the controller can choose a different
    strategy.

    State (the running best attempt, and any not-yet-adopted candidate from
    the most recent rerun) is persisted to a small JSON side-file next to the
    stage checkpoints (:func:`_stage3_interactive_state_path`) so it survives
    across the separate CLI invocations an interactive harness makes between
    checkpoints.

    issue #310: *skip_auto_cp_sat_shadow* is set by the caller when *plan*
    was itself just produced by an explicit ``optimize_plan(engine="cp_sat")``
    pass -- shadowing that candidate again here would re-solve the model CP-
    SAT just produced as if it were a fresh, unexamined baseline. In that
    case the explicit run's own diagnostics are surfaced under the same
    ``cp_sat_shadow`` fact key instead of running a second solve.
    *stage3_elapsed_seconds* is the wall-clock already spent this invocation
    on Stage 3 baseline/optimize work before this function was called; it is
    subtracted from ``stage3_wall_clock_ceiling_seconds`` (default 60s) to
    cap -- or entirely skip -- the automatic CP-SAT shadow's own budget, so
    one interactive Stage 3 invocation has a bounded total wall-clock cost
    rather than the shadow's configured budget being additive on top of an
    already-long baseline/optimize phase. This CLI process already returns
    control after exactly one Stage 3 attempt (see the module docstring), so
    the guard only needs to bound *this* invocation's automatic-shadow phase,
    not a nested/looping in-process search.
    """
    import json as _json
    from dataclasses import replace as _dc_replace
    from time import perf_counter

    from ...application.decisions import DecisionContext
    from ...application.stage3_progress import (
        build_attempt_record,
        build_search_history,
        upsert_attempt,
    )
    from ...application.stage3_session_store import Stage3SessionStore, fingerprint_plan
    from ...planning_contract import extract_candidate
    from ...stage3_ab import build_ab_report
    from ...stage3_decision import (
        STAGE3_DECISION_ACTIONS,
        _OPTIMIZE_PLAN_SCHEMAS,
        build_stage3_decision_context,
    )

    _t0 = perf_counter()
    run_id = _current_run_id(state)
    problem = _mid_planning_decision_problem(cfg, scraping, start, end, state.work_dir)

    # Capture the candidate the submitted action targeted *before* binding the
    # attempt's freshly produced candidate, so progress is measured against the
    # right revision and an identical no-op action can be recognised later.
    store = Stage3SessionStore(state.work_dir)
    session_before = store.load(expected_run_id=run_id)
    prior_fingerprint = session_before.candidate_fingerprint
    prior_revision = session_before.candidate_revision
    prior_hard_violations = session_before.latest_hard_violations()
    plan_fingerprint = fingerprint_plan(plan)
    plan_hard_violations = tuple(_baseline_hard_violations_for_plan(plan, problem))
    hard_violation_count = len(plan_hard_violations)
    made_progress = bool(plan_fingerprint) and (
        plan_fingerprint != prior_fingerprint
        or (
            prior_hard_violations is not None
            and hard_violation_count < prior_hard_violations
        )
    )
    record_action_id = (
        "optimize_plan" if candidate_transition == "run_search" else (candidate_transition or "create_baseline")
    )
    search_attempt = build_attempt_record(
        action_id=record_action_id,
        arguments=search_arguments,
        candidate_revision=prior_revision,
        candidate_fingerprint=prior_fingerprint,
        transition=candidate_transition,
        progress=made_progress,
        hard_violations=hard_violation_count,
    )
    search_history = build_search_history(
        upsert_attempt(session_before.search_attempts, search_attempt)
    )
    circuit_breaker_tripped = bool(search_history.get("circuit_breaker_tripped"))

    # Bind the exact candidate this attempt produced as the session's current
    # candidate *before* any candidate-scoped sub-decision is emitted.
    # Otherwise an arena-conflict (or repair) context computed for this
    # attempt could be checked against a previous revision's baseline, so a
    # responsibility excess that already existed in this candidate would be
    # misattributed to the local answer. Binding here makes the pending
    # context and every semantic guard refer to the same revision, and keeps
    # the transition lineage explicit (revision N -> N+1 only).
    try:
        store.bind_candidate(
            plan,
            run_id=run_id,
            source=candidate_transition,
            transition=candidate_transition,
            action_id="optimize_plan" if candidate_transition == "run_search" else candidate_transition,
            rationale="candidate produced for this Stage 3 attempt",
        )
    except Exception as exc:
        log_fn(f"stage3_interactive: could not bind candidate to Stage 3 session: {exc}")

    # Resolve every internal arena/time double-booking in *this* candidate
    # before offering the optimize/apply comparison at all -- an interactive
    # harness or headless judge decides which side of each collision keeps
    # the automatic slot; the other is demoted to manual placement in
    # *plan* itself (see ``arena_conflict_decisions``). A hard block from
    # ``planning_contract.verify_candidate``'s own arena_interval_conflict
    # check remains the backstop for any collision that reaches Stage 4
    # unresolved (e.g. re-introduced by a later optimize_plan pass).
    from .arena_conflict_decisions import _resolve_arena_conflict_decisions

    ice_time_for_age_group = (problem or {}).get("ice_time_minutes") or (problem or {}).get(
        "round_length_minutes"
    ) or {}
    arena_pause_code = _resolve_arena_conflict_decisions(
        state, plan, ice_time_for_age_group, log_fn, interactive=True
    )
    if arena_pause_code is not None:
        return arena_pause_code

    # Arena-conflict decisions mutate the candidate in memory by demoting the
    # losing tournament to manual placement. Stage 4 and later invocations read
    # the Stage 3 checkpoint from disk, so persist the resolved candidate before
    # offering apply/keep/optimize. Otherwise Stage 3 can appear "done" while
    # Stage 4 verifies the pre-resolution checkpoint and fails on the same hard
    # arena_interval_conflict again.
    try:
        from ...pipeline.state import StageName, StageStatus

        state.write_stage(StageName.PLANNING, plan, status=StageStatus.DONE)
    except Exception as exc:
        log_fn(f"stage3_interactive: could not persist resolved arena-conflict placements: {exc}")

    interactive_state = _read_stage3_interactive_state(state, expected_run_id=run_id)
    attempts_used = int(interactive_state.get("attempts_used", 0))

    try:
        shadow_source_candidate = extract_candidate(plan)
    except (ValueError, KeyError):
        shadow_source_candidate = None

    if suppress_auto_cp_sat_shadow:
        log_fn("stage3: skipping automatic CP-SAT shadow after local checkpoint repair")
        cp_sat_shadow = None
        _shadow_seconds = 0.0
    elif skip_auto_cp_sat_shadow:
        log_fn(
            "stage3: skipping automatic CP-SAT shadow -- an explicit "
            "optimize_plan(engine=\"cp_sat\") pass just produced this plan"
        )
        cp_sat_shadow = (
            {
                "attempted": True,
                "available": True,
                "engine": "cp_sat",
                "explicit": True,
                "skipped_auto_shadow_reason": "explicit_cp_sat_just_ran",
                "candidate_source": shadow_source_candidate.get("source") if shadow_source_candidate else None,
            }
            if shadow_source_candidate is not None
            else None
        )
        _shadow_seconds = 0.0
    else:
        ceiling = float(cfg.get("stage3_wall_clock_ceiling_seconds", 60.0))
        remaining_budget = (ceiling - stage3_elapsed_seconds) if ceiling > 0 else None
        _t_shadow_start = perf_counter()
        cp_sat_shadow = _maybe_run_stage3_cp_sat_shadow(
            cfg,
            problem,
            shadow_source_candidate,
            log_fn,
            state=state,
            run_id=run_id,
            max_budget_seconds=remaining_budget,
        )
        _shadow_seconds = perf_counter() - _t_shadow_start

    if attempts_used <= 0 or "best_plan" not in interactive_state:
        # First attempt this run: nothing to compare against yet — auto-
        # baseline, same as the headless multi-seed loop's
        # "best_plan is None -> adopt" first iteration.
        attempts_used = 1
        summary = _decision_summary_for_checkpoint(3, plan)
        if cp_sat_shadow is not None:
            summary = {**summary, "cp_sat_shadow": cp_sat_shadow}
        baseline_hard_violations = plan_hard_violations
        repair_context = _local_repair_context(
            plan,
            problem,
            run_id=run_id,
            candidate_ref=f"stage3_interactive:attempt_{attempts_used}",
        )
        if repair_context is not None:
            context = repair_context
            if cp_sat_shadow is not None:
                context = _dc_replace(context, facts={**context.facts, "cp_sat_shadow": cp_sat_shadow})
        else:
            available = ["optimize_plan", "keep_baseline", "request_operator", "abort"]
            if circuit_breaker_tripped:
                available.remove("optimize_plan")
            if cp_sat_shadow is not None and cp_sat_shadow.get("candidate_ref"):
                # issue #310: a verified automatic CP-SAT candidate is directly
                # applicable via candidate_ref, resolved against
                # stage3_cpsat_cache -- without this, the only way to adopt it
                # was to re-run optimize_plan(engine="cp_sat") and re-solve.
                available.append("apply_candidate")
            context = DecisionContext(
                run_id=run_id,
                capability="stage3_interactive",
                stage="planning",
                objective=(
                    "Decide whether this Stage 3 plan is good enough to finalize "
                    "(keep_baseline), or another optimization attempt is worth "
                    "the search budget (optimize_plan)."
                ),
                facts=summary,
                baseline_hard_violations=tuple(baseline_hard_violations),
                available_actions=tuple(available),
                # issue #262 P0: optimize_plan on this first attempt already
                # runs the Stage 3 v2 optimizer too (see _run_stage3_v2_optimize),
                # so it needs the same schema the else-branch below attaches.
                action_parameters=(
                    {"optimize_plan": _OPTIMIZE_PLAN_SCHEMAS["v2_optimizer"]}
                    if "optimize_plan" in available
                    else {}
                ),
            )
        interactive_state = {
            "run_id": run_id,
            "attempts_used": attempts_used,
            "best_attempt": attempts_used,
            "best_plan": plan,
        }
    else:
        attempts_used += 1
        best_plan = interactive_state["best_plan"]
        best_attempt = interactive_state.get("best_attempt", 1)
        # A later attempt that re-introduces a local hard defect (host_team_missing
        # or an underfilled roster) must not be answered by yet another opaque
        # optimize_plan retry loop: when a local repair exists, expose it here too.
        # When none exists, fall through to the ordinary comparison context so
        # keep_baseline stays available.
        repair_context = _local_repair_context(
            plan,
            problem,
            run_id=run_id,
            candidate_ref=f"stage3_interactive:attempt_{attempts_used}",
            require_options=True,
        )
        report = None
        if repair_context is not None:
            # The repair context is about the current attempt, but
            # ``keep_baseline`` would restore the previous baseline; expose an
            # explicit route to finalize the current hard-valid attempt.
            context = _with_current_candidate_adoption(
                repair_context, plan=plan, problem=problem, has_baseline=True
            )
        else:
            try:
                report = build_ab_report(extract_candidate(best_plan), extract_candidate(plan), problem)
            except (ValueError, KeyError) as exc:
                log_fn(f"stage3_interactive attempt {attempts_used}: could not build A/B report: {exc}")

            available = list(STAGE3_DECISION_ACTIONS)
            if circuit_breaker_tripped:
                available.remove("optimize_plan")
            if report is not None:
                context = build_stage3_decision_context(
                    report,
                    run_id=run_id,
                    baseline_ref=f"stage3_interactive:attempt_{best_attempt}",
                    candidate_ref=f"stage3_interactive:attempt_{attempts_used}",
                    objective=(
                        f"Decide whether Stage 3 attempt {attempts_used} should replace "
                        f"the current best attempt ({best_attempt}), request another "
                        "optimization attempt, ask the operator, or keep the current best."
                    ),
                    # issue #262 P0: optimize_plan now runs the Stage 3 v2
                    # optimizer (see _run_stage3_v2_optimize), not a legacy
                    # SeasonPlanner rerun -- attach the schema that matches what
                    # will actually execute.
                    optimize_plan_schema="v2_optimizer",
                )
                context = _dc_replace(context, available_actions=tuple(available) + ("abort",))
            else:
                # A/B report couldn't be built — fall back to keep/abort only,
                # never silently apply an uncompared candidate. Still
                # independently verify the current best plan so a hard-failing
                # baseline can't be finalized via keep_baseline just because the
                # A/B comparison itself broke.
                context = DecisionContext(
                    run_id=run_id,
                    capability="stage3_optimize",
                    stage="planning",
                    objective="Could not build an old-vs-new comparison report for this attempt.",
                    baseline_hard_violations=tuple(_baseline_hard_violations_for_plan(best_plan, problem)),
                    available_actions=("keep_baseline", "request_operator", "abort"),
                )
        if cp_sat_shadow is not None:
            context = _dc_replace(context, facts={**context.facts, "cp_sat_shadow": cp_sat_shadow})
        interactive_state["attempts_used"] = attempts_used
        interactive_state["pending_candidate"] = plan
        # A prior attempt may have been a Pareto search (issue #264 P1) --
        # drop its plural pending_candidates so a later apply_candidate
        # decision resolves against this attempt's single candidate, not a
        # stale multi-candidate list from two attempts ago.
        interactive_state.pop("pending_candidates", None)
        interactive_state["pending_attempt"] = attempts_used

    context = _dc_replace(context, facts={**context.facts, "search_history": search_history})
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
    interactive_state["last_context"] = context.to_dict()

    # Persist the freshly emitted pending decision into the canonical
    # interactive Stage 3 session. The session -- not a side file -- owns
    # candidate revision/fingerprint, pending-decision scope and transition
    # provenance; the legacy ``stage3_interactive_state.json`` is written by
    # the store only as a non-authoritative compatibility mirror.
    try:
        store.record_emission(
            interactive_state,
            candidate=plan,
            candidate_revision=attempts_used,
            run_id=run_id,
            transition=candidate_transition,
            action_id="optimize_plan" if candidate_transition == "run_search" else candidate_transition,
            search_attempt=search_attempt,
        )
    except Exception as exc:
        log_fn(f"stage3_interactive attempt {attempts_used}: could not persist Stage 3 session: {exc}")

    # issue #264 P0: append this attempt's independently-computed
    # verify/score evidence to the durable, run-scoped attempt log --
    # unlike interactive_state above, this survives past apply_candidate/
    # keep_baseline clearing the pending-decision side-state, so a later
    # Stage 4 evidence bundle can show every attempt that was tried, not
    # just the one ultimately selected.
    try:
        from ...planning_contract import extract_candidate
        from ...pipeline.evidence_bundle import append_stage3_attempt_log_entry, build_stage3_attempt_entry

        append_stage3_attempt_log_entry(
            state.work_dir,
            build_stage3_attempt_entry(attempt=attempts_used, candidate=extract_candidate(plan), problem=problem),
        )
    except Exception as exc:
        log_fn(f"stage3_interactive attempt {attempts_used}: could not append attempt-log entry: {exc}")

    if cp_sat_shadow is not None:
        # issue #288: the automatic CP-SAT shadow comparison is evidence
        # alongside the attempt that was actually decided on, not a
        # candidate of its own -- record it as its own entry so a reviewer
        # sees it was attempted even though it never became pending_candidate.
        try:
            append_stage3_attempt_log_entry(
                state.work_dir,
                {**cp_sat_shadow, "attempt": attempts_used, "engine": "cp_sat_shadow"},
            )
        except Exception as exc:
            log_fn(f"stage3_interactive attempt {attempts_used}: could not append cp_sat shadow attempt-log entry: {exc}")

    try:
        from ...pipeline.run_manifest import RunManifest

        decision_context_seconds = max(0.0, (perf_counter() - _t0) - _shadow_seconds)
        RunManifest(state.work_dir).record_timing("stage3_decision_context_seconds", decision_context_seconds)
    except Exception as exc:
        log_fn(f"stage3_interactive attempt {attempts_used}: could not record decision-context timing: {exc}")

    payload = context.to_dict()
    try:
        log_dir = resolve_active_run_log_dir(work_dir)
        log_dir.mkdir(parents=True, exist_ok=True)
        with open(log_dir / "decision_context.json", "w", encoding="utf-8") as fh:
            _json.dump(payload, fh, indent=2, ensure_ascii=False)
    except Exception:
        pass  # best-effort audit copy; stdout below is authoritative

    print(_json.dumps(payload, indent=2, ensure_ascii=False))
    return 2
