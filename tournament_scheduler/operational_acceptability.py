"""One deterministic acceptance policy for canonical schedule mutations.

Hard verification (``verify_candidate``) answers "is this candidate a
structurally legal season?". It deliberately represents some external-calendar
collisions and unresolved obligations as *non-blocking manual work* so a
schedule that still needs human placement can be represented, reviewed and
published with warnings. That makes ``verify_candidate.ok`` too weak as the
sole gate for an automatic canonical mutation: a repair can remove one defect
while silently moving a tournament onto known-unusable ice.

This module owns the separate, repository-owned question:

    Does a candidate *newly introduce* operational work relative to the
    current canonical baseline?

It is a pure non-regression predicate over a plan plus a verification result.
The categories are facts the verifier (and the plan's own placement markers)
already produce:

* a tournament placed inside a host's ``fixed_busy`` external interval
  (``manual_external_conflict_placements``);
* a tournament placed at a host whose calendar is not trustworthy this run
  (``manual_calendar_placements``);
* a manual/unresolved placement obligation (``unresolved_tournament_placements``
  plus the ``manual_booking_reason`` slot-failure marker);
* a host-confirmation dependency, i.e. a ``movable_busy`` allocation the host
  must confirm (``movable_allocations_used`` plus the tournament's own
  ``requires_host_confirmation`` flag).

Existing manual work stays visible and is never auto-converted into a hard
verification failure; the gate only rejects *new* work. A direct operator
request for an exact, known-conflicting placement and an automatic
search-selected candidate are semantically different, so an operator may opt in
explicitly through a typed flag (``allow_manual_placement`` /
``allow_host_confirmation``) rather than the system inferring consent from
``verify_candidate.ok``.
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional

OPERATIONAL_ACCEPTABILITY_SCHEMA_VERSION = 1

CATEGORY_FIXED_BUSY_PLACEMENT = "fixed_busy_placement"
CATEGORY_MANUAL_CALENDAR_PLACEMENT = "manual_calendar_placement"
CATEGORY_UNRESOLVED_PLACEMENT_OBLIGATION = "unresolved_placement_obligation"
CATEGORY_HOST_CONFIRMATION_DEPENDENCY = "host_confirmation_dependency"

# Manual/unresolved placement work an automatic repair must not *newly* create.
MANUAL_WORK_CATEGORIES = (
    CATEGORY_FIXED_BUSY_PLACEMENT,
    CATEGORY_MANUAL_CALENDAR_PLACEMENT,
    CATEGORY_UNRESOLVED_PLACEMENT_OBLIGATION,
)

# A movable_busy allocation is a real placement, but it also carries a
# host-confirmation dependency. It is allowed only with an explicit operator
# opt-in, never as an ordinary automatic improvement.
HOST_CONFIRMATION_CATEGORIES = (CATEGORY_HOST_CONFIRMATION_DEPENDENCY,)

PROFILE_CATEGORIES = MANUAL_WORK_CATEGORIES + HOST_CONFIRMATION_CATEGORIES

OPT_IN_MANUAL_PLACEMENT = "allow_manual_placement"
OPT_IN_HOST_CONFIRMATION = "allow_host_confirmation"

_OPT_IN_BY_CATEGORY: Dict[str, str] = {
    CATEGORY_FIXED_BUSY_PLACEMENT: OPT_IN_MANUAL_PLACEMENT,
    CATEGORY_MANUAL_CALENDAR_PLACEMENT: OPT_IN_MANUAL_PLACEMENT,
    CATEGORY_UNRESOLVED_PLACEMENT_OBLIGATION: OPT_IN_MANUAL_PLACEMENT,
    CATEGORY_HOST_CONFIRMATION_DEPENDENCY: OPT_IN_HOST_CONFIRMATION,
}

_CATEGORY_MESSAGES: Dict[str, str] = {
    CATEGORY_FIXED_BUSY_PLACEMENT: ("places {ids} inside a host's fixed_busy external-calendar interval"),
    CATEGORY_MANUAL_CALENDAR_PLACEMENT: ("places {ids} at a host with no trustworthy calendar evidence"),
    CATEGORY_UNRESOLVED_PLACEMENT_OBLIGATION: ("introduces unresolved/manual tournament placement work ({ids})"),
    CATEGORY_HOST_CONFIRMATION_DEPENDENCY: (
        "introduces a host-confirmation dependency (movable_busy allocation) for {ids}"
    ),
}


def _stable_ids(
    values: Any,
    identity: Any,
) -> List[str]:
    out = set()
    for value in values or []:
        if not isinstance(value, Mapping):
            continue
        resolved = identity(value)
        if resolved:
            out.add(str(resolved))
    return sorted(out)


def operational_placement_profile(
    plan: Optional[Mapping[str, Any]],
    verification: Optional[Mapping[str, Any]],
) -> Dict[str, List[str]]:
    """Return the tournament-keyed operational-work profile for one state.

    The profile is a mapping of :data:`PROFILE_CATEGORIES` to sorted stable ids.
    It is derived from the independent verifier plus plan-borne placement
    markers the verifier cannot rediscover (an exhausted slot search leaves no
    ``Tournament`` to verify), so a category is never silently dropped just
    because one representation changed.
    """

    plan_dict = plan if isinstance(plan, Mapping) else {}
    verification_dict = verification if isinstance(verification, Mapping) else {}

    fixed_busy = _stable_ids(
        verification_dict.get("manual_external_conflict_placements"),
        lambda item: item.get("tournament_id"),
    )
    manual_calendar = _stable_ids(
        verification_dict.get("manual_calendar_placements"),
        lambda item: item.get("tournament_id"),
    )

    unresolved = {
        str(entry.get("id") or "")
        for entry in plan_dict.get("unresolved_tournament_placements") or []
        if isinstance(entry, Mapping) and entry.get("id")
    }
    from .host_placement_repair import is_manual_slot_failure

    for tournament in plan_dict.get("tournaments") or []:
        if not isinstance(tournament, Mapping):
            continue
        if is_manual_slot_failure(tournament):
            tournament_id = str(tournament.get("id") or "")
            if tournament_id:
                unresolved.add(tournament_id)

    host_confirmation = set(
        _stable_ids(
            verification_dict.get("movable_allocations_used"),
            lambda item: item.get("tournament_id"),
        )
    )
    for tournament in plan_dict.get("tournaments") or []:
        if isinstance(tournament, Mapping) and tournament.get("requires_host_confirmation"):
            tournament_id = str(tournament.get("id") or "")
            if tournament_id:
                host_confirmation.add(tournament_id)

    return {
        CATEGORY_FIXED_BUSY_PLACEMENT: fixed_busy,
        CATEGORY_MANUAL_CALENDAR_PLACEMENT: manual_calendar,
        CATEGORY_UNRESOLVED_PLACEMENT_OBLIGATION: sorted(unresolved),
        CATEGORY_HOST_CONFIRMATION_DEPENDENCY: sorted(host_confirmation),
    }


def compare_operational_acceptability(
    before: Mapping[str, Any],
    after: Mapping[str, Any],
    *,
    allow_manual_placement: bool = False,
    allow_host_confirmation: bool = False,
) -> Dict[str, Any]:
    """Non-regression comparison of two operational-placement profiles.

    Returns ``ok`` only when the candidate introduces no operational work in a
    category the operator has not explicitly opted into. The added ids,
    per-category counts and the required opt-in flag are all reported so the
    rejection is inspectable rather than an opaque "not acceptable".
    """

    allowed = {
        OPT_IN_MANUAL_PLACEMENT: bool(allow_manual_placement),
        OPT_IN_HOST_CONFIRMATION: bool(allow_host_confirmation),
    }
    regressions: List[Dict[str, Any]] = []
    added_by_category: Dict[str, List[str]] = {}
    for category in PROFILE_CATEGORIES:
        before_ids = {str(item) for item in (before.get(category) or [])}
        after_ids = {str(item) for item in (after.get(category) or [])}
        added = sorted(after_ids - before_ids)
        if not added:
            continue
        added_by_category[category] = added
        opt_in = _OPT_IN_BY_CATEGORY[category]
        if allowed.get(opt_in):
            continue
        message = _CATEGORY_MESSAGES[category].format(ids=", ".join(added))
        regressions.append(
            {
                "category": category,
                "kind": "new_operational_work",
                "tournament_ids": added,
                "before_count": len(before_ids),
                "after_count": len(after_ids),
                "requires": opt_in,
                "message": f"candidate {message}",
            }
        )

    return {
        "schema_version": OPERATIONAL_ACCEPTABILITY_SCHEMA_VERSION,
        "ok": not regressions,
        "regressions": regressions,
        "added_by_category": added_by_category,
        "allowed": allowed,
        "before_counts": {category: len(before.get(category) or []) for category in PROFILE_CATEGORIES},
        "after_counts": {category: len(after.get(category) or []) for category in PROFILE_CATEGORIES},
    }


def check_operational_acceptability(
    before_plan: Optional[Mapping[str, Any]],
    before_verification: Optional[Mapping[str, Any]],
    after_plan: Optional[Mapping[str, Any]],
    after_verification: Optional[Mapping[str, Any]],
    *,
    allow_manual_placement: bool = False,
    allow_host_confirmation: bool = False,
) -> Dict[str, Any]:
    """Compute both profiles and return the non-regression verdict."""

    before_profile = operational_placement_profile(before_plan, before_verification)
    after_profile = operational_placement_profile(after_plan, after_verification)
    result = compare_operational_acceptability(
        before_profile,
        after_profile,
        allow_manual_placement=allow_manual_placement,
        allow_host_confirmation=allow_host_confirmation,
    )
    result["before_profile"] = before_profile
    result["after_profile"] = after_profile
    return result


def required_opt_in_flags(acceptability: Mapping[str, Any]) -> List[str]:
    """Return the distinct opt-in flags a rejected candidate would need."""

    flags: List[str] = []
    for regression in acceptability.get("regressions") or []:
        flag = str(regression.get("requires") or "")
        if flag and flag not in flags:
            flags.append(flag)
    return flags


__all__ = [
    "CATEGORY_FIXED_BUSY_PLACEMENT",
    "CATEGORY_HOST_CONFIRMATION_DEPENDENCY",
    "CATEGORY_MANUAL_CALENDAR_PLACEMENT",
    "CATEGORY_UNRESOLVED_PLACEMENT_OBLIGATION",
    "HOST_CONFIRMATION_CATEGORIES",
    "MANUAL_WORK_CATEGORIES",
    "OPERATIONAL_ACCEPTABILITY_SCHEMA_VERSION",
    "OPT_IN_HOST_CONFIRMATION",
    "OPT_IN_MANUAL_PLACEMENT",
    "PROFILE_CATEGORIES",
    "check_operational_acceptability",
    "compare_operational_acceptability",
    "operational_placement_profile",
    "required_opt_in_flags",
]
