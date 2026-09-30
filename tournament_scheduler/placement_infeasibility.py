"""Durable, revision-bound proof that a placement obligation is infeasible.

A deterministic bounded search that finds no legal placement proves only that
*that* search, with *those* caps, found nothing (see :mod:`search_capability`).
For an obligation whose full supported scope was actually walked, the search
result can be retained as a typed **infeasibility proof**: it names the
obligation, the authoritative capacity/calendar facts the search consumed, the
capability and scope that were exercised, the candidate/feasibility counts, and
the deterministic rejection reasons. The shared finding-resolution classifier
(:mod:`finding_resolution`) may then treat the obligation as
``proven_infeasible_with_current_capacity`` while the raw obligation stays
visible.

The proof is only current while the authoritative inputs it names are unchanged:
a different calendar fingerprint, ice-time/capacity fact or search capability
makes the proof stale and the obligation actionable again. This module owns
that identity and the pure build/validate logic. It does not run a search, does
not persist anything and encodes no hockey rules -- the provider owns the search
mechanics, the canonical-season service owns persistence and
:mod:`season_maintenance` owns attaching the evidence to findings.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping, Sequence

from .search_capability import SearchCapability, capability_is_stale
from .pipeline.fingerprints import stable_payload_sha256

PROOF_KEY = "placement_infeasibility_proofs"
PROOF_SCHEMA_VERSION = 1

ACTIVE = "active"
SUPERSEDED = "superseded"

#: The one coverage status that establishes infeasibility once a proof exists.
COVERAGE_PROVEN_INFEASIBLE = "proven_infeasible"
COVERAGE_BOUNDED_EXHAUSTED = "bounded_search_exhausted"
COVERAGE_SEARCH_INCOMPLETE = "search_incomplete"

STALE_CAPACITY_CHANGED = "authoritative_capacity_changed"
STALE_CAPABILITY_CHANGED = "search_capability_changed"
STALE_OBLIGATION_ABSENT = "obligation_no_longer_unplaced"


def _team_identity(team: Mapping[str, Any]) -> tuple[str, str, str]:
    return (
        str(team.get("club") or ""),
        str(team.get("label") or ""),
        str(team.get("age_group") or ""),
    )


def _host_arenas(plan: Mapping[str, Any], host: str) -> list[str]:
    arenas = {
        str(tournament.get("arena") or "")
        for tournament in plan.get("tournaments") or []
        if str(tournament.get("host_club") or "") == host
        and str(tournament.get("arena") or "")
    }
    return sorted(arenas)


def _relevant_tournaments(
    plan: Mapping[str, Any],
    obligation: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Tournaments whose existence/capacity the search for *obligation* consumed.

    That is every tournament hosted by the responsible host, every tournament
    in the host's arena(s) (same-arena blockers) and every tournament fielding
    one of the obligation's own teams (roster-collision blockers). The set is
    deliberately a superset of the direct dependency rather than an exact
    replay: a change anywhere in it can change placement feasibility.
    """

    host = str(obligation.get("responsible_host") or obligation.get("host_club") or "")
    arenas = set(_host_arenas(plan, host))
    roster = obligation.get("participant_teams") or []
    roster_identities = {_team_identity(team) for team in roster if isinstance(team, Mapping)}
    rows: list[dict[str, Any]] = []
    for tournament in plan.get("tournaments") or []:
        if not isinstance(tournament, Mapping):
            continue
        same_host = str(tournament.get("host_club") or "") == host
        same_arena = str(tournament.get("arena") or "") in arenas
        roster_overlap = any(
            _team_identity(team) in roster_identities
            for team in tournament.get("teams") or []
            if isinstance(team, Mapping)
        )
        if not (same_host or same_arena or roster_overlap):
            continue
        rows.append(
            {
                "id": str(tournament.get("id") or ""),
                "age_group": str(tournament.get("age_group") or ""),
                "date": str(tournament.get("date") or ""),
                "start_time": str(tournament.get("start_time") or ""),
                "arena": str(tournament.get("arena") or ""),
                "host_club": str(tournament.get("host_club") or ""),
                "cancelled": bool(tournament.get("cancelled")),
            }
        )
    return sorted(rows, key=lambda row: (row["date"], row["start_time"], row["id"]))


