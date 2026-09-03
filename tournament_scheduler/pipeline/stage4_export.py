"""Stage 4 — multi-format export (Excel, iCal, CSV).

Reads the Stage 3 plan checkpoint, reconstructs a :class:`SeasonPlan` from it,
and writes three output files:

- ``<export_dir>/season_plan.xlsx``   — Excel workbook via :class:`SeasonPlanExporter`
- ``<export_dir>/season_plan.ics``    — iCal feed via :class:`ICalExporter`
- ``<export_dir>/season_plan.csv``    — flat game CSV + ``_overview.csv`` via :class:`CsvExporter`
- ``<export_dir>/season_plan.html``   — interactive HTML overview via :class:`~tournament_scheduler.html.html_exporter.HtmlExporter`
- ``<export_dir>/season_plan_report.html``   — companion diagnostics report with fairness / travel / hosting summaries
- ``<export_dir>/manual_schedule.html``   — “Må planlegges manuelt” view listing hall time that must be booked/verified by hand: tournaments that could not be placed without an arena/sequence collision, plus tournaments hosted by clubs whose calendar could not be scraped (provisional start times). Only written when such items exist; they no longer block the export
- ``<export_dir>/season_plan_spond_games.xlsx`` — printable tournament-by-tournament schedule attachment for Spond
- ``<export_dir>/review_packets/`` — per-club approval folders with review workbook, Spond import, schedule attachment, and response template

File paths are written to the Stage 4 checkpoint.
"""

from __future__ import annotations

import html as _html
import logging
import os
import re
import sys
import tempfile
import zipfile
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from ..models import SeasonPlan
from ..arena_conflicts import find_arena_interval_collisions
from ..excel.plan_exporter import SeasonPlanExporter
from ..ical.ical_exporter import ICalExporter
from ..csv.csv_exporter import CsvExporter
from ..html.html_exporter import HtmlExporter
from ..html.data_computation import ICON_BAR_CHART, ICON_CALENDAR, ICON_CLIPBOARD, ICON_USERS, ICON_WARNING, fmt_date, season_label
from ..html.templates import STYLES_CSS
from ..review.review_packet_exporter import ReviewPacketExporter
from ..spond.spond_exporter import SpondExporter
from .stage1_config import load_effective_config
from .state import PipelineState, StageName, StageStatus
from .stage4_helpers import _dict_to_plan
from .calendar_viewer import generate_html as _generate_calendars_html
from .input_viewer import generate_html as _generate_input_html
from .activity_viewer import generate_activity_artifacts as _generate_activity_artifacts
from .not_started import NOT_STARTED_MESSAGE, render_not_started_html

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

DEFAULT_EXPORT_DIR = "export"
DEFAULT_BASENAME = "season_plan"

# Matches the "%Y-%m-%dT%H%M" directory name this module generates below.
# Callers (e.g. a stage-by-stage orchestrator that picks one export dir up
# front to keep a run's logs and export together) sometimes pass an
# already-timestamped --export-dir. Detecting that here keeps a second,
# nested timestamp from being appended on top of it.
_TIMESTAMP_DIR_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{4}$")


def _resolve_build_timestamp(build_timestamp: str | int | float | datetime | None = None) -> datetime:
    """Return the canonical UTC content timestamp for a Stage 4 export.

    ``build_timestamp`` wins when provided. Otherwise ``SOURCE_DATE_EPOCH``
    is honored for reproducible builds, falling back to the current wall
    clock. Naive datetimes/ISO strings are treated as UTC because the value
    describes generated content, not a local operator audit moment.
    """
    raw: str | int | float | datetime | None = build_timestamp
    if raw is None:
        raw = os.environ.get("SOURCE_DATE_EPOCH")

    if raw is None or raw == "":
        return datetime.now(timezone.utc).replace(microsecond=0)

    if isinstance(raw, datetime):
        moment = raw
    elif isinstance(raw, (int, float)):
        moment = datetime.fromtimestamp(float(raw), tz=timezone.utc)
    else:
        value = str(raw).strip()
        if not value:
            return datetime.now(timezone.utc).replace(microsecond=0)
        if re.fullmatch(r"\d+(?:\.\d+)?", value):
            moment = datetime.fromtimestamp(float(value), tz=timezone.utc)
        else:
            try:
                moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError as exc:
                raise Stage4Error(f"Ugyldig build timestamp '{raw}': {exc}") from exc

    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).replace(microsecond=0)


MANUAL_SCHEDULE_FILENAME = "manual_schedule.html"


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class Stage4Error(RuntimeError):
    """Raised when Stage 4 export fails."""


