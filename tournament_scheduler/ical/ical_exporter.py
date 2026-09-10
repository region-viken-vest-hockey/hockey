"""iCal exporter — writes one VEVENT per game in a SeasonPlan.

Uses the ``icalendar`` library (already in requirements.txt) to produce a
standards-compliant ``.ics`` file that can be imported into any calendar app.

Each VEVENT uses:
- ``DTSTART`` / ``DTEND``: tournament date at ``tournament.start_time``
  (falling back to a configurable ``start_hour``) plus 1 h per game slot,
  or the computed tournament duration when a per-age-group round length
  is configured
- ``SUMMARY``: "<home> vs <away>"
- ``LOCATION``: arena name
- ``CATEGORIES``: age group
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from icalendar import Calendar, Event, vText

from ..models import SeasonPlan, Tournament, Game


def _stable_uid(*parts: object) -> str:
    """Return a deterministic iCal UID for generated schedule content."""
    payload = "|".join(str(part) for part in parts)
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"rvv-miniputt:{payload}"))


# ---------------------------------------------------------------------------
# Exporter
# ---------------------------------------------------------------------------


class ICalExporter:
    """Export a :class:`~tournament_scheduler.models.SeasonPlan` to an iCal file.

    Parameters
    ----------
    game_duration_minutes:
        Duration of a single game in minutes (default 60).
    start_hour:
        Hour of day (0-23) when the first game of a tournament day starts.
        Used as a fallback when a tournament has no ``start_time`` set.
    round_length_for_age_group:
        Optional mapping of age group -> round length in minutes, used
        together with ``tournament.start_time`` to compute the tournament's
        total duration/end time via ``Tournament.duration_minutes``/
        ``Tournament.end_time``.
    """

    def __init__(
        self,
        game_duration_minutes: int = 60,
        start_hour: int = 9,
        round_length_for_age_group: Optional[dict[str, int]] = None,
    ) -> None:
        self.game_duration_minutes = game_duration_minutes
        self.start_hour = start_hour
        self.round_length_for_age_group = round_length_for_age_group or {}

    # ------------------------------------------------------------------
    # Internal helpers — tournament start/end datetimes
    # ------------------------------------------------------------------

    def _tournament_start_datetime(self, tournament: Tournament) -> datetime:
        """Return the tournament's start datetime (UTC).

        Uses ``tournament.start_time`` (HH:MM) if set, falling back to
        ``self.start_hour`` for backward compatibility.
        """
        if tournament.start_time:
            try:
                hour, minute = (int(p) for p in tournament.start_time.split(":"))
            except (ValueError, AttributeError):
                hour, minute = self.start_hour, 0
        else:
            hour, minute = self.start_hour, 0

        return datetime(
            tournament.date.year,
            tournament.date.month,
            tournament.date.day,
            hour,
            minute,
            0,
            tzinfo=timezone.utc,
        )

    def _tournament_end_datetime(self, tournament: Tournament, dt_start: datetime) -> datetime:
        """Return the tournament's end datetime (UTC).

        Uses the per-age-group round length and
        ``Tournament.duration_minutes`` when both ``start_time`` and a
        round length are available. Falls back to a duration based on
        ``game_duration_minutes`` and the number of games.
        """
        round_length = self.round_length_for_age_group.get(tournament.age_group)
        if tournament.start_time and round_length:
            duration = tournament.duration_minutes(round_length)
            if duration > 0:
                return dt_start + timedelta(minutes=duration)

        return dt_start + timedelta(
            hours=max(1, len(tournament.games) * self.game_duration_minutes / 60)
        )

    # ------------------------------------------------------------------
    # Public API — per-game export (used by Stage 4 pipeline)
    # ------------------------------------------------------------------

    def export(self, plan: SeasonPlan, output_path: str | os.PathLike[str]) -> str:
        """Write the plan to an ``.ics`` file at *output_path*.

        Generates one VEVENT per game in the plan.

        Returns the path written (as a string) so callers can log it.
        """
        cal = self._build_calendar(plan)
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(cal.to_ical())
        return str(path)

    # ------------------------------------------------------------------
    # Public API — per-tournament summary export (CLI / interactive)
    # ------------------------------------------------------------------

    def export_tournament_summary(
        self,
        plan: SeasonPlan,
        output_path: str | os.PathLike[str],
        *,
        age_group_filter: Optional[str] = None,
        club: Optional[str] = None,
    ) -> str:
        """Write one VEVENT per tournament to an ``.ics`` file.

        Unlike :meth:`export` (which creates one event per game), this
        creates a single calendar event summarising each tournament —
        suitable for importing into Spond, Google Calendar, or Outlook
        where the user just wants to know *when* and *where* each
        tournament happens, not the individual game pairings.

        Parameters
        ----------
        plan:
            The season plan to export.
        output_path:
            Path for the ``.ics`` file.
        age_group_filter:
            If set, only include tournaments matching this age group
            (e.g. ``"U10"``).
        club:
            If set, only include tournaments where ``club`` has at least
            one participating team. The club label is also highlighted
            in the event description.

        Returns the path written (as a string).
        """
        cal = self._build_tournament_summary_calendar(
            plan,
            age_group_filter=age_group_filter,
            club=club,
        )
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(cal.to_ical())
        return str(path)

    # ------------------------------------------------------------------
    # Internal helpers — per-game calendar
    # ------------------------------------------------------------------

    def _build_calendar(self, plan: SeasonPlan) -> Calendar:
        cal = Calendar()
        cal.add("prodid", "-//Kongsberg Hockey Scheduler//NO")
        cal.add("version", "2.0")
        cal.add("calscale", "GREGORIAN")
        cal.add("x-wr-calname", vText("RVV Sesongplan"))

        for tournament in plan.tournaments:
            for event in self._tournament_events(tournament):
                cal.add_component(event)

        return cal

    def _tournament_events(self, tournament: Tournament) -> list[Event]:
        """Generate one VEVENT per game in the tournament."""
        events: list[Event] = []

        if tournament.cancelled:
            # Emit a single CANCELLED event for the tournament weekend.
            dt_start = self._tournament_start_datetime(tournament)
            dt_end = self._tournament_end_datetime(tournament, dt_start)
            reason = tournament.cancellation_reason or "Avlyst"
            event = Event()
            event.add(
                "uid",
                _stable_uid(
                    "cancelled-tournament",
                    tournament.date.isoformat(),
                    tournament.age_group,
                    tournament.arena,
                    tournament.host_club or "",
                    tournament.start_time or "",
                    reason,
                    ",".join(team.label for team in tournament.teams),
                ),
            )
            event.add("summary", vText(f"AVLYST: {tournament.age_group} — {tournament.arena}"))
            event.add("dtstart", dt_start)
            event.add("dtend", dt_end)
            event.add("location", vText(tournament.arena))
            event.add("categories", [tournament.age_group])
            event.add("description", vText(f"Turnering avlyst: {reason}"))
            event.add("status", "CANCELLED")
            events.append(event)
            return events

        games = list(tournament.games)

        # Group by parallel slot to assign wall-clock times
        # parallel_slot 0 means all games in one slot (sequential)
        # Build a mapping: slot_index -> [games]
        slot_map: dict[int, list[Game]] = {}
        for game in games:
            slot = game.parallel_slot
            slot_map.setdefault(slot, []).append(game)

        # Assign times: slot 0 starts at start_hour, each new slot adds duration
        unique_slots = sorted(slot_map.keys())
        slot_start_offsets = {slot: i for i, slot in enumerate(unique_slots)}

        game_dur = timedelta(minutes=self.game_duration_minutes)
        tournament_start = self._tournament_start_datetime(tournament)

        for game in games:
            slot = game.parallel_slot
            offset_slots = slot_start_offsets.get(slot, 0)
            dt_start = tournament_start + game_dur * offset_slots

            dt_end = dt_start + game_dur

            event = Event()
            event.add(
                "uid",
                _stable_uid(
                    "game",
                    tournament.date.isoformat(),
                    tournament.age_group,
                    tournament.arena,
                    tournament.host_club or "",
                    tournament.start_time or "",
                    game.home.label,
                    game.away.label,
                    game.parallel_slot,
                    game.round_number,
                ),
            )
            event.add("summary", vText(f"{game.home.label} vs {game.away.label}"))
            event.add("dtstart", dt_start)
            event.add("dtend", dt_end)
            event.add("location", vText(tournament.arena))
            event.add("categories", [tournament.age_group])
            event.add("description", vText(
                f"Turnering: {tournament.arena} | {tournament.age_group}"
            ))
            events.append(event)

        return events

    # ------------------------------------------------------------------
    # Internal helpers — per-tournament summary calendar
    # ------------------------------------------------------------------

    def _build_tournament_summary_calendar(
        self,
        plan: SeasonPlan,
        *,
        age_group_filter: Optional[str] = None,
        club: Optional[str] = None,
    ) -> Calendar:
        cal = Calendar()
        cal.add("prodid", "-//Kongsberg Hockey Scheduler//NO")
        cal.add("version", "2.0")
        cal.add("calscale", "GREGORIAN")

        cal_name = "RVV Sesongplan"
        if age_group_filter:
            cal_name += f" — {age_group_filter}"
        if club:
            cal_name += f" — {club}"
        cal.add("x-wr-calname", vText(cal_name))

        for tournament in plan.tournaments:
            if age_group_filter and tournament.age_group != age_group_filter:
                continue
            if club and not any(team.club == club for team in tournament.teams):
                continue

            event = self._tournament_summary_event(tournament, club=club)
            cal.add_component(event)

        return cal

    def _tournament_summary_event(
        self,
        tournament: Tournament,
        *,
        club: Optional[str] = None,
    ) -> Event:
        """Create a single VEVENT summarising one tournament.

        The event spans the full tournament day (09:00-17:00 UTC as a
        reasonable all-day placeholder). SUMMARY and DESCRIPTION are
        built from the tournament's age group, arena, and team list.
        """
        dt_start = self._tournament_start_datetime(tournament)
        round_length = self.round_length_for_age_group.get(tournament.age_group)
        if tournament.start_time and round_length:
            duration = tournament.duration_minutes(round_length)
            if duration > 0:
                dt_end = dt_start + timedelta(minutes=duration)
            else:
                dt_end = dt_start + timedelta(hours=8)
        else:
            dt_end = dt_start + timedelta(hours=8)

        summary = f"{tournament.age_group} — {tournament.arena}"

        team_list = ", ".join(team.label for team in tournament.teams)
        description_lines = [
            f"Arena: {tournament.arena}",
            f"Aldersgruppe: {tournament.age_group}",
            f"Vert: {tournament.host_club or '—'}",
            f"Deltakende lag ({len(tournament.teams)}): {team_list}",
        ]
        if club:
            club_teams = [t.label for t in tournament.teams if t.club == club]
            if club_teams:
                description_lines.append(f"\nDine lag: {', '.join(club_teams)}")

        if tournament.games:
            matchups = "\n".join(
                f"  {game.home.label} vs {game.away.label}"
                for game in tournament.games
            )
            description_lines.append(f"\nKamper:\n{matchups}")

        description = "\n".join(description_lines)

        if tournament.cancelled:
            summary = f"AVLYST: {summary}"
            description = f"AVLYST: {tournament.cancellation_reason or 'ingen grunn oppgitt'}\n\n{description}"

        event = Event()
        event.add(
            "uid",
            _stable_uid(
                "tournament-summary",
                tournament.date.isoformat(),
                tournament.age_group,
                tournament.arena,
                tournament.host_club or "",
                tournament.start_time or "",
                club or "",
                ",".join(team.label for team in tournament.teams),
                ",".join(
                    f"{game.home.label}>{game.away.label}:{game.parallel_slot}:{game.round_number}"
                    for game in tournament.games
                ),
            ),
        )
        event.add("summary", vText(summary))
        event.add("dtstart", dt_start)
        event.add("dtend", dt_end)
        event.add("location", vText(tournament.arena))
        event.add("categories", [tournament.age_group])
        event.add("description", vText(description))
        if tournament.cancelled:
            event.add("status", "CANCELLED")
        return event
