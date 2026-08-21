"""Standalone public registered-team overview generation.

The SharePoint export used here may contain private/internal columns. This
module intentionally projects only ``club``, ``label`` and ``age_group`` into
public artifacts; validation/source metadata stays in the private validation
report.
"""

from __future__ import annotations

import csv
import hashlib
import html
import json
import re
import shutil
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from tournament_scheduler.html.templates import REGISTERED_TEAMS

from .activity_publish import copy_latest_snapshot

PUBLIC_COLUMNS: tuple[str, ...] = ("club", "label", "age_group")
DEFAULT_REGISTERED_TEAMS_DIR = "registered-teams"
REGISTERED_TEAMS_HTML = "pameldte-lag.html"
REGISTERED_TEAMS_JSON = "pameldte-lag.json"
REGISTERED_TEAMS_VALIDATION_REPORT = "validation-report.json"
_SCHEMA_VERSION = 1
_WHITESPACE_RE = re.compile(r"\s+")


class RegisteredTeamsValidationError(ValueError):
    """Raised when a registered-team CSV cannot be safely rendered."""

    def __init__(self, errors: list[str], report: dict[str, Any]):
        super().__init__("; ".join(errors))
        self.errors = errors
        self.report = report


class RegisteredTeamsPublishError(RuntimeError):
    """Raised when a registered-team publish staging snapshot cannot be prepared."""


def default_registered_teams_run_id(now: datetime | None = None) -> str:
    """Return a stable run id prefix for registered-team publishes."""
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    moment = moment.astimezone(timezone.utc)
    return f"registered-teams-{moment.strftime('%Y%m%dT%H%M%SZ')}"


