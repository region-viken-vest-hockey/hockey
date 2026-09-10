"""Search history manager for tournament scheduler."""

import json
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Optional
from rich.console import Console

console = Console()


class SearchHistory:
    """Manages search history for the tournament scheduler."""

    def __init__(self, history_file: Optional[str] = None):
        """Initialize search history manager.

        Args:
            history_file: Path to history file. If None, uses default location.
        """
        if history_file:
            self.history_file = Path(history_file)
        else:
            home = Path.home()
            self.history_file = home / '.hockey_scheduler_history.json'

    def save_search(self, search_params: Dict) -> None:
        """Save a search to history.

        Args:
            search_params: Dictionary containing search parameters
        """
        # Add timestamp
        search_params['timestamp'] = datetime.now().isoformat()

        # Load existing history
        history = self.load_history()

        # Add new search to the beginning
        history.insert(0, search_params)

        # Keep only last 50 searches
        history = history[:50]

        # Save to file
        try:
            with open(self.history_file, 'w', encoding='utf-8') as f:
                json.dump(history, f, indent=2, ensure_ascii=False)
        except Exception as e:
            console.print(f"  [yellow]⚠[/yellow] Advarsel: Kunne ikke lagre søkehistorikk: {e}", style="yellow")

    def load_history(self) -> List[Dict]:
        """Load search history from file.

        Returns:
            List of search parameter dictionaries
        """
        if not self.history_file.exists():
            return []

        try:
            with open(self.history_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            console.print(f"  [yellow]⚠[/yellow] Advarsel: Kunne ikke laste søkehistorikk: {e}", style="yellow")
            return []

    def format_search_summary(self, search_params: Dict) -> str:
        """Format a search entry for display.

        Args:
            search_params: Search parameters

        Returns:
            Formatted string summary
        """
        parts = []

        # Mode
        if search_params.get('is_reschedule'):
            parts.append("Omplassering")
        elif search_params.get('season_plan'):
            parts.append("Sesongplan")
        else:
            parts.append("Nytt søk")

        # Date range
        start = search_params.get('start_date', '')
        end = search_params.get('end_date', '')
        if start and end:
            parts.append(f"{start} til {end}")

        # Excel file
        if search_params.get('excel_file'):
            excel_path = Path(search_params['excel_file'])
            parts.append(f"Excel: {excel_path.name}")

        # Tournament date (for reschedule)
        if search_params.get('tournament_date'):
            parts.append(f"Turnering: {search_params['tournament_date']}")

        # Calendars
        calendars = []
        if search_params.get('check_kongsberg_ice'):
            calendars.append("K-is")
        if search_params.get('check_kongsberg_ball'):
            calendars.append("K-ball")
        if search_params.get('check_skien_ice'):
            calendars.append("Skien")
        if calendars:
            parts.append(f"Kalendere: {', '.join(calendars)}")

        # Timestamp
        if 'timestamp' in search_params:
            try:
                ts = datetime.fromisoformat(search_params['timestamp'])
                time_str = ts.strftime('%d.%m.%Y %H:%M')
                parts.append(f"({time_str})")
            except (ValueError, TypeError):
                pass

        return " | ".join(parts)

    def clear_history(self) -> None:
        """Clear all search history."""
        if self.history_file.exists():
            self.history_file.unlink()
