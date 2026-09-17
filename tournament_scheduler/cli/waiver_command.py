"""``rvv-miniputt waiver`` — explicit operator authorization of hard-rule exceptions.

This is the canonical, harness-neutral operator surface for creating, listing
and revoking narrowly scoped waivers (see :mod:`tournament_scheduler.operator_waivers`).

It is deliberately *not* a decision action the planner/optimizer/agent may
invoke: only an explicit operator action can authorize an exception to a hard
planning rule. The command validates the requested scope against the current
run's persisted plan/config before writing anything, so a waiver is always
tied to a real, present overage rather than an invented one.
"""

from __future__ import annotations

import argparse
import json
from typing import Any, Dict, List, Tuple

from rich.console import Console

from ..operator_waivers import (
    WAIVABLE_RULE_IDS,
    WaiverError,
    create_waiver,
    load_active_waivers,
    load_waivers,
    revoke_waiver,
)
from .. import planning_half
from ..planning_contract import _parse_date, build_planning_problem, extract_candidate
from ..pipeline.state import PipelineState, StageName

_console = Console()

_HALF_LABELS = {"before_christmas": "før jul", "after_christmas": "etter jul"}


def _cmd_waiver(args: argparse.Namespace) -> int:
    command = getattr(args, "waiver_command", None)
    if command == "list":
        return _cmd_waiver_list(args)
    if command == "create":
        return _cmd_waiver_create(args)
    if command == "revoke":
        return _cmd_waiver_revoke(args)
    _console.print("[yellow]Bruk: rvv-miniputt waiver list|create|revoke[/yellow]")
    return 1


def _cmd_waiver_list(args: argparse.Namespace) -> int:
    records = load_waivers(args.work_dir) if args.all else load_active_waivers(args.work_dir)
    applied = _applied_waiver_ids(args.work_dir)
    if args.json:
        print(json.dumps(records, indent=2, ensure_ascii=False))
        return 0
    if not records:
        _console.print("[dim]Ingen aktive operatør-unntak.[/dim]" if not args.all else "[dim]Ingen unntak registrert.[/dim]")
        return 0
    for record in records:
        scope = record.get("scope") or {}
        team = scope.get("team") or {}
        if not record.get("active", True):
            status = "[red]tilbakekalt[/red]"
        elif applied is None or str(record.get("id")) in applied:
            status = "aktiv"
        else:
            status = "[yellow]aktiv, men samsvarer ikke med gjeldende plan[/yellow]"
        _console.print(
            f"[cyan]{record.get('id')}[/cyan] [{status}] {record.get('rule')}: "
            f"{team.get('label')} ({team.get('club')}, {team.get('age_group')}) "
            f"{_HALF_LABELS.get(str(scope.get('half')), scope.get('half') or '')} "
            f"{record.get('allowed_value')}/{record.get('configured_value')} "
            f"turnering={scope.get('tournament_id') or '-'} "
            f"av {record.get('created_by')} @ {record.get('created_at')}"
        )
    return 0


def _applied_waiver_ids(work_dir: str) -> "set[str] | None":
    """Best-effort ids of waivers that currently match an overage in the plan.

    Returns ``None`` when the current plan/problem can't be reconstructed, so
    callers don't wrongly label an active waiver as unmatched.
    """
    try:
        state = PipelineState(work_dir)
        config = state.read_stage(StageName.CONFIG)
        planning_checkpoint = state.read_stage(StageName.PLANNING)
        if not config or not planning_checkpoint:
            return None
        candidate = extract_candidate(planning_checkpoint)
        problem = _build_problem(state, config, planning_checkpoint)
        if problem is None:
            return None
        from ..planning_contract import verify_candidate

        result = verify_candidate(candidate, problem)
        return {
            str(item.get("waiver_id"))
            for item in (result.get("waived_violations") or [])
            if item.get("waiver_id")
        }
    except Exception:
        return None


def _build_problem(state: PipelineState, config: Dict[str, Any], planning_checkpoint: Dict[str, Any]) -> "Dict[str, Any] | None":
    start_date = _parse_date(planning_checkpoint.get("plan", {}).get("start_date"))
    end_date = _parse_date(planning_checkpoint.get("plan", {}).get("end_date"))
    if start_date is None or end_date is None:
        start_date = _parse_date(config.get("start_date"))
        end_date = _parse_date(config.get("end_date"))
    if start_date is None or end_date is None:
        return None
    scraping = state.read_stage(StageName.SCRAPING)
    return build_planning_problem(
        config, scraping, start_date, end_date, waivers=load_active_waivers(state.work_dir)
    )


