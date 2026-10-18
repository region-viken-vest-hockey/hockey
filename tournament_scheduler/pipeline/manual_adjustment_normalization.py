"""Normalization of persisted manual-adjustment settings.

Converts the raw ``manual_adjustments`` mapping on a season plan into typed
sets and lists, and back into its JSON-safe form.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any


@dataclass
class NormalizedAdjustments:
    locked_dates: set[date] = field(default_factory=set)
    banned_dates: set[date] = field(default_factory=set)
    forced_host_clubs: list[str] = field(default_factory=list)
    excluded_host_clubs: set[str] = field(default_factory=set)
    pinned_tournament_ids: set[str] = field(default_factory=set)


def normalize_adjustments(raw: dict[str, Any]) -> NormalizedAdjustments:
    def _parse_dates(values: Any) -> set[date]:
        result: set[date] = set()
        for value in values or []:
            if isinstance(value, date):
                result.add(value)
                continue
            if isinstance(value, str):
                try:
                    result.add(date.fromisoformat(value))
                except ValueError:
                    continue
        return result

    forced_hosts = [str(v) for v in (raw.get("forced_host_clubs") or []) if str(v).strip()]
    excluded_hosts = {str(v) for v in (raw.get("excluded_host_clubs") or []) if str(v).strip()}

    return NormalizedAdjustments(
        locked_dates=_parse_dates(raw.get("locked_dates")),
        banned_dates=_parse_dates(raw.get("banned_dates")),
        forced_host_clubs=forced_hosts,
        excluded_host_clubs=excluded_hosts,
        pinned_tournament_ids={str(v) for v in (raw.get("pinned_tournament_ids") or []) if str(v).strip()},
    )


def serialise_adjustments(adjustments: NormalizedAdjustments) -> dict[str, list[str]]:
    return {
        "locked_dates": sorted(d.isoformat() for d in adjustments.locked_dates),
        "banned_dates": sorted(d.isoformat() for d in adjustments.banned_dates),
        "forced_host_clubs": list(adjustments.forced_host_clubs),
        "excluded_host_clubs": sorted(adjustments.excluded_host_clubs),
        "pinned_tournament_ids": sorted(adjustments.pinned_tournament_ids),
    }
