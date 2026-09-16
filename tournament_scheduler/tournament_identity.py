"""Durable tournament identity helpers.

Tournament IDs are opaque lifecycle identifiers. They are assigned once when a
tournament becomes part of a season plan and must not be derived from mutable
placement fields such as date, arena, host, participants or start time.
"""

from __future__ import annotations

from collections import Counter
from typing import Any, Iterable


def allocate_tournament_id(existing_ids: Iterable[str], *, prefix: str = "rvv") -> str:
    """Return the next deterministic opaque tournament id not in *existing_ids*.

    The allocator depends only on the set of already-issued IDs, not on any
    mutable tournament placement fields. It is intended for newly-created
    logical tournaments; ordinary edits must keep the existing id.
    """

    used = {str(value) for value in existing_ids if str(value)}
    counter = 1
    while True:
        candidate = f"{prefix}-{counter:04d}"
        if candidate not in used:
            return candidate
        counter += 1


def active_tournament_ids(candidate: dict[str, Any]) -> list[str]:
    """Return IDs for non-cancelled tournaments, preserving candidate order."""

    ids: list[str] = []
    for tournament in candidate.get("tournaments", []) or []:
        if not isinstance(tournament, dict) or tournament.get("cancelled"):
            continue
        tid = str(tournament.get("id") or "").strip()
        ids.append(tid)
    return ids


def validate_tournament_identity(candidate: dict[str, Any]) -> list[dict[str, Any]]:
    """Validate structural identity invariants for a serialized candidate.

    Checks are planner-neutral and deterministic: every active tournament must
    have exactly one non-empty ID, active IDs must be unique, and explicit
    lineage/manual/waiver references must point at a known tournament ID.
    """

    violations: list[dict[str, Any]] = []
    all_ids: set[str] = set()
    active_ids: list[str] = []

    for index, tournament in enumerate(candidate.get("tournaments", []) or [], start=1):
        if not isinstance(tournament, dict):
            continue
        tid = str(tournament.get("id") or "").strip()
        if tid:
            all_ids.add(tid)
        if tournament.get("cancelled"):
            continue
        if not tid:
            violations.append(
                {
                    "code": "missing_tournament_id",
                    "message": f"Active tournament #{index} is missing a durable id",
                }
            )
        else:
            active_ids.append(tid)

    for tid, count in sorted(Counter(active_ids).items()):
        if count > 1:
            violations.append(
                {
                    "code": "duplicate_tournament_id",
                    "message": f"Active tournament id {tid!r} appears {count} times",
                    "tournament_id": tid,
                }
            )

    registry = candidate.get("identity_registry") or {}
    registry_ids = set()
    if isinstance(registry, dict):
        registry_ids = {str(value) for value in registry.get("known_tournament_ids", []) or [] if str(value)}
    known = set(all_ids) | registry_ids
    for tournament in candidate.get("tournaments", []) or []:
        if not isinstance(tournament, dict):
            continue
        tid = str(tournament.get("id") or "").strip()
        for source_id in tournament.get("derived_from", []) or []:
            source = str(source_id or "").strip()
            if source and source not in known:
                violations.append(
                    {
                        "code": "unknown_lineage_tournament_id",
                        "message": f"Tournament {tid or '?'} derives from unknown tournament id {source!r}",
                        "tournament_id": tid or None,
                    }
                )

    manual = candidate.get("manual_adjustments") or {}
    if isinstance(manual, dict):
        for raw in manual.get("pinned_tournament_ids", []) or []:
            ref = str(raw or "").strip()
            if ref and ref not in known:
                violations.append(
                    {
                        "code": "unknown_pinned_tournament_id",
                        "message": f"Manual adjustment references unknown tournament id {ref!r}",
                        "tournament_id": ref,
                    }
                )

    for waiver in candidate.get("operator_waivers", []) or []:
        if not isinstance(waiver, dict):
            continue
        scope = waiver.get("scope") or {}
        if not isinstance(scope, dict):
            continue
        ref = str(scope.get("tournament_id") or "").strip()
        if ref and ref not in known:
            violations.append(
                {
                    "code": "unknown_waiver_tournament_id",
                    "message": f"Operator waiver references unknown tournament id {ref!r}",
                    "tournament_id": ref,
                }
            )

    return violations
