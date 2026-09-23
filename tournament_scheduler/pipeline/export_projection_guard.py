"""Schedule-preservation guard for canonical season exports.

A promoted season export is a projection of canonical state.  This module keeps
that invariant independent from renderers and export formats by comparing the
stable tournament-id projection before Stage 4 writes artifacts.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
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


_TOURNAMENTS_RE = re.compile(r"^\s*const\s+TOURNAMENTS\s*=\s*(\[.*\]);\s*$", re.MULTILINE)


def projection_from_export_artifacts(
    export_dir: str | Path,
    *,
    canonical_plan: Mapping[str, Any] | None = None,
) -> dict[str, dict[str, Any]]:
    """Reconstruct a stable-id projection from legacy published artifacts.

    Manifests written before the publication guard did not carry
    ``schedule_projection``.  Prefer the embedded HTML tournament payload when
    present.  The committed 2026-09-21 publication did not track that HTML, but
    it does track the Spond season workbook; for that legacy shape, recover the
    published placement/participants from the workbook and bind them to the
    current canonical stable ids by exact match first, then remaining order for
    rows whose published placement has since changed.  Refuse partial
    reconstruction: publication safety must not silently compare against an
    incomplete baseline.
    """

    html_path = Path(export_dir) / "season_plan.html"
    try:
        text = html_path.read_text(encoding="utf-8")
    except OSError:
        return _projection_from_spond_workbook(export_dir, canonical_plan=canonical_plan)
    match = _TOURNAMENTS_RE.search(text)
    if not match:
        raise ExportProjectionError(
            {
                "summary": (
                    "Refusing season export: published export has no schedule_projection "
                    f"and {html_path} does not contain embedded TOURNAMENTS data"
                ),
                "export_dir": str(export_dir),
            }
        )
    try:
        rendered = json.loads(match.group(1))
    except json.JSONDecodeError as exc:
        raise ExportProjectionError(
            {
                "summary": (
                    "Refusing season export: published export has no schedule_projection "
                    f"and {html_path} contains invalid embedded TOURNAMENTS JSON"
                ),
                "export_dir": str(export_dir),
            }
        ) from exc
    if not isinstance(rendered, list):
        raise ExportProjectionError(
            {
                "summary": "Refusing season export: legacy TOURNAMENTS payload is not a list",
                "export_dir": str(export_dir),
            }
        )
    projection: dict[str, dict[str, Any]] = {}
    for item in rendered:
        if not isinstance(item, Mapping):
            continue
        tournament_id = str(item.get("id") or "")
        if not tournament_id:
            continue
        participants = item.get("p") or []
        if not isinstance(participants, list):
            participants = []
        projection[tournament_id] = {
            "id": tournament_id,
            "date": str(item.get("d") or ""),
            "start_time": str(item.get("ts") or ""),
            "arena": str(item.get("a") or ""),
            "host_club": str(item.get("h") or ""),
            "age_group": str(item.get("g") or ""),
            "participants": sorted(
                _participant_key(
                    {"club": team.get("c"), "label": team.get("l"), "age_group": team.get("g")}
                )
                for team in participants
                if isinstance(team, Mapping)
            ),
        }
    if not projection:
        raise ExportProjectionError(
            {
                "summary": "Refusing season export: legacy projection reconstruction produced no tournaments",
                "export_dir": str(export_dir),
            }
        )
    return projection


def _projection_from_spond_workbook(
    export_dir: str | Path,
    *,
    canonical_plan: Mapping[str, Any] | None,
) -> dict[str, dict[str, Any]]:
    workbook_path = Path(export_dir) / "season_plan_spond.xlsx"
    if not canonical_plan:
        raise ExportProjectionError(
            {
                "summary": (
                    "Refusing season export: published export has no schedule_projection, "
                    "HTML fallback is unavailable and canonical ids are needed to bind "
                    "the legacy Spond workbook projection"
                ),
                "export_dir": str(export_dir),
            }
        )
    try:
        from openpyxl import load_workbook
    except ImportError as exc:  # pragma: no cover - dependency is installed in normal runtime
        raise ExportProjectionError(
            {
                "summary": (
                    "Refusing season export: published export has no schedule_projection "
                    "and openpyxl is unavailable for legacy Spond workbook reconstruction"
                ),
                "export_dir": str(export_dir),
            }
        ) from exc
    try:
        workbook = load_workbook(workbook_path, read_only=True, data_only=True)
    except OSError as exc:
        raise ExportProjectionError(
            {
                "summary": (
                    "Refusing season export: published export has no schedule_projection "
                    f"and legacy projection artifacts are unreadable: {workbook_path}"
                ),
                "export_dir": str(export_dir),
            }
        ) from exc
    if "Sesongplan" not in workbook.sheetnames:
        raise ExportProjectionError(
            {
                "summary": "Refusing season export: legacy Spond workbook has no Sesongplan sheet",
                "export_dir": str(export_dir),
            }
        )
    rows = list(workbook["Sesongplan"].iter_rows(values_only=True))
    if not rows:
        raise ExportProjectionError(
            {
                "summary": "Refusing season export: legacy Spond workbook is empty",
                "export_dir": str(export_dir),
            }
        )
    header = [str(value or "") for value in rows[0]]
    index = {name: idx for idx, name in enumerate(header)}
    required = ["Dato", "Sted", "Start", "Aldersgruppe", "Vertsklubb", "Deltakende lag"]
    missing = [name for name in required if name not in index]
    if missing:
        raise ExportProjectionError(
            {
                "summary": "Refusing season export: legacy Spond workbook lacks required columns",
                "missing_columns": missing,
                "export_dir": str(export_dir),
            }
        )

    canonical_tournaments = [
        tournament
        for tournament in (canonical_plan or {}).get("tournaments", []) or []
        if isinstance(tournament, Mapping) and tournament.get("id")
    ]
    data_rows = [row for row in rows[1:] if any(cell not in (None, "") for cell in row)]
    if len(data_rows) > len(canonical_tournaments):
        raise ExportProjectionError(
            {
                "summary": (
                    "Refusing season export: legacy Spond workbook has more tournaments "
                    "than the current canonical stable-id list"
                ),
                "workbook_tournament_count": len(data_rows),
                "canonical_tournament_count": len(canonical_tournaments),
                "export_dir": str(export_dir),
            }
        )

    team_lookup: dict[tuple[str, str], Mapping[str, Any]] = {}
    for tournament in canonical_tournaments:
        for team in tournament.get("teams") or []:
            if isinstance(team, Mapping):
                team_lookup[(str(team.get("age_group") or ""), str(team.get("label") or ""))] = team

    def cell(row: tuple[Any, ...], name: str) -> str:
        value = row[index[name]] if index[name] < len(row) else ""
        return str(value or "").strip()

    def iso_date(value: str) -> str:
        parts = value.split(".")
        if len(parts) == 3:
            return f"{parts[2]}-{parts[1]}-{parts[0]}"
        return value

    def row_projection(row: tuple[Any, ...]) -> dict[str, Any]:
        age_group = cell(row, "Aldersgruppe")
        participant_labels = [
            label.strip()
            for label in cell(row, "Deltakende lag").split(",")
            if label.strip()
        ]
        participants = []
        for label in participant_labels:
            team = team_lookup.get((age_group, label))
            if team is None:
                participants.append("\u001f".join(("", label, age_group)))
            else:
                participants.append(_participant_key(team))
        return {
            "date": iso_date(cell(row, "Dato")),
            "start_time": cell(row, "Start"),
            "arena": cell(row, "Sted"),
            "host_club": cell(row, "Vertsklubb"),
            "age_group": age_group,
            "participants": sorted(participants),
        }

    def comparable(projection_item: Mapping[str, Any]) -> tuple[Any, ...]:
        return (
            projection_item.get("date"),
            projection_item.get("start_time"),
            projection_item.get("arena"),
            projection_item.get("host_club"),
            projection_item.get("age_group"),
            tuple(projection_item.get("participants") or []),
        )

    canonical_by_exact: dict[tuple[Any, ...], list[Mapping[str, Any]]] = {}
    for tournament in canonical_tournaments:
        item = tournament_projection({"tournaments": [tournament]}).get(str(tournament.get("id")))
        if item:
            canonical_by_exact.setdefault(comparable(item), []).append(tournament)

    row_items = [row_projection(row) for row in data_rows]
    assigned_ids: set[str] = set()
    row_bindings: list[Mapping[str, Any] | None] = []
    for item in row_items:
        bucket = canonical_by_exact.get(comparable(item)) or []
        canonical = next((candidate for candidate in bucket if str(candidate.get("id")) not in assigned_ids), None)
        if canonical is not None:
            assigned_ids.add(str(canonical.get("id")))
        row_bindings.append(canonical)

    remaining = [
        tournament
        for tournament in canonical_tournaments
        if str(tournament.get("id")) not in assigned_ids
    ]
    remaining_iter = iter(remaining)
    projection: dict[str, dict[str, Any]] = {}
    for item, canonical in zip(row_items, row_bindings, strict=True):
        if canonical is None:
            canonical = next(remaining_iter, None)
        if canonical is None:
            raise ExportProjectionError(
                {
                    "summary": "Refusing season export: legacy Spond reconstruction ran out of stable ids",
                    "export_dir": str(export_dir),
                }
            )
        tournament_id = str(canonical.get("id") or "")
        projection[tournament_id] = {
            "id": tournament_id,
            "date": item["date"],
            "start_time": item["start_time"],
            "arena": item["arena"],
            "host_club": item["host_club"],
            "age_group": item["age_group"],
            "participants": list(item["participants"]),
        }
    if not projection:
        raise ExportProjectionError(
            {
                "summary": "Refusing season export: legacy Spond reconstruction produced no tournaments",
                "export_dir": str(export_dir),
            }
        )
    return projection


def _published_schedule_projection(
    published_export: Mapping[str, Any] | None,
    *,
    canonical_plan: Mapping[str, Any] | None = None,
) -> Mapping[str, Any] | None:
    if not published_export:
        return None
    published_projection = published_export.get("schedule_projection")
    if isinstance(published_projection, Mapping):
        return published_projection
    export_dir = published_export.get("export_dir")
    if export_dir:
        return projection_from_export_artifacts(export_dir, canonical_plan=canonical_plan)
    raise ExportProjectionError(
        {
            "summary": (
                "Refusing season export: published export has no schedule_projection "
                "and no export_dir for legacy reconstruction"
            ),
            "published_export": {
                key: published_export.get(key)
                for key in ("export_id", "export_fingerprint", "canonical_revision", "lifecycle_status")
                if published_export.get(key) is not None
            },
        }
    )


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
    published_projection = _published_schedule_projection(published_export, canonical_plan=canonical_plan)
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
    "projection_from_export_artifacts",
    "tournament_projection",
]
