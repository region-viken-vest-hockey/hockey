"""``rvv-miniputt plan`` — harness-neutral candidate verification/scoring (issue #257).

These commands operate only on the stable ``planning_problem``/``candidate``
contracts in :mod:`tournament_scheduler.planning_contract`. They do not call
into ``SeasonPlanner`` and do not require an LLM, so any planner
implementation's output can be checked the same way.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from typing import Any, Dict

from rich.console import Console

_console = Console()


def _load_json_file(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _parse_weight_overrides(raw: Any) -> "tuple[Dict[str, float], Dict[str, Dict[str, float]]]":
    """Parse repeated ``--weight`` args into global and per-age-group overrides.

    Accepts two forms:

    - ``--weight NAME=VALUE`` — overrides the weight globally.
    - ``--weight AGE_GROUP:NAME=VALUE`` — overrides the weight for just that
      age group (issue #257 follow-up: a single global weight vector can't
      resolve every age group's opponent-diversity/turnaround tradeoff, so
      the CLI needs to be able to tune weights per age group).
    """
    weights: Dict[str, float] = {}
    per_age_group: Dict[str, Dict[str, float]] = {}
    for item in raw or []:
        scope, colon, rest = item.partition(":")
        if not colon:
            name, sep, value = item.partition("=")
            if not sep:
                raise ValueError(f"Invalid --weight {item!r}, expected NAME=VALUE or AGE_GROUP:NAME=VALUE")
            weights[name.strip()] = float(value)
            continue
        name, sep, value = rest.partition("=")
        if not sep:
            raise ValueError(f"Invalid --weight {item!r}, expected NAME=VALUE or AGE_GROUP:NAME=VALUE")
        per_age_group.setdefault(scope.strip(), {})[name.strip()] = float(value)
    return weights, per_age_group


def _cmd_plan(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt plan ...`` — dispatches to sub-subcommands."""
    if args.plan_command == "verify":
        return _cmd_plan_verify(args)
    if args.plan_command == "score":
        return _cmd_plan_score(args)
    if args.plan_command == "problem":
        return _cmd_plan_problem(args)
    if args.plan_command == "optimize":
        return _cmd_plan_optimize(args)
    if args.plan_command == "ab":
        return _cmd_plan_ab(args)
    if args.plan_command == "ab-participants":
        return _cmd_plan_ab_participants(args)
    if args.plan_command == "decision-context":
        return _cmd_plan_decision_context(args)
    if args.plan_command == "decide":
        return _cmd_plan_decide(args)
    _console.print(
        "[yellow]Bruk: rvv-miniputt plan verify|score|problem|optimize|ab|ab-participants|"
        "decision-context|decide[/yellow]"
    )
    return 1


def _cmd_plan_verify(args: argparse.Namespace) -> int:
    from ..planning_contract import extract_candidate, verify_candidate

    try:
        candidate = extract_candidate(_load_json_file(args.candidate))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        _console.print(f"[red]✗[/red] Kunne ikke lese kandidatplan: {exc}")
        return 1

    problem = None
    if args.problem:
        try:
            problem = _load_json_file(args.problem)
        except (OSError, json.JSONDecodeError) as exc:
            _console.print(f"[red]✗[/red] Kunne ikke lese planning_problem: {exc}")
            return 1

    result = verify_candidate(candidate, problem)

    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0 if result["ok"] else 1

    if result["ok"]:
        _console.print("[green]✓[/green] Kandidatplan består alle harde krav.")
    else:
        _console.print(f"[red]✗[/red] Kandidatplan bryter {len(result['violations'])} harde krav:")
        for v in result["violations"]:
            _console.print(f"  [red]•[/red] [{v['code']}] {v['message']}")
    if result["skipped"]:
        _console.print(
            f"[dim]Hoppet over (mangler --problem): {', '.join(result['skipped'])}[/dim]"
        )
    return 0 if result["ok"] else 1


