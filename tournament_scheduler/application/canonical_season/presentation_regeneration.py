"""Presentation-only canonical regeneration: same canonical truth, new renderer.

A renderer, template or CSS change is expected to alter the artifact bytes, so
byte comparison proves nothing. This contract instead proves that one canonical
export (a) ran against the revision captured before regeneration, (b) left the
canonical revision unchanged, and (c) projects that revision faithfully through
the existing artifact parity/freshness verifier.

It never replans, normalizes, rescrapes or publishes. The semantic audit is
reported as required-but-not-run: that judgment belongs to the active harness.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from tournament_scheduler.pipeline.export_parity import (
    STATUS_FAIL,
    STATUS_NOT_CHECKABLE,
    STATUS_PASS,
    verify_export_parity,
)
from tournament_scheduler.pipeline.export_parity.verify import DEFAULT_BASENAME
from tournament_scheduler.pipeline.stage4_export_errors import Stage4Error
from tournament_scheduler.publication.projection_guard import ExportProjectionError

from .store_facade import SeasonStateError

# Failures an export is expected to raise for a bad canonical or renderer state.
# They become a FAIL report rather than a traceback, so the harness always gets
# the bounded JSON result.
_EXPECTED_EXPORT_FAILURES = (SeasonStateError, Stage4Error, ExportProjectionError, OSError)

PRESENTATION_REGENERATION_SCHEMA_VERSION = 1

_NOT_RUN = "NOT_RUN"


def _reason(code: str, message: str) -> dict[str, str]:
    return {"code": code, "message": message}


def run_presentation_only_regeneration(
    *,
    season: str,
    read_canonical_revision: Callable[[], str],
    export: Callable[[], dict[str, Any]],
) -> dict[str, Any]:
    """Run one canonical export and return the bounded presentation-only report.

    ``export`` renders canonical artifacts through the read-only export boundary
    and returns its checkpoint. ``read_canonical_revision`` re-reads authoritative
    state. The report is ``PASS`` only when the revision is unchanged across the
    render, the artifacts were not overtaken, and parity passes against the
    pre-regeneration revision.
    """

    revision_before = str(read_canonical_revision() or "")
    reasons: list[dict[str, str]] = []

    result: dict[str, Any] = {}
    try:
        result = export() or {}
    except _EXPECTED_EXPORT_FAILURES as exc:
        reasons.append(_reason("export_failed", f"{type(exc).__name__}: {exc}"))

    revision_after = str(read_canonical_revision() or "")
    export_revision = str(result.get("canonical_revision") or "")
    revision_unchanged = bool(revision_before) and revision_before == revision_after

    if result:
        if result.get("errors"):
            reasons.append(_reason("export_errors", "; ".join(str(item) for item in result["errors"])))
        if result.get("stale_export"):
            reasons.append(
                _reason(
                    "stale_export",
                    "canonical state advanced during export; the artifacts are not publishable",
                )
            )
        if export_revision != revision_before:
            reasons.append(
                _reason(
                    "canonical_revision_changed",
                    f"export covered revision {export_revision or 'unknown'}, "
                    f"but regeneration started at {revision_before or 'unknown'}",
                )
            )
    if not revision_unchanged:
        reasons.append(
            _reason(
                "canonical_revision_changed",
                f"canonical revision moved from {revision_before or 'unknown'} to "
                f"{revision_after or 'unknown'}; this is not a presentation-only regeneration",
            )
        )

    parity: dict[str, Any] = {"status": _NOT_RUN, "reasons": []}
    export_dir = result.get("export_dir")
    if export_dir:
        parity = verify_export_parity(
            export_dir,
            basename=DEFAULT_BASENAME,
            required_canonical_revision=revision_before,
            requires_fresh_export=False,
            canonical_publication=True,
            require_spond=True,
            require_spond_revision=True,
        )

    parity_status = str(parity.get("status") or _NOT_RUN)
    if reasons or parity_status == STATUS_FAIL or parity_status == _NOT_RUN:
        if parity_status == _NOT_RUN and not reasons:
            reasons.append(_reason("export_not_written", "the export produced no artifact directory"))
        status = STATUS_FAIL
    elif parity_status == STATUS_NOT_CHECKABLE:
        status = STATUS_NOT_CHECKABLE
    else:
        status = STATUS_PASS

    return {
        "schema_version": PRESENTATION_REGENERATION_SCHEMA_VERSION,
        "season": season,
        "status": status,
        "canonical_revision_before": revision_before or None,
        "canonical_revision_after": revision_after or None,
        "canonical_revision_unchanged": revision_unchanged,
        "canonical_schedule_or_decisions_changed": not revision_unchanged,
        "export_dir": export_dir,
        "export_fingerprint": result.get("export_fingerprint"),
        "artifact_parity": parity,
        "semantic_audit": {
            "status": "REQUIRED_NOT_RUN",
            "note": (
                "Run the in-session semantic audit against this exact export before "
                "publication; this command does not judge semantic change."
            ),
        },
        "publication": {"performed": False, "authorized": False},
        "reasons": reasons,
    }


__all__ = ["PRESENTATION_REGENERATION_SCHEMA_VERSION", "run_presentation_only_regeneration"]
