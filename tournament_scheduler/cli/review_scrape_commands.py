"""Review and scrape-llm command handlers for the RVV Miniputt CLI."""

from __future__ import annotations

import argparse

from rich.console import Console

_console = Console()


def _cmd_review(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt review`` — apply club responses and re-export."""
    from .review_command import ReviewCommand

    cmd = ReviewCommand()
    return cmd.run(
        args.response,
        work_dir=args.work_dir,
        export_dir=args.export_dir,
        timestamped_export=args.timestamped_export,
    )


def _cmd_scrape_llm(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt scrape-llm`` — browser-tool capability guidance."""
    from ..club_registry import club_for_source_name
    from ..pipeline.scraper_strategies import STRATEGIES, get_strategy, needs_llm_agent

    requested_club = args.club.strip()
    club_name = club_for_source_name(requested_club) or requested_club
    if club_name != requested_club:
        _console.print(f"[dim]Alias resolved:[/dim] {requested_club} → {club_name}")

    strategy = get_strategy(club_name)
    if strategy is None:
        _console.print(f"[red]✗[/red] Ukjent klubb: '{requested_club}'")
        _console.print("\n[bold]Kjente skrapestrategier:[/bold]")
        for name in sorted(STRATEGIES):
            _console.print(f"  [cyan]{name}[/cyan]")
        return 1

    _console.print(f"[bold]LLM-guidet recovery:[/bold] {club_name}")
    _console.print(f"  URL: [dim]{strategy.url}[/dim]")
    _console.print(f"  Engine: [dim]{strategy.engine.value}[/dim]")
    if strategy.note:
        _console.print(f"  [dim]{strategy.note}[/dim]")

    if not needs_llm_agent(strategy):
        _console.print("\n[yellow]![/yellow] Denne kilden har allerede en deterministisk skraper.")
        _console.print(
            f"  Bruk [bold]rvv-miniputt scrape --club \"{requested_club}\"[/bold] i stedet."
        )
        _console.print(
            "  Hvis du bare har terminal og trenger å fylle cache på nytt, bruk [bold]rvv-miniputt recovery-targets[/bold] for å finne blokkerte kilder og [bold]rvv-miniputt recovery-inject --source \"<navn>\"[/bold] når du har event-JSON."
        )
        return 1

    _console.print(
        "\n[yellow]![/yellow] Denne kilden krever browser-verktøy (Playwright/browser_worker) "
        "i et allerede browser-aktivert harness."
    )
    _console.print(
        "  Agent-harness (Claude Code, Codex, ChatGPT, Pi): bruk den felles "
        "[bold]/rvv-miniputt:operate[/bold]-inngangen og følg den delte "
        "[bold]scrape-llm[/bold]-prosedyren for browser/navigasjon."
    )
    _console.print(
        "  Rent terminal/CI: kan ikke drive siden direkte; bruk [bold]rvv-miniputt recovery-targets[/bold] for å liste blokkede kilder, og [bold]rvv-miniputt recovery-inject --source \"<navn>\"[/bold] når du har event-JSON fra et eget script eller WebFetch."
    )
    if strategy.credential_env_vars:
        _console.print(
            f"  Krever miljøvariabler: {', '.join(strategy.credential_env_vars)}"
        )
    if strategy.initial_navigation:
        _console.print(
            f"  Oppstartssekvens: {len(strategy.initial_navigation)} steg før agent-løkken."
        )
    _console.print(
        f"  For strategi-JSON: [bold]python3 -m tournament_scheduler.pipeline.scraper_strategies --name \"{club_name}\"[/bold]"
    )
    return 1
