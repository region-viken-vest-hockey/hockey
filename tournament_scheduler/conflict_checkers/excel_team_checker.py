"""Excel team conflict checker - checks if teams have other games in Excel."""

from datetime import date, timedelta
from typing import List, Dict
from tournament_scheduler.interfaces import ConflictChecker
from tournament_scheduler.models import ConflictContext, ConflictResult
from tournament_scheduler.utils.date_parser import DateParser
from tournament_scheduler.utils.rich_output import TournamentOutput
import openpyxl


class ExcelTeamConflictChecker(ConflictChecker):
    """Checks if teams have other games scheduled in the Excel file."""

    def __init__(self, excel_file: str, tournament_teams: List[str], date_parser: DateParser):
        """Initialize Excel team conflict checker.

        Args:
            excel_file: Path to Excel file with schedule
            tournament_teams: List of team names to check for
            date_parser: DateParser instance
        """
        self.excel_file = excel_file
        self.tournament_teams = tournament_teams
        self.date_parser = date_parser
        self.weekend_warnings = {}  # Store weekend conflict warnings

    def check_conflicts(self, dates: List[date], context: ConflictContext) -> ConflictResult:
        """Check for team conflicts in Excel file.

        Args:
            dates: Dates to check
            context: Context with additional info

        Returns:
            ConflictResult with dates where teams have other games on same date (blocked)
            Weekend conflicts are stored as warnings but don't block dates
        """
        excluded_dates = set()
        reasons = {}
        self.weekend_warnings = {}

        if not self.tournament_teams:
            return ConflictResult(
                excluded_dates=set(),
                reasons={},
                checker_name=self.get_checker_name()
            )

        TournamentOutput.print_info(f"Sjekker Excel-fil for lag-konflikter ({len(self.tournament_teams)} lag)...")

        # Scan Excel file for team mentions with event details
        team_events = self._find_team_events_in_excel()

        same_day_list = []
        weekend_conflicts = []

        for check_date in dates:
            # Check for same-day conflicts (these block the date)
            conflicting_teams = []
            for team in self.tournament_teams:
                if check_date in team_events.get(team, {}):
                    conflicting_teams.append(team)

            if conflicting_teams:
                excluded_dates.add(check_date)
                team_list = ', '.join(conflicting_teams[:2])
                if len(conflicting_teams) > 2:
                    team_list += f" og {len(conflicting_teams) - 2} flere"
                reasons[check_date] = f"Excel: {team_list} spiller andre kamper"
                same_day_list.append((check_date, team_list))
            else:
                # Check for same-weekend conflicts (these warn but don't block)
                weekend_date = self._get_weekend_pair(check_date)
                if weekend_date:
                    warning_teams = []
                    for team in self.tournament_teams:
                        if weekend_date in team_events.get(team, {}):
                            event_name = team_events[team][weekend_date]
                            warning_teams.append((team, event_name, weekend_date))

                    if warning_teams:
                        self.weekend_warnings[check_date] = warning_teams
                        weekend_conflicts.append((check_date, warning_teams))

        if same_day_list:
            TournamentOutput.print_conflict_table(
                "EXCEL SAMME DAG-KONFLIKTER",
                same_day_list
            )
        else:
            TournamentOutput.print_success("Ingen samme dag-konflikter i Excel")

        if weekend_conflicts:
            TournamentOutput.print_warning(
                f"HELGE-KONFLIKTER (samme helg, ulik dag - {len(weekend_conflicts)} advarsler - blokkerer IKKE):"
            )
            from rich.console import Console
            console = Console()
            for check_date, warning_teams in weekend_conflicts[:10]:
                for team, event, conflict_date in warning_teams[:2]:
                    console.print(f"  [yellow]{check_date.strftime('%Y-%m-%d')}: {team} spiller {conflict_date.strftime('%Y-%m-%d')} - {event[:50]}[/yellow]")
            if len(weekend_conflicts) > 10:
                console.print(f"  [yellow]... og {len(weekend_conflicts) - 10} advarsler til[/yellow]")

        return ConflictResult(
            excluded_dates=excluded_dates,
            reasons=reasons,
            checker_name=self.get_checker_name()
        )

    def get_checker_name(self) -> str:
        """Get checker name.

        Returns:
            'excel_team_conflict'
        """
        return 'excel_team_conflict'

    def _find_team_events_in_excel(self) -> Dict[str, Dict[date, str]]:
        """Find all dates where each team appears in the Excel file with event names.

        Returns:
            Dict mapping team name to dict of (date -> event_name)
        """
        team_events = {team: {} for team in self.tournament_teams}

        try:
            wb = openpyxl.load_workbook(self.excel_file, data_only=True)
            ws = wb.active

            # Track current tournament context
            current_tournament_num = None
            current_location = None
            current_date = None

            all_rows = list(ws.iter_rows(min_row=1, values_only=True))

            for row_idx, row in enumerate(all_rows, start=1):
                # Check each cell in the row for headers
                for cell_idx, cell in enumerate(row):
                    if cell and isinstance(cell, str):
                        cell_str = str(cell).strip()
                        cell_lower = cell_str.lower()

                        # Look for tournament number header row
                        if 'turnering nr' in cell_lower:
                            current_tournament_num = cell_str
                            current_location = None
                            current_date = None

                            # Find column index of "Arrangør" or "Arrangør:"
                            arranger_col_idx = None
                            for i, header_cell in enumerate(row):
                                if header_cell and isinstance(header_cell, str):
                                    if header_cell.strip().lower().startswith('arrangør'):
                                        arranger_col_idx = i
                                        break

                            # Get location from next row at the same column
                            if arranger_col_idx is not None and row_idx < len(all_rows):
                                next_row = all_rows[row_idx]  # row_idx is 1-based, list is 0-based
                                if arranger_col_idx < len(next_row):
                                    location_cell = next_row[arranger_col_idx]
                                    if location_cell and str(location_cell).strip():
                                        current_location = str(location_cell).strip()

                # Check for date in this row
                for cell in row:
                    parsed = self.date_parser.parse_datetime_cell(cell)
                    if parsed:
                        current_date = parsed.date()
                        break

                # After processing the row, check for team names if we have a date
                if current_date:
                    for cell in row:
                        if cell and isinstance(cell, str):
                            cell_str = str(cell).strip()

                            # Check each team
                            for team in self.tournament_teams:
                                if self._team_matches(team, cell_str):
                                    # Build event name from available context
                                    if current_location:
                                        event_name = current_location
                                    elif current_tournament_num:
                                        event_name = current_tournament_num
                                    else:
                                        event_name = "Ukjent turnering"

                                    # Store event name with the date
                                    if current_date not in team_events[team]:
                                        team_events[team][current_date] = event_name

            wb.close()

        except Exception as e:
            TournamentOutput.print_warning(f"Kunne ikke skanne Excel-fil for lag-konflikter: {e}")

        return team_events

    def _get_weekend_pair(self, check_date: date) -> date:
        """Get the other day of the same weekend.

        Args:
            check_date: Date to find weekend pair for

        Returns:
            The other weekend date (Saturday if input is Sunday, Sunday if input is Saturday)
            None if not a weekend day
        """
        weekday = check_date.weekday()

        if weekday == 5:  # Saturday
            return check_date + timedelta(days=1)  # Sunday
        elif weekday == 6:  # Sunday
            return check_date - timedelta(days=1)  # Saturday
        else:
            return None

    def _team_matches(self, team: str, text: str) -> bool:
        """Check if a team name matches text.

        Args:
            team: Team name to look for
            text: Text to search in

        Returns:
            True if team matches
        """
        team_lower = team.lower().strip()
        text_lower = text.lower().strip()

        # Exact match
        if team_lower == text_lower:
            return True

        # Normalize spaces and hyphens
        team_normalized = team_lower.replace(' ', '').replace('-', '')
        text_normalized = text_lower.replace(' ', '').replace('-', '').replace('/', '')

        # Check normalized match
        if team_normalized == text_normalized:
            return True

        # Check if team appears as a word in the text
        # But be more strict - need at least 2 significant words to match
        team_words = [w for w in team_lower.split() if len(w) > 2]
        text_words = set(text_lower.split())

        if len(team_words) >= 2:
            # Need at least 2 words to match
            matches = sum(1 for word in team_words if word in text_words)
            if matches >= 2:
                return True

        # For single-word teams or short teams, require exact substring match
        if team_lower in text_lower:
            return True

        return False
