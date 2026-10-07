"""Search capability and coverage for maintenance findings.

This module owns the search capability configuration and coverage tracking
for maintenance findings, including bounded search exhaustion tracking.
"""

from __future__ import annotations

from typing import Any, Iterable, List, Mapping, Optional

from .search_capability import SearchCapability


# Search coverage status constants
SEARCH_COVERAGE_INCOMPLETE = "incomplete"
SEARCH_COVERAGE_OPTION_AVAILABLE = "option_available"
SEARCH_COVERAGE_BOUNDED_EXHAUSTED = "bounded_exhausted"

# Maintenance search version - increment when the search algorithm changes
MAINTENANCE_SEARCH_VERSION = 1


def maintenance_search_capability(finding: Mapping[str, Any]) -> SearchCapability:
    """Current bounded-search capability for a generic maintenance finding.

    The unplaced-placement family owns a richer capability fingerprint in its
    provider; this covers the remaining generic providers so every coverage
    record still names the search that produced it.
    """
    from .season_maintenance import supported_dimensions_for_finding

    supported = supported_dimensions_for_finding(finding)
    category = str(finding.get("category") or "generic")
    return SearchCapability(
        family=category or "generic",
        version=MAINTENANCE_SEARCH_VERSION,
        parameters={"dimensions": list(supported)},
    )


def cheap_search_coverage(finding: Mapping[str, Any]) -> Dict[str, Any]:
    """Conservative coverage for a finding whose bounded search has not run yet.

    The cheap listing path never runs the provider search, so every supported
    dimension is still untried: ``search_incomplete``, never a false
    ``bounded_search_exhausted``.
    """
    from .season_maintenance import supported_dimensions_for_finding

    supported = list(supported_dimensions_for_finding(finding))
    return {
        "status": SEARCH_COVERAGE_INCOMPLETE,
        "supported": supported,
        "untried": list(supported),
        "attempted": [],
        "search_requested": False,
        "proven_infeasible": False,
        "capability": maintenance_search_capability(finding).to_dict(),
    }


def derive_search_coverage(
    *,
    option_count: int,
    rejected_count: int,
    allow_search: bool,
    supported: Iterable[str],
    capability: Optional[SearchCapability] = None,
) -> Dict[str, Any]:
    """Resolved coverage after a provider actually ran for one finding.

    ``bounded_search_exhausted`` means exactly "the configured bounded search
    ran and produced no verified option", never "no solution exists".
    ``rejected_count`` is carried so a caller can inspect the deterministic
    rejection evidence instead of treating the absence of options as proof.
    The capability fingerprint records *which* search this evidence describes.
    """
    supported_list = [str(item) for item in supported]
    if option_count > 0:
        status = SEARCH_COVERAGE_OPTION_AVAILABLE
    elif allow_search:
        status = SEARCH_COVERAGE_BOUNDED_EXHAUSTED
    else:
        status = SEARCH_COVERAGE_INCOMPLETE
    payload: Dict[str, Any] = {
        "status": status,
        "supported": supported_list,
        "untried": [] if allow_search or option_count > 0 else list(supported_list),
        "attempted": list(supported_list) if allow_search else [],
        "search_requested": bool(allow_search),
        "rejected_count": int(rejected_count),
        "proven_infeasible": False,
    }
    if capability is not None:
        payload["capability"] = capability.to_dict()
    return payload