"""Stage 2 — deterministic calendar scraping.

For each configured calendar source:
  1. ``outlook`` / ``html`` sources use Playwright to load the page and extract
     events from the Outlook Web Calendar iframe (if present).
  2. ``ical`` / ``google`` sources use the deterministic ICAL scraper.
  3. Strategy-backed sources like BookUp, Sportello, and StyledCalendar route to
     dedicated deterministic scrapers.
  4. If a source returns zero events, record it separately as an empty calendar
     unless the scraper itself failed.

Source config format (inside the validated Stage 1 config)::

    "sources": [
        {
            "name": "Kongsberg ishall",
            "type": "outlook",
            "url": "https://kongsberghallen.no/webkalender/ishall/"
        },
        ...
    ]

If no ``sources`` key is present in the Stage 1 config, the stage writes an
empty ``sources`` list to the checkpoint (useful for tests / partial runs).
"""

from __future__ import annotations

import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from typing import Any

from ..club_registry import club_for_source_name, CLUB_REGISTRY
from ..models import CalendarEvent
from .cache_manager import ScrapedDataCache
from ..utils.calendar_cache import CalendarCache

from .not_started import NOT_STARTED_MESSAGE
from .scraper_strategies import get_strategy, requires_credentials, needs_llm_agent, get_deterministic_scraper_type
from .state import PipelineState, StageName, StageStatus
from .scraper_constants import (
    SOURCE_OUTLOOK, SOURCE_HTML, SOURCE_ICAL, SOURCE_GOOGLE,
    _BROWSER_SOURCE_TYPES, _ICAL_SOURCE_TYPES,
)
from .scraper_bookup import _run_bookup_scraper, _bookup_navigate_to_date, _parse_bookup_timegrid
from .scraper_brp_exigo import _run_brp_exigo_scraper
from .scraper_credentialed import _credentialed_scrape_months, _run_credentialed_bookup_or_outlook, _try_credentialed_scrape
from .scraper_event_helpers import _events_to_dicts, _group_events_by_club
from .scraper_forumbooking import _run_forumbooking_scraper
from .scraper_ical import _run_ical_scraper
from .scraper_outlook import _run_outlook_scraper, _parse_date_param_calendar, _parse_outlook_calendar
from .scraper_recovery import _blocked_sources_warning, _empty_sources_warning, _recovery_hint_for_source
from .scraper_styledcalendar import _run_styledcalendar_scraper
from .scraper_sportello import _run_sportello_scraper

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _make_source_result(
    name: str,
    url: str,
    source_type: str,
    events: list,
    event_count: int,
    blocked: bool,
    block_reason: str,
    llm_fallback: bool,
    *,
    skipped: bool = False,
    skip_reason: str | None = None,
    scraper_error: str | None = None,
    from_cache: bool = False,
) -> dict[str, Any]:
    """Build the canonical source-result dict used throughout stage 2.

    All callers (skipped sources, executor exception handler, and
    :func:`_scrape_source`) must go through this helper so the dict shape
    stays consistent.
    """
    result: dict[str, Any] = {
        "name": name,
        "url": url,
        "type": source_type,
        "events": events,
        "event_count": event_count,
        "blocked": blocked,
        "block_reason": block_reason,
        "llm_fallback": llm_fallback,
    }
    if skipped:
        result["skipped"] = True
    if skip_reason is not None:
        result["skip_reason"] = skip_reason
    if scraper_error is not None:
        result["scraper_error"] = scraper_error
    if from_cache:
        result["from_cache"] = True
    return result


def _active_age_groups(config: dict[str, Any]) -> list[str]:
    """Return configured age groups used to size Stage 2 event-count expectations."""
    configured = config.get("age_groups")
    if isinstance(configured, list):
        groups = [str(group).strip() for group in configured if str(group).strip()]
        if groups:
            return sorted(set(groups))

    teams = config.get("teams") or []
    if isinstance(teams, list):
        groups = [
            str(team.get("age_group", "")).strip()
            for team in teams
            if isinstance(team, dict) and str(team.get("age_group", "")).strip()
        ]
        if groups:
            return sorted(set(groups))

    return []


