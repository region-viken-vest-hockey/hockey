"""Calendar event model: one source booking as scraped, checkpointed and planned against."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping, Tuple


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

    @classmethod
    def from_source_dict(cls, data: Mapping[str, Any]) -> "CalendarEvent":
        """Rebuild an event from a checkpoint dict written by ``scraper_event_helpers._events_to_dicts``."""

        return cls(
            date=data["date"],
            name=data.get("name", ""),
            datetime=datetime.fromisoformat(data["datetime"]),
            duration_hours=data.get("duration_hours", 0.0),
            location=data.get("location", ""),
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

