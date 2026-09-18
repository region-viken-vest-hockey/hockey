"""Immutable public/source presentation context for a reviewed Stage 4 handoff.

Stage 4 renders source/public information that is *not* part of the schedule
itself -- the scrape source/event counts, the "Skrapede kalendere" viewer, the
registered-team (``input.html``) overview, the public activity calendar and the
blocked-source status. That information only exists in the transient
``.pipeline`` workspace, so a canonical ``season export`` that runs later (with
``use_pipeline_metadata=False``) used to fall back to ``0 kilder · 0 hendelser``
and drop every companion page and navbar link.

Canonical verification must stay bound to the promoted verification problem and
must never re-read mutable ``.pipeline`` scrape state. Presentation context is a
different concern: it must be frozen at the reviewed handoff and carried with
the promoted season.

This module owns:

* :func:`build_public_export_context` -- the compact, JSON-serialisable snapshot
  Stage 4 stores alongside its export checkpoint;
* :func:`fingerprint_public_export_context` -- its deterministic fingerprint; and
* :func:`verify_public_export_context` -- the check promotion/export use to prove
  a stored snapshot matches its recorded fingerprint.

The snapshot deliberately excludes anything that is not publicly presentable
(internal workbook sheets, absolute workstation paths, machine feed URLs are
kept only because the calendar viewer already exposes the configured source
links).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from .fingerprints import stable_payload_sha256

logger = logging.getLogger(__name__)

PUBLIC_EXPORT_CONTEXT_SCHEMA_VERSION = 1


class PublicExportContextError(RuntimeError):
    """Raised when a stored public export context cannot be trusted."""


def _public_teams(input_path: str | Path) -> list[dict[str, Any]]:
    """Return the publicly presentable team rows for *input_path*.

    Only the whitelisted ``Lag`` worksheet is read, and only the public
    columns are retained -- internal planning columns
    (``target_tournament_count``) stay out of the published context.
    """
    from .input_workbook import read_public_teams

    teams: list[dict[str, Any]] = []
    for team in read_public_teams(input_path):
        club = str(team.get("club") or "").strip()
        label = str(team.get("label") or "").strip()
        age_group = str(team.get("age_group") or "").strip()
        if not club or not label:
            continue
        teams.append({"club": club, "label": label, "age_group": age_group})
    return teams


def _calendar_snapshot(cache_data: dict[str, Any]) -> dict[str, Any] | None:
    """Return the payload the calendar viewer needs, or ``None`` when empty."""
    if not isinstance(cache_data, dict):
        return None
    sources = cache_data.get("sources") or {}
    source_count = int(cache_data.get("source_count") or len(sources))
    total_events = int(cache_data.get("total_events") or 0)
    if source_count <= 0 and total_events <= 0:
        return None
    return {
        "_meta": dict(cache_data.get("_meta") or {}),
        "source_count": source_count,
        "total_events": total_events,
        "sources": sources,
    }


def build_public_export_context(
    state: Any,
    effective_config: dict[str, Any],
    *,
    generated_at: str,
) -> dict[str, Any]:
    """Collect the immutable public presentation context for a Stage 4 export.

    Reads the transient workspace once, at export time, and returns a
    self-contained snapshot so a later canonical ``season export`` never has to
    read ``.pipeline`` again.
    """
    from .cache_manager import ScrapedDataCache
    from .state import StageName

    context: dict[str, Any] = {
        "schema_version": PUBLIC_EXPORT_CONTEXT_SCHEMA_VERSION,
        "generated_at": generated_at,
        "start_date": effective_config.get("start_date"),
        "end_date": effective_config.get("end_date"),
        "age_groups": list(dict.fromkeys(effective_config.get("age_groups") or [])),
    }

    cache_data: dict[str, Any] = {}
    try:
        cache_data = ScrapedDataCache(state.work_dir).read() or {}
    except Exception:  # noqa: BLE001 - presentation context is best-effort
        cache_data = {}

    scraping_checkpoint: dict[str, Any] = {}
    scraping_updated_at = ""
    try:
        envelope = state.read_envelope(StageName.SCRAPING)
        scraping_checkpoint = envelope.get("data") or {}
        scraping_updated_at = str(envelope.get("updated_at") or "")
    except Exception as exc:  # noqa: BLE001 - presentation context is best-effort
        logger.warning("Kunne ikke lese scraping-checkpoint for rapporten: %s", exc)
        scraping_checkpoint = {}
        scraping_updated_at = ""

    calendar = _calendar_snapshot(cache_data)
    cache_updated_at = ""
    if isinstance(cache_data.get("_meta"), dict):
        cache_updated_at = str(cache_data["_meta"].get("updated_at") or "")
    scrape_meta: dict[str, Any] = {
        "source_count": int((calendar or {}).get("source_count") or 0),
        "total_events": int((calendar or {}).get("total_events") or 0),
        "blocked": list((scraping_checkpoint or {}).get("blocked") or []),
        "updated_at": scraping_updated_at or cache_updated_at,
    }
    if calendar is not None:
        context["calendar"] = calendar
    if scrape_meta["source_count"] or scrape_meta["total_events"] or scrape_meta["blocked"]:
        context["scrape"] = scrape_meta

    confidence = scraping_checkpoint.get("confidence")
    if isinstance(confidence, dict) and confidence:
        context["scrape_confidence"] = confidence

    input_path = effective_config.get("input_path")
    if input_path and Path(input_path).exists():
        try:
            teams = _public_teams(input_path)
        except Exception:  # noqa: BLE001 - never block export on optional context
            teams = []
        context["input"] = {"file_name": Path(input_path).name, "teams": teams}

        try:
            from .activity_export import build_activities_payload

            start_year = None
            start_value = effective_config.get("start_date")
            if isinstance(start_value, str) and len(start_value) >= 4:
                start_year = int(start_value[:4])
            activities = build_activities_payload(
                input_path, default_year=start_year, generated_at=generated_at
            )
            if activities:
                context["activities"] = activities
        except Exception:  # noqa: BLE001
            pass

    return context


def fingerprint_public_export_context(context: dict[str, Any] | None) -> str | None:
    """Return the deterministic fingerprint of *context*, or ``None``."""
    if not isinstance(context, dict) or not context:
        return None
    return stable_payload_sha256(context)


def verify_public_export_context(
    context: dict[str, Any] | None, *, expected_fingerprint: str | None
) -> str | None:
    """Return the context fingerprint, refusing a mismatch.

    Raises :class:`PublicExportContextError` when a recorded fingerprint no
    longer matches the stored snapshot (tampering/replay), so canonical export
    never renders source metadata from an unverified snapshot.
    """
    actual = fingerprint_public_export_context(context)
    if actual is None:
        return None
    if expected_fingerprint and expected_fingerprint != actual:
        raise PublicExportContextError(
            "Public export context does not match its recorded fingerprint; "
            "re-promote the reviewed handoff."
        )
    return actual


def resolve_promoted_public_export_context(
    schedule: dict[str, Any],
    context: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Return the fingerprint-verified public export context for *schedule*.

    *context* is the separately persisted ``export_context.json`` snapshot (the
    canonical store keeps the large, immutable blob out of ``schedule.json``).
    Legacy seasons promoted before this context existed simply have none; they
    export without source/public metadata rather than failing.
    """
    if context is None:
        context = schedule.get("export_context")
    if not isinstance(context, dict) or not context:
        return None
    promoted_from = schedule.get("promoted_from") or {}
    expected = promoted_from.get("public_export_context_fingerprint")
    verify_public_export_context(context, expected_fingerprint=expected)
    return context


__all__ = [
    "PUBLIC_EXPORT_CONTEXT_SCHEMA_VERSION",
    "PublicExportContextError",
    "build_public_export_context",
    "fingerprint_public_export_context",
    "resolve_promoted_public_export_context",
    "verify_public_export_context",
]
