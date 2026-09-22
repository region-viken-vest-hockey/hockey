"""Schedule-preservation guard for canonical season exports.

A promoted season export is a projection of canonical state.  This module keeps
that invariant independent from renderers and export formats by comparing the
stable tournament-id projection before Stage 4 writes artifacts.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class ExportProjectionError(ValueError):
    """Raised when an export would change canonical schedule semantics."""

    report: dict[str, Any]

    def __str__(self) -> str:
        summary = self.report.get("summary") or "season export would change canonical schedule"
        return str(summary)


def _participant_key(team: Mapping[str, Any]) -> str:
    return "\u001f".join(
        str(team.get(field) or "") for field in ("club", "label", "age_group")
    )


def tournament_projection(plan: Mapping[str, Any] | None) -> dict[str, dict[str, Any]]:
    """Return the stable-id schedule projection relevant for export safety."""

    projection: dict[str, dict[str, Any]] = {}
    for tournament in (plan or {}).get("tournaments", []) or []:
        if not isinstance(tournament, Mapping):
            continue
        tournament_id = str(tournament.get("id") or "")
        if not tournament_id:
            continue
        teams = tournament.get("teams") or []
        projection[tournament_id] = {
            "id": tournament_id,
            "date": str(tournament.get("date") or ""),
            "start_time": str(tournament.get("start_time") or ""),
            "arena": str(tournament.get("arena") or ""),
            "host_club": str(tournament.get("host_club") or ""),
            "age_group": str(tournament.get("age_group") or ""),
            "participants": sorted(
                _participant_key(team)
                for team in teams
                if isinstance(team, Mapping)
            ),
        }
    return projection


def diff_tournament_projection(
    before: Mapping[str, Mapping[str, Any]],
    after: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    before_ids = set(before)
    after_ids = set(after)
    removed = sorted(before_ids - after_ids)
    added = sorted(after_ids - before_ids)
    placement_changes: list[dict[str, Any]] = []
    participant_changes: list[dict[str, Any]] = []
    placement_fields = ("date", "start_time", "arena", "host_club")
    for tournament_id in sorted(before_ids & after_ids):
        old = before[tournament_id]
        new = after[tournament_id]
        changed_fields = {
            field: {"before": old.get(field), "after": new.get(field)}
            for field in placement_fields
            if old.get(field) != new.get(field)
        }
        if changed_fields:
            placement_changes.append({"tournament_id": tournament_id, "fields": changed_fields})
        if list(old.get("participants") or []) != list(new.get("participants") or []):
            participant_changes.append(
                {
                    "tournament_id": tournament_id,
                    "before": list(old.get("participants") or []),
                    "after": list(new.get("participants") or []),
                }
            )
    return {
        "removed_tournament_ids": removed,
        "added_tournament_ids": added,
        "placement_changes": placement_changes,
        "participant_changes": participant_changes,
        "changed": bool(removed or added or placement_changes or participant_changes),
    }


def assert_export_preserves_canonical_plan(
    *,
    canonical_plan: Mapping[str, Any],
    proposed_plan: Mapping[str, Any],
    season: str | None = None,
    canonical_revision: str | None = None,
    published_export: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Raise if a proposed export is not an exact projection of canonical state."""

    canonical = tournament_projection(canonical_plan)
    proposed = tournament_projection(proposed_plan)
    canonical_delta = diff_tournament_projection(canonical, proposed)
    published_projection = (published_export or {}).get("schedule_projection")
    published_delta: dict[str, Any] | None = None
    if isinstance(published_projection, Mapping):
        published_delta = diff_tournament_projection(published_projection, canonical)
    report: dict[str, Any] = {
        "season": season,
        "canonical_revision": canonical_revision,
        "canonical_tournament_count": len(canonical),
        "proposed_tournament_count": len(proposed),
        "canonical_delta": canonical_delta,
        "published_export": {
            key: (published_export or {}).get(key)
            for key in ("export_id", "export_fingerprint", "canonical_revision", "lifecycle_status")
            if (published_export or {}).get(key) is not None
        },
        "published_to_canonical_delta": published_delta,
    }
    if canonical_delta["changed"]:
        report["summary"] = (
            "Refusing season export: proposed artifacts would remove, move, "
            "or change participants relative to the canonical schedule. "
            "Resolve conflicts through an explicit canonical season operation; "
            "export never repairs a published schedule."
        )
        raise ExportProjectionError(report)
    report["summary"] = "export projection preserves canonical schedule"
    return report


__all__ = [
    "ExportProjectionError",
    "assert_export_preserves_canonical_plan",
    "diff_tournament_projection",
    "tournament_projection",
]