def _cmd_plan_score(args: argparse.Namespace) -> int:
    from ..planning_contract import extract_candidate, score_candidate

    try:
        candidate = extract_candidate(_load_json_file(args.candidate))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        _console.print(f"[red]✗[/red] Kunne ikke lese kandidatplan: {exc}")
        return 1

    report = score_candidate(candidate)

    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 0

    participation = report["participation"]
    diversity = report["opponent_diversity"]
    turnaround = report["turnaround"]
    hosting = report["hosting"]

    _console.print("[bold]Deltakelse[/bold]")
    _console.print(f"  Spredning (maks-min kamper): {participation['spread']}")
    _console.print("[bold]Motstanderdiversitet[/bold]")
    _console.print(f"  Unike lagpar: {diversity['unique_pairs']}")
    _console.print(f"  Andel nye kamper (pairwise novelty): {diversity['pairwise_novelty']:.1%}")
    _console.print(f"  Lagpar som møtes 3+ ganger: {diversity['pairs_meeting_3_plus']}")
    _console.print(f"  Maks repetisjon for ett lagpar: {diversity['max_pair_repeat']}")
    _console.print(f"  Diversitet mellom klubber: {diversity['inter_club_diversity']:.1%}")
    _console.print(f"  Antall samme-klubb-oppgjør: {diversity['same_club_pairing_count']}")
    _console.print(f"  Maks lag fra samme klubb i én turnering: {diversity['max_same_club_teams_per_tournament']}")
    _console.print("[bold]Pause mellom turneringer[/bold]")
    _console.print(f"  Minste pause: {turnaround['min_turnaround_days']} dager")
    for threshold, count in turnaround["gaps_under_days"].items():
        _console.print(f"  Pauser under {threshold} dager: {count}")
    _console.print("[bold]Vertskap[/bold]")
    _console.print(f"  Spredning (maks-min turneringer som vert): {hosting['spread']}")
    _console.print("[bold]Månedsfordeling[/bold]")
    for month, count in sorted(report["month_distribution"].items()):
        _console.print(f"  {month}: {count}")
    return 0


