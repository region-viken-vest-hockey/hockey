"""Source-neutral ice-time evidence exposed to ``operate``, plus validated manual edits.

Reads snapshots through the existing ``IceTimeSnapshot`` contract (manual / imported /
live / fixed alike) and exposes revision, declared coverage, venue identity, local+UTC
intervals and provenance. Nothing scraper-specific is exposed. A source update only
swaps evidence: it never confirms a booking or moves a tournament; it changes the
*evidence revision*, which invalidates any proposal computed against the old one.
Booking assertions stay owned by the canonical booking-assertion use cases.
"""

from __future__ import annotations

import hashlib
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping
from zoneinfo import ZoneInfo

from tournament_scheduler.application.ice_time_sources.authority import Authority
from tournament_scheduler.application.ice_time_sources.validation import IceTimeValidationError
from tournament_scheduler.application.ports.common import PortError, PortErrorCode
from tournament_scheduler.application.ports.ice_time import SourceScope, WindowKind
from tournament_scheduler.infrastructure.ice_time.manual import ManualSnapshotStore, build_manual_snapshot
from tournament_scheduler.infrastructure.ice_time.snapshot import IceTimeSnapshot

from .contracts import ApiError, ErrorCode, DURABLE_ID

MAX_WINDOWS = 2000


def _interval(start: datetime, end: datetime, tz: str) -> dict[str, str]:
    zone = ZoneInfo(tz)
    return {"local_start": start.astimezone(zone).isoformat(), "local_end": end.astimezone(zone).isoformat(),
            "utc_start": start.astimezone(ZoneInfo("UTC")).isoformat(),
            "utc_end": end.astimezone(ZoneInfo("UTC")).isoformat()}


def snapshot_view(snap: IceTimeSnapshot) -> dict[str, Any]:
    ref, tz = snap.source, snap.source.timezone
    return {
        "source_id": ref.source_id, "mode": ref.kind.value, "revision": snap.revision,
        "venue_id": snap.scope.venue_id,
        "coverage": {"completeness": snap.completeness.value, **_interval(snap.scope.from_date, snap.scope.to_date, tz),
                     "fresh_until": snap.fresh_until.isoformat() if snap.fresh_until else None},
        "state": "unknown" if snap.completeness.value == "unknown" else snap.completeness.value,
        "provenance": {"actor": ref.actor, "document": ref.document, "captured_at": ref.captured_at.isoformat(),
                       "timezone": tz, "source_revision": ref.revision},
        "windows": [{"venue_id": w.venue_id, "kind": w.kind.value, **_interval(w.starts_at, w.ends_at, tz)}
                    for w in snap.windows],
        "events": [{"event_id": e.event_id, "venue_id": e.venue_id, "title": e.title,
                    **_interval(e.starts_at, e.ends_at, tz)} for e in snap.events],
    }


class IceTimeEvidence:
    def __init__(self, directory: Path) -> None:
        self.store = ManualSnapshotStore(directory)
        self.directory = Path(directory)
        # Shared with OperateService: evidence swaps and canonical commits never interleave.
        self.gate = threading.RLock()

    def source_ids(self) -> list[str]:
        if not self.directory.is_dir():
            return []
        return sorted(p.stem for p in self.directory.glob("*.json") if not p.name.startswith("."))

    def list(self) -> list[dict[str, Any]]:
        out = []
        for sid in self.source_ids():
            try:
                snap = self.store.load(sid)
                out.append({"source_id": sid, "status": "ok", "revision": snap.revision,
                            "mode": snap.source.kind.value, "venue_id": snap.scope.venue_id,
                            "completeness": snap.completeness.value})
            except Exception:  # an unreadable source is reported as unknown, never as free ice
                out.append({"source_id": sid, "status": "unreadable", "completeness": "unknown"})
        return out

    def get(self, source_id: str) -> dict[str, Any]:
        try:
            return snapshot_view(self.store.load(source_id))
        except PortError as exc:
            raise ApiError(404, ErrorCode.NOT_FOUND, exc.message) from exc

    def evidence_revision(self) -> str:
        parts = [f"{i['source_id']}={i.get('revision', i['status'])}" for i in self.list()]
        return hashlib.sha256("|".join(parts).encode()).hexdigest()[:32]

    def put_manual(self, body: Mapping[str, Any], actor: str) -> dict[str, Any]:
        """Validate, diff and revision-bind a manual edit. Never touches bookings."""
        try:
            sid = str(body["source_id"])
            if not DURABLE_ID.match(sid):
                raise ValueError("invalid source_id")
            tz = str(body["timezone"])
            ZoneInfo(tz)
            raw = body.get("windows") or []
            if not isinstance(raw, list) or len(raw) > MAX_WINDOWS:
                raise ValueError("windows must be a list of bounded size")
            windows = [(datetime.fromisoformat(w["start"]), datetime.fromisoformat(w["end"]), WindowKind(w["kind"]))
                       for w in raw]
            scope = SourceScope(str(body["venue_id"]), datetime.fromisoformat(body["from"]),
                                datetime.fromisoformat(body["to"]))
            a = body.get("authority")
            authority = None if a is None else Authority(
                str(a["issuer"]), str(a["scope"]), datetime.fromisoformat(a["from"]),
                datetime.fromisoformat(a["until"]), scope.venue_id, datetime.fromisoformat(a["covers_from"]),
                datetime.fromisoformat(a["covers_to"]), str(a.get("document", "")))
            captured = datetime.now(ZoneInfo("UTC"))
            snap = build_manual_snapshot(source_id=sid, actor=actor, timezone=tz, scope=scope, windows=windows,
                                         authority=authority, captured_at=captured)
        except (KeyError, TypeError, ValueError, IceTimeValidationError) as exc:
            raise ApiError(400, ErrorCode.BAD_REQUEST, f"invalid manual ice-time snapshot: {exc}") from exc
        expected = body.get("expected_revision")
        try:
            with self.gate:
                diff = self.store.replace(snap, expected if isinstance(expected, str) else None)
        except PortError as exc:
            code = ErrorCode.STALE_REVISION if exc.code is PortErrorCode.REVISION_CONFLICT else ErrorCode.BAD_REQUEST
            raise ApiError(409 if code is ErrorCode.STALE_REVISION else 400, code, exc.message) from exc
        return {"source": snapshot_view(snap), "diff": {"is_empty": diff.is_empty,
                "invalidates_previews": diff.invalidates_previews, "window_changes": len(diff.changes),
                "event_changes": len(diff.event_changes), "completeness_changed": diff.completeness_changed},
                "evidence_revision": self.evidence_revision(),
                "bookings_changed": False}
