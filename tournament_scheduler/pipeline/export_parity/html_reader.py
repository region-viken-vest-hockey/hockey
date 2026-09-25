"""Read the on-disk ``season_plan.html`` back into normalized records.

Only the embedded machine-readable tournament payload and the
``season-revision`` meta tag are read; the renderers' business policy is not
duplicated. Legacy HTML without a stable-id payload is reported uncheckable.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from .records import (
    ArtifactProjection,
    TournamentRecord,
    normalize_iso_date,
    normalize_participants,
    normalize_text,
    normalize_time,
)

_TOURNAMENTS_RE = re.compile(r"^\s*const\s+TOURNAMENTS\s*=\s*(\[.*\]);\s*$", re.MULTILINE)
_REVISION_RE = re.compile(
    r'<meta\s+name="season-revision"\s+content="([^"]*)"\s*/?>'
)


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def read_html(path: str | Path) -> ArtifactProjection:
    file_path = Path(path)
    projection = ArtifactProjection(kind="html", path=str(file_path), exists=file_path.exists())
    if not file_path.exists():
        return projection
    try:
        projection.sha256 = _file_sha256(file_path)
        text = file_path.read_text(encoding="utf-8")
        revision_match = _REVISION_RE.search(text)
        projection.season_revision = normalize_text(revision_match.group(1)) if revision_match else ""
        payload_match = _TOURNAMENTS_RE.search(text)
        if payload_match is None:
            projection.read_error = "embedded TOURNAMENTS payload is missing"
            return projection
        payload = json.loads(payload_match.group(1))
        if not isinstance(payload, list):
            projection.read_error = "embedded TOURNAMENTS payload is not a list"
            return projection
        projection.records = _read_payload(payload)
        if any(not record.tournament_id for record in projection.records):
            projection.unsupported_fields.append("tournament_id")
    except (OSError, ValueError, json.JSONDecodeError) as exc:  # noqa: BLE001 - unreadable bytes are NOT_CHECKABLE
        projection.read_error = f"could not read HTML artifact: {exc}"
    return projection


def _read_payload(payload: list[Any]) -> list[TournamentRecord]:
    records: list[TournamentRecord] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        records.append(
            TournamentRecord(
                tournament_id=normalize_text(item.get("id")),
                date=normalize_iso_date(item.get("d")),
                start_time=normalize_time(item.get("ts")),
                end_time=normalize_time(item.get("te")),
                arena=normalize_text(item.get("a")),
                host_club=normalize_text(item.get("h")),
                age_group=normalize_text(item.get("g")),
                participants=normalize_participants(
                    (team or {}).get("l")
                    for team in (item.get("p") or [])
                    if isinstance(team, dict)
                ),
                cancelled=bool(item.get("cx", False)),
                cancellation_reason=normalize_text(item.get("cr")),
                approval_status=normalize_text(item.get("ap")),
                locked=bool(item.get("apl", False)),
                booking_status=normalize_text(item.get("bs")),
                booking_needs_attention=bool(item.get("ba", False)),
            )
        )
    return records
