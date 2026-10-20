"""Typed affected-team consequences for a canonical consequence preview.

The preview reads these views from dry-run results the operations already
compute. Batch dry-runs report booked reality per team; single-operation
dry-runs report the planned schedule. This module only reshapes those values
and owns no rule of its own.
"""

from __future__ import annotations

from typing import Any, Mapping

HALF_KEYS = ("before_christmas", "after_christmas", "unsplit")
FINDING_BUCKETS = (
    "resolved",
    "unchanged_accepted",
    "unchanged_unaccepted",
    "introduced",
    "worsened",
    "materially_modified",
    "improved",
)

# Consequences of a preview that never reached a dry-run (stale or refused).
NO_CONSEQUENCES: dict[str, Any] = {
    "basis": None,
    "provisional": None,
    "affected_teams": [],
    "material_regressions": [],
}



def unclassified_findings() -> dict[str, Any]:
    """Findings of a preview with no baseline-classified dry-run behind it."""

    return {
        "basis": None,
        "classified": False,
        # None means unset: no classifier verdict exists, which is not the same
        # as an empty blocking list.
        "acceptable": None,
        "blocking": None,
        "counts": {bucket: 0 for bucket in FINDING_BUCKETS},
        "buckets": {bucket: [] for bucket in FINDING_BUCKETS},
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

    if operation == "repair":
        # A repair dry-run measures the whole season through the adoption guard,
        # not per team, so it reports season-wide material regressions only.
        adoption = details.get("adoption") or {}
        return {
            "basis": "planned_schedule",
            "provisional": None,
            "affected_teams": [],
            "material_regressions": [
                {"role": "season", **dict(item)}
                for item in adoption.get("material_regressions") or []
            ],
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


def build_findings(details: Mapping[str, Any]) -> dict[str, Any]:
    """Hard-finding buckets classified against the pre-operation baseline.

    Batch and single-operation dry-runs both verify the candidate against the
    pre-operation baseline through the shared gate, so each carries the same
    ``violation_classification`` its writer admits on. ``classified`` stays
    false only when no baseline comparison ran (for example a refused or stale
    preview), so no caller infers a comparison that never happened.
    """

    classification = (details.get("verification_result") or {}).get("violation_classification")
    if not isinstance(classification, Mapping):
        return unclassified_findings()
    buckets = {bucket: [dict(item) for item in classification.get(bucket) or []] for bucket in FINDING_BUCKETS}
    return {
        "basis": "baseline_verification",
        "classified": True,
        # The classifier's own admission verdict and blocking list, passed
        # through so callers never infer policy from bucket names.
        "acceptable": bool(classification["acceptable"]),
        "blocking": [dict(item) for item in classification.get("blocking") or []],
        "counts": {bucket: len(entries) for bucket, entries in buckets.items()},
        "buckets": buckets,
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
