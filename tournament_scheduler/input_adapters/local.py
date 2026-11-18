"""Local structured input: a JSON file or a CSV bundle, no workbook, no Git.

CSV bundle (a directory): ``settings.csv`` (``felt,verdi``) and ``teams.csv``
(``club,label,age_group[,target_tournament_count]``) are required; optional:
``age_groups.csv``, ``sources.csv`` (``name,type,url``; no URL needed),
``allocations.csv`` (``venue_id,starts_at,ends_at,kind``) and ``bookings.csv``
(``tournament_id,venue_id,starts_at,ends_at,trust``). A JSON file carries the
same keys as the legacy raw config plus ``allocations``/``bookings`` lists of
objects. A missing optional file means "nothing supplied"; a present but
malformed one is a typed diagnostic with file, row and column.

Allocations and bookings are *operator-supplied* facts and need an accountable
``actor``. Nothing supplied is never free capacity.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping
from zoneinfo import ZoneInfo

from tournament_scheduler.application.ports.common import PortCapabilities, SourceKind, SourceRef
from tournament_scheduler.application.ports.ice_time import (
    AllocationWindow,
    AssertionTrust,
    BookingAssertion,
    WindowKind,
)
from tournament_scheduler.application.ports.season_input import (
    DiagnosticCode as Code,
    InputDiagnostic,
    NormalizedSeasonInput,
    SeasonInputError,
    season_input_capabilities,
)

from .csv_tables import read_table
from .normalize import normalize_raw_config

_AGE_COLUMNS = ("parallel_games", "round_length_minutes", "rounds_per_tournament", "ice_time_minutes", "preferanse_vekt")
_TARGET_COLUMNS = (("deltakelser_per_lag_før_jul", "before_christmas"), ("deltakelser_per_lag_etter_jul", "after_christmas"))
_FACT_COLUMNS = {
    "allocations": ("venue_id", "starts_at", "ends_at"),
    "bookings": ("tournament_id", "venue_id", "starts_at", "ends_at"),
}

Rows = list[tuple[int, Mapping[str, Any]]]


def _scalar(value: str) -> Any:
    for cast in (int, float):
        try:
            return cast(value)
        except ValueError:
            pass
    return value


def _settings(rows: Rows, name: str, found: list[InputDiagnostic]) -> dict[str, Any]:
    raw: dict[str, Any] = {}
    for n, row in rows:
        key, value = row["felt"], row["verdi"]
        if not key:
            found.append(InputDiagnostic(Code.MISSING_FIELD, f"{name}#{n}.felt", "setting name is blank"))
        elif key in raw:
            found.append(InputDiagnostic(Code.DUPLICATE, f"{name}#{n}.felt", f"setting {key!r} is repeated"))
        elif value:
            raw[key] = _scalar(value)
    return raw


def _age_groups(rows: Rows) -> dict[str, Any]:
    cells = [r for _, r in rows if r.get("age_group")]
    raw: dict[str, Any] = {"age_groups": [r["age_group"] for r in cells]}
    for column in _AGE_COLUMNS:
        values = {r["age_group"]: _scalar(r[column]) for r in cells if r.get(column)}
        if values:
            raw[column] = values
    targets: dict[str, dict[str, Any]] = {}
    for r in cells:
        for key, name in _TARGET_COLUMNS:
            if r.get(key):
                targets.setdefault(r["age_group"], {})[name] = _scalar(r[key])
    if targets:
        raw["participation_targets_by_age_group"] = targets
    return raw


def _team(row: Mapping[str, Any]) -> dict[str, Any]:
    team: dict[str, Any] = {k: v for k, v in row.items() if v and k != "target_tournament_count"}
    if row.get("target_tournament_count"):
        team["target_tournament_count"] = _scalar(row["target_tournament_count"])
    return team


def _json_rows(data: dict[str, Any], key: str, name: str, found: list[InputDiagnostic]) -> Rows:
    value = data.pop(key, [])
    if not isinstance(value, list):
        found.append(InputDiagnostic(Code.INVALID_VALUE, f"{name}:{key}", f"expected a list of objects, got {type(value).__name__}"))
        return []
    rows: Rows = []
    for n, item in enumerate(value, start=1):
        if isinstance(item, Mapping):
            rows.append((n, item))
        else:
            found.append(InputDiagnostic(Code.INVALID_VALUE, f"{name}:{key}#{n}", f"expected an object, got {type(item).__name__}"))
    return rows


class LocalBundleAdapter:
    adapter_id = "local-bundle"

    def __init__(
        self,
        location: str | Path,
        *,
        actor: str | None = None,
        timezone_name: str = "Europe/Oslo",
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self._location = Path(location)
        self._actor = actor
        self._timezone = timezone_name
        self._clock = clock

    def capabilities(self) -> PortCapabilities:
        return season_input_capabilities(self.adapter_id)

    def _load_csv(self, found: list[InputDiagnostic]) -> tuple[dict[str, Any], Rows, Rows, bytes]:
        loc = self._location
        settings = read_table(loc / "settings.csv", ("felt", "verdi"), found, optional_file=False)
        teams = read_table(loc / "teams.csv", ("club", "label", "age_group"), found, optional_file=False)
        ages = read_table(loc / "age_groups.csv", ("age_group",), found, optional_file=True)
        sources = read_table(loc / "sources.csv", ("name",), found, optional_file=True)
        facts = {
            key: read_table(loc / f"{key}.csv", cols, found, optional_file=True) or []
            for key, cols in _FACT_COLUMNS.items()
        }
        raw: dict[str, Any] = _settings(settings or [], "settings.csv", found)
        raw.update(_age_groups(ages or []))
        if not raw["age_groups"]:
            del raw["age_groups"]
        raw["teams"] = [_team(r) for _, r in teams or []]
        if sources:
            raw["sources"] = [dict(r) for _, r in sources]
        digest = b"".join(p.read_bytes() for p in sorted(loc.glob("*.csv")))
        return raw, facts["allocations"], facts["bookings"], digest

    def _load_json(self, found: list[InputDiagnostic]) -> tuple[dict[str, Any], Rows, Rows, bytes]:
        loc = self._location
        try:
            data = json.loads(loc.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            found.append(InputDiagnostic(Code.UNREADABLE, loc.name, str(exc)))
            return {}, [], [], b""
        if not isinstance(data, dict):
            found.append(InputDiagnostic(Code.INVALID_VALUE, loc.name, "top level must be an object"))
            return {}, [], [], b""
        allocations = _json_rows(data, "allocations", loc.name, found)
        bookings = _json_rows(data, "bookings", loc.name, found)
        return data, allocations, bookings, loc.read_bytes()

    def _instant(self, value: Any, where: str, found: list[InputDiagnostic]) -> datetime | None:
        try:
            moment = datetime.fromisoformat(str(value))
        except ValueError:
            found.append(InputDiagnostic(Code.INVALID_VALUE, where, f"expected ISO date-time, got {value!r}"))
            return None
        return moment if moment.tzinfo else moment.replace(tzinfo=ZoneInfo(self._timezone))

    def _facts(
        self, ref: SourceRef | None, allocations: Rows, bookings: Rows, found: list[InputDiagnostic]
    ) -> tuple[tuple[AllocationWindow, ...], tuple[BookingAssertion, ...]]:
        if not (allocations or bookings):
            return (), ()
        if ref is None:
            found.append(InputDiagnostic(Code.MISSING_FIELD, "actor", "operator-supplied allocations/bookings need an accountable actor"))
            return (), ()
        windows: list[AllocationWindow] = []
        claims: list[BookingAssertion] = []
        for kind, rows in (("allocations", allocations), ("bookings", bookings)):
            for n, row in rows:
                where = f"{self._location.name}:{kind}#{n}"
                absent = [c for c in _FACT_COLUMNS[kind] if not str(row.get(c) or "").strip()]
                if absent:
                    found.append(InputDiagnostic(Code.MISSING_FIELD, where, f"missing {', '.join(absent)}"))
                    continue
                start = self._instant(row["starts_at"], f"{where}.starts_at", found)
                end = self._instant(row["ends_at"], f"{where}.ends_at", found)
                if start is None or end is None:
                    continue
                try:
                    if kind == "allocations":
                        windows.append(AllocationWindow(ref, str(row["venue_id"]), start, end, WindowKind(row.get("kind") or "available")))
                    else:
                        claims.append(BookingAssertion(ref, str(row["tournament_id"]), str(row["venue_id"]), start, end, AssertionTrust(row.get("trust") or "provisional")))
                except ValueError as exc:
                    found.append(InputDiagnostic(Code.INVALID_VALUE, where, str(exc)))
        return tuple(windows), tuple(claims)

    def read(self) -> NormalizedSeasonInput:
        loc = self._location
        found: list[InputDiagnostic] = []
        if loc.is_dir():
            raw, allocations, bookings, digest = self._load_csv(found)
            if found:  # structural file problems: later diagnostics would only be consequences
                raise SeasonInputError(tuple(found))
        elif loc.is_file() and loc.suffix.lower() == ".json":
            raw, allocations, bookings, digest = self._load_json(found)
        else:
            code = Code.MISSING_INPUT if not loc.exists() else Code.UNSUPPORTED_FORMAT
            raise SeasonInputError((InputDiagnostic(code, loc.name, "expected a directory of CSV files or a .json file"),))
        name = loc.name
        now = self._clock()
        fingerprint = hashlib.sha256(digest).hexdigest()
        origin = SourceRef(f"{self.adapter_id}:{name}", SourceKind.FILE, now, self._timezone, document=name, fingerprint=fingerprint)
        manual = (
            SourceRef(f"{self.adapter_id}:{name}:operator", SourceKind.MANUAL, now, self._timezone, actor=self._actor, document=name, fingerprint=fingerprint)
            if self._actor
            else None
        )
        windows, claims = self._facts(manual, allocations, bookings, found)
        return normalize_raw_config(
            raw, origin=origin, document=name, allocations=windows, assertions=claims, extra_diagnostics=tuple(found)
        )
