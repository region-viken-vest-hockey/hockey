"""Canonical season baseline for accepted non-hard findings.

A season baseline is a reviewed snapshot of the current *quality debt* in a
promoted canonical season. It is not a waiver and it never suppresses hard
verification failures. The baseline only records non-hard findings with stable
finding ids and deterministic measurements, then compares future fresh findings
against that reference as known/improved/resolved/regressed/new.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Mapping

from tournament_scheduler.canonical_state import schedule_fingerprint
from tournament_scheduler.pipeline.fingerprints import stable_payload_sha256

SEASON_BASELINE_SCHEMA_VERSION = 1

KNOWN = "KNOWN"
IMPROVED = "IMPROVED"
RESOLVED = "RESOLVED"
REGRESSED = "REGRESSED"
NEW = "NEW"

_NON_HARD_STATUSES = (KNOWN, IMPROVED, RESOLVED, REGRESSED, NEW)

_LOWER_IS_BETTER_KEYS = (
    "deficit",
    "spread",
    "material_spread",
    "team_count",
    "home_tournament_count",
    "club_pool_deviation",
)
_ABSOLUTE_KEYS = ("deviation",)
_HIGHER_IS_BETTER_KEYS = ("min_gap_days", "movable_date_count")

_MEASUREMENT_KEYS = (
    "actual",
    "target",
    "deficit",
    "deviation",
    "direction",
    "avoidability",
    "scope",
    "coverage_unresolved",
    "manual_placement_kind",
    "bounded_repair_exhausted",
    "bounded_repair_exhausted_stale",
    "search_attempted",
    "requires_host_confirmation",
    "movable_date_count",
    "min_gap_days",
    "spread",
    "material_spread",
    "home_appearances",
    "club_pool_target",
    "club_pool_actual",
    "club_pool_deviation",
    "min_actual",
    "max_actual",
    "classification",
)


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _actor(actor: str | None) -> str:
    return actor or os.environ.get("RVV_OPERATOR") or os.environ.get("USER") or "operator"


def is_baseline_eligible_finding(finding: Mapping[str, Any]) -> bool:
    """Return True for findings a season baseline may accept as known debt."""

    return str(finding.get("severity") or "").lower() != "hard" and str(
        finding.get("category") or ""
    ) != "hard_violation"


def finding_measurements(finding: Mapping[str, Any]) -> dict[str, Any]:
    measurements = {
        key: finding.get(key)
        for key in _MEASUREMENT_KEYS
        if key in finding and finding.get(key) is not None
    }
    coverage = finding.get("search_coverage")
    if isinstance(coverage, Mapping):
        measurements["search_coverage"] = {
            key: coverage.get(key)
            for key in ("status", "supported", "attempted", "untried", "proven_infeasible")
            if key in coverage
        }
        capability = coverage.get("capability")
        if isinstance(capability, Mapping):
            measurements["search_capability"] = dict(capability)
    return measurements


def finding_severity_score(finding: Mapping[str, Any]) -> float:
    """Return a deterministic lower-is-better severity score for one finding.

    The score intentionally uses the finding's own measured fields rather than
    aggregate counts. Unknown non-hard findings still get a stable present/absent
    score of 1.0 so disappearance is RESOLVED and reappearance is KNOWN.
    """

    score = 0.0
    used = False
    for key in _LOWER_IS_BETTER_KEYS:
        if key in finding and isinstance(finding.get(key), (int, float)):
            score += max(0.0, float(finding[key]))
            used = True
    for key in _ABSOLUTE_KEYS:
        if key in finding and isinstance(finding.get(key), (int, float)):
            score += abs(float(finding[key]))
            used = True
    for key in _HIGHER_IS_BETTER_KEYS:
        if key in finding and isinstance(finding.get(key), (int, float)):
            # Preserve a lower-is-better orientation while allowing higher
            # measured values (e.g. wider min gap, more options) to improve.
            score -= float(finding[key])
            used = True
    if finding.get("coverage_unresolved"):
        score += 1.0
        used = True
    if finding.get("bounded_repair_exhausted"):
        score += 1.0
        used = True
    if finding.get("requires_host_confirmation"):
        score += 1.0
        used = True
    return score if used else 1.0


def finding_identity_record(finding: Mapping[str, Any]) -> dict[str, Any]:
    finding_id = str(finding.get("finding_id") or "")
    code = str(finding.get("code") or "")
    category = str(finding.get("category") or "")
    measurements = finding_measurements(finding)
    severity_score = finding_severity_score(finding)
    return {
        "finding_id": finding_id,
        "code": code,
        "category": category,
        "severity": str(finding.get("severity") or ""),
        "rule_id": finding.get("rule_id"),
        "age_group": finding.get("age_group"),
        "measurements": measurements,
        "severity_score": severity_score,
        "fingerprint": stable_payload_sha256(
            {
                "finding_id": finding_id,
                "code": code,
                "category": category,
                "measurements": measurements,
                "severity_score": severity_score,
            }
        ),
    }


def create_baseline_record(
    *,
    season: str,
    schedule: Mapping[str, Any],
    findings_report: Mapping[str, Any],
    note: str = "",
    actor: str | None = None,
    export_audit_fingerprint: str | None = None,
) -> dict[str, Any]:
    findings = [
        finding_identity_record(finding)
        for finding in findings_report.get("findings") or []
        if isinstance(finding, Mapping) and is_baseline_eligible_finding(finding)
    ]
    findings.sort(key=lambda item: item["finding_id"])
    counts: dict[str, int] = {}
    for entry in findings:
        counts[entry["code"]] = counts.get(entry["code"], 0) + 1
    return {
        "schema_version": SEASON_BASELINE_SCHEMA_VERSION,
        "season": season,
        "created_at": _now_iso(),
        "created_by": _actor(actor),
        "note": note or "",
        "source_canonical_state_revision": str(findings_report.get("revision") or ""),
        "schedule_fingerprint": schedule_fingerprint(schedule.get("plan") or {}),
        "finding_context_fingerprint": stable_payload_sha256(findings),
        "export_audit_fingerprint": export_audit_fingerprint,
        "finding_count": len(findings),
        "counts_by_code": counts,
        "findings": findings,
    }


def compare_findings_to_baseline(
    baseline: Mapping[str, Any] | None,
    current_findings: list[Mapping[str, Any]],
) -> dict[str, Any]:
    """Compare fresh findings against a stored baseline record."""

    if not baseline:
        return {
            "active": False,
            "summary": {status: 0 for status in _NON_HARD_STATUSES},
            "entries": [],
            "regression_count": 0,
            "new_count": 0,
            "ok_to_advance": False,
        }

    baseline_entries = {
        str(entry.get("finding_id") or ""): dict(entry)
        for entry in baseline.get("findings") or []
        if isinstance(entry, Mapping) and entry.get("finding_id")
    }
    current_entries = {
        str(finding.get("finding_id") or ""): finding_identity_record(finding)
        for finding in current_findings
        if isinstance(finding, Mapping)
        and finding.get("finding_id")
        and is_baseline_eligible_finding(finding)
    }
    entries: list[dict[str, Any]] = []
    summary = {status: 0 for status in _NON_HARD_STATUSES}

    for finding_id, old in sorted(baseline_entries.items()):
        current = current_entries.get(finding_id)
        if current is None:
            status = RESOLVED
        else:
            old_score = float(old.get("severity_score") or 0.0)
            new_score = float(current.get("severity_score") or 0.0)
            if new_score > old_score:
                status = REGRESSED
            elif new_score < old_score:
                status = IMPROVED
            else:
                status = KNOWN
        summary[status] += 1
        entries.append({"status": status, "finding_id": finding_id, "baseline": old, "current": current})

    for finding_id, current in sorted(current_entries.items()):
        if finding_id in baseline_entries:
            continue
        summary[NEW] += 1
        entries.append({"status": NEW, "finding_id": finding_id, "baseline": None, "current": current})

    return {
        "active": True,
        "baseline_created_at": baseline.get("created_at"),
        "baseline_created_by": baseline.get("created_by"),
        "baseline_note": baseline.get("note"),
        "baseline_revision": baseline.get("source_canonical_state_revision"),
        "summary": summary,
        "entries": entries,
        "regression_count": summary[REGRESSED],
        "new_count": summary[NEW],
        "ok_to_advance": summary[REGRESSED] == 0 and summary[NEW] == 0,
    }
