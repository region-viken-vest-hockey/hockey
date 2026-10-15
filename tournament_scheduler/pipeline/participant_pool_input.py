"""Controlled-workbook parsing and validation for rotatable participant pools."""

from __future__ import annotations

from typing import Any, Callable


def read_participant_pools(
    workbook: Any, read_table: Callable[..., list[dict[str, Any]]]
) -> dict[str, list[dict[str, Any]]]:
    """Normalize the optional one-row-per-label workbook declarations."""

    if "Deltakerpooler" not in workbook.sheetnames:
        return {}
    rows = read_table(
        workbook["Deltakerpooler"],
        required_columns=("club", "age_group", "pool_id", "team_label"),
    )
    grouped: dict[tuple[str, str, str], set[str]] = {}
    for row in rows:
        key = (
            str(row.get("club") or "").strip(),
            str(row.get("age_group") or "").strip(),
            str(row.get("pool_id") or "").strip(),
        )
        label = str(row.get("team_label") or "").strip()
        if all(key) and label:
            grouped.setdefault(key, set()).add(label)
    pools = [
        {"club": club, "age_group": age_group, "pool_id": pool_id, "team_labels": sorted(labels)}
        for (club, age_group, pool_id), labels in sorted(grouped.items())
    ]
    return {"rotatable_participant_pools": pools} if pools else {}


def participant_pool_config_errors(raw: dict[str, Any]) -> list[str]:
    """Validate explicit pools against the canonical registered-team identities."""

    pools = raw.get("rotatable_participant_pools")
    if pools is None:
        return []
    if not isinstance(pools, list):
        return ["'rotatable_participant_pools' må være en liste."]
    registered = {
        (
            str(team.get("club") or ""),
            str(team.get("label") or ""),
            str(team.get("age_group") or ""),
        )
        for team in (raw.get("teams") or [])
        if isinstance(team, dict)
    }
    claimed: set[tuple[str, str, str]] = set()
    errors: list[str] = []
    for index, pool in enumerate(pools, start=1):
        prefix = f"Deltakerpool #{index}"
        if not isinstance(pool, dict):
            errors.append(f"{prefix}: forventet et objekt.")
            continue
        club = str(pool.get("club") or "").strip()
        age_group = str(pool.get("age_group") or "").strip()
        pool_id = str(pool.get("pool_id") or "").strip()
        labels = pool.get("team_labels")
        if not club or not age_group or not pool_id:
            errors.append(f"{prefix}: 'club', 'age_group' og 'pool_id' må være utfylt.")
            continue
        if not isinstance(labels, list) or len(set(labels)) < 2:
            errors.append(f"{prefix}: minst to unike 'team_label'-verdier er påkrevd.")
            continue
        for raw_label in labels:
            identity = (club, str(raw_label or "").strip(), age_group)
            if identity not in registered:
                errors.append(
                    f"{prefix}: laget '{identity[1]}' finnes ikke i 'Lag' for {club} {age_group}."
                )
            if identity in claimed:
                errors.append(
                    f"{prefix}: laget '{identity[1]}' er allerede medlem av en annen deltakerpool."
                )
            claimed.add(identity)
    return errors


__all__ = ["read_participant_pools", "participant_pool_config_errors"]