def _cmd_plan_optimize(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt plan optimize`` — the Stage 3 v2 generic optimizer (issue #257).

    Explicit opt-in command only: never invoked by the regular pipeline, so
    it stays behind the "feature flag or explicit command" the issue asks
    for while it's being A/B tested against ``SeasonPlanner``.
    """
    from ..planning_contract import extract_candidate, verify_candidate
    from ..stage3_optimizer import optimize_candidate

    try:
        candidate = extract_candidate(_load_json_file(args.candidate))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        _console.print(f"[red]✗[/red] Kunne ikke lese kandidatplan: {exc}")
        return 1

    problem = None
    if args.problem:
        try:
            problem = _load_json_file(args.problem)
        except (OSError, json.JSONDecodeError) as exc:
            _console.print(f"[red]✗[/red] Kunne ikke lese planning_problem: {exc}")
            return 1

    try:
        weight_overrides, per_age_group_weights = _parse_weight_overrides(args.weights)
    except ValueError as exc:
        _console.print(f"[red]✗[/red] {exc}")
        return 1

    optimized = optimize_candidate(
        candidate,
        problem,
        iterations=args.iterations,
        seed=args.seed,
        weights=weight_overrides or None,
        per_age_group_weights=per_age_group_weights or None,
        move_dates=args.move_dates,
    )

    payload = json.dumps(optimized, indent=2, ensure_ascii=False)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as fh:
            fh.write(payload)
        _console.print(f"[green]✓[/green] Skrev optimalisert kandidatplan til {args.output}")
    else:
        print(payload)

    source = optimized.get("source", {})
    before = source.get("objective_before")
    after = source.get("objective_after")
    if before is not None and after is not None:
        _console.print(f"[dim]Objektivfunksjon: {before:.1f} → {after:.1f}[/dim]")

    verification = verify_candidate(optimized, problem)
    if verification["ok"]:
        _console.print("[green]✓[/green] Optimalisert kandidat består alle harde krav.")
    else:
        _console.print(
            f"[yellow]![/yellow] Optimalisert kandidat bryter {len(verification['violations'])} harde krav "
            "(bør ikke skje — se stage3_optimizer):"
        )
        for v in verification["violations"]:
            _console.print(f"  [red]•[/red] [{v['code']}] {v['message']}")
    return 0


def _cmd_plan_ab(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt plan ab`` — old-vs-new full-season A/B benchmark (issue #257).

    Runs the existing ``SeasonPlanner`` baseline already sitting in the
    Stage 3 checkpoint and the Stage 3 v2 optimizer repair pass from the
    *same* normalized planning problem, then reports whether the new
    candidate is a strict-or-better improvement per age group and overall.
    """
    import os

    from ..planning_contract import build_planning_problem, extract_candidate
    from ..pipeline.state import PipelineState, StageName
    from ..stage3_ab import build_ab_report
    from ..stage3_optimizer import optimize_candidate

    state = PipelineState(args.work_dir)
    config = state.read_stage(StageName.CONFIG)
    if not config:
        _console.print("[red]✗[/red] Fant ingen Stage 1-konfigurasjon i arbeidsmappen. Kjør Stage 1 først.")
        return 1
    scraping_result = state.read_stage(StageName.SCRAPING)
    planning_checkpoint = state.read_stage(StageName.PLANNING)
    if not planning_checkpoint:
        _console.print("[red]✗[/red] Fant ingen Stage 3-sjekkpunkt (baseline-plan) i arbeidsmappen. Kjør Stage 3 først.")
        return 1

    try:
        old_candidate = extract_candidate(planning_checkpoint)
    except ValueError as exc:
        _console.print(f"[red]✗[/red] Kunne ikke lese baseline-kandidat fra Stage 3-sjekkpunktet: {exc}")
        return 1

    plan_dict = planning_checkpoint.get("plan", {})
    start_date = date.fromisoformat(args.start_date) if args.start_date else None
    end_date = date.fromisoformat(args.end_date) if args.end_date else None
    if start_date is None and plan_dict.get("start_date"):
        start_date = date.fromisoformat(plan_dict["start_date"])
    if end_date is None and plan_dict.get("end_date"):
        end_date = date.fromisoformat(plan_dict["end_date"])
    if start_date is None or end_date is None:
        _console.print(
            "[red]✗[/red] Kunne ikke bestemme planleggingsvinduet — oppgi --start-date/--end-date."
        )
        return 1

    try:
        weight_overrides, per_age_group_weights = _parse_weight_overrides(args.weights)
    except ValueError as exc:
        _console.print(f"[red]✗[/red] {exc}")
        return 1

    problem = build_planning_problem(config, scraping_result, start_date, end_date)
    new_candidate = optimize_candidate(
        old_candidate,
        problem,
        iterations=args.iterations,
        seed=args.seed,
        weights=weight_overrides or None,
        per_age_group_weights=per_age_group_weights or None,
        move_dates=args.move_dates,
    )

    report = build_ab_report(old_candidate, new_candidate, problem)

    if args.output_dir:
        os.makedirs(args.output_dir, exist_ok=True)
        for name, payload in (
            ("old_candidate.json", old_candidate),
            ("new_candidate.json", new_candidate),
            ("ab_report.json", report),
        ):
            with open(os.path.join(args.output_dir, name), "w", encoding="utf-8") as fh:
                json.dump(payload, fh, indent=2, ensure_ascii=False)
        if not args.json:
            _console.print(f"[green]✓[/green] Skrev old_candidate.json/new_candidate.json/ab_report.json til {args.output_dir}")

    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 0 if report["promotable"] else 1

    _print_ab_report(report)
    return 0 if report["promotable"] else 1


def _print_ab_report(report: Dict[str, Any]) -> None:
    old_v, new_v = report["old"]["verification"], report["new"]["verification"]
    old_status = "OK" if old_v["ok"] else f"{len(old_v['violations'])} brudd"
    new_status = "OK" if new_v["ok"] else f"{len(new_v['violations'])} brudd"
    _console.print("[bold]Harde krav[/bold]")
    _console.print(f"  Baseline (gammel planlegger): {old_status}")
    _console.print(f"  Ny kandidat: {new_status}")
    if report["hard_constraint_regressed"]:
        _console.print("  [red]✗ Ny kandidat introduserer harde brudd baseline ikke hadde[/red]")
        for v in new_v["violations"]:
            _console.print(f"    [red]•[/red] [{v['code']}] {v['message']}")

    _console.print("\n[bold]Kvalitet, hele sesongen[/bold]")
    for m in report["overall_comparison"]["metrics"]:
        marker = "[red]▼ regresjon[/red]" if m["regressed"] else ("[green]▲[/green]" if m["delta"] != 0 else "=")
        _console.print(f"  {m['metric']}: {m['old']} → {m['new']} ({marker})")

    _console.print("\n[bold]Per aldersgruppe[/bold]")
    for age_group, entry in sorted(report["by_age_group"].items()):
        regressions = entry["comparison"]["regressions"]
        status = "[red]regresjon[/red]" if regressions else "[green]OK[/green]"
        _console.print(f"  {age_group}: {status}" + (f" ({', '.join(regressions)})" if regressions else ""))

    _console.print()
    if report["dominates_baseline"]:
        _console.print("[green]✓ dominates_baseline: ingen nye harde brudd, ingen kvalitetsregresjon (helhet eller per aldersgruppe).[/green]")
    else:
        _console.print("[yellow]✗ dominates_baseline: false[/yellow] — se regresjoner over.")
    if report["production_ready"]:
        _console.print("[green]✓ production_ready: kandidaten består verifikator uten brudd og kan forfremmes.[/green]")
    else:
        reason = "består ikke verifikator uten brudd" if not report["new"]["verification"]["ok"] else "dominerer ikke baseline"
        _console.print(f"[yellow]✗ production_ready: false[/yellow] ({reason}).")


def _cmd_plan_ab_participants(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt plan ab-participants`` (issue #257 Tasks 2-4).

    Full-season baseline-bounded participant-optimization benchmark: builds
    the same normalized planning problem as ``plan ab``, but compares the
    Stage 3 baseline against :func:`optimize_candidate_participants_bounded_multi_seed`
    instead of the unconstrained weighted-sum optimizer, and reports the
    result via the same :func:`build_ab_report` contract so ``dominates_baseline``/
    ``production_ready`` apply identically.
    """
    import os

    from ..planning_contract import build_planning_problem, extract_candidate
    from ..pipeline.state import PipelineState, StageName
    from ..stage3_ab import build_ab_report
    from ..stage3_optimizer import (
        optimize_candidate_participants_bounded_multi_seed,
        repair_schedule_conflicts_bounded_multi_seed,
    )

    state = PipelineState(args.work_dir)
    config = state.read_stage(StageName.CONFIG)
    if not config:
        _console.print("[red]✗[/red] Fant ingen Stage 1-konfigurasjon i arbeidsmappen. Kjør Stage 1 først.")
        return 1
    scraping_result = state.read_stage(StageName.SCRAPING)
    planning_checkpoint = state.read_stage(StageName.PLANNING)
    if not planning_checkpoint:
        _console.print("[red]✗[/red] Fant ingen Stage 3-sjekkpunkt (baseline-plan) i arbeidsmappen. Kjør Stage 3 først.")
        return 1

    try:
        old_candidate = extract_candidate(planning_checkpoint)
    except ValueError as exc:
        _console.print(f"[red]✗[/red] Kunne ikke lese baseline-kandidat fra Stage 3-sjekkpunktet: {exc}")
        return 1

    plan_dict = planning_checkpoint.get("plan", {})
    start_date = date.fromisoformat(args.start_date) if args.start_date else None
    end_date = date.fromisoformat(args.end_date) if args.end_date else None
    if start_date is None and plan_dict.get("start_date"):
        start_date = date.fromisoformat(plan_dict["start_date"])
    if end_date is None and plan_dict.get("end_date"):
        end_date = date.fromisoformat(plan_dict["end_date"])
    if start_date is None or end_date is None:
        _console.print(
            "[red]✗[/red] Kunne ikke bestemme planleggingsvinduet — oppgi --start-date/--end-date."
        )
        return 1

    try:
        seeds = tuple(int(s.strip()) for s in args.seeds.split(",") if s.strip())
    except ValueError:
        _console.print(f"[red]✗[/red] Ugyldig --seeds {args.seeds!r}, forventet f.eks. 1,2,3,4,5")
        return 1
    if not seeds:
        _console.print("[red]✗[/red] --seeds må inneholde minst ett heltall.")
        return 1

    problem = build_planning_problem(config, scraping_result, start_date, end_date)
    new_candidate = optimize_candidate_participants_bounded_multi_seed(
        old_candidate, problem, seeds=seeds, iterations=args.iterations
    )
    schedule_repair_source = None
    if args.repair_schedule:
        # A participant-only optimizer never moves a tournament's date/arena,
        # so it structurally cannot fix violations like arena_interval_conflict.
        # Run the date-swap-only repair pass on top, still baseline-bounded
        # against the participant-optimized candidate (issue #257 skeleton
        # follow-up).
        repaired_candidate = repair_schedule_conflicts_bounded_multi_seed(
            new_candidate, problem, seeds=seeds, iterations=args.iterations
        )
        schedule_repair_source = repaired_candidate.get("source")
        new_candidate = repaired_candidate

    report = build_ab_report(old_candidate, new_candidate, problem)
    per_age_group_status = new_candidate.get("source", {}).get("base_source", {}).get(
        "per_age_group_status", {}
    ) if args.repair_schedule else new_candidate.get("source", {}).get("per_age_group_status", {})

    if args.output_dir:
        os.makedirs(args.output_dir, exist_ok=True)
        for name, payload in (
            ("problem.json", problem),
            ("old_candidate.json", old_candidate),
            ("new_candidate.json", new_candidate),
            ("ab_report.json", report),
        ):
            with open(os.path.join(args.output_dir, name), "w", encoding="utf-8") as fh:
                json.dump(payload, fh, indent=2, ensure_ascii=False)
        if not args.json:
            _console.print(
                f"[green]✓[/green] Skrev problem.json/old_candidate.json/new_candidate.json/ab_report.json til {args.output_dir}"
            )

    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 0 if report["dominates_baseline"] else 1

    _print_ab_report(report)
    _console.print("\n[bold]Deltakeroptimalisering per aldersgruppe[/bold]")
    for age_group, status in sorted(per_age_group_status.items()):
        marker = "[green]improved[/green]" if status["status"] == "improved" else "[dim]unchanged[/dim]"
        seed_note = f" (seed {status['seed_used']})" if status.get("seed_used") is not None else ""
        _console.print(f"  {age_group}: {marker}{seed_note}")
        if status["status"] == "unchanged" and status.get("reason"):
            _console.print(f"    [dim]{status['reason']}[/dim]")

    if schedule_repair_source is not None:
        _console.print("\n[bold]Skjelett-reparasjon (dato-bytte for harde brudd)[/bold]")
        marker = (
            "[green]improved[/green]" if schedule_repair_source["status"] == "improved" else "[dim]unchanged[/dim]"
        )
        _console.print(
            f"  {marker}: harde brudd {schedule_repair_source['baseline_violations']} → "
            f"{schedule_repair_source['best_violations']}"
        )
        if schedule_repair_source.get("reason"):
            _console.print(f"    [dim]{schedule_repair_source['reason']}[/dim]")

    return 0 if report["dominates_baseline"] else 1


def _resolve_run_id(explicit: "str | None", work_dir: str) -> str:
    """Default ``--run-id`` to the active run manifest's run_id, if any."""
    if explicit:
        return explicit
    try:
        from ..pipeline.run_manifest import RunManifest

        return str(RunManifest(work_dir).read().get("run_id") or "")
    except Exception:
        return ""


def _execute_optimize_plan(args: argparse.Namespace) -> "str | None":
    """Execute an accepted ``optimize_plan`` decision (issue #260 Phase 4).

    Re-runs the Stage 3 v2 optimizer against the Stage 3 checkpoint's
    baseline candidate — starting from ``--candidate`` if the LLM/agent
    passed one along (e.g. a previous ``optimize_plan`` pass's
    ``new_candidate.json``), otherwise from the baseline itself — using the
    decision's ``--weight``/``--iterations``/``--seed``/``--move-dates``
    settings, and writes ``old_candidate.json``/``new_candidate.json``/
    ``ab_report.json`` to ``--output-dir`` exactly like ``plan ab`` does.
    This is what makes ``optimize_plan`` an executable action instead of a
    recorded no-op the operator had to act on manually out of band: the
    returned path is a new ab_report the same LLM/agent loop can immediately
    feed back into ``plan decision-context`` to choose again (apply_candidate/
    keep_baseline/optimize_plan/request_operator), closing the
    optimizer→verifier→LLM loop. Returns the new ab_report.json path, or
    ``None`` if a required input could not be read.
    """
    import os

    from ..pipeline.state import PipelineState, StageName
    from ..planning_contract import extract_candidate
    from ..stage3_ab import build_ab_report
    from ..stage3_optimizer import optimize_candidate

    state = PipelineState(args.work_dir)
    planning_checkpoint = state.read_stage(StageName.PLANNING)
    if not planning_checkpoint:
        _console.print("[red]✗[/red] Fant ingen Stage 3-sjekkpunkt (baseline-plan) i arbeidsmappen.")
        return None
    try:
        baseline_candidate = extract_candidate(planning_checkpoint)
    except ValueError as exc:
        _console.print(f"[red]✗[/red] Kunne ikke lese baseline-kandidat fra Stage 3-sjekkpunktet: {exc}")
        return None

    starting_candidate = baseline_candidate
    if args.candidate:
        try:
            starting_candidate = extract_candidate(_load_json_file(args.candidate))
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            _console.print(f"[red]✗[/red] Kunne ikke lese startkandidat: {exc}")
            return None

    problem = None
    if args.problem:
        try:
            problem = _load_json_file(args.problem)
        except (OSError, json.JSONDecodeError) as exc:
            _console.print(f"[red]✗[/red] Kunne ikke lese planning_problem: {exc}")
            return None

    try:
        weight_overrides, per_age_group_weights = _parse_weight_overrides(args.weights)
    except ValueError as exc:
        _console.print(f"[red]✗[/red] {exc}")
        return None

    new_candidate = optimize_candidate(
        starting_candidate,
        problem,
        iterations=args.iterations,
        seed=args.seed,
        weights=weight_overrides or None,
        per_age_group_weights=per_age_group_weights or None,
        move_dates=args.move_dates,
    )
    report = build_ab_report(baseline_candidate, new_candidate, problem)

    os.makedirs(args.output_dir, exist_ok=True)
    for name, payload in (
        ("old_candidate.json", baseline_candidate),
        ("new_candidate.json", new_candidate),
        ("ab_report.json", report),
    ):
        with open(os.path.join(args.output_dir, name), "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, ensure_ascii=False)

    return os.path.join(args.output_dir, "ab_report.json")


def _cmd_plan_decision_context(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt plan decision-context`` (issue #260 Phase 4).

    Builds and prints the :class:`DecisionContext` for an old-vs-new Stage 3
    A/B report (``plan ab``/``plan ab-participants`` output), so an
    LLM/agent controller can choose ``apply_candidate``/``keep_baseline``/
    ``optimize_plan``/``request_operator`` instead of a Python quality
    heuristic deciding automatically.
    """
    from ..stage3_decision import build_stage3_decision_context

    try:
        report = _load_json_file(args.ab_report)
    except (OSError, json.JSONDecodeError) as exc:
        _console.print(f"[red]✗[/red] Kunne ikke lese ab-rapport: {exc}")
        return 1

    context = build_stage3_decision_context(
        report,
        run_id=_resolve_run_id(args.run_id, args.work_dir),
        baseline_ref=args.baseline_ref,
        candidate_ref=args.candidate_ref,
        objective=args.objective or "",
    )
    payload = json.dumps(context.to_dict(), indent=2, ensure_ascii=False)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as fh:
            fh.write(payload)
        _console.print(f"[green]✓[/green] Skrev decision_context.json til {args.output}")
    else:
        print(payload)
    return 0


def _cmd_plan_decide(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt plan decide`` (issue #260 Phase 4).

    Validates one :class:`DecisionAction` against the :class:`DecisionContext`
    built from *ab_report* using the same deterministic validator
    ``_judge_stage``/the interactive stage loop use
    (:func:`application.decisions.decide`), records it into the run
    manifest's ``decision_log``, and — only when the action is accepted —
    executes it: ``apply_candidate`` swaps the Stage 3 checkpoint's plan for
    the new candidate; ``optimize_plan`` re-runs the Stage 3 v2 optimizer
    (see :func:`_execute_optimize_plan`) and writes a new ``ab_report.json``
    the caller feeds straight back into ``plan decision-context`` to decide
    again, closing the optimizer→verifier→LLM loop; ``keep_baseline``/
    ``request_operator`` are recorded but do not touch pipeline state.

    A hard-violation or human-approval conflict, an unknown action, or a
    missing required argument (e.g. ``apply_candidate`` without
    ``--candidate``) is rejected before anything executes — an LLM response
    cannot bypass the verifier by prose.
    """
    from ..application.decisions import DecisionAction, decide, record_llm_decision
    from ..stage3_decision import apply_stage3_candidate, build_stage3_decision_context

    try:
        report = _load_json_file(args.ab_report)
    except (OSError, json.JSONDecodeError) as exc:
        _console.print(f"[red]✗[/red] Kunne ikke lese ab-rapport: {exc}")
        return 1

    context = build_stage3_decision_context(
        report,
        run_id=_resolve_run_id(args.run_id, args.work_dir),
        baseline_ref=args.baseline_ref,
        candidate_ref=args.candidate_ref,
        objective=args.objective or "",
    )

    arguments: Dict[str, Any] = {}
    if args.action == "apply_candidate":
        candidate_ref = args.candidate_ref or args.candidate
        if candidate_ref:
            arguments["candidate_ref"] = candidate_ref
    elif args.action == "request_operator":
        if args.question:
            arguments["question"] = args.question

    action = DecisionAction(
        action_id=args.action,
        target=args.target or "",
        arguments=arguments,
        rationale=args.rationale or "",
    )
    result = decide(context, action)
    try:
        record_llm_decision(args.work_dir, context, action, result)
    except Exception as exc:
        _console.print(f"[yellow]⚠[/yellow] Kunne ikke lagre avgjørelsen i run manifest: {exc}")

    if not result.accepted:
        _console.print(f"[red]✗[/red] Avgjørelse avvist: {result.rejection_reason}")
        if args.json:
            print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))
        return 1

    if action.action_id == "apply_candidate":
        if not args.candidate:
            _console.print("[red]✗[/red] --candidate (sti til candidate.json) kreves for apply_candidate.")
            return 1
        from ..planning_contract import extract_candidate

        try:
            candidate = extract_candidate(_load_json_file(args.candidate))
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            _console.print(f"[red]✗[/red] Kunne ikke lese kandidatplan: {exc}")
            return 1
        apply_stage3_candidate(args.work_dir, candidate)
        _console.print("[green]✓[/green] Ny kandidat skrevet til Stage 3-sjekkpunktet.")
    elif action.action_id == "keep_baseline":
        _console.print("[dim]Baseline beholdes — Stage 3-sjekkpunktet er uendret.[/dim]")
    elif action.action_id == "optimize_plan":
        if not args.output_dir:
            _console.print("[red]✗[/red] --output-dir kreves for optimize_plan.")
            return 1
        next_report_path = _execute_optimize_plan(args)
        if next_report_path is None:
            return 1
        _console.print(
            f"[green]✓[/green] Skrev ny kandidat/ab_report til {args.output_dir}. "
            f"Kjør 'plan decision-context {next_report_path}' for neste avgjørelse."
        )
    elif action.action_id == "request_operator":
        _console.print("[dim]Avgjørelse registrert: ber operatør om avklaring.[/dim]")

    if args.json:
        print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))
    return 0


def _cmd_plan_problem(args: argparse.Namespace) -> int:
    from ..planning_contract import build_planning_problem
    from ..pipeline.state import PipelineState, StageName

    state = PipelineState(args.work_dir)
    config = state.read_stage(StageName.CONFIG)
    if not config:
        _console.print("[red]✗[/red] Fant ingen Stage 1-konfigurasjon i arbeidsmappen. Kjør Stage 1 først.")
        return 1
    scraping_result = state.read_stage(StageName.SCRAPING)

    start_date = date.fromisoformat(args.start_date) if args.start_date else None
    end_date = date.fromisoformat(args.end_date) if args.end_date else None
    if start_date is None or end_date is None:
        planning_checkpoint = state.read_stage(StageName.PLANNING) or {}
        plan_dict = planning_checkpoint.get("plan", {})
        if start_date is None and plan_dict.get("start_date"):
            start_date = date.fromisoformat(plan_dict["start_date"])
        if end_date is None and plan_dict.get("end_date"):
            end_date = date.fromisoformat(plan_dict["end_date"])
    if start_date is None or end_date is None:
        _console.print(
            "[red]✗[/red] Kunne ikke bestemme planleggingsvinduet — oppgi --start-date/--end-date "
            "eller kjør Stage 3 først."
        )
        return 1

    problem = build_planning_problem(config, scraping_result, start_date, end_date)
    payload = json.dumps(problem, indent=2, ensure_ascii=False)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as fh:
            fh.write(payload)
        _console.print(f"[green]✓[/green] Skrev planning_problem.json til {args.output}")
    else:
        print(payload)
    return 0