def _zip_datetime(build_timestamp: datetime) -> tuple[int, int, int, int, int, int]:
    """Return a ZIP-compatible UTC timestamp tuple.

    ZIP stores local DOS timestamps and cannot represent years before 1980;
    reproducible builds using earlier epochs are clamped to that minimum.
    """
    moment = build_timestamp.astimezone(timezone.utc).replace(microsecond=0)
    if moment.year < 1980:
        moment = moment.replace(year=1980, month=1, day=1, hour=0, minute=0, second=0)
    return (moment.year, moment.month, moment.day, moment.hour, moment.minute, moment.second)


def _normalize_xlsx_core_properties(xml_bytes: bytes, build_timestamp: datetime) -> bytes:
    fixed = build_timestamp.astimezone(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")
    text = xml_bytes.decode("utf-8")
    for field in ("created", "modified"):
        pattern = rf"(<dcterms:{field}[^>]*>)(.*?)(</dcterms:{field}>)"
        replacement = rf"\g<1>{fixed}\g<3>"
        text, count = re.subn(pattern, replacement, text)
        if count == 0:
            insert_at = text.find("</cp:coreProperties>")
            if insert_at != -1:
                text = (
                    text[:insert_at]
                    + f'<dcterms:{field} xsi:type="dcterms:W3CDTF">{fixed}</dcterms:{field}>'
                    + text[insert_at:]
                )
    return text.encode("utf-8")


def _normalize_xlsx(path: Path, build_timestamp: datetime) -> None:
    """Normalize an XLSX workbook's embedded and ZIP metadata in place."""
    import openpyxl

    workbook = openpyxl.load_workbook(path)
    workbook.properties.created = build_timestamp.replace(tzinfo=None)
    workbook.properties.modified = build_timestamp.replace(tzinfo=None)
    workbook.save(path)

    fixed_date_time = _zip_datetime(build_timestamp)
    with tempfile.NamedTemporaryFile(delete=False, dir=path.parent, suffix=".xlsx") as handle:
        tmp_path = Path(handle.name)

    try:
        with zipfile.ZipFile(path, "r") as source, zipfile.ZipFile(tmp_path, "w", compression=zipfile.ZIP_DEFLATED) as dest:
            for name in sorted(source.namelist()):
                original_info = source.getinfo(name)
                info = zipfile.ZipInfo(filename=name, date_time=fixed_date_time)
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = original_info.external_attr
                info.comment = original_info.comment
                info.create_system = original_info.create_system
                data = source.read(name)
                if name == "docProps/core.xml":
                    data = _normalize_xlsx_core_properties(data, build_timestamp)
                dest.writestr(info, data)
        tmp_path.replace(path)
    finally:
        tmp_path.unlink(missing_ok=True)


def _normalize_export_workbooks(primary_export_path: Path, build_timestamp: datetime) -> None:
    for workbook_path in sorted(primary_export_path.rglob("*.xlsx")):
        _normalize_xlsx(workbook_path, build_timestamp)


def _write_not_started_exports(primary_export_path: Path, basename: str, message: str) -> dict[str, str]:
    """Write the normal export surface as small placeholder files."""
    import openpyxl

    primary_export_path.mkdir(parents=True, exist_ok=True)
    output_files: dict[str, str] = {}

    def _write_workbook(path: Path, title: str) -> None:
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = title[:31]
        ws.append([message])
        wb.save(path)

    html = render_not_started_html(message)

    excel_path = primary_export_path / f"{basename}.xlsx"
    _write_workbook(excel_path, "Ikke begynt")
    output_files["excel"] = str(excel_path)

    ical_path = primary_export_path / f"{basename}.ics"
    ical_path.write_text(
        "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//RVV Miniputt//Not Started//NO\r\n"
        "X-WR-CALNAME:Ikke begynt\r\nEND:VCALENDAR\r\n",
        encoding="utf-8",
    )
    output_files["ical"] = str(ical_path)

    csv_path = primary_export_path / f"{basename}.csv"
    csv_path.write_text(f"status\n{message}\n", encoding="utf-8")
    output_files["csv_games"] = str(csv_path)

    overview_path = primary_export_path / f"{basename}_overview.csv"
    overview_path.write_text(f"status\n{message}\n", encoding="utf-8")
    output_files["csv_overview"] = str(overview_path)

    for key, filename in (
        ("input_html", "input.html"),
        ("calendars_html", "calendars.html"),
        ("html", f"{basename}.html"),
        ("html_report", f"{basename}_report.html"),
    ):
        path = primary_export_path / filename
        path.write_text(html, encoding="utf-8")
        output_files[key] = str(path)

    spond_path = primary_export_path / f"{basename}_spond.xlsx"
    _write_workbook(spond_path, "Ikke begynt")
    output_files["spond"] = str(spond_path)

    spond_games_path = primary_export_path / f"{basename}_spond_games.xlsx"
    _write_workbook(spond_games_path, "Ikke begynt")
    output_files["spond_games"] = str(spond_games_path)

    review_dir = primary_export_path / "review_packets"
    review_dir.mkdir(parents=True, exist_ok=True)
    (review_dir / "README.txt").write_text(message + "\n", encoding="utf-8")
    output_files["review_packets"] = str(review_dir)

    return output_files


def _manual_schedule_html(
    plan: SeasonPlan,
    *,
    manual_entries: list[dict[str, str]] | None = None,
    generated_at: str = "",
    input_path: str = "",
    date_range: str = "",
    source_count: int = 0,
    event_count: int = 0,
    blocked: list[str] | None = None,
    scrape_age: str = "",
    calendars_href: str = "",
    season_plan_href: str = "",
    report_href: str = "",
    input_href: str = "",
) -> str:
    """Render the dedicated “Må planlegges manuelt” page.

    Lists everything that cannot be treated as auto-confirmed hall time:

    - tournaments that ended up with a same-arena/sequence overflow collision
      during host/time assignment (the auto-planner already tried to shift
      them; hall time must be booked by hand or the plan re-run), and
    - tournaments hosted by clubs whose calendar source could not be scraped
      (they still receive their share of home tournaments, but the assigned
      start time is provisional — the istid must be booked/verified manually).

    Each entry carries a ``type`` (Grunn) so the arena scheduler can see why
    it must act.
    """
    from ..html.data_computation import canonical_rvv_club_name

    entries = sorted(
        list(manual_entries or []),
        key=lambda c: (c.get("date", ""), c.get("arena", ""), c.get("tournament_id", "")),
    )
    rows: list[str] = []
    for idx, c in enumerate(entries, start=1):
        arena = str(c.get("arena", "") or "")
        raw_host = str(c.get("host_club", "") or "")
        host = canonical_rvv_club_name(raw_host) if raw_host else ""
        if not host or host == "-":
            host = raw_host or arena or "?"
        tournament_id = str(c.get("tournament_id", "") or "")
        age_group = str(c.get("age_group", "") or "")
        date_val = str(c.get("date", "") or "")
        interval = str(c.get("interval", "") or "")
        if not interval and date_val:
            try:
                from datetime import date as _date
                interval = fmt_date(_date.fromisoformat(date_val)) or date_val
            except ValueError:
                interval = date_val
        entry_type = str(c.get("type", "") or "Arena-/tidskollisjon")
        conflict_id = str(c.get("conflicting_tournament_id", "") or "")
        conflict_ag = str(c.get("conflicting_age_group", "") or "")
        conflict_interval = str(c.get("conflicting_interval", "") or "")
        detail = str(c.get("message", "") or "")
        if not detail:
            detail = f"{interval} kolliderer med {conflict_interval}" if conflict_interval else interval
        conflict_cell = " ".join(part for part in (conflict_id, conflict_ag, conflict_interval) if part) or "-"
        rows.append(
            "<tr>"
            f"<td class=\"numeric-cell\">{idx}</td>"
            f"<td>{_html.escape(tournament_id)}</td>"
            f"<td>{_html.escape(date_val)}</td>"
            f"<td><strong>{_html.escape(age_group)}</strong></td>"
            f"<td>{_html.escape(host)}</td>"
            f"<td>{_html.escape(arena)}</td>"
            f"<td>{_html.escape(interval)}</td>"
            f"<td>{_html.escape(entry_type)}</td>"
            f"<td>{_html.escape(conflict_cell)}</td>"
            f"<td>{_html.escape(detail)}</td>"
            "</tr>"
        )
    if not rows:
        rows.append('<tr><td colspan=\"10\" class=\"empty-cell\">Ingen turneringer trenger manuell planlegging.</td></tr>')

    rows_html = "".join(rows)

    def _nav_link(href: str, label: str, icon: str, active: bool = False) -> str:
        cls = "active" if active else ""
        return f'<a href="{_html.escape(href)}" class="{cls}"><span class="nav-icon">{icon}</span> {_html.escape(label)}</a>'

    calendar_nav = _nav_link(calendars_href, "Skrapede kalendere", ICON_CALENDAR) if calendars_href else ""
    season_plan_nav = (
        _nav_link(season_plan_href, "Sesongplan", ICON_CLIPBOARD) if season_plan_href else ""
    )
    report_nav = _nav_link(report_href, "Rapport", ICON_BAR_CHART) if report_href else ""
    input_nav = _nav_link(input_href, "Påmeldte lag", ICON_USERS) if input_href else ""

    scrape_meta_parts: list[str] = []
    if source_count:
        scrape_meta_parts.append(f"{source_count} kilder")
    if event_count:
        scrape_meta_parts.append(f"{event_count} hendelser")
    if scrape_age:
        scrape_meta_parts.append(f"Data: {scrape_age}")
    scrape_meta = " &middot; ".join(scrape_meta_parts)
    if scrape_meta:
        scrape_meta = f'<span class="meta-nav">{scrape_meta}</span>'

    subtitle = "RVV Hockey &mdash; manuelt behov"
    if season_label(plan):
        subtitle = f"{_html.escape(season_label(plan))} &mdash; manuelt behov"

    extra_note = ""
    if blocked:
        names = ", ".join(str(item) for item in blocked)
        extra_note = (
            '<div class="report-action report-action--warn"><strong>Datagrunnlag</strong>'
            f"<p>{_html.escape(names)} var utilgjengelig under skraping; husk å følge opp manuelt.</p></div>"
        )

    hidden_parts: list[str] = []
    if date_range:
        hidden_parts.append(f"Periode: {date_range}")
    if generated_at:
        hidden_parts.append(f"Generert: {generated_at}")
    if input_path:
        hidden_parts.append(f"Input: {input_path}")

    return """<!DOCTYPE html>
<html lang="no">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Må planlegges manuelt — RVV Hockey</title>
<style>
""" + STYLES_CSS + """
</style>
</head>
<body>
<script>if (window.self !== window.top) { document.documentElement.classList.add('rvv-embedded'); }</script>
<div class="navbar">
  <span class="brand">RVV Miniputt</span>
  """ + calendar_nav + season_plan_nav + report_nav + input_nav + scrape_meta + """
</div>
<div class="app">
  <header class="header-main">
    <div class="header-icon">""" + ICON_WARNING + """</div>
    <div class="header-text"><h1>Må planlegges manuelt</h1><p>""" + subtitle + """</p></div>
  </header>
  <div class="report-overview" id="reportOverview">
    <div class="report-hero report-hero--warn">
      <div>
        <p class="eyebrow">Istidsplanlegging</p>
        <h2>Turneringer som må settes inn i ishall-kalenderen manuelt</h2>
        <p class="report-hero-note">""" + str(len(entries)) + """ turnering(er) krever manuell istidsplanlegging: enten fordi auto-planen ikke fant en kollisjonsfri plass, eller fordi vertsklubbens kalender ikke kunne skrapes (da er starttiden foreløpig og må bookes/verifiseres for hånd). Turneringene ligger i sesongplanen, men istiden er ikke endelig før den er booket.</p>
      </div>
      <span class="report-status-pill report-status-pill--warn">MANUELL OPPFØLGING · """ + str(len(entries)) + """ stk</span>
    </div>
    <section class="report-section report-section--priority" id="priorityActions">
      <div class="section-head">
        <div>
          <p class="eyebrow">Viktigst først</p>
          <h2>Hvem må gjøre hva?</h2>
        </div>
        <p class="section-note">Kontakt vertsklubbens ishall-/timeansvarlige for disse datoene.</p>
      </div>
      <div class="report-action-list">
        """ + extra_note + """
      </div>
    </section>
    <div class="table-wrap"><table class="report-table"><thead><tr>
      <th>#</th><th>Turnering</th><th>Dato</th><th>Aldersgruppe</th><th>Vert</th><th>Arena</th><th>Intervall</th><th>Grunn</th><th>Konflikt med</th><th>Detaljer</th>
    </tr></thead><tbody>""" + rows_html + """</tbody></table></div>
    <p class="report-hidden-context" aria-hidden="true">""" + _html.escape(" · ".join(hidden_parts)) + """</p>
  </div>
</div>
</body>
</html>
"""

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def run(
    plan_checkpoint: dict[str, Any],
    state: PipelineState,
    *,
    export_dir: str | os.PathLike[str] = DEFAULT_EXPORT_DIR,
    basename: str = DEFAULT_BASENAME,
    strict: bool = True,
    timestamped_export: bool = True,
    build_timestamp: str | int | float | datetime | None = None,
) -> dict[str, Any]:
    """Export the Stage 3 plan to Excel, iCal, and CSV.

    Parameters
    ----------
    plan_checkpoint:
        Stage 3 checkpoint data (must contain a ``plan`` key).
    state:
        :class:`PipelineState` managing the work directory.
    export_dir:
        Directory where output files are written (created if needed).
    basename:
        Base filename without extension (default ``season_plan``).
    strict:
        If ``True``, raise :class:`Stage4Error` on any export failure.

    Returns
    -------
    dict
        Checkpoint data with output file paths.
    """
    def _progress(message: str) -> None:
        print(f"[progress] {message}", file=sys.stdout, flush=True)

    state.write_stage(StageName.EXPORT, {}, status=StageStatus.RUNNING)
    _progress("Klarmaker eksport: laster plan og forbereder filer")

    plan_dict = plan_checkpoint.get("plan", {})
    if not plan_dict:
        reason = "Ingen plan funnet i Stage 3 checkpoint — kjør Stage 3 først."
        state.write_stage(StageName.EXPORT, {}, status=StageStatus.FAILED)
        if strict:
            raise Stage4Error(reason)
        return {}

    plan = _dict_to_plan(plan_dict)
    export_path = Path(export_dir)
    export_path.mkdir(parents=True, exist_ok=True)
    canonical_build_timestamp = _resolve_build_timestamp(build_timestamp)

    # Store the primary export path (may be flat or timestamped)
    primary_export_path = export_path
    already_timestamped = bool(_TIMESTAMP_DIR_RE.match(export_path.name))
    if timestamped_export and not already_timestamped:
        ts_dir = canonical_build_timestamp.strftime("%Y-%m-%dT%H%M")
        primary_export_path = export_path / ts_dir
        primary_export_path.mkdir(parents=True, exist_ok=True)

    errors: list[str] = []
    output_files: dict[str, str] = {}
    effective_config: dict[str, Any] = {}
    try:
        effective_config = load_effective_config(state)
    except Exception:
        effective_config = {}
    generated_at = canonical_build_timestamp.isoformat()
    input_path = str(effective_config.get("input_path") or "input.xlsx")

    if plan_dict.get("placeholder") == "not_started" or (plan_checkpoint.get("not_started") and not plan.tournaments):
        message = str(plan_dict.get("message") or NOT_STARTED_MESSAGE)
        _progress("Genererer tomme ikke-begynt-filer")
        output_files = _write_not_started_exports(primary_export_path, basename, message)
        _normalize_export_workbooks(primary_export_path, canonical_build_timestamp)
        checkpoint = {
            "generated_at": generated_at,
            "input_path": input_path,
            "output_files": output_files,
            "errors": [],
            "not_started": True,
            "message": message,
        }
        state.write_stage(StageName.EXPORT, checkpoint, status=StageStatus.DONE)
        _progress("Eksport ferdig")
        return checkpoint

    round_length_for_age_group: dict[str, int] = dict(effective_config.get("round_length_minutes", {}))
    derived_collisions = find_arena_interval_collisions(plan.tournaments, round_length_for_age_group)
    stored_collisions = derived_collisions or list(plan.arena_day_collisions or [])
    # Enrich planner-stored collision dicts with the host club from the plan
    # (they may omit it) so the manual view can name who must act.
    host_by_tournament_id = {
        tournament.id: tournament.host_club for tournament in plan.tournaments if tournament.host_club
    }
    collision_entries: list[dict[str, str]] = []
    for collision in stored_collisions:
        item = dict(collision)
        if not item.get("host_club"):
            item["host_club"] = host_by_tournament_id.get(str(item.get("tournament_id", "")))
        item["type"] = item.get("type", "Arena-/tidskollisjon")
        collision_entries.append(item)
    # Clubs whose calendar source could not be scraped still receive their
    # proportional share of home tournaments; those tournaments are marked on
    # the plan (Tournament.manual_booking_reason) because the auto-assigned
    # start time cannot be validated against the real hall calendar — the istid
    # must be booked/verified by hand. Surface them in the same manual view.
    manual_host_entries: list[dict[str, str]] = []
    for tournament in plan.tournaments:
        if not tournament.manual_booking_reason:
            continue
        interval = tournament.start_time or tournament.date.isoformat()
        manual_host_entries.append(
            {
                "type": "Kalender utilgjengelig — istid må bookes manuelt",
                "date": tournament.date.isoformat(),
                "arena": tournament.arena,
                "host_club": tournament.host_club or "",
                "age_group": tournament.age_group,
                "tournament_id": tournament.id,
                "interval": interval,
                "conflicting_tournament_id": "",
                "conflicting_age_group": "",
                "conflicting_interval": "",
                "message": tournament.manual_booking_reason,
            }
        )
    manual_entries = collision_entries + manual_host_entries
    if collision_entries:
        plan.arena_day_collisions = collision_entries
        first = collision_entries[0]
        detail = first.get("message") if isinstance(first, dict) else str(first)
        logger.warning(
            "%d arena-/dagskollisjon(er) krever manuell oppfølging (eksport fortsetter): %s",
            len(collision_entries),
            detail,
        )
    if manual_host_entries:
        logger.warning(
            "%d turnering(er) hos klubb(er) uten tilgjengelig kalender er merket for manuell istidsbooking: %s",
            len(manual_host_entries),
            ", ".join(sorted({str(e.get("host_club", "")) for e in manual_host_entries})),
        )
    configured_age_groups = list(dict.fromkeys(effective_config.get("age_groups", [])))
    if not configured_age_groups and not effective_config.get("age_groups_from_input", False):
        configured_age_groups = sorted({t.age_group for t in plan.tournaments})

    # --- Excel ---
    try:
        _progress("Eksporterer Excel-arbeidsbok")
        excel_path = str(primary_export_path / f"{basename}.xlsx")
        rules_report = plan_checkpoint.get("rules_report")
        SeasonPlanExporter().export(
            plan,
            excel_path,
            rules_report=rules_report,
            round_length_for_age_group=round_length_for_age_group,
        )
        output_files["excel"] = excel_path
    except Exception as exc:  # noqa: BLE001
        errors.append(f"Excel-eksport feilet: {exc}")

    # --- iCal ---
    try:
        _progress("Eksporterer iCal-feed")
        ical_path = str(primary_export_path / f"{basename}.ics")
        ICalExporter(round_length_for_age_group=round_length_for_age_group).export_tournament_summary(plan, ical_path)
        output_files["ical"] = ical_path
    except Exception as exc:  # noqa: BLE001
        errors.append(f"iCal-eksport feilet: {exc}")

    # --- CSV ---
    try:
        _progress("Eksporterer CSV-filer")
        csv_path = str(primary_export_path / f"{basename}.csv")
        games_path, overview_path = CsvExporter().export(plan, csv_path)
        output_files["csv_games"] = games_path
        output_files["csv_overview"] = overview_path
    except Exception as exc:  # noqa: BLE001
        errors.append(f"CSV-eksport feilet: {exc}")

    # --- HTML export + manual-schedule view ---
    # Shared metadata is prepared up front so both the season-plan/report pages and
    # the manual-schedule view (written when collisions or calendar-less hosts
    # remain) can use it.
    html_path = str(primary_export_path / f"{basename}.html")
    pipeline_meta: dict[str, Any] = {
        "generated_at": generated_at,
        "input_path": input_path,
        "input_file": Path(input_path).name,
    }
    meta: dict[str, Any] | None = None
    _scrape_cache_data: dict[str, Any] = {}
    _calendars_path: str | None = None
    _input_html_path: str | None = None
    try:
        _progress("Samler pipeline-metadata for rapporten")
        scraping_envelope = state.read_envelope(StageName.SCRAPING)
    except Exception as exc:
        logger.warning("Kunne ikke lese scraping-checkpoint for rapporten: %s", exc)
        scraping_envelope = None
    scraping_ckpt = scraping_envelope.get("data", {}) if scraping_envelope else None
    if scraping_ckpt and isinstance(scraping_ckpt, dict):
        # read_envelope() returns the full wrapper so updated_at is accessible at top level
        sources = scraping_ckpt.get("sources", [])
        pipeline_meta["source_count"] = len(sources)
        pipeline_meta["total_events"] = sum(s.get("event_count", 0) for s in sources)
        pipeline_meta["blocked"] = scraping_ckpt.get("blocked", [])
        pipeline_meta["date_range"] = (
            f"{effective_config.get('start_date', '')} &ndash; {effective_config.get('end_date', '')}"
        )
        pipeline_meta["age_groups"] = configured_age_groups
        updated = scraping_envelope.get("updated_at", "") if scraping_envelope else ""
        if updated:
            pipeline_meta["scrape_updated_at"] = updated
            from datetime import datetime as _dt, timezone as _tz
            try:
                delta = _dt.now(tz=_tz.utc) - _dt.fromisoformat(updated)
                if delta.total_seconds() < 3600:
                    pipeline_meta["scrape_age"] = f"{int(delta.total_seconds() // 60)}m siden"
                elif delta.days < 1:
                    pipeline_meta["scrape_age"] = f"{int(delta.total_seconds() // 3600)}t siden"
                else:
                    pipeline_meta["scrape_age"] = f"{delta.days}d siden"
            except Exception as exc:
                logger.warning(
                    "Kunne ikke tolke updated_at='%s' i scraping-checkpoint: %s",
                    updated,
                    exc,
                )
    # Scrape metadata from cache for navbar
    try:
        from .cache_manager import ScrapedDataCache
        _scrape_cache_data = ScrapedDataCache(state.work_dir).read()
        meta = _scrape_cache_data.get("_meta")
    except Exception as exc:
        logger.warning("Kunne ikke lese scrape-cache for rapporten: %s", exc)
    # --- Input viewer (input.html) — public overview of registered clubs/teams ---
    # Generated before the calendar viewer so calendars.html's navbar can link to it.
    # Only the whitelisted "Lag" worksheet is read (see input_workbook.PUBLIC_SHEET_WHITELIST).
    # Only generated when Stage 1 actually recorded an input workbook path that exists on
    # disk — deliberately not the "input.xlsx" fallback default used for cosmetic display
    # elsewhere in this function, so callers that skip Stage 1 (e.g. most stage4 tests, or
    # a plan built directly) never accidentally pick up an unrelated input.xlsx from cwd.
    _configured_input_path = effective_config.get("input_path")
    if _configured_input_path and os.path.exists(_configured_input_path):
        try:
            _progress("Genererer oversikt over påmeldte lag")
            _generate_input_html(
                input_path=_configured_input_path,
                export_dir=str(primary_export_path),
            )
            _input_html_path = str(primary_export_path / "input.html")
            output_files["input_html"] = _input_html_path
        except Exception as exc:  # noqa: BLE001
            errors.append(f"Input-visning feilet: {exc}")

        try:
            start_year = None
            start_date_value = effective_config.get("start_date")
            if isinstance(start_date_value, str) and len(start_date_value) >= 4:
                start_year = int(start_date_value[:4])
            _progress("Genererer aktivitetskalender")
            activity_files = _generate_activity_artifacts(
                input_path=_configured_input_path,
                export_dir=str(primary_export_path),
                default_year=start_year,
                generated_at=generated_at,
            )
            if activity_files:
                output_files.update(activity_files)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"Aktivitetskalender feilet: {exc}")
    # --- Calendar viewer (calendars.html) ---
    # Generate before HtmlExporter so calendars_path can be passed in and the navbar can link to it.
    # Only generate when scrape data exists — without it the file would be empty and the navbar link would be broken.
    # total_events/source_count are top-level keys in the cache, not inside _meta.
    if _scrape_cache_data.get("total_events", 0) > 0 or _scrape_cache_data.get("source_count", 0) > 0:
        try:
            _progress("Genererer kalenderoversikt")
            _generate_calendars_html(
                work_dir=str(state.work_dir),
                export_dir=str(primary_export_path),
            )
            _calendars_path = str(primary_export_path / "calendars.html")
            output_files["calendars_html"] = _calendars_path
        except Exception as exc:  # noqa: BLE001
            errors.append(f"Kalendervisning feilet: {exc}")

    # --- Main HTML pages (season_plan.html + season_plan_report.html) ---
    _manual_schedule_path: str | None = None
    try:
        _progress("Genererer HTML-rapport")
        HtmlExporter().export(
            plan,
            html_path,
            meta=meta,
            output_files=output_files,
            pipeline_meta=pipeline_meta,
            age_groups=configured_age_groups,
            calendars_path=_calendars_path,
            input_html_path=_input_html_path,
        )
        output_files["html"] = html_path
        output_files["html_report"] = str(Path(html_path).with_name(f"{Path(html_path).stem}_report{Path(html_path).suffix}"))
    except Exception as exc:  # noqa: BLE001
        errors.append(f"HTML-eksport feilet: {exc}")

    # --- Manual-schedule view (manual_schedule.html) ---
    # Anything that cannot be treated as auto-confirmed hall time goes here:
    # arena/sequence collisions that could not be placed, plus tournaments
    # hosted by clubs whose calendar could not be scraped (provisional istid).
    if manual_entries and not errors:
        try:
            _progress("Genererer manuell-oppfølgingsvisning")
            _manual_path = primary_export_path / MANUAL_SCHEDULE_FILENAME
            manual_html = _manual_schedule_html(
                plan,
                manual_entries=manual_entries,
                generated_at=generated_at,
                input_path=input_path,
                date_range=str(pipeline_meta.get("date_range", "")),
                source_count=int(pipeline_meta.get("source_count", 0)),
                event_count=int(pipeline_meta.get("total_events", 0)),
                blocked=pipeline_meta.get("blocked", []),
                scrape_age=str(pipeline_meta.get("scrape_age", "")),
                calendars_href="calendars.html" if (_calendars_path and os.path.exists(_calendars_path)) else "",
                season_plan_href="season_plan.html",
                report_href="season_plan_report.html",
                input_href="input.html" if (_input_html_path and os.path.exists(_input_html_path)) else "",
            )
            _manual_path.write_text(manual_html, encoding="utf-8")
            _manual_schedule_path = str(_manual_path)
            output_files["manual_schedule"] = _manual_schedule_path
        except Exception as exc:  # noqa: BLE001
            errors.append(f"Manuell-oppfølgingsvisning feilet: {exc}")

    # Re-export the season_plan/report pages when the manual view was written so
    # their navbars pick up the manual-schedule link (the manual page links back).
    if _manual_schedule_path and not errors:
        try:
            _progress("Genererer HTML-rapport med manuell-lenke")
            HtmlExporter().export(
                plan,
                html_path,
                meta=meta,
                output_files=output_files,
                pipeline_meta=pipeline_meta,
                age_groups=configured_age_groups,
                calendars_path=_calendars_path,
                input_html_path=_input_html_path,
                manual_schedule_path=_manual_schedule_path,
            )
            output_files["html"] = html_path
            output_files["html_report"] = str(Path(html_path).with_name(f"{Path(html_path).stem}_report{Path(html_path).suffix}"))
        except Exception as exc:  # noqa: BLE001
            errors.append(f"HTML-eksport (manuell-oppfølgingsvisning) feilet: {exc}")

    # --- Spond ---
    try:
        _progress("Genererer Spond-eksport")
        spond_path = str(primary_export_path / f"{basename}_spond.xlsx")
        schedule_path = str(primary_export_path / f"{basename}_spond_games.xlsx")
        exporter = SpondExporter()
        exporter.export(
            plan,
            spond_path,
            round_length_for_age_group=round_length_for_age_group,
        )
        exporter.export_schedule_attachment(
            plan,
            schedule_path,
            round_length_for_age_group=round_length_for_age_group,
        )
        output_files["spond"] = spond_path
        output_files["spond_games"] = schedule_path
    except Exception as exc:  # noqa: BLE001
        errors.append(f"Spond-eksport feilet: {exc}")

    # --- Per-club review packets ---
    try:
        _progress("Genererer klubbreview-pakker")
        review_dir = primary_export_path / "review_packets"
        clubs = sorted({team.club for tournament in plan.tournaments for team in tournament.teams})
        ReviewPacketExporter().export(
            plan,
            review_dir,
            clubs=clubs,
            round_length_for_age_group=round_length_for_age_group,
        )
        output_files["review_packets"] = str(review_dir)
    except Exception as exc:  # noqa: BLE001
        errors.append(f"Review-pakker feilet: {exc}")

    try:
        _normalize_export_workbooks(primary_export_path, canonical_build_timestamp)
    except Exception as exc:  # noqa: BLE001
        errors.append(f"Normalisering av Excel-filer feilet: {exc}")

    checkpoint: dict[str, Any] = {
        "generated_at": generated_at,
        "input_path": input_path,
        "output_files": output_files,
        "errors": errors,
        "arena_day_collisions": list(plan.arena_day_collisions or []),
        "manual_booking_count": len(manual_host_entries),
    }

    if errors and strict:
        state.write_stage(StageName.EXPORT, checkpoint, status=StageStatus.FAILED)
        _progress("Eksport feilet")
        raise Stage4Error("\n".join(errors))

    status = StageStatus.DONE if not errors else StageStatus.FAILED
    state.write_stage(StageName.EXPORT, checkpoint, status=status)
    _progress("Eksport ferdig")
    return checkpoint


