"""Repair option discovery and provider dispatch for maintenance.

This module owns the discovery of repair options for findings and the
dispatch to appropriate repair providers, including option generation,
rejection tracking, and provider family coordination.
"""

from __future__ import annotations

from .findings_construction import BOOKING_FEASIBILITY
from .findings_construction import CLUB_DISTRIBUTION
from .findings_construction import HARD_VIOLATION
from .findings_construction import HOME_REPRESENTATION
from .findings_construction import HOSTING
from .findings_construction import MANUAL_PLACEMENT
from .findings_construction import MOVABLE_CAPACITY
from .findings_construction import PARTICIPATION
from .findings_construction import ROSTER_SHAPE
from .findings_construction import TEMPORAL_CLUSTERING
from .local_repair_options import enumerate_local_repair_options
from .search_coverage import derive_search_coverage
from .search_coverage import maintenance_search_capability
from typing import Any
from typing import Dict
from typing import Iterable
from typing import List
from typing import Mapping
from typing import Optional
from typing import Tuple


DEFAULT_DIMENSIONS: Tuple[str, str, str] = ("participants", "host")


SEASON_MAINTENANCE_SCHEMA_VERSION = 1


DEFAULT_DIMENSIONS: Tuple[str, str, str] = ("participants", "host")




# Findings that require a genuine date move (not just participant/host
# reselection) to repair. Kept in sync with
# ``search_neighborhood_repair.DATE_MOVE_VIOLATION_CODES``: the search provider
# owns the mechanics, this module owns the coverage vocabulary.
DATE_MOVING_FINDING_CODES = frozenset({"holiday_date_used"})


# Supported repair/search dimensions per finding category. These describe the
# neighborhood a bounded search may widen into, not a scheduling rule.
SUPPORTED_DIMENSIONS_BY_CATEGORY: Dict[str, Tuple[str, ...]] = {
    HOSTING: ("participants", "host"),
    PARTICIPATION: ("participants",),
    MANUAL_PLACEMENT: ("participants", "host"),
    MOVABLE_CAPACITY: ("host",),
    HARD_VIOLATION: ("participants", "host"),
    ROSTER_SHAPE: (),
    TEMPORAL_CLUSTERING: ("date", "participants"),
    HOME_REPRESENTATION: ("participants",),
    CLUB_DISTRIBUTION: ("participants",),
    BOOKING_FEASIBILITY: (),
}


def supported_dimensions_for_finding(finding: Mapping[str, Any]) -> Tuple[str, ...]:
    """Canonical supported search dimensions for one finding's category."""
    code = str(finding.get("code") or "")
    if code in DATE_MOVING_FINDING_CODES:
        # A date-admissibility defect is only repairable by moving the
        # tournament to a new admissible date, not by re-pairing participants.
        return ("date", "participants", "host")
    category = str(finding.get("category") or "")
    return SUPPORTED_DIMENSIONS_BY_CATEGORY.get(category, DEFAULT_DIMENSIONS)


def _options_for_finding(
    plan: Mapping[str, Any],
    problem: Mapping[str, Any],
    finding: Mapping[str, Any],
    *,
    allow_search: bool,
    dimensions: Iterable[str] = DEFAULT_DIMENSIONS,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Any]]:
    if finding.get("code") == "unplaced_tournament_placement":
        return _unplaced_options(plan, problem, finding, allow_search=allow_search)
    category = finding["category"]
    if category == HOSTING:
        options, rejected, families = _hosting_options(
            plan, problem, finding, allow_search=allow_search, dimensions=dimensions
        )
    elif category == PARTICIPATION:
        options, rejected, families = _participation_options(
            plan, problem, finding, allow_search=allow_search, dimensions=dimensions
        )
    elif category == MOVABLE_CAPACITY:
        options, rejected, families = _movable_capacity_options(plan, problem, finding)
    elif category == TEMPORAL_CLUSTERING:
        options, rejected, families = _coupled_placement_options(plan, problem, finding)
    elif category == HOME_REPRESENTATION:
        options, rejected, families = _home_representation_options(plan, problem, finding)
    elif category == CLUB_DISTRIBUTION:
        options, rejected, families = _intra_club_distribution_options(
            plan, problem, finding, allow_search=allow_search
        )
    else:
        options, rejected, families = _hard_options(
            plan, problem, finding, allow_search=allow_search, dimensions=dimensions
        )
    # Record the resolved coverage on the finding the caller receives, unless
    # the owning provider already produced its own authoritative coverage
    # (the unplaced provider does; that branch returned above).
    finding["search_coverage"] = derive_search_coverage(
        option_count=len(options),
        rejected_count=len(rejected),
        allow_search=allow_search,
        supported=supported_dimensions_for_finding(finding),
        capability=maintenance_search_capability(finding),
    )
    return options, rejected, families


