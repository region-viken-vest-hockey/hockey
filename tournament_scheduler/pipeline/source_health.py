"""Calendar source health capability.

Turns Stage 2 scraping output and the unified scrape cache into an
inspectable, agent-friendly capability: one :class:`CapabilityResult` per
configured calendar source, covering reachability/block state, event count
versus expectation, cache age, parser/strategy, source-integrity/coverage
proof, and a comparison against the previous scrape (the last generation of
history the cache retains) to flag sources that went suspiciously sparse or
duplicate-heavy.

This module only reads Stage 2's checkpoint and the unified cache — it does
not scrape anything itself. Recovery remains the existing composable
``rvv-miniputt recovery-targets`` / ``recovery-inject`` / ``scrape`` /
``scrape-llm`` commands; this capability tells an operator *which* source
needs one of those and *why*.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping

from .cache_manager import ScrapedDataCache
from .capability_result import CapabilityResult
from .source_integrity import (
    INTEGRITY_COMPLETE,
    duplicate_ratio,
    evaluate_source_integrity,
    hardcoded_value_signal,
    non_schedulable_arena_signal,
)
from .state import PipelineState, StageName

# A source's cache is considered worth refreshing once it is older than this,
# even if nothing else looks wrong with it.
_STALE_CACHE_SECONDS = 24 * 3600

# Coarse heuristic threshold, not a hard validation rule (mirrors the
# expected-event-count heuristic in stage2_scraping.py).
_DUPLICATE_RATIO_WARNING_THRESHOLD = 0.3

_CONFIDENCE_BY_STATUS = {"ok": 1.0, "warning": 0.6, "blocked": 0.2, "failed": 0.0}


def _cache_age_seconds(scrape_timestamp: str) -> float | None:
    if not scrape_timestamp:
        return None
    try:
        scraped_at = datetime.fromisoformat(scrape_timestamp)
    except ValueError:
        return None
    now = datetime.now(scraped_at.tzinfo) if scraped_at.tzinfo else datetime.now()
    return (now - scraped_at).total_seconds()


def _format_age(seconds: float | None) -> str:
    if seconds is None:
        return "ukjent"
    if seconds < 0:
        seconds = 0
    if seconds < 60:
        return f"{int(seconds)}s"
    if seconds < 3600:
        return f"{int(seconds // 60)}m"
    if seconds < 86400:
        return f"{int(seconds // 3600)}t"
    return f"{int(seconds // 86400)}d"


def _strategy_label(source_name: str) -> str | None:
    """Best-effort human label for the scraper strategy backing *source_name*."""
    try:
        from ..club_registry import club_for_source_name
        from .scraper_strategies import get_deterministic_scraper_type, get_strategy, needs_llm_agent
    except ImportError:
        return None

    club = club_for_source_name(source_name) or source_name
    strategy = get_strategy(club)
    if strategy is None:
        return None
    if needs_llm_agent(strategy):
        return "llm_agent"
    return get_deterministic_scraper_type(strategy) or strategy.engine.value


def compute_source_health(work_dir: str = ".pipeline") -> list[CapabilityResult]:
    """Return one :class:`CapabilityResult` per configured source.

    Reads the Stage 2 checkpoint (event counts, expectations, block reasons)
    and the unified scrape cache (timestamps, previous-snapshot comparison).
    Returns an empty list when Stage 2 has not produced a checkpoint yet.
    """
    state = PipelineState(work_dir)
    checkpoint = state.read_stage(StageName.SCRAPING)
    sources = checkpoint.get("sources", []) if isinstance(checkpoint, dict) else []
    if not sources:
        return []

    cache = ScrapedDataCache(work_dir=work_dir).read()
    cache_sources = cache.get("sources", {}) if isinstance(cache, dict) else {}
    previous_sources = cache.get("previous_sources", {}) if isinstance(cache, dict) else {}

    return [_source_health_result(source, cache_sources, previous_sources) for source in sources]


def _source_health_result(
    source: dict[str, Any],
    cache_sources: dict[str, Any],
    previous_sources: dict[str, Any],
) -> CapabilityResult:
    name = str(source.get("name", "ukjent"))
    event_count = int(source.get("event_count") or 0)
    blocked = bool(source.get("blocked"))
    skipped = bool(source.get("skipped"))
    empty_calendar = bool(source.get("empty_calendar"))
    llm_fallback = bool(source.get("llm_fallback"))
    expectation = source.get("event_expectation") or {}
    expectation_status = expectation.get("status", "not_applicable")

    cache_entry = cache_sources.get(name) or {}
    cache_age_seconds = _cache_age_seconds(str(cache_entry.get("scrape_timestamp", "")))
    previous_entry = previous_sources.get(name) or {}
    previous_event_count = previous_entry.get("event_count")

    evidence: list[str] = [
        f"event_count={event_count}",
        f"cache_age={_format_age(cache_age_seconds)}",
    ]
    if expectation.get("expected_min_events") is not None:
        evidence.append(f"expected_min_events={expectation.get('expected_min_events')}")
    strategy_label = _strategy_label(name)
    if strategy_label:
        evidence.append(f"strategy={strategy_label}")
    if previous_event_count is not None:
        evidence.append(f"previous_event_count={previous_event_count}")

    problems: list[str] = []
    suggested_actions: list[str] = []
    requires_human = False
    status = "ok"

    if skipped:
        status = "warning"
        problems.append(str(source.get("skip_reason") or "Kilde hoppet over."))
    elif blocked:
        status = "blocked"
        requires_human = True
        problems.append(str(source.get("block_reason") or "Kilde blokkert uten oppgitt grunn."))
        try:
            from ..club_registry import club_for_source_name
            from .scraper_strategies import get_strategy, requires_credentials
        except ImportError:
            get_strategy = None  # type: ignore[assignment]
        if get_strategy is not None:
            strategy = get_strategy(club_for_source_name(name) or name)
            if strategy is not None and requires_credentials(strategy):
                suggested_actions.append(
                    f"Sett legitimasjon via miljøvariabler: {', '.join(strategy.credential_env_vars)}"
                )
        if llm_fallback:
            suggested_actions.append(f"Kjør 'rvv-miniputt scrape-llm --club \"{name}\"' i en browser-aktivert Pi-/Claude-/Codex-session.")
        else:
            suggested_actions.append(f"Kjør 'rvv-miniputt scrape --club \"{name}\"' for feilsøking, eller sjekk URL manuelt.")
        suggested_actions.append(
            f"Hvis du bare har terminal: bruk 'rvv-miniputt recovery-targets' for å finne blokkerte kilder, og 'rvv-miniputt recovery-inject --source \"{name}\"' når du har event-JSON."
        )
    elif empty_calendar:
        status = "warning"
        problems.append(str(source.get("empty_reason") or "Kilde returnerte 0 hendelser i perioden."))
        suggested_actions.append(
            "Dette ser ut som en tom offentlig kalender — kontroller bare at det er forventet, ikke en skrapefeil."
        )
    else:
        if event_count == 0 and int(previous_event_count or 0) > 0:
            status = "warning"
            problems.append(
                f"0 hendelser nå, {previous_event_count} ved forrige vellykkede skraping "
                "— mulig utfall eller endret sidestruktur."
            )
            suggested_actions.append("Kjør 'rvv-miniputt calendars --refresh' eller sjekk kilden manuelt.")
        elif expectation_status in {"low", "suspicious"}:
            status = "warning"
            problems.append(str(expectation.get("message") or "Kildens kalenderdata ser mistenkelig ut for perioden."))

        source_events = source.get("events") or cache_entry.get("events") or []
        dup_ratio = duplicate_ratio(source_events)
        if dup_ratio > _DUPLICATE_RATIO_WARNING_THRESHOLD:
            status = "warning"
            problems.append(f"{round(dup_ratio * 100)}% av hendelsene ser ut til å være duplikater.")
            suggested_actions.append("Sjekk kilden for gjentatte oppføringer eller en feil i skraperen.")

        hardcoded_signal = hardcoded_value_signal(source_events, str(source.get("type") or ""))
        if hardcoded_signal:
            status = "warning"
            requires_human = True
            problems.append(hardcoded_signal)
            suggested_actions.append(
                "Sjekk skraperen for en fallback-standardverdi som brukes i stedet for faktisk parset tid/varighet."
            )

        try:
            from ..club_registry import club_for_source_name as _club_for_source_name
        except ImportError:
            _club_for_source_name = None  # type: ignore[assignment]
        club_name = _club_for_source_name(name) if _club_for_source_name else None
        arena_signal = non_schedulable_arena_signal(club_name, source_events)
        if arena_signal:
            status = "warning"
            requires_human = True
            problems.append(arena_signal)
            suggested_actions.append(
                "Avklar med klubben hvilke LOCATION-verdier som faktisk er hallen RVV kan booke."
            )

        # The canonical source-integrity verdict (Stage 2 attaches it; legacy
        # checkpoints are recomputed here). A non-complete verdict is an
        # explicit fail-closed signal: the source must not be trusted for a
        # negative occupancy claim, and the operator sees why.
        integrity = source.get("integrity")
        if not isinstance(integrity, Mapping):
            integrity = evaluate_source_integrity(source, previous_event_count=previous_event_count)
        if integrity.get("status") and integrity.get("status") != INTEGRITY_COMPLETE:
            status = "warning"
            requires_human = True
            evidence.append(f"integrity_status={integrity.get('status')}")
            evidence.append(f"coverage_proven={bool(integrity.get('coverage_proven'))}")
            for reason in integrity.get("reasons") or []:
                if reason not in problems:
                    problems.append(str(reason))
            suggested_actions.append(
                "Kjør 'rvv-miniputt calendars --refresh' eller verifiser kilden manuelt; "
                "bruk ikke denne kilden som bevis for at et arrangement ikke er booket."
            )

        if status == "ok" and cache_age_seconds is not None and cache_age_seconds > _STALE_CACHE_SECONDS:
            status = "warning"
            problems.append(f"Cache er {_format_age(cache_age_seconds)} gammel.")
            suggested_actions.append("Kjør 'rvv-miniputt calendars --refresh' for å hente ferske data.")

    return CapabilityResult(
        status=status,
        summary=f"{name}: {event_count} hendelser, status={status}",
        evidence=evidence,
        confidence=_CONFIDENCE_BY_STATUS.get(status, 0.5),
        artifacts=["stage2_scraping.json", "cache/scraped_data.json"],
        problems=problems,
        suggested_actions=suggested_actions,
        requires_human=requires_human,
        capability=f"source_health:{name}",
    )
