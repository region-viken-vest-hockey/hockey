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


def participant_separator() -> str:
    """Canonical participant-identity separator (single owner: published_baseline)."""

    from tournament_scheduler.published_baseline import PARTICIPANT_SEPARATOR

    return PARTICIPANT_SEPARATOR


def participant_identity(club: Any, label: Any, age_group: Any) -> str:
    """Normalize a full ``club|label|age`` canonical participant identity.

    The club component is resolved through the canonical club registry so a
    legacy alias in one artifact/projection does not read as a different team
    than the canonical name in the other.
    """

    club_name = normalize_text(club)
    if club_name:
        try:
            from tournament_scheduler.club_registry import canonicalize_club_name

            club_name = canonicalize_club_name(club_name)
        except Exception:  # noqa: BLE001 - identity normalization must not fail parity
            pass
    return participant_separator().join((club_name, normalize_text(label), normalize_text(age_group)))


def normalize_participant_keys(values: Iterable[Any]) -> tuple[str, ...]:
    """Normalize canonical participant identities, preserving full identity.

    A key is ``club|label|age``; a legacy plain label (no separator) is kept as
    a label-only identity rather than being misparsed into the club slot.
    """

    separator = participant_separator()
    normalized: list[str] = []
    for value in values or []:
        text = str(value or "")
        if separator in text:
            club, label, age_group = text.split(separator, 2)
            normalized.append(participant_identity(club, label, age_group))
        elif normalize_text(text):
            normalized.append(participant_identity("", text, ""))
    return tuple(sorted(set(normalized)))


def participant_label(key: Any) -> str:
    """Return the human label component of a canonical participant identity."""

    text = str(key or "")
    separator = participant_separator()
    if separator in text:
        from tournament_scheduler.published_baseline import participant_parts

        _, label, _ = participant_parts(text)
        return normalize_text(label)
    return normalize_text(text)


def normalize_game(round_number: Any, home: Any, away: Any, slot: Any) -> str:
    """Stable game identity: ``round|home|away|0-based-parallel-slot``."""
    round_text = normalize_text(round_number)
    try:
        slot_text = str(int(slot))
    except (TypeError, ValueError):
        slot_text = normalize_text(slot)
    return "|".join((round_text, normalize_text(home), normalize_text(away), slot_text))


def normalize_games(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(sorted(normalize_text(value) for value in values if normalize_text(value)))


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
    # Human label set and the full ``club|label|age`` identities where the
    # format carries them. Both are compared against the frozen projection so a
    # format that can only expose labels does not silently hide a club/age
    # reassignment that the other format (HTML) can expose.
    participants: tuple[str, ...] = ()
    participant_keys: tuple[str, ...] = ()
    # ``(open, filled, reserved, released)`` guest-reservation counts. Empty
    # when a format carries no guest information at all.
    guest_slots_summary: tuple[int, ...] = ()
    games: tuple[str, ...] = ()
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
