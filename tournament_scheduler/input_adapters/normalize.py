"""Shared raw-config -> :class:`NormalizedSeasonInput` normalizer.

``raw`` is the legacy workbook config shape (``start_date``, ``teams``,
``parallel_games`` ...). Every adapter produces that shape from its own format
and calls :func:`normalize_raw_config`, so validation and semantics live in one
place. All problems are collected and raised together; nothing partial escapes.
Default/alias normalization of club and source names stays with the club
registry (#337); this module only checks structure and references.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Mapping

from tournament_scheduler.application.ports.common import SourceRef
from tournament_scheduler.application.ports.ice_time import AllocationWindow, BookingAssertion
from tournament_scheduler.application.ports.season_input import (
    DiagnosticCode as Code,
    InputDiagnostic,
    NormalizedSeasonInput,
    SeasonInputError,
    SourceDeclaration,
    TeamInput,
)

AGE_GROUP_PARAMS = (
    "parallel_games",
    "round_length_minutes",
    "rounds_per_tournament",
    "ice_time_minutes",
    "preferanse_vekt",
    "participation_targets_by_age_group",
)
_TYPED_KEYS = {"start_date", "end_date", "age_groups", "teams", "sources", *AGE_GROUP_PARAMS}


def _parse_day(value: Any, where: str, found: list[InputDiagnostic]) -> str | None:
    if isinstance(value, date):
        return value.isoformat()
    try:
        return date.fromisoformat(str(value)).isoformat()
    except ValueError:
        found.append(InputDiagnostic(Code.INVALID_VALUE, where, f"expected YYYY-MM-DD, got {value!r}"))
        return None


def _teams(raw: Any, age_groups: tuple[str, ...], doc: str, found: list[InputDiagnostic]) -> tuple[TeamInput, ...]:
    where = f"{doc}:teams"
    if not isinstance(raw, (list, tuple)) or not raw:
        found.append(InputDiagnostic(Code.MISSING_FIELD, where, "at least one team is required"))
        return ()
    teams: list[TeamInput] = []
    seen: dict[tuple[str, str], int] = {}
    for n, row in enumerate(raw, start=1):
        loc = f"{where}#{n}"
        missing = [k for k in ("club", "label", "age_group") if not (isinstance(row, Mapping) and row.get(k))]
        if missing:
            found.append(InputDiagnostic(Code.MISSING_FIELD, loc, f"missing {', '.join(missing)}"))
            continue
        club, label, group = (str(row[k]).strip() for k in ("club", "label", "age_group"))
        if age_groups and group not in age_groups:
            found.append(InputDiagnostic(Code.UNKNOWN_REFERENCE, f"{loc}.age_group", f"age group {group!r} is not declared"))
        if (label, group) in seen:
            found.append(InputDiagnostic(Code.DUPLICATE, loc, f"label {label!r} repeats #{seen[(label, group)]} in {group}"))
        seen.setdefault((label, group), n)
        target = row.get("target_tournament_count")
        if target is not None and (isinstance(target, bool) or not isinstance(target, int) or target < 0):
            found.append(InputDiagnostic(Code.INVALID_VALUE, f"{loc}.target_tournament_count", f"expected a non-negative integer, got {target!r}"))
            continue
        teams.append(TeamInput(club, label, group, target))
    return tuple(teams)


def _sources(raw: Any, doc: str, found: list[InputDiagnostic]) -> tuple[SourceDeclaration, ...]:
    declared: dict[str, SourceDeclaration] = {}
    for n, row in enumerate(raw or (), start=1):
        loc = f"{doc}:sources#{n}"
        name = str(row.get("name") or "").strip()
        if not name:
            found.append(InputDiagnostic(Code.MISSING_FIELD, loc, "a source needs a name"))
            continue
        decl = SourceDeclaration(name, row.get("type") or None, row.get("url") or None)
        if name in declared and declared[name] != decl:
            found.append(InputDiagnostic(Code.AMBIGUOUS, loc, f"source {name!r} is declared twice with different definitions"))
        declared.setdefault(name, decl)
    return tuple(declared.values())


def normalize_raw_config(
    raw: Mapping[str, Any],
    *,
    origin: SourceRef,
    document: str,
    allocations: tuple[AllocationWindow, ...] = (),
    assertions: tuple[BookingAssertion, ...] = (),
    extra_diagnostics: tuple[InputDiagnostic, ...] = (),
) -> NormalizedSeasonInput:
    """Validate *raw* and return the normalized input, or raise :class:`SeasonInputError`."""
    found: list[InputDiagnostic] = list(extra_diagnostics)
    days: dict[str, str | None] = {}
    for key in ("start_date", "end_date"):
        if raw.get(key) in (None, ""):
            found.append(InputDiagnostic(Code.MISSING_FIELD, f"{document}:{key}", "required (YYYY-MM-DD)"))
        else:
            days[key] = _parse_day(raw[key], f"{document}:{key}", found)
    start, end = days.get("start_date"), days.get("end_date")
    if start and end and start > end:
        found.append(InputDiagnostic(Code.INVALID_VALUE, f"{document}:end_date", "season ends before it starts"))
    for legacy in ("deltakelser_per_lag", "target_tournament_count"):
        if legacy in raw:
            found.append(InputDiagnostic(Code.INVALID_VALUE, f"{document}:{legacy}", "global participation target is no longer supported; use per-age-group targets"))
    groups = tuple(dict.fromkeys(str(g) for g in raw.get("age_groups") or ()))
    teams = _teams(raw.get("teams"), groups, document, found)
    for param in AGE_GROUP_PARAMS:
        for group in raw.get(param) or {}:
            if groups and group not in groups:
                found.append(InputDiagnostic(Code.UNKNOWN_REFERENCE, f"{document}:{param}", f"age group {group!r} is not declared"))
    sources = _sources(raw.get("sources"), document, found)
    if found:
        raise SeasonInputError(tuple(found))
    return NormalizedSeasonInput(
        start_date=start or "",
        end_date=end or "",
        age_groups=groups,
        teams=teams,
        origin=origin,
        age_group_params={p: dict(raw[p]) for p in AGE_GROUP_PARAMS if raw.get(p)},
        rules={k: v for k, v in raw.items() if k not in _TYPED_KEYS},
        sources=sources,
        operator_allocations=allocations,
        operator_assertions=assertions,
    )


def to_legacy_config(season: NormalizedSeasonInput) -> dict[str, Any]:
    """The legacy Stage 1 raw-config shape, for consumers not yet on the DTO."""
    raw: dict[str, Any] = {"start_date": season.start_date, "end_date": season.end_date}
    if season.age_groups:
        raw["age_groups"] = list(season.age_groups)
    raw.update({k: dict(v) for k, v in season.age_group_params.items()})
    raw["teams"] = [
        {"club": t.club, "label": t.label, "age_group": t.age_group,
         **({} if t.target_tournament_count is None else {"target_tournament_count": t.target_tournament_count})}
        for t in season.teams
    ]
    if season.sources:
        raw["sources"] = [
            {"name": s.name, **({"type": s.source_type} if s.source_type else {}), **({"url": s.url} if s.url else {})}
            for s in season.sources
        ]
    raw.update(season.rules)
    return raw
