"""Rich-based output formatting for tournament scheduler."""

from datetime import date
from typing import List, Dict, Tuple, TYPE_CHECKING
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich import box
from rich.text import Text

if TYPE_CHECKING:
    from tournament_scheduler.club_distances import furthest_traveling_team
    from tournament_scheduler.models import SeasonPlan, Tournament

console = Console()


class TournamentOutput:
    """Handles Rich-based output formatting."""

    @staticmethod
    def print_header(title: str) -> None:
        """Print section header.

        Args:
            title: Section title
        """
        console.print(f"\n[bold cyan]{'=' * 60}[/bold cyan]")
        console.print(f"[bold cyan]{title}[/bold cyan]")
        console.print(f"[bold cyan]{'=' * 60}[/bold cyan]\n")

    @staticmethod
    def print_success(message: str) -> None:
        """Print success message.

        Args:
            message: Success message
        """
        console.print(f"[green]✓[/green] {message}")

    @staticmethod
    def print_warning(message: str) -> None:
        """Print warning message.

        Args:
            message: Warning message
        """
        console.print(f"[yellow]⚠[/yellow]  {message}")

    @staticmethod
    def print_error(message: str) -> None:
        """Print error message.

        Args:
            message: Error message
        """
        console.print(f"[red]✗[/red] {message}")

    @staticmethod
    def print_info(message: str) -> None:
        """Print info message.

        Args:
            message: Info message
        """
        console.print(f"[blue]ℹ[/blue]  {message}")

    @staticmethod
    def create_progress() -> Progress:
        """Create progress indicator.

        Returns:
            Progress instance
        """
        return Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
            transient=True
        )

    @staticmethod
    def print_conflict_table(
        title: str,
        conflicts: List[Tuple[date, str]],
        max_rows: int = 10
    ) -> None:
        """Print conflicts in a table.

        Args:
            title: Table title
            conflicts: List of (date, reason) tuples
            max_rows: Maximum rows to display
        """
        if not conflicts:
            return

        table = Table(
            title=f"⚠️  {title} ({len(conflicts)} datoer blokkert)",
            box=box.ROUNDED,
            show_header=True,
            header_style="bold magenta"
        )
        table.add_column("Dato", style="cyan", no_wrap=True)
        table.add_column("Grunn", style="yellow")

        for conflict_date, reason in sorted(conflicts)[:max_rows]:
            table.add_row(
                conflict_date.strftime('%Y-%m-%d'),
                reason[:80]
            )

        if len(conflicts) > max_rows:
            table.add_row(
                "[dim]...[/dim]",
                f"[dim]og {len(conflicts) - max_rows} flere datoer[/dim]"
            )

        console.print(table)

    @staticmethod
    def print_available_dates(
        dates_with_slots: List[Tuple[date, str, str, bool]],
        show_details: bool = True
    ) -> None:
        """Print available dates with time slots.

        Args:
            dates_with_slots: List of (date, day_name, time_slot, has_warning) tuples
            show_details: Whether to show detailed time slot information
        """
        table = Table(
            title="✓ Ledige datoer",
            box=box.ROUNDED,
            show_header=True,
            header_style="bold green"
        )
        table.add_column("Dato", style="green", no_wrap=True)
        table.add_column("Dag", style="cyan")
        table.add_column("Foreslått tidspunkt", style="yellow")
        table.add_column("Advarsel", style="red")

        for check_date, day_name, time_slot, has_warning in dates_with_slots:
            warning = "⚠️  HELGEKONFLIKT" if has_warning else ""
            table.add_row(
                check_date.strftime('%Y-%m-%d'),
                day_name,
                time_slot or "-",
                warning
            )

        console.print(table)

    @staticmethod
    def print_summary(
        total_checked: int,
        available_count: int,
        blocked_count: int,
        breakdown: Dict[str, int]
    ) -> None:
        """Print search summary.

        Args:
            total_checked: Total dates checked
            available_count: Number of available dates
            blocked_count: Number of blocked dates
            breakdown: Conflict breakdown by checker type
        """
        # Create summary panel
        summary_text = Text()
        summary_text.append(f"Søkte: {total_checked} helgedatoer\n", style="bold")
        summary_text.append(f"Ledige: {available_count} datoer\n", style="green")
        summary_text.append(f"Blokkert: {blocked_count} datoer\n", style="red")

        if breakdown:
            summary_text.append("\nGrunner for blokkerte datoer:\n", style="bold")
            checker_names = {
                'ice_hall': 'Ishall turneringer',
                'timeslot': 'Ingen ledige tidslukker',
                'team_conflict': 'Lag opptatt',
                'excel_team_conflict': 'Excel konflikter',
                'holiday': 'Helligdager',
                'ball_hall': 'Ballhall opptatt'
            }
            for checker_name, count in sorted(breakdown.items()):
                if checker_name != 'ball_hall_warning' and count > 0:
                    display_name = checker_names.get(checker_name, checker_name.replace('_', ' ').title())
                    summary_text.append(f"  • {display_name}: {count} datoer\n", style="yellow")

        console.print(Panel(
            summary_text,
            title="Søkeresultat",
            border_style="cyan",
            box=box.DOUBLE
        ))

    @staticmethod
    def print_no_dates_found() -> None:
        """Print no dates available message."""
        console.print(Panel(
            "[bold red]Ingen ledige datoer funnet[/bold red]\n\n"
            "Alle datoer har konflikter. Prøv:\n"
            "  • Utvid datoperioden\n"
            "  • Sjekk færre kalendere\n"
            "  • Sjekk om lag-planer kan justeres",
            title="✗ Ingen resultater",
            border_style="red",
            box=box.DOUBLE
        ))

    @staticmethod
    def print_time_slots_detail(
        dates_with_all_slots: List[Tuple[date, str, List[Tuple[str, str]]]]
    ) -> None:
        """Print detailed time slot information.

        Args:
            dates_with_all_slots: List of (date, day_name, [(start, end), ...]) tuples
        """
        console.print(f"\n[bold]{'─' * 60}[/bold]")
        console.print("[bold]Detaljert tidsluke-oversikt:[/bold]\n")

        for check_date, day_name, slots in dates_with_all_slots:
            console.print(f"[cyan]{check_date.strftime('%Y-%m-%d')} ({day_name}):[/cyan]")
            for start, end in slots:
                console.print(f"  • {start}-{end}")
            console.print()

    @staticmethod
    @staticmethod
    def print_skipped_age_groups(plan: "SeasonPlan") -> None:
        """Print a list of age groups that were intentionally skipped.

        Args:
            plan: The proposed SeasonPlan with ``skipped_age_groups`` populated.
        """
        if not plan.skipped_age_groups:
            return

        title = f"⏭ Hoppet over ({len(plan.skipped_age_groups)} aldersgruppe{'r' if len(plan.skipped_age_groups) > 1 else ''})"
        table = Table(
            title=title,
            box=box.ROUNDED,
            show_header=True,
            header_style="bold yellow",
        )
        table.add_column("Aldersgruppe", style="yellow", no_wrap=True)
        table.add_column("Lag", style="cyan", justify="right")
        table.add_column("Årsak", style="white")

        for entry in plan.skipped_age_groups:
            table.add_row(
                str(entry.get("age_group", "?")),
                str(entry.get("team_count", 0)),
                str(entry.get("reason", "")),
            )

        console.print(table)

    def print_season_overview(plan: "SeasonPlan") -> None:
        """Print a season overview table — one row per proposed tournament.

        Args:
            plan: The proposed SeasonPlan
        """
        TournamentOutput.print_skipped_age_groups(plan)

        if not plan.tournaments:
            console.print(Panel(
                "[bold red]Ingen turneringer foreslått[/bold red]",
                title="✗ Ingen sesongplan",
                border_style="red",
                box=box.DOUBLE
            ))
            return

        table = Table(
            title=f"📅 Sesongoversikt ({len(plan.tournaments)} turneringer)",
            box=box.ROUNDED,
            show_header=True,
            header_style="bold magenta"
        )
        table.add_column("Dato", style="cyan", no_wrap=True)
        table.add_column("Aldersgruppe", style="green")
        table.add_column("Arena", style="yellow")
        table.add_column("Vertsklubb", style="blue")
        table.add_column("Lag", style="white")

        for tournament in sorted(plan.tournaments, key=lambda t: t.date):
            table.add_row(
                tournament.date.strftime('%Y-%m-%d'),
                tournament.age_group,
                tournament.arena,
                tournament.host_club or "-",
                ", ".join(team.label for team in tournament.teams)
            )

        console.print(table)

    @staticmethod
    def print_tournament_schedule(tournament: "Tournament") -> None:
        """Print a single tournament's participating teams and full game schedule.

        Games are grouped by parallel slot/round so the round-robin layout
        within the tournament is easy to review.

        Args:
            tournament: The Tournament to render
        """
        console.print(
            f"\n[bold cyan]{tournament.date.strftime('%Y-%m-%d')} — "
            f"{tournament.age_group} — {tournament.arena}[/bold cyan]"
        )
        console.print(
            f"[white]Deltakende lag:[/white] "
            f"{', '.join(team.label for team in tournament.teams)}\n"
        )

        if not tournament.games:
            console.print("[dim]Ingen kamper generert ennå[/dim]")
            return

        table = Table(
            title=f"🏒 Kampoppsett ({len(tournament.games)} kamper)",
            box=box.ROUNDED,
            show_header=True,
            header_style="bold magenta"
        )
        table.add_column("Kamp #", style="dim", no_wrap=True)
        table.add_column("Parallellbane", style="cyan", no_wrap=True)
        table.add_column("Hjemmelag", style="green")
        table.add_column("Bortelag", style="yellow")

        for game_number, game in enumerate(tournament.games, start=1):
            table.add_row(
                str(game_number),
                str(game.parallel_slot + 1),
                game.home.label,
                game.away.label
            )

        console.print(table)

        # Print travel-distance info for this tournament
        travel = furthest_traveling_team(tournament)
        if travel is not None:
            team, km = travel
            console.print(f"[yellow]🚗 Lengst anslått reise:[/yellow] {team.label} (~{km} km)")
        else:
            console.print("[dim]🚗 Reise: kun lokale lag (0 km)[/dim]")

    @staticmethod
    def print_diversity_summary(plan: "SeasonPlan") -> None:
        """Print a summary panel of matchup-diversity metrics for a season plan.

        Args:
            plan: The proposed SeasonPlan
        """
        summary_text = Text()
        summary_text.append(f"Antall turneringer: {len(plan.tournaments)}\n", style="bold")
        summary_text.append(
            f"Motstandervariasjon (andel mulige motstandere møtt): {plan.diversity_score:.2f}\n",
            style="green"
        )
        summary_text.append(
            f"Kampmangfold (andel ferske motstanderpar): {plan.pairwise_matchup_score:.2f}\n",
            style="green"
        )
        summary_text.append(
            f"Månedsbalanse (jevn fordeling over sesongen): {plan.month_balance_score:.2f}\n",
            style="green"
        )

        if plan.arena_counts:
            summary_text.append("\nTurneringer per arena:\n", style="bold")
            for arena, count in sorted(plan.arena_counts.items()):
                if arena.startswith("_"):
                    continue
                summary_text.append(f"  • {arena}: {count} turnering(er)\n", style="cyan")

        # Travel-distance summary: longest single-leg trip in the plan
        longest_team = None
        longest_km = 0
        for t in plan.tournaments:
            travel = furthest_traveling_team(t)
            if travel is not None:
                team, km = travel
                if km > longest_km:
                    longest_team = team
                    longest_km = km
        if longest_team:
            summary_text.append(
                f"\n🚗 Lengste anslåtte enkeltreise: {longest_team.label} ({longest_km} km)\n",
                style="yellow"
            )

        collisions = plan.arena_counts.get("_age_group_overlap_collisions", 0)
        if collisions:
            summary_text.append(
                f"\n⚠ {collisions} uunngåelig(e) kollisjon(er) mellom overlappende "
                "aldersgrupper samme helg\n",
                style="red"
            )
        else:
            summary_text.append(
                "\n✓ Ingen kollisjoner mellom overlappende aldersgrupper\n",
                style="green"
            )

        arena_day_collisions = getattr(plan, "arena_day_collisions", []) or []
        if arena_day_collisions:
            summary_text.append(
                f"⚠ {len(arena_day_collisions)} arena-/dagskollisjon(er) som ellers ville gitt dobbelbooking\n",
                style="red"
            )
            for entry in arena_day_collisions[:4]:
                summary_text.append(
                    f"  • {entry.get('date')}: {entry.get('arena')} — {entry.get('age_group')} "
                    f"mot {entry.get('conflicting_age_group')}\n",
                    style="red"
                )
        else:
            summary_text.append("✓ Ingen arena-/dagskollisjoner\n", style="green")

        # Global date-range preferences (Datopreferanser)
        date_pref_weights = getattr(plan, "date_preference_weights", []) or []
        if date_pref_weights:
            summary_text.append("\nDatopreferanser (aktive under planlegging):\n", style="bold")
            for entry in date_pref_weights:
                vekt = entry.get("vekt", 0.0)
                style = "red" if vekt > 0 else "green"
                direction = "straff" if vekt > 0 else "belønning"
                summary_text.append(
                    f"  • {entry.get('fra')}–{entry.get('til')}: vekt={vekt:+.2f} ({direction})\n",
                    style=style,
                )

        # Per-tournament preference weight terms (non-zero only)
        weighted_tournaments = [
            t for t in plan.tournaments
            if getattr(t, "scoring_weight_term", 0.0) != 0.0
        ]
        if weighted_tournaments:
            summary_text.append("\nTurneringer med justeringsvekt:\n", style="bold")
            for t in weighted_tournaments:
                wt = getattr(t, "scoring_weight_term", 0.0)
                style = "red" if wt > 0 else "green"
                direction = "straff" if wt > 0 else "belønning"
                summary_text.append(
                    f"  • {t.date.isoformat()} {t.age_group} ({t.arena}): "
                    f"vekt={wt:+.2f} ({direction})\n",
                    style=style,
                )

        console.print(Panel(
            summary_text,
            title="Mangfold og fordeling",
            border_style="cyan",
            box=box.DOUBLE
        ))

    @staticmethod
    def print_game_count_table(plan: "SeasonPlan") -> None:
        """Print a table showing per-team round-robin game counts.

        One row per team: team label, total games played across the season,
        and the date of their last game. Useful for spotting uneven game
        load at a glance.

        Args:
            plan: The proposed SeasonPlan with ``team_game_counts`` populated.
        """
        if not plan.team_game_counts:
            return

        table = Table(
            title=f"🏒 Kamper per lag ({len(plan.team_game_counts)} lag)",
            box=box.ROUNDED,
            show_header=True,
            header_style="bold magenta",
        )
        table.add_column("Lag", style="cyan", no_wrap=True)
        table.add_column("Kamper totalt", style="green", justify="right")
        table.add_column("Siste kamp", style="yellow")

        sorted_teams = sorted(
            plan.team_game_counts.items(),
            key=lambda kv: (-kv[1], kv[0]),
        )
        for label, count in sorted_teams:
            last_date = plan.team_last_game_dates.get(label)
            date_str = last_date.strftime("%Y-%m-%d") if last_date else "-"
            table.add_row(label, str(count), date_str)

        spread_by_ag = getattr(plan, "game_count_spread_by_age_group", {}) or {}
        if spread_by_ag:
            # Show per-age-group spreads so U7 and U12 are not conflated.
            parts = [
                f"{ag}: {sp}"
                for ag, sp in sorted(spread_by_ag.items())
                if sp > 0
            ]
            if parts:
                table.caption = "Spredning per aldersgruppe — " + ", ".join(parts) + " kamper"
        elif plan.game_count_spread > 0:
            table.caption = (
                f"Spredning: {plan.game_count_spread} kamper "
                f"(min={min(plan.team_game_counts.values())}, "
                f"maks={max(plan.team_game_counts.values())})"
            )

        console.print(table)

    @staticmethod
    def print_game_count_warnings(warnings: "list[tuple[str, int, int, str]]") -> None:
        """Print game-count spread and temporal-coverage warnings.

        Each entry is ``(team_label, value, threshold_or_gap, warning_type)``.
        ``warning_type`` is ``"spread"`` (value=games_played, threshold=spread_diff)
        or ``"early_finish"`` (value=games_played, gap=max_gap_days — the largest
        gap anywhere along season_start -> first tournament -> ... -> last
        tournament -> season_end).

        Args:
            warnings: The structured warnings from
                ``SeasonPlanner.game_count_warnings``.
        """
        if not warnings:
            return

        spread_entries = [w for w in warnings if w[3] == "spread"]
        early_entries = [w for w in warnings if w[3] == "early_finish"]

        if spread_entries:
            spread = spread_entries[0][2]
            TournamentOutput.print_warning(
                f"Spredning i kampantall ({spread} kamper) overstiger grensen:"
            )
            for label, count, _, _ in spread_entries:
                TournamentOutput.print_warning(f"  • {label}: {count} kamper")

        if early_entries:
            TournamentOutput.print_warning(
                "Disse lagene har et opphold i sesongdekningen (før første, mellom to, "
                "eller etter siste turnering) som overstiger terskelen:"
            )
            for label, count, gap, _ in early_entries:
                TournamentOutput.print_warning(
                    f"  • {label}: {count} kamper, {gap} dagers opphold i sesongdekningen"
                )

    @staticmethod
    def print_rules_report(rules: "list[dict[str, str]]") -> None:
        """Print a structured rules-and-decisions report.

        Each rule dict has keys ``regel``, ``forklaring``, ``kategori``.
        Rules are grouped by ``kategori`` and printed as a Rich Panel
        with a table for each category.

        Args:
            rules: The list returned by ``SeasonPlanner.rules_report()``.
        """
        if not rules:
            return

        # Group by kategori, preserving order of first appearance.
        groups: dict[str, list[dict[str, str]]] = {}
        group_order: list[str] = []
        for rule in rules:
            kat = rule.get("kategori", "Annet")
            if kat not in groups:
                groups[kat] = []
                group_order.append(kat)
            groups[kat].append(rule)

        for kat in group_order:
            entries = groups[kat]
            table = Table(
                title=f"{kat} ({len(entries)})",
                box=box.ROUNDED,
                show_header=True,
                header_style="bold cyan",
                title_style="bold white",
            )
            table.add_column("Regel", style="cyan", no_wrap=True, width=40)
            table.add_column("Forklaring", style="white")

            for rule in entries:
                table.add_row(
                    rule.get("regel", ""),
                    rule.get("forklaring", ""),
                )
            console.print(table)


