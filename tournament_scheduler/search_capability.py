"""Canonical identity of a bounded repair/search capability.

``bounded_search_exhausted`` / ``bounded_repair_exhausted`` means only:

    exhausted under *this particular* search implementation, dimensions and
    configured budget.

It is not proof that no legal placement exists (see the shared RVV runbook).
That epistemic limit has a second, easily missed consequence: coverage
evidence is only current for the capability that produced it. If the
repository later widens a neighborhood (a larger date cap, more start times, a
new repair dimension, a changed roster heuristic), the old exhaustion evidence
must become stale and the finding must become eligible for the new search
instead of being trusted as if nothing had changed.

This module owns the one canonical capability identity used by every coverage
surface: the deterministic unplaced-placement provider, the generic
maintenance coverage vocabulary and the Stage 3 convergence controller. A
provider declares its capability (family + version + the parameters that
actually shape the search); a coverage record persists it; a controller
compares the recorded fingerprint against the current one and treats a
mismatch as stale/retryable. It contains no hockey rules and no search
mechanics -- it only makes "which search produced this evidence" explicit.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Mapping

# Bumping the schema changes every capability fingerprint, which correctly
# invalidates every previously recorded exhaustion as stale.
SEARCH_CAPABILITY_SCHEMA_VERSION = 1

# Coverage vocabulary shared with the maintenance/convergence surfaces.
COVERAGE_OPTION_AVAILABLE = "option_available"
COVERAGE_SEARCH_INCOMPLETE = "search_incomplete"
COVERAGE_BOUNDED_EXHAUSTED = "bounded_search_exhausted"
COVERAGE_PROVEN_INFEASIBLE = "proven_infeasible"


def _canonical(value: Any) -> Any:
    """Recursively order mappings/sequences so the payload hash is stable."""
    if isinstance(value, Mapping):
        return {str(key): _canonical(item) for key, item in sorted(value.items(), key=lambda kv: str(kv[0]))}
    if isinstance(value, (list, tuple)):
        return [_canonical(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return sorted((_canonical(item) for item in value), key=str)
    return value


def capability_fingerprint(
    family: str, version: str, parameters: Mapping[str, Any] | None = None
) -> str:
    """Stable short fingerprint for one search capability declaration."""
    payload = json.dumps(
        {
            "schema": SEARCH_CAPABILITY_SCHEMA_VERSION,
            "family": str(family),
            "version": str(version),
            "parameters": _canonical(parameters or {}),
        },
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class SearchCapability:
    """One provider's declared bounded-search identity."""

    family: str
    version: str = "1"
    parameters: Mapping[str, Any] = field(default_factory=dict)

    @property
    def fingerprint(self) -> str:
        return capability_fingerprint(self.family, self.version, self.parameters)

    def to_dict(self) -> dict[str, Any]:
        return {
            "family": str(self.family),
            "version": str(self.version),
            "fingerprint": self.fingerprint,
            "parameters": dict(self.parameters),
        }


def recorded_capability_fingerprint(coverage: Mapping[str, Any] | None) -> str:
    """Fingerprint recorded inside a coverage/marker mapping, or ``""``."""
    if not isinstance(coverage, Mapping):
        return ""
    capability = coverage.get("capability")
    if isinstance(capability, Mapping):
        return str(capability.get("fingerprint") or "")
    # Accept a bare ``search_capability`` fingerprint/marker for legacy or
    # simplified callers.
    marker = coverage.get("search_capability")
    if isinstance(marker, Mapping):
        return str(marker.get("fingerprint") or "")
    if marker:
        return str(marker)
    # A bare capability record (family + fingerprint) is accepted directly so a
    # persisted planner marker and a coverage wrapper compare the same way.
    if coverage.get("family") and coverage.get("fingerprint"):
        return str(coverage.get("fingerprint"))
    return ""


def capability_is_stale(
    recorded: Mapping[str, Any] | str | None,
    current: SearchCapability | str | None,
) -> bool:
    """True when recorded exhaustion evidence predates the current capability.

    A missing *current* capability means the caller cannot decide staleness and
    is never treated as changed. A declared current capability with no recorded
    fingerprint is stale: the evidence cannot be shown to describe the search
    that is about to run, so it must be re-evaluated rather than trusted.
    """
    if isinstance(recorded, str):
        recorded_fp = recorded
    else:
        recorded_fp = recorded_capability_fingerprint(recorded)
    current_fp = (
        current.fingerprint if isinstance(current, SearchCapability) else str(current or "")
    )
    if not current_fp:
        return False
    return recorded_fp != current_fp


def mark_coverage_stale(
    coverage: Mapping[str, Any],
    *,
    current: SearchCapability,
    reason: str = "search_capability_changed",
) -> dict[str, Any]:
    """Return *coverage* rewritten as retryable stale evidence under *current*.

    Only a recorded exhaustion is rewritten: an option-available or
    already-incomplete coverage has nothing to invalidate, but the current
    capability is still attached so the next resolved record is comparable.
    """
    payload = dict(coverage)
    payload["capability"] = current.to_dict()
    if str(payload.get("status") or "") != COVERAGE_BOUNDED_EXHAUSTED:
        return payload
    payload["previous_status"] = COVERAGE_BOUNDED_EXHAUSTED
    payload["status"] = COVERAGE_SEARCH_INCOMPLETE
    payload["capability_stale"] = True
    payload["stale_reason"] = reason
    # Every supported dimension is retryable under the new search; never keep
    # the old "attempted" claim, which described the superseded capability.
    supported = [str(item) for item in payload.get("supported") or []]
    payload["attempted"] = []
    payload["untried"] = supported or list(payload.get("untried") or [])
    payload["search_requested"] = False
    payload["proven_infeasible"] = False
    return payload


__all__ = [
    "COVERAGE_BOUNDED_EXHAUSTED",
    "COVERAGE_OPTION_AVAILABLE",
    "COVERAGE_PROVEN_INFEASIBLE",
    "COVERAGE_SEARCH_INCOMPLETE",
    "SEARCH_CAPABILITY_SCHEMA_VERSION",
    "SearchCapability",
    "capability_fingerprint",
    "capability_is_stale",
    "mark_coverage_stale",
    "recorded_capability_fingerprint",
]
