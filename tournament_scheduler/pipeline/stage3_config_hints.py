"""Stage 3 planner-config hints.

Pure readers of Stage 1 config for planning critic hints and shared-host
decisions. Kept out of ``stage3_planning`` so the orchestration module stays
focused on the planning run.
"""

from __future__ import annotations

from typing import Any


def _extract_planning_critic_hints(config: dict[str, Any]) -> tuple[dict[str, Any] | None, dict[str, float] | None]:
    """Return structured planning critic metadata and flat planner penalties.

    ``penalty_hints`` remains backwards compatible as a flat mapping.  New
    pre-export critic loops may instead pass ``planning_critic_hints`` with a
    nested ``penalty_hints`` mapping plus metadata such as source, tone, and
    issues; Stage 3 persists that metadata while sending only the numeric
    penalties into ``SeasonPlanner``.
    """
    structured = config.get("planning_critic_hints")
    raw_penalties: Any = config.get("penalty_hints")

    if structured is None and isinstance(raw_penalties, dict) and "penalty_hints" in raw_penalties:
        structured = raw_penalties

    if isinstance(structured, dict):
        nested = structured.get("penalty_hints")
        if isinstance(nested, dict):
            raw_penalties = nested

    if not isinstance(raw_penalties, dict):
        return structured if isinstance(structured, dict) else None, None

    penalties: dict[str, float] = {}
    for key, value in raw_penalties.items():
        try:
            penalties[str(key)] = float(value)
        except (TypeError, ValueError):
            continue

    return structured if isinstance(structured, dict) else None, penalties or None


def _shared_host_choices_from_config(config: dict[str, Any]) -> dict[tuple[str, str], str]:
    """Convert ``config["shared_host_decisions"]`` (a JSON-safe list, since
    config must round-trip through checkpoints) into the
    ``{(registration, age_group): chosen_club}`` shape
    :class:`~..season_planner.SeasonPlanner` expects."""
    choices: dict[tuple[str, str], str] = {}
    for entry in config.get("shared_host_decisions") or []:
        if not isinstance(entry, dict):
            continue
        registration = str(entry.get("registration", ""))
        age_group = str(entry.get("age_group", ""))
        chosen_club = entry.get("chosen_club")
        if registration and age_group and chosen_club:
            choices[(registration, age_group)] = str(chosen_club)
    return choices