def capacity_fingerprint(
    plan: Mapping[str, Any],
    problem: Mapping[str, Any] | None,
    obligation: Mapping[str, Any],
) -> str:
    """Fingerprint the authoritative capacity/calendar facts a search depends on.

    Bound into every proof so the resolution is reopened when any of them
    changes: the responsible host's authoritative calendar evidence, the
    relevant occupied/blocking tournaments, the age-group ice-time contract and
    any per-tournament ice-time override.
    """

    from .calendar_bookings import club_calendar_fingerprint

    host = str(obligation.get("responsible_host") or obligation.get("host_club") or "")
    age_group = str(obligation.get("age_group") or "")
    overrides = (problem or {}).get("ice_time_minutes_overrides") or {}
    relevant_overrides = {
        str(tournament_id): minutes
        for tournament_id, minutes in overrides.items()
        if isinstance(overrides, Mapping)
    }
    payload = {
        "schema": PROOF_SCHEMA_VERSION,
        "host_club": host,
        "host_calendar": club_calendar_fingerprint(problem, host) if host else "",
        "age_group_ice_time_minutes": ((problem or {}).get("ice_time_minutes") or {}).get(age_group),
        "ice_time_minutes_overrides": relevant_overrides,
        "banned_dates": sorted(
            str(item)
            for item in ((problem or {}).get("manual_adjustments") or {}).get("banned_dates") or []
        ),
        "obligation": {
            "id": str(obligation.get("id") or ""),
            "age_group": age_group,
            "date": str(obligation.get("date") or ""),
            "responsible_host": host,
            "round_count": obligation.get("round_count"),
            "required_duration_minutes": obligation.get("required_duration_minutes"),
            "participant_teams": sorted(
                _team_identity(team)
                for team in obligation.get("participant_teams") or []
                if isinstance(team, Mapping)
            ),
        },
        "relevant_tournaments": _relevant_tournaments(plan, obligation),
    }
    return stable_payload_sha256(payload)


def search_scope_complete(coverage: Mapping[str, Any] | None) -> bool:
    """Whether a coverage record walked the whole declared search scope.

    A bounded search only establishes infeasibility for its own scope. This
    requires the search to have actually been requested, every supported
    dimension to be either attempted or structurally inapplicable, and no
    dimension left untried.
    """

    if not isinstance(coverage, Mapping):
        return False
    if not coverage.get("search_requested"):
        return False
    if str(coverage.get("status") or "") != COVERAGE_BOUNDED_EXHAUSTED:
        return False
    if list(coverage.get("untried") or []):
        return False
    supported = {str(item) for item in coverage.get("supported") or []}
    resolved = {str(item) for item in coverage.get("attempted") or []} | {
        str(item) for item in coverage.get("inapplicable") or []
    }
    return supported <= resolved


