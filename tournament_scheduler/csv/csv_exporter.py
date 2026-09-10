"""CSV exporter — writes a flat game schedule and a season overview.

Writes two CSV files:

1. ``<output_path>`` — one row per game with columns:
   ``date, arena, age_group, home, away, parallel_slot``

2. ``<output_path_stem>_overview.csv`` — one row per tournament with columns:
   ``date, arena, age_group, host_club, team_count, game_count``
"""

from __future__ import annotations

import csv
import os
from pathlib import Path

from tournament_scheduler.club_distances import furthest_traveling_team
from ..models import SeasonPlan


class CsvExporter:
    """Export a :class:`~tournament_scheduler.models.SeasonPlan` to CSV files."""

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def export(
        self,
        plan: SeasonPlan,
        output_path: str | os.PathLike[str],
    ) -> tuple[str, str]:
        """Write games CSV and overview CSV.

        Returns
        -------
        tuple[str, str]
            ``(games_path, overview_path)``
        """
        games_path = Path(output_path)
        games_path.parent.mkdir(parents=True, exist_ok=True)

        stem = games_path.stem
        overview_path = games_path.parent / f"{stem}_overview.csv"
        team_counts_path = games_path.parent / f"{stem}_team_counts.csv"

        self._write_games(plan, games_path)
        self._write_overview(plan, overview_path)
        self._write_team_game_counts(plan, team_counts_path)

        return str(games_path), str(overview_path)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _write_games(self, plan: SeasonPlan, path: Path) -> None:
        with path.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            writer.writerow(["date", "arena", "age_group", "home", "away", "parallel_slot"])
            for tournament in plan.tournaments:
                date_str = tournament.date.isoformat()
                for game in tournament.games:
                    writer.writerow([
                        date_str,
                        tournament.arena,
                        tournament.age_group,
                        game.home.label,
                        game.away.label,
                        game.parallel_slot,
                    ])
                # Bye rows for odd-numbered tournaments.
                bye_rounds = tournament.get_bye_rounds()
                for round_num in sorted(bye_rounds):
                    for team_label in bye_rounds[round_num]:
                        writer.writerow([
                            date_str,
                            tournament.arena,
                            tournament.age_group,
                            "(Pause)",
                            team_label,
                            "",
                        ])

    def _write_overview(self, plan: SeasonPlan, path: Path) -> None:
        with path.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            writer.writerow([
                "date", "arena", "age_group", "host_club",
                "team_count", "game_count", "furthest_travel",
                "cancelled", "cancellation_reason",
            ])
            for tournament in plan.tournaments:
                travel = furthest_traveling_team(tournament)
                travel_str = f"{travel[0].label}~{travel[1]}km" if travel else ""
                writer.writerow([
                    tournament.date.isoformat(),
                    tournament.arena,
                    tournament.age_group,
                    tournament.host_club or "",
                    len(tournament.teams),
                    len(tournament.games),
                    travel_str,
                    "ja" if tournament.cancelled else "nei",
                    tournament.cancellation_reason or "",
                ])

    def _write_team_game_counts(self, plan: SeasonPlan, path: Path) -> None:
        """Write per-team game count data to a separate CSV file.

        Columns: team, games_played, last_game_date.
        Rows are sorted by games_played descending.
        """
        if not plan.team_game_counts:
            path.write_text("team,games_played,last_game_date\n", encoding="utf-8")
            return

        sorted_teams = sorted(
            plan.team_game_counts.items(),
            key=lambda kv: (-kv[1], kv[0]),
        )
        with path.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            writer.writerow(["team", "games_played", "last_game_date"])
            for label, count in sorted_teams:
                last_date = plan.team_last_game_dates.get(label)
                date_str = last_date.isoformat() if last_date else ""
                writer.writerow([label, count, date_str])
        return path