def _count_weekend_days(start_date: datetime, end_date: datetime) -> int:
    """Count Saturdays and Sundays in the inclusive scrape date range."""
    current: date = start_date.date()
    end: date = end_date.date()
    count = 0
    while current <= end:
        if current.weekday() >= 5:
            count += 1
        current += timedelta(days=1)
    return count


def _expected_min_events(config: dict[str, Any], start_date: datetime, end_date: datetime) -> int:
    """Estimate a lower-bound event count for each configured arena source.

    This is intentionally a coarse warning heuristic, not a hard validation rule.
    Across a normal RVV season, an active arena calendar should usually expose at
    least roughly one relevant booking for every four weekend days. The active
    age-group count scales that expectation down for tiny test/special-purpose
    configurations while keeping full RVV workbooks near the historical ~16 event
    lower bound over a September-April season.
    """
    age_group_count = len(_active_age_groups(config))
    if age_group_count == 0:
        return 0

    weekend_days = _count_weekend_days(start_date, end_date)
    if weekend_days == 0:
        return 0

    age_group_factor = min(1.0, max(0.25, age_group_count / 4.0))
    return max(1, round((weekend_days / 4.0) * age_group_factor))


def _apply_event_count_expectations(
    source_results: list[dict[str, Any]],
    *,
    config: dict[str, Any],
    start_date: datetime,
    end_date: datetime,
) -> list[dict[str, Any]]:
    """Attach per-source expectation metadata and return source-shape warnings."""
    expected_min = _expected_min_events(config, start_date, end_date)
    age_groups = _active_age_groups(config)
    weekend_days = _count_weekend_days(start_date, end_date)
    warnings: list[dict[str, Any]] = []

    for source in source_results:
        actual = int(source.get("event_count") or 0)
        status = "not_applicable" if expected_min <= 0 or source.get("skipped") else "ok"
        message = ""
        if status == "ok" and actual < expected_min and not source.get("blocked"):
            status = "low"
            message = (
                f"{source.get('name', 'ukjent kilde')}: {actual} hendelser funnet, "
                f"forventet minst ca. {expected_min} for perioden."
            )
        elif status == "ok" and not source.get("blocked"):
            message = _suspicious_event_shape_message(source, weekend_days=weekend_days)
            if message:
                status = "suspicious"

        if message:
            warnings.append({
                "name": source.get("name", "ukjent kilde"),
                "event_count": actual,
                "expected_min_events": expected_min,
                "status": status,
                "message": message,
            })

        source["event_expectation"] = {
            "status": status,
            "event_count": actual,
            "expected_min_events": expected_min,
            "age_group_count": len(age_groups),
            "weekend_days": weekend_days,
            "basis": "weekend_days/4 scaled by active age-group count plus source-shape sanity checks",
        }
        if message:
            source["event_expectation"]["message"] = message

    return warnings


