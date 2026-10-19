"""Typed affected-team consequences for a canonical consequence preview.

The preview reads these views from dry-run results the operations already
compute. Batch dry-runs report booked reality per team; single-operation
dry-runs report the planned schedule. This module only reshapes those values
and owns no rule of its own.
"""

from __future__ import annotations

from typing import Any, Mapping

HALF_KEYS = ("before_christmas", "after_christmas", "unsplit")

# Consequences of a preview that never reached a dry-run (stale or refused).
NO_CONSEQUENCES: dict[str, Any] = {
    "basis": None,
    "provisional": None,
    "affected_teams": [],
    "material_regressions": [],
}


def build_consequences(operation: str, details: Mapping[str, Any]) -> dict[str, Any]:
    """Typed view of the affected-team consequences the dry-run already computed.

    Batch previews report booked reality, so their rows carry booked and
    unconfirmed participations and the provisional flag. Single-operation
    previews report the planned schedule only; booked evidence is not
    evaluated for them, so ``provisional`` stays unset rather than claiming
    completeness. Material regressions are passed through per team role, so an
    aggregate improvement can never hide a regression on another team.
    """

    if operation == "batch":
        fairness = dict(details.get("booked_fairness") or {})
        review = dict((details.get("verdict") or {}).get("review") or {})
        # The batch review lists the complete material set, independent of any
        # explicit acceptance, so an accepted regression stays visible here.
        return {
            "basis": "booked_reality",
            "provisional": bool(fairness.get("provisional", True)),
            "affected_teams": [_booked_team_row(row) for row in fairness.get("impacted_teams") or []],
            "material_regressions": [dict(item) for item in review.get("consequences") or []],
        }

    regressions = [
        {"role": role, **dict(item)}
        for role, analysis in (details.get("team_consequences") or {}).items()
        for item in (analysis or {}).get("material_regressions") or []
    ]
    return {
        "basis": "planned_schedule",
        "provisional": None,
        "affected_teams": [
            _planned_team_row(entry)
            for entry in (details.get("participation_counts") or {}).values()
        ],
        "material_regressions": regressions,
    }


def _booked_team_row(row: Mapping[str, Any]) -> dict[str, Any]:
    before = {"booked": int(row.get("before_booked", 0)), **_halves(row.get("before_booked_halves"))}
    after = {"booked": int(row.get("after_booked", 0)), **_halves(row.get("after_booked_halves"))}
    return {
        "club": row.get("club"),
        "team": row.get("team"),
        "age_group": row.get("age_group"),
        "before": before,
        "after": after,
        "delta": {key: after[key] - before[key] for key in before},
        "after_unconfirmed": int(row.get("after_unconfirmed", 0)),
        "provisional": bool(row.get("before_provisional", False) or row.get("after_provisional", False)),
    }


def _planned_team_row(entry: Mapping[str, Any]) -> dict[str, Any]:
    identity = dict(entry.get("identity") or {})
    before = _planned_counts(entry.get("before"))
    after = _planned_counts(entry.get("after"))
    return {
        "club": identity.get("club"),
        "team": identity.get("label"),
        "age_group": identity.get("age_group"),
        "before": before,
        "after": after,
        "delta": {key: after[key] - before[key] for key in before},
    }


def _planned_counts(counts: Any) -> dict[str, int]:
    counts = dict(counts or {})
    return {"planned": int(counts.get("season", 0)), **_halves(counts)}


def _halves(values: Any) -> dict[str, int]:
    values = dict(values or {})
    return {half: int(values.get(half, 0)) for half in HALF_KEYS}