def build_registered_teams_payload(
    csv_path: str | Path,
    *,
    config_path: str | Path | None = None,
    generated_at: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return ``(public_payload, validation_report)`` for a SharePoint CSV."""
    source = Path(csv_path)
    if not source.exists():
        report = _base_report(source, [], [], [], [], config_path=config_path)
        errors = [f"Filen finnes ikke: {source}"]
        report["errors"] = errors
        report["error_count"] = len(errors)
        raise RegisteredTeamsValidationError(errors, report)

    raw_bytes = source.read_bytes()
    source_fingerprint = hashlib.sha256(raw_bytes).hexdigest()
    reader = csv.DictReader(raw_bytes.decode("utf-8-sig").splitlines())
    raw_headers = list(reader.fieldnames or [])
    header_by_canonical = {_canonical_header(header): header for header in raw_headers}
    included_columns = [header_by_canonical[column] for column in PUBLIC_COLUMNS if column in header_by_canonical]
    excluded_columns = [header for header in raw_headers if _canonical_header(header) not in PUBLIC_COLUMNS]

    errors: list[str] = []
    warnings: list[str] = []
    missing_columns = [column for column in PUBLIC_COLUMNS if column not in header_by_canonical]
    errors.extend(f"Mangler påkrevd kolonne: {column}" for column in missing_columns)

    configured_age_groups = _load_configured_age_groups(config_path)
    rows: list[dict[str, str]] = []
    seen: dict[tuple[str, str, str], int] = {}
    if not missing_columns:
        for index, raw_row in enumerate(reader, start=2):
            row = {column: _normalize_value(raw_row.get(header_by_canonical[column], "")) for column in PUBLIC_COLUMNS}
            for column, value in row.items():
                if not value:
                    errors.append(f"Rad {index}: '{column}' mangler verdi.")
            if configured_age_groups and row["age_group"] and row["age_group"] not in configured_age_groups:
                errors.append(f"Rad {index}: aldersgruppen '{row['age_group']}' finnes ikke i konfigurert age_groups.")
            if all(row.values()):
                key = tuple(_dedupe_key(row[column]) for column in PUBLIC_COLUMNS)
                if key in seen:
                    errors.append(
                        f"Rad {index}: duplikat av rad {seen[key]} for club+label+age_group "
                        f"({row['club']} / {row['label']} / {row['age_group']})."
                    )
                else:
                    seen[key] = index
                rows.append(row)

    if excluded_columns:
        warnings.append("Ignorerte ekstra kolonner som ikke publiseres: " + ", ".join(sorted(excluded_columns, key=str.casefold)))

    report = _base_report(source, raw_headers, included_columns, excluded_columns, warnings, config_path=config_path)
    report.update(
        source_sha256=source_fingerprint,
        row_count=len(rows),
        configured_age_groups=configured_age_groups,
        errors=errors,
        error_count=len(errors),
    )
    if errors:
        raise RegisteredTeamsValidationError(errors, report)

    payload = _build_public_payload(
        rows,
        configured_age_groups=configured_age_groups,
        generated_at=generated_at or _utc_now(),
    )
    return payload, report


def prepare_registered_teams_latest_export(
    *,
    csv_path: str | Path,
    export_dir: str | Path,
    repo_dir: str = ".",
    branch: str = "gh-pages",
    config_path: str | Path | None = None,
    generated_at: str | None = None,
    include_latest_base: bool = True,
    require_latest_base: bool = True,
) -> dict[str, Any]:
    """Prepare a complete Pages snapshot with refreshed registered-team artifacts."""
    export_path = Path(export_dir)
    if export_path.exists():
        shutil.rmtree(export_path)
    export_path.mkdir(parents=True, exist_ok=True)

    base_file_count = 0
    if include_latest_base:
        base_file_count = copy_latest_snapshot(repo_dir=repo_dir, branch=branch, destination_dir=export_path)
        if require_latest_base and base_file_count == 0:
            raise RegisteredTeamsPublishError(
                f"Fant ingen eksisterende /latest/-snapshot på branch '{branch}'. "
                "Avbryter for å unngå å publisere bare Påmeldte lag og slette andre sider."
            )

    registered_team_files = generate_registered_team_artifacts(
        csv_path=csv_path,
        export_dir=export_path,
        config_path=config_path,
        generated_at=generated_at,
    )
    return {
        "export_dir": str(export_path),
        "base_file_count": base_file_count,
        "registered_team_files": registered_team_files,
    }


def generate_registered_team_artifacts(
    *,
    csv_path: str | Path,
    export_dir: str | Path = "export",
    config_path: str | Path | None = None,
    generated_at: str | None = None,
) -> dict[str, str]:
    """Validate *csv_path* and write public/review registered-team artifacts."""
    payload, report = build_registered_teams_payload(csv_path, config_path=config_path, generated_at=generated_at)
    target_dir = Path(export_dir) / DEFAULT_REGISTERED_TEAMS_DIR
    target_dir.mkdir(parents=True, exist_ok=True)

    json_path = target_dir / REGISTERED_TEAMS_JSON
    validation_path = target_dir / REGISTERED_TEAMS_VALIDATION_REPORT
    html_path = target_dir / REGISTERED_TEAMS_HTML
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    validation_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    html_path.write_text(render_registered_teams_html(payload), encoding="utf-8")
    return {
        "registered_teams_html": str(html_path),
        "registered_teams_json": str(json_path),
        "registered_teams_validation_report": str(validation_path),
    }


def render_registered_teams_html(payload: dict[str, Any]) -> str:
    """Render a compact, searchable Norwegian club overview."""
    generated_raw = str(payload.get("generated_at", ""))
    generated_at = _format_generated_at(generated_raw)
    total_teams = int(payload.get("total_teams", 0) or 0)

    clubs: dict[str, list[dict[str, str]]] = defaultdict(list)
    for group in payload.get("age_groups", []) or []:
        age_group = str(group.get("age_group", ""))
        for club in group.get("clubs", []) or []:
            club_name = str(club.get("club", ""))
            for team in club.get("teams", []) or []:
                clubs[club_name].append({"age_group": age_group, "label": str(team)})

    def age_sort_key(value: str) -> tuple[int, int | str, str]:
        match = re.fullmatch(r"(J?U)(\d+)", value, flags=re.IGNORECASE)
        return (0, int(match.group(2)), match.group(1).upper()) if match else (1, value.casefold(), value)

    sections: list[str] = []
    for club_name in sorted(clubs, key=str.casefold):
        teams = sorted(clubs[club_name], key=lambda team: (age_sort_key(team["age_group"]), team["label"].casefold()))
        team_items = "".join(
            f'<li class="team" data-search="{_e((team["age_group"] + " " + team["label"]).casefold())}">'
            f'<span class="age-badge">{_e(team["age_group"])}</span>'
            f'<span class="team-name">{_e(team["label"])}</span>'
            '</li>'
            for team in teams
        )
        sections.append(
            f'<section class="club" data-club-search="{_e(club_name.casefold())}">'
            '<div class="club-heading">'
            f'<h2>{_e(club_name)}</h2>'
            f'<span class="club-count">{len(teams)} lag</span>'
            '</div>'
            f'<ul>{team_items}</ul>'
            '</section>'
        )

    if sections:
        content = "\n".join(sections)
        empty_hidden = '<p class="no-results" id="no-results" hidden>Ingen klubber eller lag matcher søket.</p>'
    else:
        content = '<section class="empty-state"><h2>Ingen lag er registrert ennå</h2><p>Oversikten oppdateres når påmeldinger er godkjent.</p></section>'
        empty_hidden = ""

    template = REGISTERED_TEAMS
    replacements = {
        "@@TOTAL_TEAMS@@": str(total_teams),
        "@@GENERATED_RAW@@": _e(generated_raw),
        "@@GENERATED_AT@@": _e(generated_at),
        "@@CONTENT@@": content,
        "@@EMPTY_HIDDEN@@": empty_hidden,
    }
    for marker, value in replacements.items():
        template = template.replace(marker, value)
    return template


def _build_public_payload(
    rows: Iterable[dict[str, str]],
    *,
    configured_age_groups: list[str],
    generated_at: str,
) -> dict[str, Any]:
    grouped: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    clubs: set[str] = set()
    total_teams = 0
    for row in rows:
        grouped[row["age_group"]][row["club"]].add(row["label"])
        clubs.add(row["club"])
        total_teams += 1

    age_order = {age_group: index for index, age_group in enumerate(configured_age_groups)}

    def age_sort_key(age_group: str) -> tuple[int, int | str, str]:
        if age_group in age_order:
            return (0, age_order[age_group], age_group.casefold())
        match = re.fullmatch(r"(J?U)(\d+)", age_group, flags=re.IGNORECASE)
        if match:
            return (1, int(match.group(2)), match.group(1).upper())
        return (2, age_group.casefold(), age_group)

    age_groups = []
    for age_group in sorted(grouped, key=age_sort_key):
        club_entries = []
        team_count = 0
        for club in sorted(grouped[age_group], key=str.casefold):
            teams = sorted(grouped[age_group][club], key=str.casefold)
            team_count += len(teams)
            club_entries.append({"club": club, "team_count": len(teams), "teams": teams})
        age_groups.append({"age_group": age_group, "team_count": team_count, "club_count": len(club_entries), "clubs": club_entries})

    return {
        "schema_version": _SCHEMA_VERSION,
        "generated_at": generated_at,
        "title": "Påmeldte lag",
        "total_teams": total_teams,
        "total_clubs": len(clubs),
        "age_groups": age_groups,
    }


def _base_report(
    source: Path,
    raw_headers: list[str],
    included_columns: list[str],
    excluded_columns: list[str],
    warnings: list[str],
    *,
    config_path: str | Path | None,
) -> dict[str, Any]:
    return {
        "schema_version": _SCHEMA_VERSION,
        "generated_at": _utc_now(),
        "source_file": source.name,
        "source_sha256": None,
        "config_file": Path(config_path).name if config_path else None,
        "required_columns": list(PUBLIC_COLUMNS),
        "input_columns": raw_headers,
        "included_columns": included_columns,
        "excluded_columns": excluded_columns,
        "privacy_note": "Kun club, label og age_group brukes i offentlige artefakter.",
        "warnings": warnings,
        "errors": [],
        "error_count": 0,
        "row_count": 0,
    }


def _load_configured_age_groups(config_path: str | Path | None) -> list[str]:
    if not config_path:
        return []
    path = Path(config_path)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError:
        return []
    groups = data.get("age_groups")
    if not isinstance(groups, list):
        return []
    return [_normalize_value(group) for group in groups if _normalize_value(group)]


def _format_generated_at(value: str) -> str:
    if not value:
        return "ukjent"
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return value
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    moment = moment.astimezone()
    months = ("januar", "februar", "mars", "april", "mai", "juni", "juli", "august", "september", "oktober", "november", "desember")
    return f"{moment.day}. {months[moment.month - 1]} {moment.year} kl. {moment:%H:%M}"


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _normalize_value(value: object) -> str:
    return _WHITESPACE_RE.sub(" ", str(value or "").strip())


def _canonical_header(value: object) -> str:
    return _normalize_value(value).casefold().replace(" ", "_").replace("-", "_")


def _dedupe_key(value: str) -> str:
    return _normalize_value(value).casefold()


def _e(value: object) -> str:
    return html.escape(str(value or ""), quote=True)
