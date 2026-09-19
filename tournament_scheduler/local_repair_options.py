"""One common application boundary over deterministic repair-option providers.

Stage 3's local repair capability is split into small, planner-neutral
providers (``host_team_missing_repair``, ``underfilled_roster_repair``, and
future families). Each provider enumerates hard-feasible options for its own
finding and applies only a selected option id against an unchanged candidate
fingerprint, rerunning the independent verifier.

This module is the single entry point a controller uses for the generic
``apply_repair_option`` action, so it does not need to know which provider owns
a finding. The provider's option stays authoritative: the repository decides
what is legal and what a mutation does; the harness only chooses among the
returned option ids.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Mapping, Tuple

from .date_policy_relocation import (
    apply_date_policy_relocation_option,
    enumerate_date_policy_relocations,
)
from .host_placement_repair import (
    apply_host_placement_repair_option,
    enumerate_host_placement_repairs,
)
from .hosting_balance_repair import (
    apply_hosting_balance_repair_option,
    enumerate_hosting_balance_repairs,
)
from .host_team_missing_repair import (
    apply_host_team_missing_repair_option,
    candidate_fingerprint,
    enumerate_host_team_missing_repairs,
)
from .movable_capacity_repair import (
    apply_movable_capacity_repair_option,
    enumerate_movable_capacity_repairs,
)
from .participation_deviation_repair import (
    apply_participation_deviation_repair_option,
    enumerate_participation_deviation_repairs,
)
from .placement_preserving_roster_repair import (
    apply_placement_preserving_roster_repair_option,
    enumerate_placement_preserving_roster_repairs,
)
from .search_neighborhood_repair import (
    apply_search_neighborhood_repair_option,
    enumerate_search_neighborhood_repairs,
)
from .underfilled_roster_repair import (
    apply_underfilled_roster_repair_option,
    enumerate_underfilled_roster_repairs,
)

EnumerateFn = Callable[..., Dict[str, Any]]
ApplyFn = Callable[..., Dict[str, Any]]

# Ordered so the smallest/local defect family is offered before broader
# placement families. Adding a family is adding one tuple here -- the
# controller/action surface stays unchanged.
REPAIR_PROVIDERS: Tuple[Tuple[str, EnumerateFn, ApplyFn], ...] = (
    ("underfilled_roster", enumerate_underfilled_roster_repairs, apply_underfilled_roster_repair_option),
    ("host_team_missing", enumerate_host_team_missing_repairs, apply_host_team_missing_repair_option),
    (
        "placement_preserving_roster",
        enumerate_placement_preserving_roster_repairs,
        apply_placement_preserving_roster_repair_option,
    ),
    ("host_placement", enumerate_host_placement_repairs, apply_host_placement_repair_option),
    (
        "date_policy_relocation",
        enumerate_date_policy_relocations,
        apply_date_policy_relocation_option,
    ),
    ("hosting_balance", enumerate_hosting_balance_repairs, apply_hosting_balance_repair_option),
    (
        "participation_deviation",
        enumerate_participation_deviation_repairs,
        apply_participation_deviation_repair_option,
    ),
    ("movable_capacity", enumerate_movable_capacity_repairs, apply_movable_capacity_repair_option),
    ("search_neighborhood", enumerate_search_neighborhood_repairs, apply_search_neighborhood_repair_option),
)

# Broad families run a bounded search rather than enumerating cheap discrete
# mutations, so the dispatcher only invokes them when no cheaper family has a
# legal option. This keeps a localized defect from paying for a solver pass
# when a direct fill/swap/rehost already verifies.
BROAD_REPAIR_FAMILIES = frozenset({"search_neighborhood"})

# Strong-goal families (hosting balance, participation deviation) are owned by
# the promoted-season maintenance surface and are only enumerated when a
# caller explicitly asks for them. The default cheap-first hard-violation
# enumeration stays exactly as it was, so existing Stage 3 behavior -- and the
# order in which a bounded search is offered -- is unchanged. The apply
# dispatcher still includes them, so an explicitly selected goal option id can
# always be applied through the common boundary.
GOAL_REPAIR_FAMILIES = frozenset({"hosting_balance", "participation_deviation"})


def enumerate_local_repair_options(
    candidate: Mapping[str, Any],
    problem: Mapping[str, Any],
    *,
    run_id: str = "",
    include_goal_families: bool = False,
) -> Dict[str, Any]:
    """Enumerate every provider's options, tagged with their owning family."""
    options = []
    rejected = []
    candidate_weekends: list = []
    families: Dict[str, Dict[str, Any]] = {}
    for family, enumerate_fn, _apply_fn in REPAIR_PROVIDERS:
        if family in GOAL_REPAIR_FAMILIES and not include_goal_families:
            families[family] = {
                "option_count": 0,
                "rejected_count": 0,
                "skipped": "goal_family_not_requested",
            }
            continue
        if family in BROAD_REPAIR_FAMILIES and options:
            # A cheaper family already exposed a legal option; do not burn a
            # bounded search just to add redundant alternatives.
            families[family] = {
                "option_count": 0,
                "rejected_count": 0,
                "skipped": "cheaper_family_has_options",
            }
            continue
        repair_set = enumerate_fn(candidate, problem, run_id=run_id)
        family_options = [{**option, "family": family} for option in repair_set["options"]]
        family_rejected = [
            {**entry, "family": family} for entry in repair_set["rejected_candidates"]
        ]
        options.extend(family_options)
        rejected.extend(family_rejected)
        candidate_weekends.extend(
            {**bundle, "family": family}
            for bundle in repair_set.get("candidate_weekends", ())
        )
        families[family] = {
            "option_count": len(family_options),
            "rejected_count": len(family_rejected),
        }
    return {
        "run_id": run_id,
        "candidate_fingerprint": candidate_fingerprint(candidate),
        "options": options,
        "rejected_candidates": rejected,
        # Read-only, conflict-aware manual-placement weekend evidence from the
        # family that owns it (currently host_placement); never a committed
        # placement and never an applyable option id.
        "candidate_weekends": candidate_weekends,
        "families": families,
    }


