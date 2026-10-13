"""Physical layouts for one canonical season.

One logical canonical season is ``schedule`` + ``decisions`` (plus the separate
``export_context``). It is stored in one of two physical layouts that encode the
same logical payloads, so the canonical revision is one value in both:

* ``monolithic`` (legacy): ``schedule.json`` and ``decisions.json``.
* ``partitioned``: a ``season.json`` manifest, ``schedule.json`` for the schedule
  facts, and ``decisions/<partition>.json`` files grouped by ownership and
  lifecycle rather than by file size.

Only the canonical store and the migration module use this module. Callers above
the persistence boundary read the assembled payloads and never assemble partition
files themselves.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from tournament_scheduler.season.state import canonical_state_revision

LAYOUT_MONOLITHIC = "monolithic"
LAYOUT_PARTITIONED = "partitioned"
PARTITIONED_LAYOUT_VERSION = 2
MANIFEST_FILE_NAME = "season.json"
SCHEDULE_FILE_NAME = "schedule.json"
MONOLITHIC_DECISIONS_FILE_NAME = "decisions.json"
SCHEDULE_PARTITION_NAME = "schedule"
SEASON_STATE_SCHEMA_VERSION = 1
DECISIONS_SCHEMA_VERSION = 1


class SeasonStateError(RuntimeError):
    """Raised when canonical season state cannot be read or written safely."""


@dataclass(frozen=True)
class DecisionsPartition:
    """One ownership/lifecycle group of top-level ``decisions`` keys."""

    name: str
    path: str
    keys: frozenset[str]


# Every top-level decisions key belongs to exactly one partition. A key that is
# not assigned here is refused on write, so partitioning can never silently drop
# durable state.
DECISIONS_PARTITIONS: tuple[DecisionsPartition, ...] = (
    DecisionsPartition(
        "operations",
        "decisions/operations.json",
        frozenset(
            {
                "actor",
                "canonical_state_revision",
                "created_at",
                "decisions",
                "export_state",
                "schedule_fingerprint",
                "schema_version",
                "season",
                "season_lifecycle",
                "updated_at",
            }
        ),
    ),
    DecisionsPartition("history", "decisions/history.json", frozenset({"history"})),
    DecisionsPartition(
        "booking_evidence",
        "decisions/booking_evidence.json",
        frozenset(
            {
                "calendar_booking_associations",
                "club_booking_source_assertions",
                "manual_booking_assertions",
                "rejected_booking_evidence",
                "tournament_booking_evidence",
            }
        ),
    ),
    DecisionsPartition(
        "protections",
        "decisions/protections.json",
        frozenset(
            {
                "banned_dates",
                "change_protections",
                "holiday_date_exceptions",
                "ice_time_minutes_overrides",
                "participation_withdrawals",
                "request_constraints",
            }
        ),
    ),
)

_ALL_DECISIONS_KEYS = frozenset().union(*(partition.keys for partition in DECISIONS_PARTITIONS))
_PARTITION_BY_NAME = {partition.name: partition for partition in DECISIONS_PARTITIONS}


def json_bytes(payload: Mapping[str, Any]) -> bytes:
    return (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def load_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise SeasonStateError(f"Canonical season file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise SeasonStateError(f"Invalid JSON in canonical season file {path}: {exc}") from exc


def validate_schedule_payload(payload: dict[str, Any]) -> dict[str, Any]:
    version = int(payload.get("schema_version", 0) or 0)
    if version != SEASON_STATE_SCHEMA_VERSION:
        raise SeasonStateError(f"Unsupported schedule schema_version: {version!r}")
    if not isinstance(payload.get("plan"), dict):
        raise SeasonStateError("Canonical schedule is missing its plan payload")
    return payload


def validate_decisions_payload(payload: dict[str, Any]) -> dict[str, Any]:
    version = int(payload.get("schema_version", 0) or 0)
    if version != DECISIONS_SCHEMA_VERSION:
        raise SeasonStateError(f"Unsupported decisions schema_version: {version!r}")
    if not isinstance(payload.get("decisions"), dict):
        raise SeasonStateError("Canonical decisions file is missing its decisions object")
    return payload


def detect_layout(season_directory: Path) -> str:
    """The layout a season directory currently uses; the manifest marks partitioned seasons."""

    if (season_directory / MANIFEST_FILE_NAME).is_file():
        return LAYOUT_PARTITIONED
    return LAYOUT_MONOLITHIC


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def encode_layout(
    season: str,
    schedule: Mapping[str, Any],
    decisions: Mapping[str, Any],
    layout: str,
) -> dict[str, bytes]:
    """Return ``{relative_path: bytes}`` for every canonical file of one layout."""

    if layout == LAYOUT_MONOLITHIC:
        return {
            SCHEDULE_FILE_NAME: json_bytes(schedule),
            MONOLITHIC_DECISIONS_FILE_NAME: json_bytes(decisions),
        }
    if layout != LAYOUT_PARTITIONED:
        raise SeasonStateError(f"Unknown canonical season layout: {layout!r}")

    unassigned = sorted(set(decisions) - _ALL_DECISIONS_KEYS)
    if unassigned:
        raise SeasonStateError(
            f"Refusing to partition canonical decisions with unassigned keys: {unassigned}"
        )
    files: dict[str, bytes] = {SCHEDULE_FILE_NAME: json_bytes(schedule)}
    entries: list[dict[str, str]] = [
        {
            "name": SCHEDULE_PARTITION_NAME,
            "path": SCHEDULE_FILE_NAME,
            "sha256": _sha256(files[SCHEDULE_FILE_NAME]),
        }
    ]
    for partition in DECISIONS_PARTITIONS:
        payload = {key: decisions[key] for key in sorted(partition.keys) if key in decisions}
        data = json_bytes(payload)
        files[partition.path] = data
        entries.append({"name": partition.name, "path": partition.path, "sha256": _sha256(data)})
    manifest = {
        "layout_version": PARTITIONED_LAYOUT_VERSION,
        "season": season,
        "canonical_state_revision": canonical_state_revision(schedule, decisions),
        "schedule_fingerprint": str(schedule.get("fingerprint") or ""),
        "partitions": entries,
    }
    files[MANIFEST_FILE_NAME] = json_bytes(manifest)
    return files


def _parse_json(data: bytes, name: str) -> Any:
    try:
        return json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SeasonStateError(f"Invalid JSON in canonical partition {name}: {exc}") from exc


def _verified_partition(files: Mapping[str, bytes], entry: Mapping[str, Any]) -> Any:
    name = str(entry.get("name") or "")
    path_text = str(entry.get("path") or "")
    expected_path = SCHEDULE_FILE_NAME if name == SCHEDULE_PARTITION_NAME else (
        _PARTITION_BY_NAME[name].path if name in _PARTITION_BY_NAME else None
    )
    if expected_path is None or path_text != expected_path:
        raise SeasonStateError(f"Unknown canonical partition in manifest: {name!r} at {path_text!r}")
    data = files.get(path_text)
    if data is None:
        raise SeasonStateError(f"Canonical partition {name} is missing")
    if _sha256(data) != str(entry.get("sha256") or ""):
        raise SeasonStateError(f"Canonical partition {name} failed its integrity check")
    return _parse_json(data, name)


def decode_files(files: Mapping[str, bytes], season: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """Assemble ``(schedule, decisions)`` from in-memory partition bytes and verify one revision.

    This is the single decoder. The store uses it on disk and the migration uses
    it on staged bytes before anything is installed.
    """

    if MANIFEST_FILE_NAME not in files:
        raise SeasonStateError(f"Canonical manifest is missing for {season}")
    manifest = _parse_json(files[MANIFEST_FILE_NAME], MANIFEST_FILE_NAME)
    if not isinstance(manifest, dict):
        raise SeasonStateError("Canonical manifest is not an object")
    if int(manifest.get("layout_version", 0) or 0) != PARTITIONED_LAYOUT_VERSION:
        raise SeasonStateError(f"Unsupported canonical layout_version: {manifest.get('layout_version')!r}")
    if str(manifest.get("season") or "") != season:
        raise SeasonStateError(f"Canonical manifest belongs to {manifest.get('season')!r}, not {season!r}")
    entries = manifest.get("partitions")
    if not isinstance(entries, list):
        raise SeasonStateError("Canonical manifest is missing its partition list")
    by_name = {str(entry.get("name") or ""): entry for entry in entries if isinstance(entry, Mapping)}
    expected = {SCHEDULE_PARTITION_NAME, *_PARTITION_BY_NAME}
    if len(by_name) != len(entries) or set(by_name) != expected:
        raise SeasonStateError("Canonical manifest partitions do not match the layout")

    schedule = validate_schedule_payload(_verified_partition(files, by_name[SCHEDULE_PARTITION_NAME]))
    decisions: dict[str, Any] = {}
    for partition in DECISIONS_PARTITIONS:
        part = _verified_partition(files, by_name[partition.name])
        if not isinstance(part, dict) or not set(part) <= partition.keys:
            raise SeasonStateError(f"Canonical partition {partition.name} holds keys it does not own")
        overlap = set(part) & set(decisions)
        if overlap:
            raise SeasonStateError(f"Canonical decisions key appears in two partitions: {sorted(overlap)}")
        decisions.update(part)
    validate_decisions_payload(decisions)

    if str(schedule.get("fingerprint") or "") != str(manifest.get("schedule_fingerprint") or ""):
        raise SeasonStateError("Canonical schedule partition does not match the manifest fingerprint")
    if canonical_state_revision(schedule, decisions) != str(manifest.get("canonical_state_revision") or ""):
        raise SeasonStateError("Canonical partitions do not share one revision; refusing mixed state")
    return schedule, decisions


def decode_layout(season_directory: Path, season: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """Read a partitioned season from disk and decode it through :func:`decode_files`.

    Only the known partition paths are read, so a manifest cannot direct the reader
    to any other file.
    """

    names = (MANIFEST_FILE_NAME, SCHEDULE_FILE_NAME, *(partition.path for partition in DECISIONS_PARTITIONS))
    files: dict[str, bytes] = {}
    for name in names:
        if (season_directory / name).exists():
            try:
                files[name] = (season_directory / name).read_bytes()
            except OSError as exc:
                raise SeasonStateError(f"Canonical file {name} is unreadable: {exc}") from exc
    return decode_files(files, season)


__all__ = [
    "DECISIONS_PARTITIONS",
    "DECISIONS_SCHEMA_VERSION",
    "LAYOUT_MONOLITHIC",
    "LAYOUT_PARTITIONED",
    "MANIFEST_FILE_NAME",
    "MONOLITHIC_DECISIONS_FILE_NAME",
    "PARTITIONED_LAYOUT_VERSION",
    "SCHEDULE_FILE_NAME",
    "SEASON_STATE_SCHEMA_VERSION",
    "SeasonStateError",
    "decode_files",
    "decode_layout",
    "detect_layout",
    "encode_layout",
    "json_bytes",
    "load_json",
    "validate_decisions_payload",
    "validate_schedule_payload",
]
