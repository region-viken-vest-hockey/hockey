"""HTML templates used by the season exporter and public viewers.

Each template is a standalone file loaded at import time.
"""

from pathlib import Path

_TEMPLATE_DIR = Path(__file__).parent


def _load(name: str) -> str:
    """Load a template fragment from the templates directory."""
    return (_TEMPLATE_DIR / name).read_text(encoding="utf-8")


# Shared CSS for dark theme
STYLES_CSS = _load("styles.css")

# Navbar fragment (shared across pages)
NAVBAR = _load("navbar.html")

# Season plan specific sections
HEADER = _load("header.html")
SCORES = _load("scores.html")
METRICS = _load("metrics.html")
FILTERS = _load("filters.html")
COUNT_BAR = _load("count_bar.html")

# Team detail sections
TEAM_STATS = _load("team_stats.html")
TRAVEL_STATS = _load("travel_stats.html")
HEATMAP = _load("heatmap.html")
CLUB_DASHBOARD = _load("club_dashboard.html")
REVIEW_SUMMARY = _load("review_summary.html")
REPORT_OVERVIEW = _load("report_overview.html")

# Full page templates
PAGE_TEMPLATE = _load("page_template.html")
NOT_STARTED = _load("not_started.html")
ACTIVITY_VIEWER = _load("activity_viewer.html")
CALENDAR_VIEWER = _load("calendar_viewer.html")
INPUT_VIEWER = _load("input_viewer.html")
REGISTERED_TEAMS = _load("registered_teams.html")
PAGES_ROOT_INDEX = _load("pages_root_index.html")
PAGES_EMPTY_INDEX = _load("pages_empty_index.html")

# JavaScript for interactivity
JAVASCRIPT = _load("script.js")
SHARED_JAVASCRIPT = _load("script_shared.js")
SCHEDULE_JAVASCRIPT = _load("script_schedule.js")