def _cmd_waiver_create(args: argparse.Namespace) -> int:
    if args.rule not in WAIVABLE_RULE_IDS:
        _console.print(
            f"[red]✗[/red] Reglen {args.rule!r} kan ikke innvilges. "
            f"Kan innvilges: {', '.join(sorted(WAIVABLE_RULE_IDS))}"
        )
        return 1
    if args.rule == "participation_hard_max_exceeded":
        return _create_hard_max_waiver(args)
    if args.rule == "participation_target_exceeded":
        return _create_participation_waiver(args)
    _console.print(f"[red]✗[/red] Ingen operatørflyt implementert for {args.rule!r} ennå.")
    return 1


def _create_hard_max_waiver(args: argparse.Namespace) -> int:
    """Create a waiver for an explicit participation *hard maximum* overage.

    A participation target is a strong goal, not a hard ceiling, so it never
    needs a waiver. Only an explicitly configured ``participation_hard_max``
    (or per-age-group variant) is waivable here.
    """
    from ..participation_targets import resolve_hard_max

    state = PipelineState(args.work_dir)
    config = state.read_stage(StageName.CONFIG)
    if not config:
        _console.print("[red]✗[/red] Fant ingen Stage 1-konfigurasjon i arbeidsmappen. Kjør Stage 1 først.")
        return 1
    planning_checkpoint = state.read_stage(StageName.PLANNING)
    if not planning_checkpoint:
        _console.print("[red]✗[/red] Fant ingen Stage 3-plan i arbeidsmappen. Kjør Stage 3 først.")
        return 1
    try:
        candidate = extract_candidate(planning_checkpoint)
    except ValueError as exc:
        _console.print(f"[red]✗[/red] Kunne ikke lese Stage 3-planen: {exc}")
        return 1

    problem = _build_problem(state, config, planning_checkpoint)
    if problem is None:
        _console.print("[red]✗[/red] Kunne ikke bestemme planleggingsvinduet.")
        return 1

    team_label = str(args.team or "").strip()
    registered = [
        t
        for t in problem.get("teams", [])
        if str(t.get("label")) == team_label
        and (not args.age_group or str(t.get("age_group")) == str(args.age_group))
        and (not args.club or str(t.get("club")) == str(args.club))
    ]
    if len(registered) != 1:
        _console.print("[red]✗[/red] Fant ikke et entydig registrert lag; oppgi --club og --age-group.")
        return 1
    team = registered[0]
    identity = (str(team.get("club", "")), str(team.get("label", "")), str(team.get("age_group", "")))
    hard_max = resolve_hard_max(identity, problem, team=team)
    if not isinstance(hard_max, int):
        _console.print(
            "[red]✗[/red] Aldersgruppen/laget har ingen konfigurert `participation_hard_max`. "
            "Et vanlig deltakelsesmål krever ikke unntak."
        )
        return 1

    tournament_id = str(args.tournament or "").strip()
    if not tournament_id:
        _console.print("[red]✗[/red] --tournament er påkrevd for participation_hard_max_exceeded.")
        return 1
    if not any(str(t.get("id")) == tournament_id for t in candidate.get("tournaments", []) or []):
        _console.print(f"[red]✗[/red] Fant ingen turnering {tournament_id!r} i Stage 3-planen.")
        return 1

    participates_here = any(
        str(t.get("id")) == tournament_id
        and any(
            (str(tm.get("club", "")), str(tm.get("label", "")), str(tm.get("age_group", ""))) == identity
            for tm in t.get("teams", []) or []
        )
        for t in candidate.get("tournaments", []) or []
    )
    current = sum(
        1
        for t in candidate.get("tournaments", []) or []
        if any(
            (str(tm.get("club", "")), str(tm.get("label", "")), str(tm.get("age_group", ""))) == identity
            for tm in t.get("teams", []) or []
        )
    )
    if args.allowed_value is None:
        _console.print("[red]✗[/red] --allowed-value er påkrevd (faktisk antall unntaket godkjenner).")
        return 1
    allowed = int(args.allowed_value)
    if allowed != current and allowed != current + 1:
        _console.print(
            f"[red]✗[/red] Laget deltar i {current} turneringer nå — unntaket kan bare godkjenne "
            f"{current} eller {current + 1}."
        )
        return 1
    if participates_here and allowed != current:
        _console.print(
            f"[red]✗[/red] Laget deltar allerede i {tournament_id!r} — bruk --allowed-value {current}."
        )
        return 1
    if not participates_here and allowed != current + 1:
        _console.print(
            f"[red]✗[/red] Laget deltar ikke i {tournament_id!r} ennå — bruk --allowed-value {current + 1}."
        )
        return 1
    if allowed <= hard_max:
        _console.print(
            f"[red]✗[/red] Unntaket må godkjenne et faktisk antall over hard maks ({hard_max}); fikk {allowed}."
        )
        return 1

    try:
        record, created = create_waiver(
            args.work_dir,
            rule="participation_hard_max_exceeded",
            team={"club": identity[0], "label": identity[1], "age_group": identity[2]},
            tournament_id=tournament_id,
            half=None,
            configured_value=hard_max,
            allowed_value=allowed,
            reason=args.reason,
            actor=args.actor,
            evidence={"current_season_count": current, "team_participates_in_tournament": participates_here},
        )
    except WaiverError as exc:
        _console.print(f"[red]✗[/red] {exc}")
        return 1

    if args.json:
        print(json.dumps({"created": created, "waiver": record}, indent=2, ensure_ascii=False))
        return 0
    verb = "Opprettet" if created else "Fant allerede aktivt"
    _console.print(
        f"[green]✓[/green] {verb} operatør-unntak [cyan]{record['id']}[/cyan]: "
        f"{identity[1]} {allowed}/{hard_max} (hard maks) i turnering {tournament_id}."
    )
    _console.print(f"  Begrunnelse: {record['reason']}")
    return 0


