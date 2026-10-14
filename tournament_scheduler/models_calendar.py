"""Calendar event model: one source booking as scraped, checkpointed and planned against."""

from dataclasses import dataclass
from datetime import datetime, timedelta
import math
from typing import Any, Mapping, Tuple


# Float hours are a compatibility input used by existing source adapters. Values
# this close to a whole minute differ only by binary representation noise; any
# larger fractional remainder is genuine sub-minute data and is truncated so
# normalized occupancy never expands beyond source evidence.
_FLOAT_MINUTE_NOISE = 1e-9


def minute_duration_from_hours(duration_hours: float) -> int:
    """Normalize compatibility float hours to non-expanding minute precision.

    Calendar evidence is represented at whole-minute precision. A value within
    ``_FLOAT_MINUTE_NOISE`` of a whole minute is snapped to that minute to undo
    binary float noise (for example a source 11:15-12:55 interval). Genuine
    sub-minute remainders are truncated and therefore never expand occupancy.
    """
    try:
        raw_minutes = float(duration_hours) * 60
    except (TypeError, ValueError):
        return 0
    if not math.isfinite(raw_minutes) or raw_minutes <= 0:
        return 0
    nearest_minute = round(raw_minutes)
    if abs(raw_minutes - nearest_minute) <= _FLOAT_MINUTE_NOISE:
        return nearest_minute
    return math.floor(raw_minutes)


@dataclass(frozen=True)
class SourceResource:
    """One bookable resource a source booking occupies, as the source reports it."""

    id: str  # Source resourceId, e.g. "362"
    label: str  # Source resource name, e.g. "Ishockey - bane 1"


@dataclass
class CalendarEvent:
    """Represents a calendar event with scheduling information."""

    date: str  # Format: DD.MM.YYYY
    name: str
    datetime: datetime
    duration_hours: float = 0.0
    location: str = ""  # Display label. Sources that report resources derive it from ``resources``
    # Structured upstream identity for sources that report one booking across
    # several resources. Empty for sources that do not report resources.
    resources: Tuple[SourceResource, ...] = ()
    order_id: str = ""
    # Canonical normalized duration. ``duration_hours`` remains as a
    # compatibility input/output for existing adapters, but consumers should
    # use this integer or ``end_datetime`` instead of reinterpreting the float.
    duration_minutes: int | None = None

    def __post_init__(self) -> None:
        if self.duration_minutes is None:
            normalized_minutes = minute_duration_from_hours(self.duration_hours)
        else:
            if isinstance(self.duration_minutes, bool) or int(self.duration_minutes) != self.duration_minutes:
                raise ValueError("duration_minutes must be a whole number")
            normalized_minutes = max(0, int(self.duration_minutes))
        self.duration_minutes = normalized_minutes
        self.duration_hours = normalized_minutes / 60

    @property
    def end_datetime(self) -> datetime:
        """Return the exact normalized minute-precision event end."""
        return self.datetime + timedelta(minutes=self.duration_minutes)

    @classmethod
    def from_source_dict(cls, data: Mapping[str, Any]) -> "CalendarEvent":
        """Rebuild an event from a checkpoint dict written by ``scraper_event_helpers._events_to_dicts``."""

        return cls(
            date=data["date"],
            name=data.get("name", ""),
            datetime=datetime.fromisoformat(data["datetime"]),
            duration_hours=data.get("duration_hours", 0.0),
            location=data.get("location", ""),
            duration_minutes=data.get("duration_minutes"),
            **source_identity_from_dict(data),
        )


def source_identity_to_dict(event: CalendarEvent) -> dict[str, Any]:
    """Serialise the structured resource identity; empty for sources that report none."""

    identity: dict[str, Any] = {}
    if event.resources:
        identity["resources"] = [{"id": res.id, "label": res.label} for res in event.resources]
    if event.order_id:
        identity["order_id"] = event.order_id
    return identity


def source_identity_from_dict(data: Mapping[str, Any]) -> dict[str, Any]:
    """Return ``CalendarEvent`` keyword arguments for the identity written by :func:`source_identity_to_dict`."""

    resources = tuple(SourceResource(id=str(res["id"]), label=str(res["label"])) for res in data.get("resources", ()))
    return {"resources": resources, "order_id": str(data.get("order_id") or "")}

