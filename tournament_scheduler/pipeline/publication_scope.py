"""Tournament-scoped publication eligibility for an incremental republish.

Publication eligibility belongs to each *active tournament*, not to
season-wide planning completeness. A full-season semantic audit treats
unresolved JU8 shapes, unplaced hosting obligations and participation
shortfalls as one global verdict; that verdict is planning debt, and an
unchanged, already-published tournament must not be blocked because of it.

This module owns the deterministic, typed answer the publish preflight (and
the semantic audit context) consume:

* what the last publication actually contained (the immutable published
  baseline projection);
* which tournaments the current export adds, removes or changes relative to
  that baseline (the complete last-publication delta);
* which of those changed/new tournaments have accepted, source-backed booking
  evidence for their exact published interval;
* whether the change silently transferred hosting responsibility to a club
  that does not owe it; and
* the full-season planning debt, retained explicitly as *diagnostic* rather
  than silently dropped.

It never invents booking authority or a new verdict from prose. It reuses the
canonical booking evidence owner (:mod:`tournament_scheduler.calendar_bookings`)
and the canonical hosting-responsibility owner
(:mod:`tournament_scheduler.hosting_responsibility`); it only classifies what
those owners already establish.
"""

from __future__ import annotations

import os
from typing import Any, Mapping

from .export_projection_guard import diff_tournament_projection, tournament_projection

PUBLICATION_SCOPE_SCHEMA_VERSION = 1
PUBLICATION_SCOPE = "publication_scope"

STATUS_ELIGIBLE = "ELIGIBLE"
STATUS_HELD = "HELD"
STATUS_BLOCKED = "BLOCKED"
STATUS_NOT_CHECKABLE = "NOT_CHECKABLE"

# Blocking (global safety) reasons.
CODE_PROJECTION_SCHEMA_ERROR = "projection_schema_error"
CODE_UNAUTHORIZED_HOSTING_TRANSFER = "unauthorized_hosting_responsibility_transfer"

# Held (affected-tournament) reasons.
CODE_ADDED_WITHOUT_BOOKING = "added_tournament_without_accepted_booking"
CODE_CHANGED_INTERVAL_WITHOUT_BOOKING = "changed_interval_without_accepted_booking"
CODE_UNEXPLAINED_REMOVAL = "unexplained_public_entry_removal"

# Not-checkable reasons (fail closed to the existing semantic-audit gate).
CODE_NO_PUBLISHED_BASELINE = "no_published_baseline"
CODE_NO_CANONICAL_SEASON = "no_canonical_season"
CODE_BOOKING_EVIDENCE_UNAVAILABLE = "booking_evidence_unavailable"
CODE_HOSTING_EVIDENCE_UNAVAILABLE = "hosting_evidence_unavailable"

#: Placement/occupied-interval and cancellation fields whose change requires
#: fresh accepted booking evidence. A roster- or guest-only change keeps the
#: published interval, which the published baseline already accepted. A
#: ``cancelled`` transition is included on purpose: reactivating a previously
#: published cancellation (``cancelled: true -> false``) has to prove an
#: accepted booking for the interval, while an active -> cancelled transition
#: is handled as a deliberate public-entry removal.
_INTERVAL_FIELDS = (
    "date",
    "start_time",
    "arena",
    "host_club",
    "duration_minutes",
    "end_time",
    "cancelled",
)

_OPERATIONAL_BOOKED = "booked"


def _empty_result(
    *,
    export_fingerprint: str | None,
    season: str | None,
    canonical_revision: str | None,
) -> dict[str, Any]:
    return {
        "schema_version": PUBLICATION_SCOPE_SCHEMA_VERSION,
        "scope": PUBLICATION_SCOPE,
        "status": STATUS_NOT_CHECKABLE,
        "export_fingerprint": export_fingerprint,
        "canonical_season": season,
        "canonical_revision": canonical_revision,
        "last_publication": None,
        "delta": {
            "added_tournament_ids": [],
            "removed_tournament_ids": [],
            "changed_tournament_ids": [],
            "interval_changed_tournament_ids": [],
            "participant_only_changed_tournament_ids": [],
            "summary": {},
        },
        "eligible_tournament_ids": [],
        "held": [],
        "blocking": [],
        "diagnostic": {"full_season_reasons": []},
        "reasons": [],
    }


