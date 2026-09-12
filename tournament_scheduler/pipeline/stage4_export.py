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

The module is split into focused siblings kept under the repo's
300-line-per-file guideline: ``stage4_export_errors`` (``Stage4Error``),
``stage4_export_timing`` (build-timestamp resolution, export pruning),
``stage4_export_xlsx`` (reproducible XLSX normalization),
``stage4_export_not_started`` (placeholder exports before planning starts),
``stage4_export_manual_schedule`` (the "Må planlegges manuelt" view), and
``stage4_export_verification`` (the hard-verification problem builder).
This file owns the ``run()`` orchestration itself and the CLI entry point.
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from ..arena_conflicts import find_arena_interval_collisions
from ..planning_contract import extract_candidate, verify_candidate
from .fingerprints import stable_payload_sha256
from ..excel.plan_exporter import SeasonPlanExporter
from ..ical.ical_exporter import ICalExporter
from ..csv.csv_exporter import CsvExporter
from ..html.html_exporter import HtmlExporter
from .stage1_config import load_effective_config
from .state import PipelineState, StageName, StageStatus
from .stage4_helpers import _dict_to_plan
from .calendar_viewer import generate_html as _generate_calendars_html
from .input_viewer import generate_html as _generate_input_html
from .activity_viewer import generate_activity_artifacts as _generate_activity_artifacts
from .not_started import NOT_STARTED_MESSAGE
from ..review.review_packet_exporter import ReviewPacketExporter
from ..spond.spond_exporter import SpondExporter
from .stage4_export_errors import Stage4Error
from .stage4_export_timing import (
    DEFAULT_EXPORT_DIR,
    DEFAULT_BASENAME,
    _TIMESTAMP_DIR_RE,
    _prune_old_exports,
    _resolve_build_timestamp,
)
from .stage4_export_xlsx import _normalize_export_workbooks
from .stage4_export_not_started import _write_not_started_exports
from .stage4_export_manual_schedule import (
    MANUAL_SCHEDULE_FILENAME,
    MANUAL_SCHEDULE_CATEGORIES,
    _manual_schedule_html,
)
from .stage4_export_verification import _build_export_verification_problem

