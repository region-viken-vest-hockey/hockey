"""Normalized artifact records and shared vocabulary for export parity.

The readers in this package convert each on-disk format into the same
:class:`TournamentRecord` shape. Normalization here is *format-only* (Norwegian
``dd.mm.yyyy`` dates, an ``(AVLYST)`` date prefix, whitespace): it never erases
a substantive difference between the two artifacts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping

PARITY_SCHEMA_VERSION = 1
PARITY_FILENAME = "export_parity.json"

STATUS_PASS = "PASS"
STATUS_FAIL = "FAIL"
STATUS_NOT_CHECKABLE = "NOT_CHECKABLE"

# Schedule fields every artifact pair must expose before parity is meaningful.
SCHEDULE_FIELDS: tuple[str, ...] = (
    "date",
    "start_time",
    "end_time",
    "arena",
    "host_club",
    "age_group",
    "participants",
    "cancelled",
)

# Status fields compared only when an artifact format exposes them. A format
# that does not carry them is reported uncheckable rather than silently passed.
STATUS_FIELDS: tuple[str, ...] = (
    "cancellation_reason",
    "approval_status",
    "locked",
    "booking_status",
    "booking_needs_attention",
)

_CANCEL_PREFIXES = ("(AVLYST)", "AVLYST")
_DATE_FORMATS = ("%Y-%m-%d", "%d.%m.%Y", "%d.%m.%y")


def normalize_text(value: Any) -> str:
    """Collapse whitespace and coerce to a trimmed string."""
    return " ".join(str(value or "").split())


def has_cancel_prefix(value: Any) -> bool:
    text = normalize_text(value).upper()
    return any(text.startswith(prefix) for prefix in _CANCEL_PREFIXES)


def normalize_iso_date(value: Any) -> str:
    """Normalize a date (legacy ``dd.mm.yyyy`` or ISO) to ``YYYY-MM-DD``.

    A leading ``(AVLYST)``/``AVLYST`` marker is stripped; the cancellation fact
    is carried separately by the record.
    """
    text = normalize_text(value)
    for prefix in _CANCEL_PREFIXES:
        if text.upper().startswith(prefix):
            text = text[len(prefix):].strip()
            break
    if not text:
        return ""
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return text


def normalize_time(value: Any) -> str:
    return normalize_text(value)[:5]


def normalize_participants(values: Iterable[Any]) -> tuple[str, ...]:
    return tuple(sorted(text for text in (normalize_text(value) for value in values) if text))


def yes_no(value: Any) -> bool:
    return normalize_text(value).lower() in {"ja", "yes", "true", "1", "x"}


@dataclass(frozen=True)
class TournamentRecord:
    """One tournament as projected from a single artifact format."""

    tournament_id: str
    date: str = ""
    start_time: str = ""
    end_time: str = ""
    arena: str = ""
    host_club: str = ""
    age_group: str = ""
    participants: tuple[str, ...] = ()
    cancelled: bool = False
    cancellation_reason: str = ""
    approval_status: str = ""
    locked: bool = False
    booking_status: str = ""
    booking_needs_attention: bool = False

    def field(self, name: str) -> Any:
        return getattr(self, name)

    def schedule_fields(self) -> dict[str, Any]:
        return {name: self.field(name) for name in SCHEDULE_FIELDS}

    def public_dict(self) -> dict[str, Any]:
        data = {"id": self.tournament_id}
        data.update(self.schedule_fields())
        data.update({name: self.field(name) for name in STATUS_FIELDS})
        data["participants"] = list(self.participants)
        return data


@dataclass
class ArtifactProjection:
    """A normalized projection read back from one on-disk artifact."""

    kind: str
    path: str
    exists: bool
    sha256: str = ""
    season_revision: str = ""
    records: list[TournamentRecord] = field(default_factory=list)
    # Fields the format genuinely cannot carry (never silently treated as equal).
    unsupported_fields: list[str] = field(default_factory=list)
    read_error: str = ""

    @property
    def readable(self) -> bool:
        return self.exists and not self.read_error

    def records_by_id(self) -> dict[str, TournamentRecord]:
        return {record.tournament_id: record for record in self.records}

    def duplicate_ids(self) -> list[str]:
        seen: set[str] = set()
        duplicates: set[str] = set()
        for record in self.records:
            if record.tournament_id in seen:
                duplicates.add(record.tournament_id)
            seen.add(record.tournament_id)
        return sorted(duplicates)

    def artifact_ref(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "path": Path(self.path).name,
            "exists": self.exists,
            "sha256": self.sha256,
            "season_revision": self.season_revision,
            "tournament_count": len(self.records),
            "duplicate_ids": self.duplicate_ids(),
            "unsupported_fields": list(self.unsupported_fields),
            "read_error": self.read_error or None,
        }


def missing_identity(projection: ArtifactProjection) -> bool:
    """True when a projection has records but cannot be compared by stable id."""
    return bool(projection.records) and any(
        not record.tournament_id for record in projection.records
    )


def as_mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}