def _add_reason(result: dict[str, Any], code: str, message: str, **extra: Any) -> None:
    entry = {"code": code, "message": message}
    entry.update(extra)
    result["reasons"].append(entry)


def _delta_view(delta: Mapping[str, Any]) -> dict[str, Any]:
    field_changes = list(delta.get("field_changes") or [])
    changed_ids = [str(entry.get("tournament_id") or "") for entry in field_changes]
    interval_changed: list[str] = []
    participant_only: list[str] = []
    for entry in field_changes:
        fields = entry.get("fields") or {}
        tournament_id = str(entry.get("tournament_id") or "")
        if any(field in fields for field in _INTERVAL_FIELDS):
            interval_changed.append(tournament_id)
        elif fields:
            participant_only.append(tournament_id)
    return {
        "added_tournament_ids": [str(item) for item in delta.get("added_tournament_ids") or []],
        "removed_tournament_ids": [str(item) for item in delta.get("removed_tournament_ids") or []],
        "changed_tournament_ids": [str(item) for item in changed_ids if item],
        "interval_changed_tournament_ids": [item for item in interval_changed if item],
        "participant_only_changed_tournament_ids": [item for item in participant_only if item],
        "summary": {
            "added": len(delta.get("added_tournament_ids") or []),
            "removed": len(delta.get("removed_tournament_ids") or []),
            "changed": len(changed_ids),
        },
    }


def _booking_rows_by_tournament(
    *,
    season: str,
    season_root: str | os.PathLike[str],
) -> dict[str, dict[str, Any]]:
    from tournament_scheduler.season_state import booking_status_report

    report = booking_status_report(season=season, root=season_root)
    rows: dict[str, dict[str, Any]] = {}
    for row in report.get("tournaments") or []:
        if isinstance(row, Mapping):
            rows[str(row.get("tournament_id") or "")] = dict(row)
    return rows


def _has_accepted_booking(row: Mapping[str, Any] | None) -> bool:
    if not isinstance(row, Mapping):
        return False
    return str(row.get("operational_state") or "") == _OPERATIONAL_BOOKED


def _hosting_transfer_findings(
    *,
    published_projection: Mapping[str, Mapping[str, Any]],
    reviewed_plan: Mapping[str, Any],
    problem: Mapping[str, Any] | None,
) -> list[dict[str, Any]] | None:
    """Return unauthorized hosting transfers, or ``None`` when not checkable."""

    if not isinstance(problem, Mapping) or not problem.get("teams"):
        return None
    from tournament_scheduler.hosting_responsibility import (
        unexplained_responsibility_transfers,
    )

    before = {"tournaments": [dict(entry) for entry in published_projection.values()]}
    after = {"tournaments": list(reviewed_plan.get("tournaments") or [])}
    return list(unexplained_responsibility_transfers(before, after, problem))


