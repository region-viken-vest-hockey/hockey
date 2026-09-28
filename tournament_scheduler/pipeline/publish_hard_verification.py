"""Publication-time hard verification over the reviewed Stage 4 export.

The public publish preflight must re-verify the *exact* plan the export
represents against the *exact* provenance-bound problem the export was accepted
with -- the same reviewed handoff promotion uses. Rebuilding a problem from the
mutable Stage 1/2 checkpoints that happen to be on disk applies a different
ruleset: it loses canonical overlays such as host-confirmed per-tournament
ice-time overrides, durable participation withdrawals and canonical roster
renames, and so reports an already accepted, source-backed booking as a hard
violation at publication time while ``season findings`` correctly reports none.

This module owns that one projection. It fails closed: a missing, stale or
inconsistent checkpoint/problem/revision is reported as unverifiable with an
actionable error instead of a silent zero-violation pass, and a successful
semantic ``REVIEW_REQUIRED`` audit can never override a true hard failure.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

#: Human/machine identifier for the verifier layered on top of the planner
#: contract. Kept here so the preflight can explain which rule set it applied.
HARD_VERIFICATION_RULE_SET = "final_verification.verify_final_candidate"


def current_hard_verification(work_dir: str) -> dict[str, Any]:
    """Independently re-verify the reviewed export against its bound context.

    Returns a compact structured projection so the preflight can explain the
    checked revision, plan/problem fingerprints, verifier and per-code counts:

    ``verifiable`` is ``False`` (with ``error``) whenever the reviewed export,
    its verification context, problem, plan snapshot or provenance cannot be
    proven consistent. Callers must treat that as a publication block, not as
    "no violations found".
    """
    from ..final_verification import verify_final_candidate
    from ..planning_contract import extract_candidate
    from .verification_context import (
        VerificationContextError,
        resolve_publish_verification_context,
    )

    report: dict[str, Any] = {
        "schema_version": 1,
        "verifiable": False,
        "ok": False,
        "violations": [],
        "counts_by_code": {},
        "canonical_season": None,
        "canonical_revision": None,
        "export_fingerprint": None,
        "candidate_fingerprint": None,
        "problem_fingerprint": None,
        "export_dir": None,
        "run_id": None,
        "rule_set": HARD_VERIFICATION_RULE_SET,
        "error": None,
    }
    try:
        bound = resolve_publish_verification_context(work_dir=work_dir)
    except VerificationContextError as exc:
        report["error"] = str(exc)
        return report

    report.update(
        {
            "canonical_season": bound.get("canonical_season"),
            "canonical_revision": bound.get("canonical_revision"),
            "export_fingerprint": bound.get("export_fingerprint"),
            "candidate_fingerprint": bound.get("candidate_fingerprint"),
            "problem_fingerprint": bound.get("problem_fingerprint"),
            "export_dir": bound.get("export_dir"),
            "run_id": bound.get("run_id"),
        }
    )

    try:
        candidate = extract_candidate({"plan": bound["reviewed_plan"]})
    except (ValueError, KeyError) as exc:
        report["error"] = f"the reviewed export plan could not be extracted: {exc}"
        return report
    try:
        result = verify_final_candidate(candidate, bound["problem"])
    except Exception as exc:  # noqa: BLE001 - verification failure must fail closed
        report["error"] = f"hard verification raised {type(exc).__name__}: {exc}"
        return report

    violations = [
        dict(item)
        for item in (result.get("violations") or [])
        if isinstance(item, dict)
    ]
    report["verifiable"] = True
    report["violations"] = violations
    report["counts_by_code"] = dict(
        sorted(Counter(str(v.get("code")) for v in violations).items())
    )
    report["ok"] = bool(result.get("ok", True))
    return report


def current_hard_violations(work_dir: str) -> list[str]:
    """String projection of :func:`current_hard_verification` for callers/tests.

    An unverifiable (fail-closed) report surfaces as a single actionable problem
    so no caller reading only the string list can mistake it for "valid".
    """
    report = current_hard_verification(work_dir)
    if report.get("error"):
        return [str(report["error"])]
    return [f"{v.get('code')}: {v.get('message')}" for v in report.get("violations") or []]


def hard_verification_evidence(report: dict[str, Any]) -> list[str]:
    """Compact, structured provenance for the preflight result."""
    evidence = [
        f"hard_verification_rule_set={report.get('rule_set')}",
        f"hard_verification_verifiable={bool(report.get('verifiable'))}",
        f"hard_verification_canonical_revision={report.get('canonical_revision')}",
        f"hard_verification_export_fingerprint={report.get('export_fingerprint')}",
        f"hard_verification_candidate_fingerprint={report.get('candidate_fingerprint')}",
        f"hard_verification_problem_fingerprint={report.get('problem_fingerprint')}",
    ]
    counts = report.get("counts_by_code") or {}
    counts_text = ",".join(f"{code}:{count}" for code, count in counts.items()) or "none"
    evidence.append(f"hard_verification_violation_counts={counts_text}")
    if report.get("error"):
        evidence.append(f"hard_verification_error={report['error']}")
    return evidence


def hard_verification_problems(report: dict[str, Any]) -> list[str]:
    """Human-readable blocking problems for the preflight result."""
    if report.get("error"):
        return [str(report["error"])]
    return [f"{v.get('code')}: {v.get('message')}" for v in report.get("violations") or []]
