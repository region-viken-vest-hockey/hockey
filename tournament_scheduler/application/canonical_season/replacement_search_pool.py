"""Pool-aware spacing evidence projection for replacement candidate search."""

from __future__ import annotations

from typing import Any, Mapping


def effective_spacing_delta(
    consequence: Mapping[str, Any],
    exact_delta: Mapping[str, int],
) -> tuple[dict[str, int], Mapping[str, Any] | None]:
    """Return the gate-aligned spacing delta and optional pool consequence."""

    pool = consequence.get("participant_pool")
    if not isinstance(pool, Mapping):
        return dict(exact_delta), None
    before = (pool.get("before") or {}).get("spacing") or {}
    after = (pool.get("after") or {}).get("spacing") or {}
    return (
        {
            "back_to_back_weekends": int(
                after.get("unavoidable_consecutive_weekends", 0) or 0
            )
            - int(before.get("unavoidable_consecutive_weekends", 0) or 0),
            "gaps_under_7_days": int(after.get("unavoidable_gaps_under_7", 0) or 0)
            - int(before.get("unavoidable_gaps_under_7", 0) or 0),
            "gaps_under_14_days": int(after.get("unavoidable_gaps_under_14", 0) or 0)
            - int(before.get("unavoidable_gaps_under_14", 0) or 0),
        },
        pool,
    )


__all__ = ["effective_spacing_delta"]