def evaluate_publication_scope(
    *,
    reviewed_plan: Mapping[str, Any],
    problem: Mapping[str, Any] | None = None,
    decisions: Mapping[str, Any] | None = None,
    season: str | None = None,
    season_root: str | os.PathLike[str] | None = None,
    export_fingerprint: str | None = None,
    canonical_revision: str | None = None,
    full_season_reasons: list[Any] | None = None,
) -> dict[str, Any]:
    """Compute the tournament-scoped publication eligibility for one export.

    The reviewed plan is the exact public projection represented by the export
    (already proven equal to current canonical state by the export parity gate
    and the sealed-publication reconciliation).

    The result is one of:

    ``ELIGIBLE``
        Every added/changed tournament in the last-publication delta has
        accepted source-backed booking evidence for its exact published
        interval, no tournament vanished unexplained, and hosting
        responsibility did not silently move.
    ``HELD``
        One or more affected tournaments need explicit resolution. Genuine
        contradictions are held per tournament, never silently published.
    ``BLOCKED``
        A global safety defect (projection schema inconsistency or an
        unauthorized hosting-responsibility transfer).
    ``NOT_CHECKABLE``
        The canonical season / published baseline / booking evidence needed to
        make the determination is unavailable; the caller must fall back to the
        existing semantic-audit gate rather than treat this as a pass.
    """

    result = _empty_result(
        export_fingerprint=export_fingerprint,
        season=season,
        canonical_revision=canonical_revision,
    )
    result["diagnostic"] = {"full_season_reasons": list(full_season_reasons or [])}

    if not season:
        _add_reason(
            result,
            CODE_NO_CANONICAL_SEASON,
            "the export is not bound to a canonical season, so incremental "
            "publication scope cannot be established",
        )
        return result
    if not isinstance(decisions, Mapping) or not decisions:
        _add_reason(
            result,
            CODE_NO_CANONICAL_SEASON,
            f"canonical decisions for season {season!r} could not be loaded",
        )
        return result

    from tournament_scheduler.published_baseline import (
        active_baseline,
        baseline_projection,
    )

    baseline = active_baseline(decisions)
    if not isinstance(baseline, Mapping):
        _add_reason(
            result,
            CODE_NO_PUBLISHED_BASELINE,
            "the season has no published baseline yet; the first publication "
            "uses the full semantic audit gate",
        )
        return result

    published_projection = baseline_projection(baseline)
    try:
        current_projection = tournament_projection(reviewed_plan, problem, strict=False)
    except Exception as exc:  # noqa: BLE001 - fail closed to existing gate
        _add_reason(
            result,
            CODE_PROJECTION_SCHEMA_ERROR,
            f"the reviewed plan could not be projected: {type(exc).__name__}: {exc}",
        )
        result["status"] = STATUS_BLOCKED
        return result

    delta = diff_tournament_projection(published_projection, current_projection)
    result["delta"] = _delta_view(delta)
    result["last_publication"] = {
        "publication_id": baseline.get("publication_id"),
        "canonical_revision": baseline.get("canonical_revision"),
        "tournament_count": baseline.get("tournament_count"),
    }

    if delta.get("schema_errors"):
        result["status"] = STATUS_BLOCKED
        _add_reason(
            result,
            CODE_PROJECTION_SCHEMA_ERROR,
            "the published baseline or reviewed plan projection has an "
            "incomplete/legacy schema and cannot be compared",
            schema_errors=list(delta.get("schema_errors"))[:5],
        )
        return result

    if season_root is None:
        season_root = os.environ.get("RVV_CANONICAL_SEASON_ROOT") or "season"

    try:
        booking_rows = _booking_rows_by_tournament(season=season, season_root=season_root)
    except Exception as exc:  # noqa: BLE001 - fail closed to existing gate
        _add_reason(
            result,
            CODE_BOOKING_EVIDENCE_UNAVAILABLE,
            f"canonical booking evidence could not be read: {type(exc).__name__}: {exc}",
        )
        return result

    try:
        transfer_findings = _hosting_transfer_findings(
            published_projection=published_projection,
            reviewed_plan=reviewed_plan,
            problem=problem,
        )
    except Exception as exc:  # noqa: BLE001 - fail closed to existing gate
        _add_reason(
            result,
            CODE_HOSTING_EVIDENCE_UNAVAILABLE,
            f"hosting-responsibility evidence could not be computed: {type(exc).__name__}: {exc}",
        )
        return result
    if transfer_findings is None:
        # The normalized problem/teams are unavailable, so the change cannot be
        # proven free of an unauthorized hosting-responsibility transfer. Fail
        # closed to the existing audit gate instead of treating ``None`` as no
        # findings (which would fall through to ELIGIBLE).
        _add_reason(
            result,
            CODE_HOSTING_EVIDENCE_UNAVAILABLE,
            "hosting-responsibility evidence is unavailable (missing normalized "
            "problem/teams), so the change cannot be proven free of an unauthorized "
            "hosting-responsibility transfer",
        )
        return result
    if transfer_findings:
        result["status"] = STATUS_BLOCKED
        for finding in transfer_findings:
            _add_reason(
                result,
                CODE_UNAUTHORIZED_HOSTING_TRANSFER,
                str(finding.get("message") or "hosting responsibility moved without authorization"),
                tournament_id=None,
                club=finding.get("club"),
                age_group=finding.get("age_group"),
            )
        return result

    held: list[dict[str, Any]] = []
    eligible_ids: list[str] = []

    changed_ids = set(result["delta"]["changed_tournament_ids"])
    for tournament_id in result["delta"]["added_tournament_ids"]:
        row = booking_rows.get(tournament_id)
        if _has_accepted_booking(row):
            eligible_ids.append(tournament_id)
        else:
            held.append(
                {
                    "tournament_id": tournament_id,
                    "code": CODE_ADDED_WITHOUT_BOOKING,
                    "message": (
                        f"new tournament {tournament_id} has no accepted source-backed "
                        "booking for its published interval"
                    ),
                    "booking_status": str((row or {}).get("status") or ""),
                    "operational_state": str((row or {}).get("operational_state") or ""),
                    "stale_reasons": list((row or {}).get("stale_reasons") or []),
                }
            )

    interval_changed_ids = set(result["delta"]["interval_changed_tournament_ids"])
    current_by_id = {str(entry.get("id") or ""): entry for entry in current_projection.values()}
    for tournament_id in sorted(interval_changed_ids):
        current_entry = current_by_id.get(tournament_id) or {}
        # A cancellation (or a cancelled tournament whose interval changed) is
        # a deliberate public-entry removal, not an unaccepted booking.
        if bool(current_entry.get("cancelled")):
            eligible_ids.append(tournament_id)
            continue
        row = booking_rows.get(tournament_id)
        if _has_accepted_booking(row):
            eligible_ids.append(tournament_id)
        else:
            held.append(
                {
                    "tournament_id": tournament_id,
                    "code": CODE_CHANGED_INTERVAL_WITHOUT_BOOKING,
                    "message": (
                        f"tournament {tournament_id} interval changed without accepted "
                        "source-backed booking evidence for the new published interval"
                    ),
                    "booking_status": str((row or {}).get("status") or ""),
                    "operational_state": str((row or {}).get("operational_state") or ""),
                    "stale_reasons": list((row or {}).get("stale_reasons") or []),
                }
            )

    # A roster- or guest-only change keeps the published interval the baseline
    # already accepted; it stays public without a new booking check.
    participant_only = set(result["delta"]["participant_only_changed_tournament_ids"])
    eligible_ids.extend(sorted(participant_only - interval_changed_ids))

    for tournament_id in result["delta"]["removed_tournament_ids"]:
        held.append(
            {
                "tournament_id": tournament_id,
                "code": CODE_UNEXPLAINED_REMOVAL,
                "message": (
                    f"previously published tournament {tournament_id} disappeared without "
                    "a canonical cancellation; the public entry cannot be silently removed"
                ),
            }
        )

    result["eligible_tournament_ids"] = sorted(set(eligible_ids) - {h["tournament_id"] for h in held})
    result["held"] = held
    if held:
        result["status"] = STATUS_HELD
        for entry in held:
            _add_reason(result, entry["code"], entry["message"], tournament_id=entry["tournament_id"])
        return result

    result["status"] = STATUS_ELIGIBLE
    return result


