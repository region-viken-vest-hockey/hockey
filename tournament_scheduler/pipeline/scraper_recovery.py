"""Recovery hints and blocked-source warnings for Stage 2 scraping."""

from __future__ import annotations

import os
from typing import Any

from .scraper_strategies import get_strategy, requires_credentials
from .state import PipelineState, StageName


def _recovery_hint_for_source(source_name: str) -> str:
    """Return a Norwegian hint that explains how to recover from a blocked source."""
    try:
        strategy = get_strategy(source_name)
        if strategy and requires_credentials(strategy):
            missing = [var for var in strategy.credential_env_vars if not os.environ.get(var)]
            credential_note = (
                f" Miljøvariabler som mangler ved ny innlogging: {', '.join(missing)}."
                if missing
                else ""
            )
            return (
                "BookUp-recovery skal normalt bruke lagret Playwright-innlogging fra "
                ".pipeline/auth/bookup-storage-state.json. Hvis sesjonen er utløpt, "
                "oppdater den i en synlig nettleser på macOS-verten med "
                "`RVV_BOOKUP_MANUAL_LOGIN=1 ... scripts/rvv-miniputt calendars --refresh`; "
                "Lima/headless-kjøringer gjenbruker deretter samme state."
                f"{credential_note} Kilden forblir uløst til kalenderhendelser kan leses."
            )
    except Exception:
        pass
    return (
        "Kjør `scripts/rvv-miniputt run` på nytt når kalenderen er tilgjengelig. "
        "Bruk bare `--allow-missing-sources` når en operatør uttrykkelig godkjenner delvise resultater."
    )


def _blocked_sources_warning(
    blocked: list[dict[str, Any]],
    state: PipelineState,
    *,
    allow_missing_sources: bool,
) -> str:
    names = ", ".join(sorted({b.get('name', '?') for b in blocked})) or "ukjent kilde"
    path = state.checkpoint_path(StageName.SCRAPING)
    recovery = _recovery_hint_for_source(blocked[0].get("name", "")) if blocked else _recovery_hint_for_source("")
    prefix = "Delvise resultater er lagret" if allow_missing_sources else "Delvise resultater er lagret, men Stage 2 er markert som feilet"
    return f"{prefix} i {path}. Blokkerte kilder: {names}. {recovery}"


def _temporary_unresolved_warning(
    temporary: list[dict[str, Any]],
    state: PipelineState,
) -> str:
    """Explain the temporary BookUp exception without pretending it is resolved."""
    names = ", ".join(sorted({b.get("name", "?") for b in temporary})) or "ukjent kilde"
    path = state.checkpoint_path(StageName.SCRAPING)
    return (
        f"Midlertidig ikke-blokkerende kalenderkilder i {path}: {names}. "
        "De er fortsatt uløste kalenderkilder og skal forsøkes gjenopprettet; "
        "dette er ikke en permanent manuell kalenderpolicy."
    )


def _empty_sources_warning(empty_sources: list[dict[str, Any]], state: PipelineState) -> str:
    names = ", ".join(sorted({s.get('name', '?') for s in empty_sources})) or "ukjent kilde"
    path = state.checkpoint_path(StageName.SCRAPING)
    return (
        f"Stage 2 er ferdig, men tomme kilder ble funnet i {path}: {names}. "
        "Dette betyr at kalenderen returnerte 0 hendelser i perioden, ikke at skraperen krasjet."
    )