def _hosting_options(
    plan: Mapping[str, Any],
    problem: Mapping[str, Any],
    finding: Mapping[str, Any],
    *,
    allow_search: bool,
    dimensions: Iterable[str] = DEFAULT_DIMENSIONS,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Any]]:
    from .hosting_balance_repair import enumerate_hosting_balance_repairs

    repair_set = enumerate_hosting_balance_repairs(
        plan,
        problem,
        allow_search=allow_search,
        scope={"age_group": finding.get("age_group"), "club": finding.get("club")},
        dimensions=dimensions,
    )
    return _collect(repair_set, finding["finding_id"], family="hosting_balance")


def _unplaced_options(
    plan: Mapping[str, Any],
    problem: Mapping[str, Any],
    finding: Mapping[str, Any],
    *,
    allow_search: bool,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Any]]:
    """Materialization options for one genuine unplaced obligation.

    The provider owns the whole repair ladder (same-date start time, same-host
    date, roster reselection, capacity release, coupled cross-age exchange).
    Its per-obligation coverage is folded back onto the finding so the caller
    can tell ``option_available``/``search_incomplete``/
    ``bounded_search_exhausted`` apart.
    """
    from .unplaced_placement_repair import enumerate_unplaced_placement_repairs

    repair_set = enumerate_unplaced_placement_repairs(
        plan,
        problem,
        finding_ids=[finding["finding_id"]],
        allow_search=allow_search,
    )
    options, rejected, _families = _collect(
        repair_set, finding["finding_id"], family="unplaced_placement"
    )
    coverage = dict((repair_set.get("coverage") or {}).get(finding["finding_id"]) or {})
    if coverage:
        finding["search_coverage"] = coverage
    families = {
        "unplaced_placement": {
            "option_count": len(options),
            "rejected_count": len(rejected),
            "search_coverage": coverage,
        }
    }
    return options, rejected, families


def _coupled_placement_options(
    plan: Mapping[str, Any],
    problem: Mapping[str, Any],
    finding: Mapping[str, Any],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Any]]:
    """Expose verified coupled placement + roster repair options for a cluster.

    The target tournaments are the clustered ones from the finding, and the
    provider pairs each with every other scheduled tournament -- including
    other age groups -- so a repair is not declared unavailable merely because
    same-age moves fail. When a placement exchange double-books a team, the
    provider additionally searches same-age roster reselection for the affected
    tournament instead of rejecting the candidate. Every option is the fully
    coupled, independently verified candidate.
    """

    from .coupled_placement_repair import enumerate_coupled_placement_repairs

    identity = finding.get("team_identity") or {}
    focus_team = (
        str(identity.get("club") or finding.get("club") or ""),
        str(identity.get("label") or finding.get("team") or ""),
        str(identity.get("age_group") or finding.get("age_group") or ""),
    )
    targets = list(finding.get("tournament_ids") or [])
    if finding.get("tournament_id"):
        targets.append(str(finding["tournament_id"]))
    repair_set = enumerate_coupled_placement_repairs(
        plan,
        problem,
        target_tournament_ids=targets,
        focus_team=focus_team,
        finding_id=finding["finding_id"],
    )
    return _collect(repair_set, finding["finding_id"], family="coupled_placement")


def _home_representation_options(
    plan: Mapping[str, Any],
    problem: Mapping[str, Any],
    finding: Mapping[str, Any],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Any]]:
    """Expose verified same-club sibling swaps for one skewed home pool.

    The provider owns the bounded move enumeration (simple rotations plus the
    coupled home + away swaps that keep both siblings' half-season
    participation counts). A coupled placement option is never produced here:
    host/date/arena and hosting responsibility are deliberately unchanged.
    """
    from .home_representation_repair import enumerate_home_representation_repairs

    repair_set = enumerate_home_representation_repairs(
        plan,
        problem,
        scope={"age_group": finding.get("age_group"), "club": finding.get("club")},
    )
    return _collect(repair_set, finding["finding_id"], family="home_representation")


def _intra_club_distribution_options(
    plan: Mapping[str, Any],
    problem: Mapping[str, Any],
    finding: Mapping[str, Any],
    *,
    allow_search: bool,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Any]]:
    """Expose verified same-club sibling substitutions for a distribution pool.

    The provider owns the bounded move enumeration (away tournaments before home
    tournaments, then a bounded coupled sibling-only neighborhood when
    ``allow_search``). It never moves dates/hosts/arenas/slots and never
    transfers hosting responsibility.
    """
    from .intra_club_distribution_repair import enumerate_intra_club_distribution_repairs

    repair_set = enumerate_intra_club_distribution_repairs(
        plan,
        problem,
        allow_search=allow_search,
        scope={
            "club": finding.get("club"),
            "age_group": finding.get("age_group"),
            "scope": finding.get("scope"),
        },
    )
    return _collect(repair_set, finding["finding_id"], family="intra_club_distribution")


