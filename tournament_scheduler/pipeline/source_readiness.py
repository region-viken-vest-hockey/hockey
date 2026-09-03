"""Canonical Stage 2 source-readiness policy.

Harness adapters must not duplicate this policy.  Stage 2 may preserve unresolved
calendar sources while still deciding that the available data is sufficient to
continue planning.
"""

from __future__ import annotations

from typing import Any

from ..club_registry import club_for_source_name


# Temporary operational exception: these BookUp calendars are real/expected
# sources, but their authenticated views are not always available from Lima.
# They remain unresolved and should be recovered; they are not manual calendars.
TEMPORARILY_NON_BLOCKING_UNRESOLVED_CLUBS = frozenset({
    "Tønsberg",
    "Sandefjord Penguins",
})


def _canonical_club_name(source: dict[str, Any]) -> str:
    name = str(source.get("name", "")).strip()
    return club_for_source_name(name) or name


def classify_unresolved_sources(
    unresolved: list[dict[str, Any]],
    *,
    allow_missing_sources: bool = False,
) -> dict[str, list[dict[str, Any]] | bool]:
    """Classify unresolved Stage 2 sources into temporary and blocking sets.

    ``allow_missing_sources`` remains the explicit broad operator override.  In
    its absence only the two temporary BookUp exceptions are non-blocking; every
    other unexpected unresolved source blocks Stage 3.
    """
    temporary: list[dict[str, Any]] = []
    blocking: list[dict[str, Any]] = []

    for source in unresolved:
        if _canonical_club_name(source) in TEMPORARILY_NON_BLOCKING_UNRESOLVED_CLUBS:
            temporary.append(source)
        else:
            blocking.append(source)

    operator_allowed = list(unresolved) if allow_missing_sources else []
    effective_blocking = [] if allow_missing_sources else blocking

    return {
        "unresolved": list(unresolved),
        "temporary": temporary,
        "blocking": effective_blocking,
        "operator_allowed": operator_allowed,
        "planning_ready": len(effective_blocking) == 0,
    }


def source_names(sources: list[dict[str, Any]]) -> list[str]:
    """Return stable source names for checkpoint metadata."""
    return [str(source.get("name", "?")) for source in sources]