def _create_participation_waiver(args: argparse.Namespace) -> int:
    state = PipelineState(args.work_dir)
    config = state.read_stage(StageName.CONFIG)
    if not config:
        _console.print("[red]✗[/red] Fant ingen Stage 1-konfigurasjon i arbeidsmappen. Kjør Stage 1 først.")
        return 1
    planning_checkpoint = state.read_stage(StageName.PLANNING)
    if not planning_checkpoint:
        _console.print("[red]✗[/red] Fant ingen Stage 3-plan i arbeidsmappen. Kjør Stage 3 først.")
        return 1
    try:
        candidate = extract_candidate(planning_checkpoint)
    except ValueError as exc:
        _console.print(f"[red]✗[/red] Kunne ikke lese Stage 3-planen: {exc}")
        return 1

    problem = _build_problem(state, config, planning_checkpoint)
    if problem is None:
        _console.print("[red]✗[/red] Kunne ikke bestemme planleggingsvinduet.")
        return 1

    try:
        scope = _resolve_participation_scope(candidate, problem, args)
    except WaiverError as exc:
        _console.print(f"[red]✗[/red] {exc.reason if hasattr(exc, 'reason') else exc}")
        return 1

    try:
        record, created = create_waiver(
            args.work_dir,
            rule="participation_target_exceeded",
            team=scope["team"],
            tournament_id=scope["tournament_id"],
            half=scope["half"],
            configured_value=scope["configured_value"],
            allowed_value=scope["allowed_value"],
            reason=args.reason,
            actor=args.actor,
            evidence=scope["evidence"],
        )
    except WaiverError as exc:
        _console.print(f"[red]✗[/red] {exc}")
        return 1

    if args.json:
        print(json.dumps({"created": created, "waiver": record}, indent=2, ensure_ascii=False))
        return 0
    verb = "Opprettet" if created else "Fant allerede aktivt"
    _console.print(
        f"[green]✓[/green] {verb} operatør-unntak [cyan]{record['id']}[/cyan]: "
        f"{scope['team']['label']} {scope['allowed_value']}/{scope['configured_value']} "
        f"{_HALF_LABELS.get(scope['half'], scope['half'])} i turnering {scope['tournament_id']}."
    )
    _console.print(f"  Begrunnelse: {record['reason']}")
    _console.print("  Gjenoppta kjøringen (run --interactive) slik at unntaket brukes i valgt reparasjon, og eksporter på nytt.")
    return 0


def _cmd_waiver_revoke(args: argparse.Namespace) -> int:
    try:
        record = revoke_waiver(
            args.work_dir, args.waiver_id, reason=args.reason, actor=args.actor
        )
    except WaiverError as exc:
        _console.print(f"[red]✗[/red] {exc}")
        return 1
    if args.json:
        print(json.dumps(record, indent=2, ensure_ascii=False))
        return 0
    _console.print(f"[green]✓[/green] Tilbakekalte operatør-unntak [cyan]{record['id']}[/cyan]. Hard verifisering gjelder igjen.")
    return 0