def _movable_capacity_options(
    plan: Mapping[str, Any],
    problem: Mapping[str, Any],
    finding: Mapping[str, Any],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Any]]:
    """Expose the movable-capacity provider's verified options for one finding.

    The provider does its own bounded date/slot/roster enumeration, so the
    caller's ``dimensions`` do not narrow it further: its whole result already
    is the bounded same-host search for this obligation.
    """
    from .movable_capacity_repair import enumerate_movable_capacity_repairs

    repair_set = enumerate_movable_capacity_repairs(plan, problem)
    return _collect(
        repair_set,
        finding["finding_id"],
        family="movable_capacity",
        tournament_id=finding.get("tournament_id"),
    )


def _participation_options(
    plan: Mapping[str, Any],
    problem: Mapping[str, Any],
    finding: Mapping[str, Any],
    *,
    allow_search: bool,
    dimensions: Iterable[str] = DEFAULT_DIMENSIONS,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Any]]:
    from .participation_deviation_repair import enumerate_participation_deviation_repairs

    repair_set = enumerate_participation_deviation_repairs(
        plan,
        problem,
        allow_search=allow_search,
        scope={
            "team": finding.get("team"),
            "club": finding.get("club"),
            "age_group": finding.get("age_group"),
            "scope": finding.get("scope"),
        },
        dimensions=dimensions,
    )
    return _collect(repair_set, finding["finding_id"], family="participation_deviation")


def _hard_options(
    plan: Mapping[str, Any],
    problem: Mapping[str, Any],
    finding: Mapping[str, Any],
    *,
    allow_search: bool,
    dimensions: Iterable[str] = DEFAULT_DIMENSIONS,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Any]]:
    from .search_neighborhood_repair import enumerate_search_neighborhood_repairs

    repair_set = enumerate_local_repair_options(plan, problem)
    options, rejected, families = _collect(
        repair_set,
        finding["finding_id"],
        family=None,
        tournament_id=finding.get("tournament_id"),
        tournament_ids=finding.get("tournament_ids"),
    )
    if allow_search:
        search_set = enumerate_search_neighborhood_repairs(
            plan,
            problem,
            scope={
                "age_group": finding.get("age_group"),
                "tournament_id": finding.get("tournament_id"),
            },
            dimensions=dimensions,
        )
        search_options, search_rejected, _ = _collect(
            search_set, finding["finding_id"], family="search_neighborhood", apply_finding_filter=False
        )
        options.extend(search_options)
        rejected.extend(search_rejected)
        families["search_neighborhood"] = {
            "option_count": len(search_options),
            "rejected_count": len(search_rejected),
        }
    return options, rejected, families


def _collect(
    repair_set: Mapping[str, Any],
    finding_id: str,
    *,
    family: Optional[str],
    apply_finding_filter: bool = True,
    tournament_id: Optional[str] = None,
    tournament_ids: Optional[Iterable[str]] = None,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Any]]:
    scope_ids = {str(item) for item in (tournament_ids or []) if item}
    if tournament_id:
        scope_ids.add(str(tournament_id))
    options: List[Dict[str, Any]] = []
    for option in repair_set.get("options") or []:
        if apply_finding_filter and option.get("finding_id") != finding_id:
            # A repair for the same tournament but a different finding family
            # (for example a movable-capacity opportunity for a tournament the
            # placement provider reports as manual) is still a legal repair for
            # this tournament, so keep it rather than hiding it.
            if not (
                scope_ids and str(option.get("tournament_id") or "") in scope_ids
            ):
                continue
        payload = dict(option)
        payload["family"] = family or option.get("family") or ""
        options.append(payload)
    rejected = [
        dict(entry)
        for entry in repair_set.get("rejected_candidates") or []
        if not apply_finding_filter or entry.get("finding_id") == finding_id
    ]
    families = dict(repair_set.get("families") or {})
    if family:
        families[family] = {
            "option_count": len(options),
            "rejected_count": len(rejected),
        }
    return options, rejected, families


def _option_is_applicable(option: Mapping[str, Any]) -> bool:
    """Return whether an annotated option survives every maintenance gate.

    ``_annotate_pareto`` reproduces each option before populating the request
    constraint and operational fields below.  Requiring explicit positive
    results therefore also excludes options which could not be reproduced or
    verified.  Consequence acceptability is provider-owned and defaults to
    acceptable for option families which do not expose that narrower policy.
    """

    return (
        option.get("request_constraint_acceptable") is True
        and option.get("operational_acceptable") is True
        and (option.get("effects") or {}).get("consequence_acceptable") is not False
    )
