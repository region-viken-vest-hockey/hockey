"""CSV/XLSX per-rink/date/time availability import into the normalized schema."""

from __future__ import annotations

import csv
import hashlib
import io
from datetime import date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

from tournament_scheduler.application.ice_time_sources.validation import IceTimeValidationError
from tournament_scheduler.application.ports.common import PortError, PortErrorCode, SourceKind, SourceRef
from tournament_scheduler.application.ports.ice_time import (
    AllocationWindow,
    Completeness,
    SourceScope,
    WindowKind,
)

from .snapshot import IceTimeSnapshot, SnapshotSource

FEATURES = frozenset({"allocations", "file", "offline"})
COLUMNS = ("venue_id", "date", "start", "end", "kind")


def _local(day: str, clock: str, tz: ZoneInfo) -> datetime:
    """Local wall time -> aware; nonexistent (DST gap) or ambiguous (fold) times are rejected."""
    naive = datetime.combine(date.fromisoformat(day), time.fromisoformat(clock))
    a = naive.replace(tzinfo=tz, fold=0)
    b = naive.replace(tzinfo=tz, fold=1)
    if a.utcoffset() != b.utcoffset():
        raise ValueError(f"ambiguous or nonexistent local time {day} {clock}")
    if a.astimezone(ZoneInfo("UTC")).astimezone(tz).replace(tzinfo=None) != naive:
        raise ValueError(f"nonexistent local time {day} {clock}")
    return a


def _rows_csv(data: bytes) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(data.decode("utf-8-sig"))))


def _rows_xlsx(data: bytes) -> list[dict[str, str]]:
    from openpyxl import load_workbook

    ws = load_workbook(io.BytesIO(data), read_only=True, data_only=True).active
    it = iter(ws.iter_rows(values_only=True))
    head = [str(h).strip() if h is not None else "" for h in next(it, ())]
    return [{h: ("" if v is None else str(v)) for h, v in zip(head, r)} for r in it
            if any(v is not None for v in r)]


def import_snapshot(
    path: Path, *, source_id: str, timezone: str, scope: SourceScope,
    captured_at: datetime, attested_complete: bool = False,
) -> IceTimeSnapshot:
    """Parse and validate the whole file; any bad row rejects the import (no partial result).

    Imports only ever yield AVAILABLE/BLOCKED windows, never booking assertions.
    """
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix not in (".csv", ".xlsx"):
        raise PortError(PortErrorCode.UNSUPPORTED_FEATURE, f"unsupported import format {suffix}")
    data = path.read_bytes()
    fingerprint = hashlib.sha256(data).hexdigest()
    rows = _rows_csv(data) if suffix == ".csv" else _rows_xlsx(data)
    if rows and not set(COLUMNS) <= set(rows[0]):
        raise IceTimeValidationError([f"missing columns: {sorted(set(COLUMNS) - set(rows[0]))}"])
    tz = ZoneInfo(timezone)
    ref = SourceRef(source_id, SourceKind.FILE, captured_at, timezone,
                    document=path.name, fingerprint=fingerprint)
    windows: list[AllocationWindow] = []
    issues: list[str] = []
    for n, row in enumerate(rows, start=2):
        try:
            windows.append(AllocationWindow(
                ref, row["venue_id"].strip(), _local(row["date"].strip(), row["start"].strip(), tz),
                _local(row["date"].strip(), row["end"].strip(), tz), WindowKind(row["kind"].strip().lower())))
        except (ValueError, KeyError) as exc:
            issues.append(f"row {n}: {exc}")
    if issues:
        raise IceTimeValidationError(issues)
    completeness = Completeness.COMPLETE if attested_complete else (
        Completeness.PARTIAL if windows else Completeness.UNKNOWN)
    return IceTimeSnapshot(ref, scope, completeness, tuple(windows))


def file_source(snapshot: IceTimeSnapshot) -> SnapshotSource:
    if snapshot.source.kind is not SourceKind.FILE:
        raise ValueError("file_source needs a file snapshot")
    return SnapshotSource(snapshot, FEATURES, "file-import")
