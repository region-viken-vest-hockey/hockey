"""
Calendar fidelity comparison tool.

Scrapes a specific target week from each club's calendar source and produces a
structured JSON fidelity report, flagging:

  - Zero-event sources (scraper broken or hall closed?)
  - Date-range anomalies (events outside the target week)
  - Missing day-of-week patterns (weekdays with zero events despite others
    having bookings — potential scraper truncation)
  - Duplicate events (same date+name — double-scraping bug)

Output
------
A JSON report at ``.pipeline/compare/<iso_week>.json`` with per-source
fidelity entries and a console summary.

Usage::

  python3 -m tournament_scheduler.tools.calendar_compare \\
      --week 2026-10-05 [--work-dir .pipeline] [--input input.xlsx]
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

FIDELITY_REPORT_DIR = "compare"

# Day-of-week names for reporting
_DAY_NAMES_NB = ["Mandag", "Tirsdag", "Onsdag", "Torsdag", "Fredag", "Lørdag", "Søndag"]

# Sources that cannot be compared deterministically (require the Pi ScraperAgent)
_NON_DETERMINISTIC_ENGINES = {"bookup_spa", "forumbooking"}


# ---------------------------------------------------------------------------
# Fidelity types
# ---------------------------------------------------------------------------

def _source_fidelity(
    name: str,
    url: str,
    source_type: str,
    engine: str,
    week_start: str,
    week_end: str,
    events: list[dict[str, Any]],
    error: str = "",
    agent_required: bool = False,
) -> dict[str, Any]:
    """Build a fidelity result dict for one source."""

    # ---- Fidelity checks ----
    warnings: list[str] = []

    if agent_required:
        warnings.append("Krever Pi ScraperAgent for autentisert skraping — "
                        "kjør rvv-miniputt run for full kalenderdata")

    if error:
        warnings.append(f"Skraper-feil: {error}")
        return {
            "name": name,
            "url": url,
            "source_type": source_type,
            "engine": engine,
            "week_start": week_start,
            "week_end": week_end,
            "event_count": 0,
            "events": [],
            "warnings": warnings,
            "passed": False,
        }

    event_count = len(events)

    # Zero events
    if event_count == 0:
        warnings.append("Ingen hendelser funnet — skraper ødelagt eller hallen stengt?")

    # Date-range anomalies
    ws = datetime.strptime(week_start, "%Y-%m-%d")
    we = datetime.strptime(week_end, "%Y-%m-%d")
    out_of_range = 0
    for ev in events:
        try:
            ev_date = datetime.strptime(ev.get("date", ""), "%d.%m.%Y")
            if ev_date < ws or ev_date > we:
                out_of_range += 1
        except (ValueError, KeyError):
            out_of_range += 1

    if out_of_range > 0:
        warnings.append(
            f"{out_of_range} av {event_count} hendelser er utenfor "
            f"mål-uken {week_start} – {week_end}"
        )

    # Missing day-of-week patterns
    days_with_events: set[int] = set()
    for ev in events:
        try:
            ev_date = datetime.strptime(ev.get("date", ""), "%d.%m.%Y")
            days_with_events.add(ev_date.weekday())
        except (ValueError, KeyError):
            pass

    # Find weekdays that have 0 events when at least 3 other weekdays do
    if len(days_with_events) >= 3:
        missing_days = sorted(set(range(7)) - days_with_events)
        if missing_days:
            missing_names = ", ".join(_DAY_NAMES_NB[d] for d in missing_days)
            warnings.append(
                f"Ingen hendelser på: {missing_names} — "
                "kan tyde på at skraperen ikke dekker alle ukedager"
            )

    # Duplicates
    seen: set[tuple[str, str]] = set()
    dupes = 0
    for ev in events:
        key = (ev.get("date", ""), ev.get("name", ""))
        if key in seen:
            dupes += 1
        seen.add(key)
    if dupes > 0:
        warnings.append(f"{dupes} duplikate hendelser funnet")

    return {
        "name": name,
        "url": url,
        "source_type": source_type,
        "engine": engine,
        "week_start": week_start,
        "week_end": week_end,
        "event_count": event_count,
        "events": events,
        "warnings": warnings,
        "passed": len(warnings) == 0,
    }


# ---------------------------------------------------------------------------
# Source loading
# ---------------------------------------------------------------------------


def _load_sources(input_path: str, work_dir: str) -> list[dict[str, Any]]:
    """Load calendar sources from the standard input workbook."""
    input_file = Path(input_path)
    if not input_file.exists():
        print(f"Fant ikke {input_path}", file=sys.stderr)
        return []

    from tournament_scheduler.pipeline.input_workbook import load_workbook_config

    cfg = load_workbook_config(input_file)
    return cfg.get("sources", [])


def _lookup_strategy(club_name: str) -> dict[str, Any] | None:
    """Return strategy dict for a club name."""
    from tournament_scheduler.pipeline.scraper_strategies import (
        get_strategy,
        strategy_to_dict,
    )
    s = get_strategy(club_name)
    if s is None:
        return None
    return strategy_to_dict(s)


# ---------------------------------------------------------------------------
# Scraping helpers
# ---------------------------------------------------------------------------


def _scrape_deterministic(
    source_cfg: dict[str, Any],
    start_date: datetime,
    end_date: datetime,
) -> tuple[list[dict[str, Any]], str]:
    """Run deterministic scraper for a single source and one-week range."""
    from tournament_scheduler.pipeline.stage2_scraping import (
        _scrape_source,
    )
    from tournament_scheduler.pipeline.scraper_strategies import (
        get_strategy,
    )

    name = source_cfg.get("name", "?")
    strategy = get_strategy(name)
    engine = strategy.engine.value if strategy else None

    if engine in _NON_DETERMINISTIC_ENGINES:
        return [], "agent_required"

    # Use the stage2 helper (handles Outlook, StyledCalendar, ical)
    try:
        result = _scrape_source(
            source_cfg,
            start_date=start_date,
            end_date=end_date,
            calendar_cache=None,
        )
        if result.get("blocked"):
            return [], result.get("block_reason", "blokkert (ingen hendelser)")
        return result.get("events", []), ""
    except Exception as exc:
        return [], str(exc)


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------


def _generate_report(
    results: list[dict[str, Any]],
    week_start: str,
    work_dir: str,
) -> dict[str, Any]:
    """Build the full fidelity report and write to disk."""
    total_sources = len(results)
    passed = sum(1 for r in results if r.get("passed"))
    failed = total_sources - passed
    total_events = sum(r.get("event_count", 0) for r in results)

    report = {
        "report_type": "calendar_fidelity",
        "week_start": week_start,
        "generated_at": datetime.now().isoformat(),
        "summary": {
            "total_sources": total_sources,
            "passed": passed,
            "failed": failed,
            "total_events": total_events,
        },
        "sources": results,
    }

    # Write to .pipeline/compare/
    out_dir = Path(work_dir) / FIDELITY_REPORT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    safe_week = week_start.replace("-", "") if isinstance(week_start, str) else "report"
    out_path = out_dir / f"fidelity_{safe_week}.json"
    out_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    report["output_path"] = str(out_path)
    return report


def _print_summary(report: dict[str, Any]) -> None:
    """Print human-readable console summary."""
    summary = report["summary"]
    week = report["week_start"]
    print(f"\nKalender-fidelitetsrapport — uke starter {week}")
    print("=" * 60)
    print(f"  Totalt kilder:  {summary['total_sources']}")
    print(f"  Bestått:        {summary['passed']}  ✅")
    print(f"  Feil/advarsler: {summary['failed']}  ⚠️")
    print(f"  Totalt hendelser: {summary['total_events']}")
    print()

    for src in report.get("sources", []):
        name = src["name"]
        count = src["event_count"]
        status = "✅" if src["passed"] else "⚠️"
        print(f"  {status} {name}: {count} hendelser")
        for w in src.get("warnings", []):
            print(f"     ⚡ {w}")
        if not src.get("warnings"):
            print("     ✓ Ingen advarsler")

    print()
    print(f"  Rapport lagret: {report.get('output_path', '?')}")
    print()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def run_compare(
    *,
    week_start: str,
    input_path: str = "input.xlsx",
    work_dir: str = ".pipeline",
) -> dict[str, Any]:
    """Run fidelity comparison for all configured sources for a target week.

    Parameters
    ----------
    week_start:
        ISO date string (YYYY-MM-DD) for the Monday of the target week.
    input_path:
        Path to input.xlsx with source configuration.
    work_dir:
        Pipeline work directory for output.
    """
    sources = _load_sources(input_path, work_dir)
    if not sources:
        print("Ingen kilder funnet.", file=sys.stderr)
        return {}

    start = datetime.strptime(week_start, "%Y-%m-%d")
    # Ensure start is a Monday
    wd = start.weekday()
    if wd != 0:
        start = start - timedelta(days=wd)
        week_start = start.strftime("%Y-%m-%d")
    end = start + timedelta(days=6)
    week_end = end.strftime("%Y-%m-%d")
    end_dt = end.replace(hour=23, minute=59, second=59)

    print(f"Mål-uke: {week_start} (mandag) – {week_end} (søndag)", file=sys.stderr)

    results: list[dict[str, Any]] = []
    for src_cfg in sources:
        name = src_cfg.get("name", "?")
        url = src_cfg.get("url", "")
        source_type = src_cfg.get("type", "outlook")

        strategy = _lookup_strategy(name)
        engine = strategy.get("engine", "") if strategy else ""

        print(f"  Skraper {name} ...", file=sys.stderr)

        events, error = _scrape_deterministic(src_cfg, start, end_dt)
        agent_required = error == "agent_required"

        fidelity = _source_fidelity(
            name=name,
            url=url,
            source_type=source_type,
            engine=engine,
            week_start=week_start,
            week_end=week_end,
            events=events,
            error=error if not agent_required else "",
            agent_required=agent_required,
        )
        results.append(fidelity)

    report = _generate_report(results, week_start, work_dir)
    _print_summary(report)
    return report


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":  # pragma: no cover
    import argparse

    parser = argparse.ArgumentParser(
        description="Sammenlikn skrapede kalenderdata mot kildene for en gitt uke"
    )
    parser.add_argument(
        "--week", type=str, default="2026-10-05",
        help="ISO-dato for mål-ukens mandag (standard: 2026-10-05)"
    )
    parser.add_argument(
        "--input", type=str, default="input.xlsx",
        help="Plassering av input.xlsx (standard: input.xlsx)"
    )
    parser.add_argument(
        "--work-dir", type=str, default=".pipeline",
        help="Pipeline arbeidskatalog (standard: .pipeline)"
    )
    parser.add_argument(
        "--json-only", action="store_true",
        help="Bare JSON-utdata, ingen konsoll-oppsummering"
    )
    args = parser.parse_args()

    report = run_compare(
        week_start=args.week,
        input_path=args.input,
        work_dir=args.work_dir,
    )

    if args.json_only:
        print(json.dumps(report, indent=2, ensure_ascii=False))

    # Exit 1 if any source failed
    summary = report.get("summary", {})
    if summary.get("failed", 0) > 0:
        sys.exit(1)
    sys.exit(0)