def apply_local_repair_option(
    candidate: Mapping[str, Any],
    problem: Mapping[str, Any],
    *,
    option_id: str,
    expected_fingerprint: str,
    run_id: str = "",
) -> Dict[str, Any]:
    """Dispatch a selected option id to its owning provider's atomic apply path."""
    before = candidate_fingerprint(candidate)
    if before != expected_fingerprint:
        return {
            "ok": False,
            "reason": "stale_candidate_fingerprint",
            "before_fingerprint": before,
            "expected_fingerprint": expected_fingerprint,
        }
    repair_set = enumerate_local_repair_options(candidate, problem, run_id=run_id)
    option = next(
        (entry for entry in repair_set["options"] if entry["option_id"] == option_id), None
    )
    if option is None:
        # Goal-family options (hosting balance / participation deviation) are
        # not part of the default cheap-first hard-violation enumeration, so a
        # caller that already selected one is served here without changing
        # which options the ordinary dispatcher offers.
        goal_set = enumerate_local_repair_options(
            candidate, problem, run_id=run_id, include_goal_families=True
        )
        option = next(
            (entry for entry in goal_set["options"] if entry["option_id"] == option_id), None
        )
    if option is None:
        return {"ok": False, "reason": "unknown_or_stale_option", "before_fingerprint": before}
    provider = next(
        (entry for entry in REPAIR_PROVIDERS if entry[0] == option.get("family")), None
    )
    if provider is None:
        return {
            "ok": False,
            "reason": "unknown_repair_family",
            "before_fingerprint": before,
            "family": option.get("family"),
        }
    outcome = provider[2](
        candidate,
        problem,
        option_id=option_id,
        expected_fingerprint=expected_fingerprint,
        run_id=run_id,
    )
    return {**outcome, "family": option.get("family")}
