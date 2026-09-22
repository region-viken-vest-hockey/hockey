"""Season-quality baseline creation, advance and reporting."""

from __future__ import annotations

import copy
from typing import Any

from tournament_scheduler.canonical_state import (
    SEASON_BASELINE_HISTORY_KEY,
    SEASON_BASELINE_KEY,
    canonical_state_revision,
    schedule_fingerprint,
)
from tournament_scheduler.infrastructure.canonical_season_store import (
    SeasonStateError,
)

from .shared import (
    _operator_identity,
    _now_iso,
    _append_decision_history,
)

def season_baseline_show(service, season: str) -> dict[str, Any]:
    """Return the stored season-quality baseline and current comparison."""

    from tournament_scheduler.season_maintenance import list_findings

    snapshot = service.load(season)
    report = list_findings(season, root=service.store.root)
    baseline = snapshot.decisions.get(SEASON_BASELINE_KEY) or None
    comparison = report.get("baseline_comparison") or {
        "active": bool(baseline),
        "summary": {},
        "entries": [],
    }
    hard_findings = [
        finding
        for finding in report.get("findings") or []
        if str(finding.get("severity") or "").lower() == "hard"
        or str(finding.get("category") or "") == "hard_violation"
    ]
    return {
        "season": season,
        "revision": canonical_state_revision(snapshot.schedule, snapshot.decisions),
        "schedule_fingerprint": schedule_fingerprint(snapshot.schedule.get("plan") or {}),
        "baseline": baseline,
        "comparison": comparison,
        "hard_verification_ok": bool(report.get("verification_ok")),
        "hard_finding_count": len(hard_findings),
        "hard_findings": hard_findings,
    }


def season_baseline_create(
    service,
    *,
    season: str,
    note: str = "",
    actor: str | None = None,
    replace: bool = False,
) -> dict[str, Any]:
    """Persist the current non-hard finding set as the accepted baseline."""

    from tournament_scheduler.season_baseline import create_baseline_record
    from tournament_scheduler.season_maintenance import list_findings

    snapshot = service.load(season)
    if snapshot.decisions.get(SEASON_BASELINE_KEY) and not replace:
        raise SeasonStateError(
            "A season baseline already exists; use baseline advance for equal-or-better "
            "state or an explicit replacement command when one is provided"
        )
    report = list_findings(season, root=service.store.root)
    if not bool(report.get("verification_ok")):
        raise SeasonStateError("Refusing to baseline a season with hard verification failures")
    now = _now_iso()
    baseline = create_baseline_record(
        season=season,
        schedule=snapshot.schedule,
        findings_report=report,
        note=note,
        actor=actor,
    )
    decisions = copy.deepcopy(snapshot.decisions)
    previous = decisions.get(SEASON_BASELINE_KEY)
    if previous:
        history = decisions.setdefault(SEASON_BASELINE_HISTORY_KEY, [])
        history.append({"event": "replace", "at": now, "actor": _operator_identity(actor), "baseline": previous})
    decisions[SEASON_BASELINE_KEY] = baseline
    decisions["updated_at"] = now
    _append_decision_history(
        decisions,
        event="season_baseline_create" if previous is None else "season_baseline_replace",
        tournament_id="",
        actor=actor,
        now=now,
        note=note,
        details={
            "finding_count": baseline["finding_count"],
            "finding_context_fingerprint": baseline["finding_context_fingerprint"],
        },
    )
    committed = service._commit(snapshot.with_decisions(decisions))
    return service.season_baseline_show(season) | {
        "created": True,
        "canonical_state_revision": canonical_state_revision(committed.schedule, committed.decisions),
    }


def season_baseline_advance(
    service,
    *,
    season: str,
    note: str = "",
    actor: str | None = None,
) -> dict[str, Any]:
    """Tighten the accepted baseline to the current equal-or-better state."""

    from tournament_scheduler.season_baseline import create_baseline_record, compare_findings_to_baseline
    from tournament_scheduler.season_maintenance import list_findings

    snapshot = service.load(season)
    existing = snapshot.decisions.get(SEASON_BASELINE_KEY)
    if not existing:
        raise SeasonStateError("No season baseline exists; create one first")
    report = list_findings(season, root=service.store.root)
    if not bool(report.get("verification_ok")):
        raise SeasonStateError("Refusing to advance baseline while hard verification fails")
    comparison = compare_findings_to_baseline(existing, report.get("findings") or [])
    if not comparison.get("ok_to_advance"):
        raise SeasonStateError("Refusing to advance baseline with NEW or REGRESSED findings")
    now = _now_iso()
    baseline = create_baseline_record(
        season=season,
        schedule=snapshot.schedule,
        findings_report=report,
        note=note or str(existing.get("note") or ""),
        actor=actor,
    )
    decisions = copy.deepcopy(snapshot.decisions)
    decisions.setdefault(SEASON_BASELINE_HISTORY_KEY, []).append(
        {"event": "advance", "at": now, "actor": _operator_identity(actor), "baseline": existing, "comparison": comparison.get("summary")}
    )
    decisions[SEASON_BASELINE_KEY] = baseline
    decisions["updated_at"] = now
    _append_decision_history(
        decisions,
        event="season_baseline_advance",
        tournament_id="",
        actor=actor,
        now=now,
        note=note,
        details={
            "finding_count": baseline["finding_count"],
            "comparison": comparison.get("summary"),
            "finding_context_fingerprint": baseline["finding_context_fingerprint"],
        },
    )
    committed = service._commit(snapshot.with_decisions(decisions))
    return service.season_baseline_show(season) | {
        "advanced": True,
        "canonical_state_revision": canonical_state_revision(committed.schedule, committed.decisions),
    }
