"""Calendar event value object shared by scraping, caching and scheduling."""

from dataclasses import dataclass
from datetime import datetime


@dataclass
class CalendarEvent:
    """Represents a calendar event with scheduling information."""

    date: str  # Format: DD.MM.YYYY
    name: str
    datetime: datetime
    duration_hours: float = 0.0
    location: str = ""  # Raw LOCATION field from the source calendar
    source_event_id: str = ""  # Stable upstream event/occurrence identity, when exposed
