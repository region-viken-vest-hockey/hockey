"""Headline summary for unresolved participation shortfalls.

Compact operator-facing status for the participation-shortfall rule. The exact
per-team rows are not changed here; they stay in the rule's detail rows.
"""

from __future__ import annotations

from typing import Any


# Above this many genuine per-team shortfalls the headline groups them by
# club and age group instead of naming each team. The exact per-team rows stay
# in the rule's detail rows for audit.
LISTED_SHORTFALL_LIMIT = 5


def participation_shortfall_summary(genuine: list[dict[str, Any]]) -> str:
    if len(genuine) <= LISTED_SHORTFALL_LIMIT:
        def _label(item: dict[str, Any]) -> str:
            half = item.get("period")
            half_suffix = f", {half}" if half else ""
            return f"{item.get('label', '?')} ({item.get('actual', '?')}/{item.get('target', '?')}{half_suffix})"

        return f"{len(genuine)} avvik: " + ", ".join(_label(item) for item in genuine)

    groups: dict[tuple[str, str], int] = {}
    for item in genuine:
        key = (str(item.get("club") or "?"), str(item.get("age_group") or "?"))
        groups[key] = groups.get(key, 0) + 1
    ordered = sorted(groups.items(), key=lambda entry: (-entry[1], entry[0]))
    listed = ", ".join(f"{club} {age_group} ({count} lag)" for (club, age_group), count in ordered[:LISTED_SHORTFALL_LIMIT])
    more = f" og {len(ordered) - LISTED_SHORTFALL_LIMIT} flere" if len(ordered) > LISTED_SHORTFALL_LIMIT else ""
    return (
        f"{len(genuine)} avvik fordelt på {len(groups)} klubb/aldersgruppe-kombinasjoner: "
        f"{listed}{more}. Detaljer per lag vises under."
    )