# ---------------------------------------------------------------------------
# Deserialisation
# ---------------------------------------------------------------------------


# CLI entry point — supports: python3 -m tournament_scheduler.pipeline.stage4_export
# ---------------------------------------------------------------------------

if __name__ == "__main__":  # pragma: no cover
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="Stage 4: multi-format export")
    parser.add_argument("--work-dir", default=".pipeline", help="Pipeline work directory")
    parser.add_argument("--export-dir", default="export", help="Directory for output files")
    parser.add_argument(
        "--timestamped-export",
        dest="timestamped_export",
        action="store_true",
        help="Write exports into a timestamped subfolder of --export-dir",
    )
    parser.add_argument(
        "--no-timestamped-export",
        dest="timestamped_export",
        action="store_false",
        help="Write exports flat into --export-dir",
    )
    parser.add_argument(
        "--build-timestamp",
        default=None,
        help="Canonical content timestamp (ISO-8601 or epoch seconds) for reproducible exports",
    )
    parser.set_defaults(timestamped_export=True)
    cli_args = parser.parse_args()

    from .run_log_paths import append_stage_log_line  # noqa: E402
    from .state import PipelineState, StageName  # noqa: E402

    _state = PipelineState(cli_args.work_dir)
    _plan_ckpt = _state.read_stage(StageName.PLANNING)
    if not _plan_ckpt:
        print("Stage 3 checkpoint not found — run Stage 3 first.", file=sys.stderr)
        sys.exit(1)

    try:
        _result = run(
            _plan_ckpt,
            _state,
            export_dir=cli_args.export_dir,
            timestamped_export=cli_args.timestamped_export,
            build_timestamp=cli_args.build_timestamp,
        )
        files = _result.get("output_files", {})
        print(f"Stage 4 OK — {len(files)} filer eksportert: {', '.join(files.values())}")
        # Resolved after run() so it lands in the export folder run() just used,
        # not the pre-export --export-dir/logs fallback.
        append_stage_log_line(
            _state,
            f"Stage 4 OK: {len(files)} files exported",
            preferred_export_dir=cli_args.export_dir,
        )
        sys.exit(0)
    except Stage4Error as _e:
        append_stage_log_line(_state, f"Stage 4 FAILED: {_e}", preferred_export_dir=cli_args.export_dir)
        print(str(_e), file=sys.stderr)
        sys.exit(1)
