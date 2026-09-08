"""Calendar event caching to avoid repeated scraping."""

import hashlib
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional
from tournament_scheduler.models import CalendarEvent
from rich.console import Console

console = Console()


class CalendarCache:
    """Cache calendar events to avoid repeated scraping."""

    def __init__(
        self,
        cache_dir: Optional[str] = None,
        ttl_minutes: int = 60,
        work_dir: Optional[str] = None,
    ):
        """Initialize calendar cache.

        Args:
            cache_dir: Explicit cache directory for JSON files.
            ttl_minutes: Cache time-to-live in minutes (default: 60)
            work_dir: Pipeline work directory. When provided, cache files live
                under ``<work_dir>/cache/calendars``.

        Defaults to ``.pipeline/cache/calendars``.
        """
        if cache_dir:
            self.cache_dir = Path(cache_dir)
        else:
            base_dir = Path(work_dir) if work_dir else Path(".pipeline")
            self.cache_dir = base_dir / "cache" / "calendars"

        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.ttl = timedelta(minutes=ttl_minutes)

    def _get_cache_key(
        self,
        url: str,
        calendar_name: str,
        start_date: datetime,
        end_date: datetime,
        location_filter: Optional[str] = None,
    ) -> str:
        """Generate cache key for a calendar request.

        Args:
            url: Calendar URL
            calendar_name: Name of calendar
            start_date: Start date
            end_date: End date
            location_filter: Optional location filter string; included in the
                key so that different filters produce distinct cache entries.

        Returns:
            Cache key (hex hash)
        """
        filter_part = location_filter if location_filter is not None else ""
        key_str = f"{url}|{calendar_name}|{start_date.isoformat()}|{end_date.isoformat()}|{filter_part}"
        return hashlib.md5(key_str.encode()).hexdigest()

    def get(
        self,
        url: str,
        calendar_name: str,
        start_date: datetime,
        end_date: datetime,
        location_filter: Optional[str] = None,
    ) -> Optional[List[CalendarEvent]]:
        """Get cached events if available and not stale.

        Args:
            url: Calendar URL
            calendar_name: Name of calendar
            start_date: Start date
            end_date: End date
            location_filter: Optional location filter; must match the value
                used when the entry was stored, or a cache miss is returned.

        Returns:
            List of CalendarEvent objects if cache hit, None otherwise
        """
        cache_key = self._get_cache_key(url, calendar_name, start_date, end_date, location_filter)
        cache_file = self.cache_dir / f"{cache_key}.json"

        if not cache_file.exists():
            return None

        try:
            with open(cache_file, 'r', encoding='utf-8') as f:
                data = json.load(f)

            # Check if cache is stale
            cached_time = datetime.fromisoformat(data['timestamp'])
            if datetime.now() - cached_time > self.ttl:
                return None

            # Reconstruct CalendarEvent objects. Preserve source audit fields
            # (LOCATION and iCal all-day semantics) instead of dropping them on
            # a cache hit; otherwise a fresh scrape and cached scrape would
            # produce different calendars.html evidence.
            events = []
            for event_data in data['events']:
                event = CalendarEvent(
                    date=event_data['date'],
                    name=event_data['name'],
                    datetime=datetime.fromisoformat(event_data['datetime']),
                    duration_hours=event_data['duration_hours'],
                    location=event_data.get('location', ''),
                )
                if event_data.get('all_day'):
                    event.all_day = True
                events.append(event)

            return events

        except Exception:
            # If cache is corrupted, return None
            return None

    def set(
        self,
        url: str,
        calendar_name: str,
        start_date: datetime,
        end_date: datetime,
        events: List[CalendarEvent],
        location_filter: Optional[str] = None,
    ) -> None:
        """Cache calendar events.

        Args:
            url: Calendar URL
            calendar_name: Name of calendar
            start_date: Start date
            end_date: End date
            events: List of CalendarEvent objects to cache
            location_filter: Optional location filter used when fetching these
                events; stored in the cache key so different filters are
                cached independently.
        """
        cache_key = self._get_cache_key(url, calendar_name, start_date, end_date, location_filter)
        cache_file = self.cache_dir / f"{cache_key}.json"

        # Serialize events. Keep audit metadata so cached and live iCal
        # results are semantically identical for Stage 2 and calendars.html.
        events_data = []
        for event in events:
            event_data = {
                'date': event.date,
                'name': event.name,
                'datetime': event.datetime.isoformat(),
                'duration_hours': event.duration_hours,
            }
            if event.location:
                event_data['location'] = event.location
            if bool(getattr(event, 'all_day', False)):
                event_data['all_day'] = True
            events_data.append(event_data)

        data = {
            'timestamp': datetime.now().isoformat(),
            'url': url,
            'calendar_name': calendar_name,
            'start_date': start_date.isoformat(),
            'end_date': end_date.isoformat(),
            'events': events_data
        }

        try:
            with open(cache_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            # Fail silently but log warning
            console.print(f"  [yellow]⚠[/yellow] Advarsel: Kunne ikke cache kalenderdata: {e}", style="yellow")

    def clear(self) -> None:
        """Clear all cached calendar data."""
        for cache_file in self.cache_dir.glob("*.json"):
            try:
                cache_file.unlink()
            except Exception:
                pass