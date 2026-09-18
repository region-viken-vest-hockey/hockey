"""Canonical identity for unresolved tournament-placement planning findings.

A tournament the deterministic slot search could not place is planning work,
not schedule state: it has no verified/provisional concrete placement, so it
must never be materialized as a normal ``Tournament`` and must never be
counted as hosting, participation or played games. The planner records one
structured finding per failed placement in
``SeasonPlan.unresolved_tournament_placements``.

This module owns the stable finding identity every consumer (the manual-work
projection, promoted-season findings, audit evidence, a future materializing
repair) refers to, so no layer has to invent a tournament id for an obligation
that has no tournament.
"""

from __future__ import annotations

# Category shared by the planner finding and the manual-schedule projection.
UNPLACED_PLACEMENT_CATEGORY = "manual_tournament_placement"
UNPLACED_PLACEMENT_PREFIX = "unplaced_placement"


def unplaced_placement_finding_id(age_group: str, date_iso: str, sequence: int) -> str:
    """Stable finding identity independent of any tournament id.

    ``sequence`` disambiguates parallel same-age-group/same-date obligations
    the date skeleton requested independently; it is assigned in the
    planner's deterministic schedule order, so the same planning request
    yields the same id across runs.
    """
    return f"{UNPLACED_PLACEMENT_PREFIX}:{age_group}:{date_iso}:{int(sequence)}"
