"""Local structured input: a JSON file or a CSV bundle, no workbook, no Git.

CSV bundle (a directory): ``settings.csv`` (``felt,verdi``), ``teams.csv``
(``club,label,age_group[,target_tournament_count]``) and optionally
``age_groups.csv``, ``sources.csv`` (``name,type,url``; no URL needed),
``allocations.csv`` (``venue_id,starts_at,ends_at,kind``) and ``bookings.csv``
(``tournament_id,venue_id,starts_at,ends_at,trust``). A JSON file carries the
same keys as the legacy raw config plus ``allocations``/``bookings`` lists.

Allocations and bookings are *operator-supplied* facts and need an accountable
``actor``. Absent files mean "nothing supplied", never free capacity.
"""

from __future__ import annotations

import csv
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

from .normalize import normalize_raw_config

_AGE_COLUMNS = ("parallel_games", "round_length_minutes", "rounds_per_tournament", "ice_time_minutes", "preferanse_vekt")
_TARGET_COLUMNS = (("deltakelser_per_lag_før_jul", "before_christmas"), ("deltakelser_per_lag_etter_jul", "after_christmas"))


def _scalar(value: str) -> Any:
    for cast in (int, float):
        try:
            return cast(value)
        except ValueError:
            pass
    return value


def _rows(path: Path, found: list[InputDiagnostic], *, required: bool) -> list[dict[str, str]]:
    if not path.is_file():
        if required:
            found.append(InputDiagnostic(Code.MISSING_INPUT, path.name, "required file not found"))
        return []
    try:
        with path.open(newline="", encoding="utf-8-sig") as handle:
            return [{k.strip(): (v or "").strip() for k, v in row.items() if k} for row in csv.DictReader(handle)]
    except (OSError, UnicodeDecodeError, csv.Error) as exc:
        found.append(InputDiagnostic(Code.UNREADABLE, path.name, str(exc)))
        return []


def _age_groups(rows: list[dict[str, str]]) -> dict[str, Any]:
    rows = [r for r in rows if r.get("age_group")]
    raw: dict[str, Any] = {"age_groups": [r["age_group"] for r in rows]}
    for column in _AGE_COLUMNS:
        values = {r["age_group"]: _scalar(r[column]) for r in rows if r.get(column)}
        if values:
            raw[column] = values
    targets: dict[str, dict[str, Any]] = {}
    for r in rows:
        for key, name in _TARGET_COLUMNS:
            if r.get(key):
                targets.setdefault(r["age_group"], {})[name] = _scalar(r[key])
    if targets:
        raw["participation_targets_by_age_group"] = targets
    return raw


def _team(row: dict[str, str]) -> dict[str, Any]:
    team: dict[str, Any] = {k: v for k, v in row.items() if v and k != "target_tournament_count"}
    if row.get("target_tournament_count"):
        team["target_tournament_count"] = _scalar(row["target_tournament_count"])
    return team


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

    def _load(self, found: list[InputDiagnostic]) -> tuple[dict[str, Any], list[Mapping], list[Mapping], bytes]:
        loc = self._location
        if loc.is_dir():
            settings = _rows(loc / "settings.csv", found, required=True)
            teams = _rows(loc / "teams.csv", found, required=True)
            raw: dict[str, Any] = {r["felt"]: _scalar(r["verdi"]) for r in settings if r.get("felt") and r.get("verdi")}
            raw.update(_age_groups(_rows(loc / "age_groups.csv", found, required=False)))
            if not raw["age_groups"]:
                del raw["age_groups"]
            raw["teams"] = [_team(t) for t in teams]
            sources = _rows(loc / "sources.csv", found, required=False)
            if sources:
                raw["sources"] = sources
            allocations = _rows(loc / "allocations.csv", found, required=False)
            bookings = _rows(loc / "bookings.csv", found, required=False)
            digest = b"".join(p.read_bytes() for p in sorted(loc.glob("*.csv")))
            return raw, allocations, bookings, digest
        if loc.is_file() and loc.suffix.lower() == ".json":
            try:
                data = json.loads(loc.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                found.append(InputDiagnostic(Code.UNREADABLE, loc.name, str(exc)))
                return {}, [], [], b""
            if not isinstance(data, dict):
                found.append(InputDiagnostic(Code.INVALID_VALUE, loc.name, "top level must be an object"))
                return {}, [], [], b""
            allocations, bookings = data.pop("allocations", []), data.pop("bookings", [])
            return data, allocations, bookings, loc.read_bytes()
        code = Code.MISSING_INPUT if not loc.exists() else Code.UNSUPPORTED_FORMAT
        found.append(InputDiagnostic(code, loc.name, "expected a directory of CSV files or a .json file"))
        return {}, [], [], b""

    def _instant(self, text: str, where: str, found: list[InputDiagnostic]) -> datetime | None:
        try:
            moment = datetime.fromisoformat(text)
        except ValueError:
            found.append(InputDiagnostic(Code.INVALID_VALUE, where, f"expected ISO date-time, got {text!r}"))
            return None
        return moment if moment.tzinfo else moment.replace(tzinfo=ZoneInfo(self._timezone))

    def _facts(
        self, ref: SourceRef | None, allocations: list[Mapping], bookings: list[Mapping], found: list[InputDiagnostic]
    ) -> tuple[tuple[AllocationWindow, ...], tuple[BookingAssertion, ...]]:
        if not (allocations or bookings):
            return (), ()
        if ref is None:
            found.append(InputDiagnostic(Code.MISSING_FIELD, "actor", "operator-supplied allocations/bookings need an accountable actor"))
            return (), ()
        windows: list[AllocationWindow] = []
        claims: list[BookingAssertion] = []
        for kind, rows in (("allocations", allocations), ("bookings", bookings)):
            for n, row in enumerate(rows, start=1):
                where = f"{self._location.name}:{kind}#{n}"
                start = self._instant(str(row.get("starts_at", "")), f"{where}.starts_at", found)
                end = self._instant(str(row.get("ends_at", "")), f"{where}.ends_at", found)
                if start is None or end is None:
                    continue
                try:
                    if kind == "allocations":
                        windows.append(AllocationWindow(ref, row["venue_id"], start, end, WindowKind(row.get("kind") or "available")))
                    else:
                        claims.append(BookingAssertion(ref, row["tournament_id"], row["venue_id"], start, end, AssertionTrust(row.get("trust") or "provisional")))
                except (KeyError, ValueError) as exc:
                    found.append(InputDiagnostic(Code.INVALID_VALUE, where, f"{type(exc).__name__}: {exc}"))
        return tuple(windows), tuple(claims)

    def read(self) -> NormalizedSeasonInput:
        found: list[InputDiagnostic] = []
        raw, allocations, bookings, digest = self._load(found)
        if not raw:
            raise SeasonInputError(tuple(found))
        name = self._location.name
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