def resolve_publication_scope(
    *,
    reviewed_plan: Mapping[str, Any],
    problem: Mapping[str, Any] | None = None,
    season: str | None = None,
    season_root: str | os.PathLike[str] | None = None,
    export_fingerprint: str | None = None,
    canonical_revision: str | None = None,
    full_season_reasons: list[Any] | None = None,
) -> dict[str, Any]:
    """Resolve canonical decisions for *season* and evaluate publication scope.

    This is the shared entry point so the publish preflight and the semantic
    audit context consume the exact same typed contract. A season that cannot
    be loaded (or has no canonical decisions) yields ``NOT_CHECKABLE`` rather
    than a fabricated pass.
    """

    if not season:
        return evaluate_publication_scope(
            reviewed_plan=reviewed_plan,
            problem=problem,
            decisions=None,
            season=None,
            export_fingerprint=export_fingerprint,
            canonical_revision=canonical_revision,
            full_season_reasons=full_season_reasons,
        )
    if season_root is None:
        season_root = os.environ.get("RVV_CANONICAL_SEASON_ROOT") or "season"
    decisions: dict[str, Any] | None = None
    try:
        from tournament_scheduler.season_state import load_decisions

        decisions = load_decisions(str(season), root=season_root)
    except Exception:  # noqa: BLE001 - NOT_CHECKABLE, caller falls back to audit gate
        decisions = None
    return evaluate_publication_scope(
        reviewed_plan=reviewed_plan,
        problem=problem,
        decisions=decisions,
        season=str(season),
        season_root=season_root,
        export_fingerprint=export_fingerprint,
        canonical_revision=canonical_revision,
        full_season_reasons=full_season_reasons,
    )


__all__ = [
    "PUBLICATION_SCOPE",
    "PUBLICATION_SCOPE_SCHEMA_VERSION",
    "STATUS_BLOCKED",
    "STATUS_ELIGIBLE",
    "STATUS_HELD",
    "STATUS_NOT_CHECKABLE",
    "evaluate_publication_scope",
    "resolve_publication_scope",
]