logger = logging.getLogger(__name__)

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

    effective_config: dict[str, Any] = {}
    try:
        effective_config = load_effective_config(state)
    except Exception:
        effective_config = {}

    # Hard verification boundary (issue #309): whatever candidate is about to
    # be materialized into every export format must independently re-verify
    # clean *here*, at the one chokepoint every caller of this function goes
    # through -- the interactive pipeline's own pre-export gate
    # (`cli.pipeline_orchestrator.verification._assert_hard_verification_before_export`)
    # only covers the guarded `run`/`run --interactive` CLI paths, not a
    # direct `python3 -m tournament_scheduler.pipeline.stage4_export`
    # invocation or a future caller that resumes/regenerates an export from
    # an on-disk checkpoint that was mutated (e.g. by the Stage 3 v2
    # optimizer) after it was last verified. Self-consistency checks
    # (duplicate participation, duplicate team in a tournament, age-group
    # mismatch) need no `problem` and always run. The problem-dependent hard
    # checks (arena interval conflicts, banned/locked dates, excluded host
    # clubs, capacity, window bounds) previously only ran inside the
    # evidence bundle's *post-export* re-verification
    # (`cli.pipeline_orchestrator.verification._write_run_evidence_bundle`) --
    # too late to block a bad artifact from reaching disk. Reconstructing
    # the same `planning_problem` Stage 3 used and passing it here closes
    # that bypass: any caller that reaches this `run()`, guarded CLI path or
    # not, now gets the full verifier at the point that actually matters.
    export_problem = _build_export_verification_problem(effective_config, state)
    try:
        export_candidate = extract_candidate(plan_checkpoint)
    except ValueError:
        export_candidate = dict(plan_dict)
    export_verify_result = verify_candidate(export_candidate, export_problem)
    export_fingerprint = stable_payload_sha256(export_candidate.get("tournaments", []))
    if not export_verify_result.get("ok", True):
        violations = export_verify_result.get("violations", [])
        violation_summary = "; ".join(
            f"{v.get('code')}: {v.get('message')}" for v in violations
        )
        reason = (
            "Refusing to export: candidate fails hard verification immediately "
            f"before serialization ({len(violations)} violation(s)): {violation_summary}"
        )
        state.write_stage(
            StageName.EXPORT,
            {
                "generated_at": _resolve_build_timestamp(build_timestamp).isoformat(),
                "output_files": {},
                "errors": [reason],
                "verify_result": export_verify_result,
                "export_fingerprint": export_fingerprint,
            },
            status=StageStatus.FAILED,
        )
        _progress("Eksport avbrutt: kandidaten feiler hard verifisering")
        raise Stage4Error(reason)

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
    # issue #314: a fresh recomputation is authoritative even when it comes
    # back empty. A truthiness fallback here (`derived_collisions or
    # stored`) cannot tell "not recomputed" from "recomputed and zero", so a
    # freshly verified `[]` used to silently resurrect a stale collision
    # list carried over from an earlier candidate/baseline.
    stored_collisions = find_arena_interval_collisions(plan.tournaments, round_length_for_age_group)
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
        item["category"] = item.get("category", "arena_collision")
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
                "category": "manual_calendar_verification",
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
    # issue #266 P0: a club x age-group hosting obligation the planner
    # could not resolve after exhausting every legitimate availability tier
    # -- no tournament exists to attach this to (that's the point: nothing
    # was placed), so most tournament-shaped fields are left blank. The
    # club must make room for a slot; do not treat another club's hosting
    # as satisfying this obligation.
    unresolved_hosting_entries: list[dict[str, str]] = []
    for item in getattr(plan, "unresolved_hosting_obligations", None) or []:
        club = str(item.get("club", "") or "")
        age_group = str(item.get("age_group", "") or "")
        reason = str(item.get("reason", "") or "")
        unresolved_hosting_entries.append(
            {
                "type": "MANUAL PLACEMENT REQUIRED — manglende vertskap",
                "category": item.get("category", "manual_hosting_obligation"),
                "date": "",
                "arena": "",
                "host_club": club,
                "age_group": age_group,
                "tournament_id": "",
                "interval": "",
                "conflicting_tournament_id": "",
                "conflicting_age_group": "",
                "conflicting_interval": "",
                "message": (
                    "MANUAL PLACEMENT REQUIRED. "
                    f"Club: {club}. Age group: {age_group}. "
                    f"Reason: {reason or 'required hosting obligation has no verified feasible automatic slot'}. "
                    f"Action: {club}/RVV must provide or confirm a suitable {age_group} tournament slot."
                ),
            }
        )
    # Genuine external calendar conflicts the planner/optimizer couldn't
    # route around (non-blocking, see planning_contract.verify_candidate's
    # manual_external_conflict_placements) -- surfaced the same way.
    external_conflict_entries: list[dict[str, str]] = []
    for item in getattr(plan, "unresolved_external_conflicts", None) or []:
        external_conflict_entries.append(
            {
                "type": "MANUAL PLACEMENT REQUIRED — ekstern kalenderkonflikt",
                "category": item.get("category", "manual_external_conflict"),
                "date": str(item.get("date", "") or ""),
                "arena": "",
                "host_club": str(item.get("host_club", "") or ""),
                "age_group": str(item.get("age_group", "") or ""),
                "tournament_id": str(item.get("tournament_id", "") or ""),
                "interval": "",
                "conflicting_tournament_id": "",
                "conflicting_age_group": "",
                "conflicting_interval": "",
                "message": str(item.get("reason", "") or ""),
            }
        )
    # Participation-target deviations (over- or under-target) are team-level
    # planning-quality signals, not ice-time/booking work -- they do not
    # represent a tournament that must be inserted into an arena calendar.
    # They stay out of manual_schedule.html entirely and remain visible via
    # plan.unresolved_participation_shortfalls / the season plan report
    # (rules_report.py) instead.
    candidate_entries = (
        collision_entries + manual_host_entries + unresolved_hosting_entries + external_conflict_entries
    )
    manual_entries = [entry for entry in candidate_entries if entry.get("category") in MANUAL_SCHEDULE_CATEGORIES]
    # Fresh recomputation always wins, including the empty case -- a stale
    # stored collision list must not survive a verified zero-collision plan.
    plan.arena_day_collisions = collision_entries
    if collision_entries:
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

    pruned_exports: list[str] = []
    if not errors and _TIMESTAMP_DIR_RE.match(primary_export_path.name):
        try:
            pruned_exports = _prune_old_exports(primary_export_path.parent)
            if pruned_exports:
                _progress(f"Fjernet {len(pruned_exports)} eldre eksport(er): {', '.join(pruned_exports)}")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"Opprydding av gamle eksporter feilet: {exc}")

    checkpoint: dict[str, Any] = {
        "generated_at": generated_at,
        "input_path": input_path,
        "export_dir": str(primary_export_path),
        "output_files": output_files,
        "errors": errors,
        "arena_day_collisions": list(plan.arena_day_collisions or []),
        "manual_booking_count": len(manual_host_entries),
        "pruned_exports": pruned_exports,
        "verify_result": export_verify_result,
        "export_fingerprint": export_fingerprint,
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