def _resolve_participation_scope(
    candidate: Dict[str, Any], problem: Dict[str, Any], args: argparse.Namespace
) -> Dict[str, Any]:
    """Validate the requested scope against the current plan and problem.

    Raises :class:`WaiverError` when the request does not correspond to a
    real, present overage this waiver would authorize.
    """
    tournament_id = str(args.tournament or "").strip()
    if not tournament_id:
        raise WaiverError("--tournament er påkrevd for participation_target_exceeded (unntaket skal være snevert)")
    tournament = next(
        (t for t in candidate.get("tournaments", []) if str(t.get("id")) == tournament_id), None
    )
    if tournament is None:
        raise WaiverError(f"fant ingen turnering {tournament_id!r} i Stage 3-planen")

    t_date = _parse_date(tournament.get("date"))
    if t_date is None:
        raise WaiverError(f"turnering {tournament_id!r} mangler gyldig dato")

    split = _parse_date(problem.get("christmas_split_date"))
    if split is None:
        split = planning_half.christmas_split_date(_parse_date(problem.get("start_date")), _parse_date(problem.get("end_date")))
    half = planning_half.tournament_half(t_date, split)
    if args.half and str(args.half) != half:
        raise WaiverError(
            f"turnering {tournament_id!r} ligger {_HALF_LABELS.get(half, half)}, ikke {args.half!r}"
        )

    team_label = str(args.team or "").strip()
    if not team_label:
        raise WaiverError("--team (lagnavn) er påkrevd")
    registered = [
        t
        for t in problem.get("teams", [])
        if str(t.get("label")) == team_label
        and (not args.age_group or str(t.get("age_group")) == str(args.age_group))
        and (not args.club or str(t.get("club")) == str(args.club))
    ]
    if not registered:
        raise WaiverError(f"fant ikke registrert lag {team_label!r} med gitt klubb/aldersgruppe")
    if len(registered) > 1:
        raise WaiverError(
            f"{team_label!r} er ikke entydig — oppgi --club og --age-group"
        )
    team = registered[0]
    identity = (str(team.get("club", "")), str(team.get("label", "")), str(team.get("age_group", "")))
    age_group = identity[2]

    half_targets = (problem.get("participation_targets_by_age_group") or {}).get(age_group) or {}
    configured = half_targets.get(half)
    if not isinstance(configured, int):
        raise WaiverError(
            f"aldersgruppen {age_group!r} har ingen konfigurert deltakelsestak for {half}"
        )
    if args.configured_value is not None and int(args.configured_value) != configured:
        raise WaiverError(
            f"--configured-value {args.configured_value} stemmer ikke med konfigurert tak {configured} "
            f"for {age_group} {half}"
        )

    counts, ids = _participation_by_half(candidate, problem)
    current = counts.get(half, {}).get(identity, 0)
    if args.allowed_value is None:
        raise WaiverError("--allowed-value er påkrevd (faktisk antall deltakelser unntaket godkjenner)")
    allowed = int(args.allowed_value)
    if allowed != current and allowed != current + 1:
        raise WaiverError(
            f"laget deltar i {current} turneringer {_HALF_LABELS.get(half, half)} nå — "
            f"unntaket kan bare godkjenne {current} (allerede anvendt) eller {current + 1} (neste endring)"
        )
    participates_here = tournament_id in set(ids.get(half, {}).get(identity, []))
    if participates_here and allowed != current:
        raise WaiverError(
            f"laget deltar allerede i turnering {tournament_id} — bruk --allowed-value {current}"
        )
    if not participates_here and allowed != current + 1:
        raise WaiverError(
            f"laget deltar ikke i turnering {tournament_id} ennå — bruk --allowed-value {current + 1}"
        )
    if allowed <= configured:
        raise WaiverError(
            f"unntaket må godkjenne et faktisk antall over det konfigurerte taket ({configured}); fikk {allowed}"
        )

    return {
        "team": {"club": identity[0], "label": identity[1], "age_group": identity[2]},
        "tournament_id": tournament_id,
        "half": half,
        "configured_value": configured,
        "allowed_value": allowed,
        "evidence": {
            "tournament_date": t_date.isoformat(),
            "current_half_count": current,
            "team_participates_in_tournament": participates_here,
        },
    }


def _participation_by_half(
    candidate: Dict[str, Any], problem: Dict[str, Any]
) -> Tuple[Dict[str, Dict[Tuple[str, str, str], int]], Dict[str, Dict[Tuple[str, str, str], List[str]]]]:
    split = _parse_date(problem.get("christmas_split_date"))
    counts: Dict[str, Dict[Tuple[str, str, str], int]] = {"before_christmas": {}, "after_christmas": {}}
    ids: Dict[str, Dict[Tuple[str, str, str], List[str]]] = {"before_christmas": {}, "after_christmas": {}}
    for tournament in candidate.get("tournaments", []):
        t_date = _parse_date(tournament.get("date"))
        if t_date is None:
            continue
        half = planning_half.tournament_half(t_date, split)
        if half not in counts:
            continue
        t_id = str(tournament.get("id", "?"))
        for team in tournament.get("teams", []) or []:
            identity = (
                str(team.get("club", "")),
                str(team.get("label", "")),
                str(team.get("age_group", "")),
            )
            counts[half][identity] = counts[half].get(identity, 0) + 1
            ids[half].setdefault(identity, []).append(t_id)
    return counts, ids


__all__ = ["_cmd_waiver"]