def _suspicious_event_shape_message(source: dict[str, Any], *, weekend_days: int) -> str:
    """Return a warning for event shapes that look like scraper artifacts."""
    source_type = str(source.get("type", "")).lower()
    if source_type in _ICAL_SOURCE_TYPES:
        return ""

    events = source.get("events") or []
    actual = int(source.get("event_count") or len(events))
    if actual < 20 or not isinstance(events, list):
        return ""

    dates: list[date] = []
    midnight_count = 0
    generic_booked_count = 0
    for event in events:
        if not isinstance(event, dict):
            continue
        raw_datetime = str(event.get("datetime") or "")
        raw_date = str(event.get("date") or "")
        parsed_dt: datetime | None = None
        try:
            if "T" in raw_datetime:
                parsed_dt = datetime.fromisoformat(raw_datetime.replace("Z", "+00:00"))
            elif raw_date:
                parsed_dt = datetime.strptime(raw_date, "%d.%m.%Y")
        except ValueError:
            parsed_dt = None
        if parsed_dt:
            dates.append(parsed_dt.date())
            if parsed_dt.hour == 0 and parsed_dt.minute == 0:
                midnight_count += 1
        name = str(event.get("name") or "").strip().lower()
        if name in {"booket", "booking"}:
            generic_booked_count += 1

    if not dates:
        return ""

    distinct_dates = len(set(dates))
    first_day_count = sum(1 for parsed_date in dates if parsed_date.day == 1)
    weekend_count = sum(1 for parsed_date in dates if parsed_date.weekday() >= 5)
    generic_ratio = generic_booked_count / actual
    midnight_ratio = midnight_count / actual
    first_day_ratio = first_day_count / actual

    name = source.get("name", "ukjent kilde")
    if generic_ratio >= 0.9 and midnight_ratio >= 0.9:
        return (
            f"{name}: {actual} hendelser funnet, men nesten alle heter 'Booket' "
            "og ligger kl. 00:00; kalenderen ser ut til å mangle innloggede detaljer."
        )
    if distinct_dates <= 8 and first_day_ratio >= 0.8:
        return (
            f"{name}: {actual} hendelser funnet, men bare {distinct_dates} datoer "
            "og de fleste ligger på den 1. i måneden; dette ligner en datoparsingsfeil."
        )
    if weekend_days >= 20 and weekend_count < 4:
        return (
            f"{name}: {actual} hendelser funnet, men bare {weekend_count} helgehendelser; "
            "kontroller at full arena-/helgekalender ble lest."
        )
    return ""


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class Stage2Error(RuntimeError):
    """Raised when one or more sources block the pipeline."""

    def __init__(self, blocked: list[dict[str, Any]]) -> None:
        self.blocked = blocked
        names = ", ".join(b["name"] for b in blocked)
        super().__init__(f"Stage 2 blokkert: {names}")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def run(
    config: dict[str, Any],
    state: PipelineState,
    start_date: datetime,
    end_date: datetime,
    *,
    strict: bool = True,
    allow_missing_sources: bool = False,
    max_workers: int = 4,
    force_refresh: bool = False,
) -> dict[str, Any]:
    """Run Stage 2 scraping for all sources listed in *config*.

    Sources are scraped in parallel via :class:`~concurrent.futures.ThreadPoolExecutor`
    because each source creates its own Playwright browser context (or uses HTTP-only
    iCal feeds) — there is no shared browser state.

    Before scraping, each source is checked against the unified
    :class:`~tournament_scheduler.pipeline.cache_manager.ScrapedDataCache`. If a
    source has a fresh (non-stale), non-blocked, non-empty cache entry for the
    same date range, the cached events are reused instead of re-scraping. After
    scraping, fresh results are written back to the cache so subsequent runs can
    benefit.

    Parameters
    ----------
    config:
        Validated Stage 1 config dict (from :func:`stage1_config.run`).
    state:
        :class:`PipelineState` managing the work directory.
    start_date / end_date:
        Date range for scraping.
    strict:
        If ``True``, raise :class:`Stage2Error` when any source is blocked.
    allow_missing_sources:
        If ``True``, keep partial scrape results as a successful checkpoint and
        continue downstream even when some sources are blocked.
    max_workers:
        Number of worker threads for the executor. Default 4.
    force_refresh:
        If ``True``, ignore the cache and re-scrape every source.

    Returns
    -------
    dict
        Checkpoint data with per-source results.
    """
    state._set_status(StageName.SCRAPING, StageStatus.RUNNING)

    sources: list[dict[str, Any]] = config.get("sources", [])
    teams = config.get("teams")

    if isinstance(teams, list) and not teams:
        result = {
            "sources": [],
            "events_by_club": {},
            "blocked": [],
            "empty_sources": [],
            "cached": [],
            "start_date": start_date.strftime("%Y-%m-%d"),
            "end_date": end_date.strftime("%Y-%m-%d"),
            "skipped": True,
            "skip_reason": NOT_STARTED_MESSAGE,
            "warning": f"Skraping hoppet over: {NOT_STARTED_MESSAGE}",
        }
        state.write_stage(StageName.SCRAPING, result, status=StageStatus.DONE)
        return result

    if not sources:
        reason = (
            "Ingen kalenderkilder er konfigurert. Legg til kilder i input.xlsx-arket 'Kilder' "
            "for a hente kalenderdata (f.eks. Kongsberg ishall, Skien o.l.). Uten kalenderdata "
            "kan ikke pipelinen planlegge rundt faktiske bookinger og vil foresla fantasidatoer."
        )
        if strict:
            state.write_stage(StageName.SCRAPING, {}, status=StageStatus.FAILED)
            raise Stage2Error([{"name": "(ingen kilder)", "reason": reason}])
        result: dict[str, Any] = {
            "sources": [],
            "blocked": [],
            "start_date": start_date.strftime("%Y-%m-%d"),
            "end_date": end_date.strftime("%Y-%m-%d"),
            "warning": reason,
        }
        state.write_stage(StageName.SCRAPING, result, status=StageStatus.DONE)
        return result

    # --- Split sources into cache hits and sources that need (re-)scraping ---
    cache = ScrapedDataCache(work_dir=state.work_dir)
    calendar_cache = CalendarCache(work_dir=state.work_dir)
    cache_data = cache.read()
    cache_meta = cache_data.get("_meta", {})
    cache_sources = cache_data.get("sources", {})
    date_range_matches = (
        cache_meta.get("start_date") == config.get("start_date")
        and cache_meta.get("end_date") == config.get("end_date")
    )

    sources_to_scrape: list[dict[str, Any]] = []
    source_results: list[dict[str, Any]] = []
    cached_names: list[str] = []

    for source_cfg in sources:
        name = source_cfg.get("name", "ukjent kilde")
        url = source_cfg.get("url", "").strip()
        if not url:
            source_results.append(_make_source_result(
                name=name,
                url="",
                source_type=source_cfg.get("type", SOURCE_OUTLOOK).lower(),
                events=[],
                event_count=0,
                blocked=False,
                block_reason="",
                llm_fallback=False,
                skipped=True,
                skip_reason="Tom URL — kilden er deaktivert i input.xlsx.",
            ))
            continue
        entry = cache_sources.get(name)
        _source_type_for_match = source_cfg.get("type", SOURCE_OUTLOOK).lower()
        _club_name_for_match = club_for_source_name(name)
        _location_filter_for_match = (
            CLUB_REGISTRY[_club_name_for_match].location_filter
            if _club_name_for_match and _club_name_for_match in CLUB_REGISTRY
            else None
        )
        if (
            not force_refresh
            and date_range_matches
            and entry
            and entry.get("events")
            and not entry.get("blocked")
            and not cache.is_stale(name)
            and cache.is_config_match(name, url, _source_type_for_match, _location_filter_for_match)
        ):
            _cached_events = entry.get("events", [])
            _scraped_at = entry.get("scrape_timestamp", "")
            _cache_age_hours = None
            if _scraped_at:
                try:
                    _parsed = datetime.fromisoformat(str(_scraped_at))
                    _now = datetime.now(_parsed.tzinfo) if _parsed.tzinfo else datetime.now()
                    _cache_age_hours = round((_now - _parsed).total_seconds() / 3600, 1)
                except Exception:
                    _cache_age_hours = None
            _cached_result = _make_source_result(
                name=name,
                url=source_cfg.get("url", entry.get("url", "")),
                source_type=source_cfg.get("type", SOURCE_OUTLOOK).lower(),
                events=_cached_events,
                event_count=entry.get("event_count", len(_cached_events)),
                blocked=False,
                block_reason="",
                llm_fallback=False,
                from_cache=True,
            )
            if _cache_age_hours is not None:
                _cached_result["cache_age_hours"] = _cache_age_hours
            source_results.append(_cached_result)
            cached_names.append(name)
        else:
            sources_to_scrape.append(source_cfg)

    blocked: list[dict[str, Any]] = []
    empty_sources: list[dict[str, Any]] = []

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_source = {
            executor.submit(
                _scrape_source,
                source_cfg,
                start_date=start_date,
                end_date=end_date,
                calendar_cache=calendar_cache,
            ): source_cfg
            for source_cfg in sources_to_scrape
        }
        for future in as_completed(future_to_source):
            source_cfg = future_to_source[future]
            try:
                source_result = future.result()
            except Exception as exc:
                source_result = _make_source_result(
                    name=source_cfg.get("name", "ukjent kilde"),
                    url=source_cfg.get("url", ""),
                    source_type=source_cfg.get("type", SOURCE_OUTLOOK),
                    events=[],
                    event_count=0,
                    blocked=True,
                    block_reason=f"Scraper krasjet: {exc}",
                    llm_fallback=False,
                    scraper_error=str(exc),
                )
            source_results.append(source_result)
            if source_result.get("blocked"):
                blocked.append({"name": source_cfg.get("name", "?"), **source_result})
            if source_result.get("empty_calendar"):
                empty_sources.append({"name": source_cfg.get("name", "?"), **source_result})

    event_expectation_warnings = _apply_event_count_expectations(
        source_results,
        config=config,
        start_date=start_date,
        end_date=end_date,
    )

    checkpoint: dict[str, Any] = {
        "sources": source_results,
        "events_by_club": _group_events_by_club(source_results),
        "blocked": [b["name"] for b in blocked],
        "empty_sources": [e["name"] for e in empty_sources],
        "cached": cached_names,
        "start_date": start_date.strftime("%Y-%m-%d"),
        "end_date": end_date.strftime("%Y-%m-%d"),
        "event_expectation_warnings": event_expectation_warnings,
    }

    status = StageStatus.DONE if (not blocked or allow_missing_sources) else StageStatus.FAILED
    checkpoint["checkpoint_path"] = str(state.checkpoint_path(StageName.SCRAPING))
    warnings: list[str] = []
    if blocked:
        warnings.append(
            _blocked_sources_warning(
                blocked,
                state,
                allow_missing_sources=allow_missing_sources,
            )
        )
    if empty_sources:
        warnings.append(_empty_sources_warning(empty_sources, state))
    if warnings:
        checkpoint["warning"] = " ".join(warnings)

    state.write_stage(StageName.SCRAPING, checkpoint, status=status)

    # Persist freshly-scraped results to the unified cache for future runs
    cache.build_from_checkpoint(config, checkpoint)

    if blocked and strict and not allow_missing_sources:
        raise Stage2Error(blocked)

    return checkpoint


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _scrape_source(
    source_cfg: dict[str, Any],
    *,
    start_date: datetime,
    end_date: datetime,
    calendar_cache: CalendarCache | None = None,
) -> dict[str, Any]:
    """Scrape a single source deterministically.

    Dispatch is driven by the :class:`~scraper_strategies.CalendarEngine`
    declared in ``STRATEGIES`` via :func:`~scraper_strategies.get_deterministic_scraper_type`:

    * ``"styledcalendar"`` (e.g. Bærum/Jutul) — calls ``_run_styledcalendar_scraper``
    * ``"sportello"``     (e.g. Holmen) — calls ``_run_sportello_scraper``
    * ``"bookup"``         (e.g. Tønsberg, Sandefjord) — calls ``_run_bookup_scraper``
    * ``"forumbooking"``   (e.g. Jar) — calls ``_run_forumbooking_scraper``
    * ``"brp_exigo"``      (e.g. Skien) — calls ``_run_brp_exigo_scraper``
    * sources not in ``STRATEGIES`` fall back to ``source_type``-based routing:

      * ``outlook`` / ``html`` — Playwright Outlook-iframe scraper
      * ``ical`` / ``google``  — HTTP iCal scraper

    If the deterministic scrape returns zero events and the source strategy
    requires credentials, the function automatically retries with environment-
    variable credentials injected via Playwright login.

    Direct deterministic sources that still end up empty are recorded as
    ``empty_calendar=True`` so operators can distinguish a truly empty
    calendar from a scraper crash. Strategy-backed browser sources that need
    the LLM agent still surface as blocked with ``llm_fallback=True``.
    """
    name = source_cfg.get("name", "ukjent kilde")
    url = source_cfg.get("url", "")
    source_type = source_cfg.get("type", SOURCE_OUTLOOK).lower()

    result: dict[str, Any] = _make_source_result(
        name=name,
        url=url,
        source_type=source_type,
        events=[],
        event_count=0,
        blocked=False,
        block_reason="",
        llm_fallback=False,
    )

    # --- Run the deterministic scraper ---
    events: list[CalendarEvent] = []
    scraper_error: str = ""
    deterministic_raised: bool = False
    credentialed_required: bool = False

    try:
        # Dispatch is driven by the CalendarEngine declared in scraper_strategies.
        # get_deterministic_scraper_type() returns a string token for sources
        # registered in STRATEGIES, or None for sources that only appear in
        # the generic _BROWSER_SOURCE_TYPES / _ICAL_SOURCE_TYPES fallbacks.
        _strategy = None if source_type in _ICAL_SOURCE_TYPES else get_strategy(name)
        _scraper_type = get_deterministic_scraper_type(_strategy) if _strategy is not None else None

        # BookUp sources can expose a tiny public placeholder calendar while the
        # useful arena schedule is behind login. If credentials are declared,
        # the public scrape is not trustworthy enough to use as fallback: a
        # failed login must surface as blocked/manual-recovery-needed instead
        # of silently accepting a handful of generic "Booket" entries.
        credentialed_required = _strategy is not None and requires_credentials(_strategy)
        if credentialed_required:
            events, _cred_error = _try_credentialed_scrape(
                name, url, start_date, end_date, calendar_cache
            )
            if events:
                result["credentialed"] = True
            else:
                scraper_error = _cred_error or (
                    f"Kilden '{name}' krever innlogging, men innlogget skraping "
                    "ga ingen kalenderhendelser. Manuell innlogging eller "
                    "recovery-inject kan være nødvendig."
                )
                deterministic_raised = True

        if events:
            pass
        elif credentialed_required:
            pass
        elif _scraper_type == "styledcalendar":
            events, _ = _run_styledcalendar_scraper(name, start_date, end_date)
        elif _scraper_type == "sportello":
            events, _ = _run_sportello_scraper(url, name, start_date, end_date)
        elif _scraper_type == "bookup":
            events, _ = _run_bookup_scraper(url, name, start_date, end_date)
        elif _scraper_type == "forumbooking":
            events, _ = _run_forumbooking_scraper(url, name, start_date, end_date)
        elif _scraper_type == "brp_exigo":
            events, _ = _run_brp_exigo_scraper(url, name, start_date, end_date)
        elif source_type in _BROWSER_SOURCE_TYPES:
            events, _ = _run_outlook_scraper(url, name, start_date, end_date, calendar_cache)
        elif source_type in _ICAL_SOURCE_TYPES:
            # Look up any per-source location filter registered in CLUB_REGISTRY
            _club_name = club_for_source_name(name)
            _location_filter = (
                CLUB_REGISTRY[_club_name].location_filter
                if _club_name and _club_name in CLUB_REGISTRY
                else None
            )
            events = _run_ical_scraper(url, name, start_date, end_date, source_type, calendar_cache, location_filter=_location_filter)
            result["location_filter"] = _location_filter
        else:
            scraper_error = f"Ukjent kildetype '{source_type}'."
            deterministic_raised = True
    except Exception as exc:  # noqa: BLE001
        scraper_error = str(exc)
        deterministic_raised = True

    if scraper_error:
        result["scraper_error"] = scraper_error

    # --- If deterministic succeeded but returned 0 events, try credentialed fallback ---
    # Do NOT fall through to credentialed scrape when the deterministic scraper raised an
    # exception (e.g. network error, Playwright crash) — an exception means we don't know
    # whether the source has events; only a clean zero-event return warrants the fallback.
    if not events and not deterministic_raised:
        events, cred_error = _try_credentialed_scrape(
            name, url, start_date, end_date, calendar_cache
        )
        if cred_error:
            scraper_error = scraper_error or cred_error

    # --- If still no events, assess LLM fallback viability ---
    if not events:
        strategy = get_strategy(name)
        if deterministic_raised:
            block_reason = (
                f"Kilde '{name}' feilet under skraping -- "
                "kalenderen kunne ikke leses på en trygg måte."
            )
            recovery_hint = _recovery_hint_for_source(name)
            result["blocked"] = True
            result["block_reason"] = f"{block_reason} {recovery_hint}".strip()
            result["recovery_hint"] = recovery_hint

            # Mark for browser/LLM recovery when the source is either known to
            # need the LLM agent or it requires a login that deterministic
            # scraping could not complete (for example MFA/manual approval).
            if strategy and (needs_llm_agent(strategy) or credentialed_required):
                result["llm_fallback"] = True
                result["llm_strategy"] = {
                    "engine": strategy.engine.value,
                    "url": strategy.url,
                    "initial_navigation": strategy.initial_navigation,
                    "credential_env_vars": strategy.credential_env_vars,
                    "month_selector": strategy.month_selector,
                    "event_pattern": strategy.event_pattern,
                }
        elif strategy and needs_llm_agent(strategy):
            block_reason = (
                f"Kilde '{name}' returnerte 0 hendelser -- "
                "den browserstyrte recovery-veien må brukes for denne kalenderen."
            )
            recovery_hint = _recovery_hint_for_source(name)
            result["blocked"] = True
            result["block_reason"] = f"{block_reason} {recovery_hint}".strip()
            result["recovery_hint"] = recovery_hint
            result["llm_fallback"] = True
            result["llm_strategy"] = {
                "engine": strategy.engine.value,
                "url": strategy.url,
                "initial_navigation": strategy.initial_navigation,
                "credential_env_vars": strategy.credential_env_vars,
                "month_selector": strategy.month_selector,
                "event_pattern": strategy.event_pattern,
            }
        else:
            result["empty_calendar"] = True
            result["empty_reason"] = (
                f"Kilde '{name}' returnerte 0 hendelser i perioden. "
                "Det ser ut som en tom offentlig kalender, ikke en skrapefeil."
            )

    result["events"] = _events_to_dicts(events, club_name=club_for_source_name(name))
    result["event_count"] = len(events)
    return result



# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":  # pragma: no cover
    import argparse
    import sys

    parser = argparse.ArgumentParser(
        description="Stage 2: deterministic calendar scraping"
    )
    parser.add_argument(
        "--work-dir", default=".pipeline", help="Pipeline work directory"
    )
    parser.add_argument(
        "--non-strict", action="store_true",
        help="Don't raise on blocked sources — write checkpoint anyway"
    )
    parser.add_argument(
        "--allow-missing-sources", action="store_true",
        help="Mark blocked sources as an operator-approved skip and keep partial results"
    )
    parser.add_argument(
        "--force-refresh", action="store_true",
        help="Ignore the unified scrape cache and re-scrape every source"
    )
    cli_args = parser.parse_args()

    from .run_log_paths import append_stage_log_line  # noqa: E402
    from .state import PipelineState, StageName  # noqa: E402
    from .stage1_config import load_effective_config  # noqa: E402
    from datetime import datetime as _dt  # noqa: E402

    _state = PipelineState(cli_args.work_dir)
    _cfg = load_effective_config(_state)
    if not _cfg:
        print("Stage 1 checkpoint not found -- run Stage 1 first.", file=sys.stderr)
        sys.exit(1)

    _start = _dt.strptime(_cfg["start_date"], "%Y-%m-%d")
    _end = _dt.strptime(_cfg["end_date"], "%Y-%m-%d")

    try:
        _result = run(
            _cfg, _state, _start, _end,
            strict=not cli_args.non_strict,
            allow_missing_sources=cli_args.allow_missing_sources,
            force_refresh=cli_args.force_refresh,
        )
        n_sources = len(_result.get("sources", []))
        blocked = _result.get("blocked", [])
        cached = _result.get("cached", [])
        cached_sources = [s for s in _result.get("sources", []) if isinstance(s, dict) and s.get("from_cache")]
        if cached_sources:
            ages = [s.get("cache_age_hours") for s in cached_sources if isinstance(s.get("cache_age_hours"), (int, float))]
            age_text = f", cache alder ~{max(ages):.1f}t" if ages else ", fra cache"
        else:
            age_text = ""
        if _result.get("skipped"):
            print(f"Stage 2 SKIPPED -- {_result.get('skip_reason') or 'ingen skraping nødvendig'}")
        else:
            print(f"Stage 2 OK -- {n_sources} kilder skannet, {len(cached)} fra cache{age_text}, {len(blocked)} blokkert")
        if _result.get("warning"):
            print(_result["warning"])
        append_stage_log_line(
            _state,
            f"Stage 2 OK: {n_sources} sources scanned, {len(cached)} from cache, {len(blocked)} blocked",
        )
        sys.exit(0)
    except Stage2Error as _e:
        append_stage_log_line(_state, f"Stage 2 FAILED: {_e}")
        print(str(_e), file=sys.stderr)
        sys.exit(1)