def _blocked_reasons(rejected_candidates: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for entry in rejected_candidates:
        summary = entry.get("blocked_reasons")
        if isinstance(summary, Mapping) and summary:
            for reason, count in summary.items():
                counts[str(reason)] = counts.get(str(reason), 0) + int(count or 0)
            continue
        reason = str(entry.get("reason") or "")
        if reason:
            counts[reason] = counts.get(reason, 0) + 1
    return dict(sorted(counts.items()))


def build_infeasibility_proof(
    *,
    obligation: Mapping[str, Any],
    coverage: Mapping[str, Any],
    rejected_candidates: Iterable[Mapping[str, Any]],
    capability: SearchCapability,
    plan: Mapping[str, Any],
    problem: Mapping[str, Any] | None,
    canonical_revision: str,
    actor: str,
    recorded_at: str,
) -> dict[str, Any] | None:
    """Build the typed proof for one exhausted obligation, or ``None``.

    Returns ``None`` unless the coverage establishes a completed search scope.
    The verdict is explicit (``proven_infeasible: true`` plus a
    ``proven_infeasible`` coverage status), so a later reader never has to infer
    infeasibility from the mere absence of options.
    """

    if not search_scope_complete(coverage):
        return None
    obligation_id = str(obligation.get("id") or "")
    if not obligation_id:
        return None
    rejected = [dict(entry) for entry in rejected_candidates if isinstance(entry, Mapping)]
    return {
        "schema_version": PROOF_SCHEMA_VERSION,
        "id": obligation_id,
        "obligation_id": obligation_id,
        "status": ACTIVE,
        "proven_infeasible": True,
        "coverage_status": COVERAGE_PROVEN_INFEASIBLE,
        "reason": "bounded_search_zero_feasible_candidates",
        "age_group": str(obligation.get("age_group") or ""),
        "source_date": str(obligation.get("date") or ""),
        "responsible_host": str(
            obligation.get("responsible_host") or obligation.get("host_club") or ""
        ),
        "canonical_revision": str(canonical_revision),
        "capacity_fingerprint": capacity_fingerprint(plan, problem, obligation),
        "search_capability": capability.to_dict(),
        "search_scope": {
            "supported": list(coverage.get("supported") or []),
            "attempted": list(coverage.get("attempted") or []),
            "inapplicable": list(coverage.get("inapplicable") or []),
            "untried": list(coverage.get("untried") or []),
        },
        "candidate_count": len(rejected),
        "rejected_count": len(rejected),
        "feasible_count": 0,
        "blocked_reasons": _blocked_reasons(rejected),
        "recorded_at": str(recorded_at),
        "recorded_by": str(actor),
    }


def proof_is_current(
    proof: Mapping[str, Any] | None,
    *,
    plan: Mapping[str, Any],
    problem: Mapping[str, Any] | None,
    obligation: Mapping[str, Any],
    current_capability: SearchCapability,
) -> tuple[bool, str]:
    """Whether a recorded proof still describes the current authoritative state."""

    if not isinstance(proof, Mapping):
        return False, STALE_OBLIGATION_ABSENT
    if str(proof.get("status") or ACTIVE) != ACTIVE:
        return False, STALE_OBLIGATION_ABSENT
    recorded_capability = proof.get("search_capability")
    if capability_is_stale(recorded_capability, current_capability):
        return False, STALE_CAPABILITY_CHANGED
    if str(proof.get("capacity_fingerprint") or "") != capacity_fingerprint(
        plan, problem, obligation
    ):
        return False, STALE_CAPACITY_CHANGED
    return True, ""


def coverage_from_proof(proof: Mapping[str, Any]) -> dict[str, Any]:
    """Return the finding ``search_coverage`` payload a current proof implies."""

    scope = proof.get("search_scope") or {}
    return {
        "status": COVERAGE_PROVEN_INFEASIBLE,
        "proven_infeasible": True,
        "capability_stale": False,
        "supported": list(scope.get("supported") or []),
        "attempted": list(scope.get("attempted") or []),
        "inapplicable": list(scope.get("inapplicable") or []),
        "untried": list(scope.get("untried") or []),
        "search_requested": True,
        "candidate_count": int(proof.get("candidate_count") or 0),
        "rejected_count": int(proof.get("rejected_count") or 0),
        "feasible_count": int(proof.get("feasible_count") or 0),
        "blocked_reasons": dict(proof.get("blocked_reasons") or {}),
        "capacity_fingerprint": str(proof.get("capacity_fingerprint") or ""),
        "capability": dict(proof.get("search_capability") or {}),
        "proof": {
            "id": str(proof.get("id") or ""),
            "status": str(proof.get("status") or ACTIVE),
            "schema_version": proof.get("schema_version"),
            "proven_infeasible": True,
            "coverage_status": str(proof.get("coverage_status") or COVERAGE_PROVEN_INFEASIBLE),
            "reason": str(proof.get("reason") or ""),
            "canonical_revision": str(proof.get("canonical_revision") or ""),
            "capacity_fingerprint": str(proof.get("capacity_fingerprint") or ""),
            "search_capability": dict(proof.get("search_capability") or {}),
            "search_scope": dict(scope),
            "candidate_count": int(proof.get("candidate_count") or 0),
            "rejected_count": int(proof.get("rejected_count") or 0),
            "feasible_count": int(proof.get("feasible_count") or 0),
            "blocked_reasons": dict(proof.get("blocked_reasons") or {}),
            "recorded_at": str(proof.get("recorded_at") or ""),
            "recorded_by": str(proof.get("recorded_by") or ""),
        },
    }


def stale_coverage_from_proof(
    proof: Mapping[str, Any],
    *,
    reason: str,
    fallback: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a retryable, non-resolving coverage view for a stale proof."""

    payload = dict(fallback or {})
    proof_scope = proof.get("search_scope") or {}
    payload.update(
        {
            "status": COVERAGE_SEARCH_INCOMPLETE,
            "proven_infeasible": False,
            "capability_stale": True,
            "stale_reason": reason,
            "previous_status": COVERAGE_PROVEN_INFEASIBLE,
            "search_requested": False,
            "untried": list(proof_scope.get("supported") or payload.get("untried") or []),
            "attempted": [],
            "proof": {
                "id": str(proof.get("id") or ""),
                "status": "stale",
                "capacity_fingerprint": str(proof.get("capacity_fingerprint") or ""),
                "search_capability": dict(proof.get("search_capability") or {}),
                "recorded_at": str(proof.get("recorded_at") or ""),
                "stale_reason": reason,
            },
        }
    )
    return payload


def proof_records(
    decisions: Mapping[str, Any] | None,
    *,
    include_superseded: bool = False,
) -> list[dict[str, Any]]:
    """Return the durable proofs recorded in canonical decisions state."""

    records = (decisions or {}).get(PROOF_KEY) or []
    out: list[dict[str, Any]] = []
    for record in records:
        if not isinstance(record, Mapping):
            continue
        status = str(record.get("status") or ACTIVE)
        if status != ACTIVE and not include_superseded:
            continue
        out.append(dict(record))
    return out


def proofs_by_obligation(
    decisions: Mapping[str, Any] | None,
) -> dict[str, dict[str, Any]]:
    """Index active proofs by obligation id (newest record wins)."""

    indexed: dict[str, dict[str, Any]] = {}
    for record in proof_records(decisions):
        obligation_id = str(record.get("obligation_id") or record.get("id") or "")
        if obligation_id:
            indexed[obligation_id] = record
    return indexed


def replace_obligation_proof(
    decisions: dict[str, Any],
    proof: Mapping[str, Any],
) -> dict[str, Any]:
    """Append *proof* and supersede any older active proof for the obligation."""

    obligation_id = str(proof.get("obligation_id") or proof.get("id") or "")
    records = list(decisions.get(PROOF_KEY) or [])
    for record in records:
        if not isinstance(record, dict):
            continue
        if str(record.get("obligation_id") or record.get("id") or "") != obligation_id:
            continue
        if str(record.get("status") or ACTIVE) == ACTIVE:
            record["status"] = SUPERSEDED
            record["superseded_at"] = proof.get("recorded_at")
    records.append(dict(proof))
    decisions[PROOF_KEY] = records
    return dict(proof)


def clear_obligation_proofs(
    decisions: dict[str, Any],
    *,
    obligation_ids: Iterable[str],
    cleared_at: str,
    cleared_by: str,
    reason: str = "",
) -> list[str]:
    """Supersede the active proofs for the named obligations."""

    wanted = {str(value) for value in obligation_ids if str(value)}
    cleared: list[str] = []
    for record in decisions.get(PROOF_KEY) or []:
        if not isinstance(record, dict):
            continue
        obligation_id = str(record.get("obligation_id") or record.get("id") or "")
        if obligation_id not in wanted:
            continue
        if str(record.get("status") or ACTIVE) != ACTIVE:
            continue
        record["status"] = SUPERSEDED
        record["superseded_at"] = cleared_at
        record["superseded_by"] = cleared_by
        record["superseded_reason"] = reason
        cleared.append(obligation_id)
    return cleared


__all__ = [
    "ACTIVE",
    "COVERAGE_BOUNDED_EXHAUSTED",
    "COVERAGE_PROVEN_INFEASIBLE",
    "COVERAGE_SEARCH_INCOMPLETE",
    "PROOF_KEY",
    "PROOF_SCHEMA_VERSION",
    "STALE_CAPABILITY_CHANGED",
    "STALE_CAPACITY_CHANGED",
    "STALE_OBLIGATION_ABSENT",
    "SUPERSEDED",
    "build_infeasibility_proof",
    "capacity_fingerprint",
    "clear_obligation_proofs",
    "coverage_from_proof",
    "proof_is_current",
    "proof_records",
    "proofs_by_obligation",
    "replace_obligation_proof",
    "search_scope_complete",
    "stale_coverage_from_proof",
]
