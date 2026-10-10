"""Construction and classification of maintenance findings.

This module owns the construction of findings from verification results and
planning problems, including the classification of different finding types
(hard violations, hosting issues, participation deviations, etc.).
"""

from __future__ import annotations

from .finding_resolution import annotate_resolutions
from .hosting.balance_repair import hosting_finding_id
from .maintenance_context import SeasonMaintenanceError
from .participation_deviation_repair import legacy_participation_finding_id
from .participation_deviation_repair import participation_finding_id
from .participation_targets import INTRA_CLUB_DISTRIBUTION
from .placement_infeasibility import coverage_from_proof
from .placement_infeasibility import proof_is_current
from .placement_infeasibility import proofs_by_obligation
from .placement_infeasibility import stale_coverage_from_proof
from .planning_contract import verify_candidate
from .rule_catalog import annotate_findings
from .search_coverage import cheap_search_coverage
from .unplaced_placement_repair import obligation_search_coverage
from .unplaced_placement_repair import unplaced_placement_search_capability
from typing import Any
from typing import Dict
from typing import Iterable
from typing import List
from typing import Mapping
from typing import Optional
from typing import Tuple


def construct_findings(
    plan: Mapping[str, Any],
    problem: Mapping[str, Any],
    verification: Mapping[str, Any],
    *,
    decisions: Optional[Mapping[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """Construct findings from a verification result and planning problem.

    This is the core finding construction logic that combines results from
    various finding families (hard, hosting, participation, manual, etc.)
    into a single findings list with proper annotation and resolution.

    Args:
        plan: The candidate plan being evaluated
        problem: The planning problem with canonical overlays
        verification: The result from verify_candidate(plan, problem)
        decisions: Optional canonical decisions (defaults to problem decisions)

    Returns:
        A list of finding dictionaries, annotated and sorted
    """
    if decisions is None:
        decisions = problem.get("decisions") or {}
    
    findings: List[Dict[str, Any]] = []
    findings.extend(_hard_findings(plan, verification))
    findings.extend(_hosting_findings(problem, plan))
    findings.extend(_participation_findings(verification, problem))
    findings.extend(_manual_findings(plan, verification))
    findings.extend(_unplaced_findings(plan, problem, decisions=decisions))
    findings.extend(_movable_capacity_findings(problem, plan))
    findings.extend(_shape_findings(verification))
    findings.extend(_booking_feasibility_findings(verification))
    findings.extend(_spacing_findings(problem, plan))
    findings.extend(_home_representation_findings(problem, plan))
    findings.extend(_intra_club_distribution_findings(verification))
    findings.sort(key=lambda entry: (entry["category"], entry["finding_id"]))
    
    # Attach the stable catalog rule ID to every finding whose code is a
    # registered semantic, so the controller payload carries identity instead
    # of prose categories alone.
    annotate_findings(findings)
    annotate_resolutions(findings)
    
    # Every finding carries a coverage view so a controller never has to infer
    # "untried dimensions remain" from the absence of options. The unplaced
    # provider already attached its authoritative per-obligation coverage; the
    # other families get the conservative cheap view here.
    for finding in findings:
        finding.setdefault("search_coverage", cheap_search_coverage(finding))
        
    return findings


def _require_finding(
    findings: List[Dict[str, Any]], finding_id: str, *, age_group: str | None = None
) -> Dict[str, Any]:
    finding = next((entry for entry in findings if entry["finding_id"] == finding_id), None)
    if finding is not None:
        if age_group and str(finding.get("age_group") or "") != str(age_group):
            raise SeasonMaintenanceError(
                f"Finding id {finding_id} is for age group {finding.get('age_group')}; "
                f"requested {age_group}"
            )
        return finding

    legacy_matches = _legacy_participation_matches(findings, finding_id, age_group=age_group)
    if len(legacy_matches) == 1:
        return legacy_matches[0]
    if legacy_matches:
        alternatives = ", ".join(
            f"--finding {entry['finding_id']} --age-group {entry.get('age_group')}"
            for entry in legacy_matches
        )
        raise SeasonMaintenanceError(
            f"Ambiguous legacy participation finding id {finding_id}; matches age groups "
            f"{', '.join(str(entry.get('age_group') or '') for entry in legacy_matches)}. "
            f"Use an exact selector: {alternatives}"
        )
    raise SeasonMaintenanceError(f"Unknown or stale finding id: {finding_id}")


def _legacy_participation_matches(
    findings: List[Dict[str, Any]], finding_id: str, *, age_group: str | None = None
) -> List[Dict[str, Any]]:
    parts = finding_id.split(":")
    if len(parts) != 4 or parts[0] != "participation_deviation":
        return []
    _prefix, club, team, scope = parts
    legacy_id = legacy_participation_finding_id(club, team, scope)
    if legacy_id != finding_id:
        return []
    matches = [
        entry
        for entry in findings
        if entry.get("category") == PARTICIPATION
        and str(entry.get("club") or "") == club
        and str(entry.get("team") or "") == team
        and str(entry.get("scope") or "") == scope
        and (not age_group or str(entry.get("age_group") or "") == str(age_group))
    ]
    return sorted(matches, key=lambda entry: (str(entry.get("age_group") or ""), entry["finding_id"]))


def _findings_for_option(findings: List[Dict[str, Any]], option_id: str) -> List[Dict[str, Any]]:
    # Preference-ordered scan: hosting/participation option ids encode their
    # finding scope, and hard-finding providers are cheap. A search-origin
    # option should normally be applied with an explicit finding id.
    selected = [
        finding
        for finding in findings
        if finding["category"]
        in (
            HOSTING,
            PARTICIPATION,
            HARD_VIOLATION,
            MANUAL_PLACEMENT,
            MOVABLE_CAPACITY,
            HOME_REPRESENTATION,
            CLUB_DISTRIBUTION,
        )
    ]
    return selected or findings


def _infer_finding_id(
    option_id: str, findings: List[Dict[str, Any]]
) -> Optional[Dict[str, Any]]:
    """Recover the owning finding from an option id when the caller omitted it.

    Hosting and participation option ids embed their stable finding id as a
    substring, so an apply can resolve the exact scope without re-running a
    bounded search for every finding in the season.
    """
    matches = [
        finding
        for finding in findings
        if (
            finding["category"] in (HOSTING, PARTICIPATION, MOVABLE_CAPACITY, HOME_REPRESENTATION, CLUB_DISTRIBUTION)
            or finding.get("code") == "unplaced_tournament_placement"
        )
        and finding["finding_id"] in option_id
    ]
    if not matches:
        return None
    return max(matches, key=lambda finding: len(finding["finding_id"]))


# Categories are facts about what kind of finding this is, not a mandatory
# processing order: the harness may select any finding independently.
HARD_VIOLATION = "hard_violation"


HOSTING = "hosting"


PARTICIPATION = "participation"


MANUAL_PLACEMENT = "manual_placement"


MOVABLE_CAPACITY = "movable_capacity"


ROSTER_SHAPE = "roster_shape"


TEMPORAL_CLUSTERING = "temporal_clustering"


HOME_REPRESENTATION = "home_representation"


# A multi-team club x age-group x scope player pool that has enough aggregate
# participation but uneven sibling labels. This is the actionable counterpart
# of the informational ``intra_club_participation_distribution`` rule.
CLUB_DISTRIBUTION = "club_distribution"


# A source-confirmed accepted booking interval below the governing planning
# floor: a durable audit/follow-up finding, never a hard planning violation
# (ADR 0005). It has no search/repair dimensions of its own.
BOOKING_FEASIBILITY = "booking_feasibility"


def _acceptances_by_scope(
    problem: Mapping[str, Any],
) -> Dict[Tuple[str, str, str, str], Dict[str, Any]]:
    """Index the problem's operator acceptances by ``(club, label, age, scope)``."""
    out: Dict[Tuple[str, str, str, str], Dict[str, Any]] = {}
    evidence = problem.get("participation_search_evidence")
    if not isinstance(evidence, Mapping):
        return out
    for identity, entries in evidence.items():
        if not isinstance(identity, tuple) or len(identity) != 3:
            continue
        for entry in entries if isinstance(entries, (list, tuple)) else [entries]:
            if not isinstance(entry, Mapping):
                continue
            if str(entry.get("status") or "") != "operator_accepted":
                continue
            scope = str(entry.get("scope") or "")
            out[(str(identity[0]), str(identity[1]), str(identity[2]), scope)] = dict(entry)
    return out


def findings_for_plan(
    plan: Mapping[str, Any], problem: Mapping[str, Any]
) -> List[Dict[str, Any]]:
    """Stable actionable findings over any candidate plan + planning problem."""
    return construct_findings(plan, problem, verify_candidate(dict(plan), dict(problem)))


def _hard_findings(plan: Mapping[str, Any], verification: Mapping[str, Any]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for index, violation in enumerate(verification.get("violations") or [], start=1):
        code = str(violation.get("code") or "hard_violation")
        tournament_ids = sorted(
            {str(item) for item in violation.get("tournament_ids") or [] if item}
        )
        tournament_id = str(violation.get("tournament_id") or "")
        if tournament_id and tournament_id not in tournament_ids:
            tournament_ids = sorted({tournament_id, *tournament_ids})
        # A stable finding id: prefer the exact tournament scope the finding
        # names, and only fall back to the violation index when the verifier
        # reports no tournament identity at all.
        if tournament_ids:
            finding_id = f"{code}:{'+'.join(tournament_ids)}"
        else:
            finding_id = f"{code}:{index}"
        entry: Dict[str, Any] = {
            "finding_id": finding_id,
            "code": code,
            "category": HARD_VIOLATION,
            "severity": "hard",
            "age_group": violation.get("age_group"),
            "tournament_id": tournament_ids[0] if tournament_ids else None,
            "message": violation.get("message") or code,
        }
        if tournament_ids:
            entry["tournament_ids"] = tournament_ids
        for key in ("team", "date"):
            if violation.get(key) is not None:
                entry[key] = violation[key]
        out.append(entry)
    return out


def _hosting_findings(problem: Mapping[str, Any], plan: Mapping[str, Any]) -> List[Dict[str, Any]]:
    from .hosting.balance_repair import hosting_deficit_rows

    out: List[Dict[str, Any]] = []
    for row in hosting_deficit_rows(problem, plan):
        age_group = str(row["age_group"])
        club = str(row["club"])
        out.append(
            {
                "finding_id": hosting_finding_id(age_group, club),
                "code": "unresolved_hosting_obligation" if row.get("coverage_unresolved") else "hosting_balance_imbalance",
                "category": HOSTING,
                "severity": "strong_goal",
                "age_group": age_group,
                "club": club,
                "target": int(row.get("target", 0)),
                "actual": int(row.get("actual", 0)),
                "deficit": int(row.get("deficit", 0)),
                "coverage_unresolved": bool(row.get("coverage_unresolved")),
                "message": (
                    f"{club} has a {age_group} hosting deficit of {int(row.get('deficit', 0))} "
                    f"(actual {int(row.get('actual', 0))} vs target {int(row.get('target', 0))})"
                ),
            }
        )
    return out


def _participation_findings(
    verification: Mapping[str, Any], problem: Mapping[str, Any]
) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    acceptances = _acceptances_by_scope(problem)
    for deviation in verification.get("participation_deviations") or []:
        if not _deviation_is_unresolved(deviation):
            # An aggregate-complete, uneven multi-team pool is intra-club
            # distribution, not an unresolved participation deficit -- it stays
            # visible as evidence/metrics but is not an actionable finding and
            # must not trigger repair search.
            continue
        club = str(deviation.get("club") or "")
        team = str(deviation.get("team") or "")
        scope = str(deviation.get("scope") or "")
        avoidability = str(deviation.get("avoidability") or "")
        finding: Dict[str, Any] = {
            "finding_id": participation_finding_id(club, team, str(deviation.get("age_group") or ""), scope),
            "code": "participation_deviation",
            "category": PARTICIPATION,
            "severity": "strong_goal",
            "age_group": deviation.get("age_group"),
            "club": club,
            "team": team,
            "scope": scope,
            "direction": deviation.get("direction"),
            "actual": int(deviation.get("actual", 0)),
            "target": deviation.get("target"),
            "deviation": int(deviation.get("deviation", 0)),
            "avoidability": avoidability,
            "proven_infeasible": avoidability == "proven_infeasible",
            "searchable": True,
            "message": (
                f"{team} ({club}, {deviation.get('age_group')}) is {deviation.get('direction')} "
                f"by {abs(int(deviation.get('deviation', 0)))} in {scope} "
                f"({avoidability or 'unclassified'})"
            ),
        }
        acceptance = acceptances.get((club, team, str(deviation.get("age_group") or ""), scope))
        if acceptance is not None:
            finding["acceptance_id"] = acceptance.get("id")
            if avoidability == "operator_accepted":
                finding["accepted"] = True
                finding["acceptance"] = dict(acceptance)
            else:
                # The persisted acceptance exists but no longer explains the
                # current deviation (target changed or deviation got worse), so
                # it is surfaced as stale rather than silently masking a finding.
                finding["accepted"] = False
                finding["acceptance_stale"] = True
                finding["acceptance"] = dict(acceptance)
        out.append(finding)
    return out


def _manual_findings(
    plan: Mapping[str, Any], verification: Mapping[str, Any]
) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    seen: set = set()
    for key, category in (
        ("manual_participation_placements", "participation"),
        ("manual_calendar_placements", "calendar"),
        ("manual_external_conflict_placements", "external_conflict"),
    ):
        for placement in verification.get(key) or []:
            tournament_id = str(placement.get("tournament_id") or "")
            if not tournament_id or tournament_id in seen:
                continue
            seen.add(tournament_id)
            out.append(
                {
                    "finding_id": f"manual_placement:{tournament_id}",
                    "code": "manual_placement",
                    "category": MANUAL_PLACEMENT,
                    "severity": "unresolved",
                    "age_group": placement.get("age_group"),
                    "tournament_id": tournament_id,
                    "host_club": placement.get("host_club"),
                    "manual_placement_kind": category,
                    "message": f"{tournament_id} requires manual placement ({category})",
                }
            )
    # An unresolved placement the planner marked on the tournament itself is a
    # plan-level fact verification cannot always rediscover: a manually chosen
    # slot may be calendar-free while still being an unconfirmed booking. Own
    # the same stable finding id as ``host_placement_repair`` so its repair
    # options are reachable without reconstructing the marker in a caller.
    from .host_placement_repair import is_manual_slot_failure

    for tournament in plan.get("tournaments") or []:
        tournament_id = str(tournament.get("id") or "")
        if not tournament_id or tournament_id in seen:
            continue
        if not is_manual_slot_failure(tournament):
            continue
        seen.add(tournament_id)
        out.append(
            {
                "finding_id": f"manual_placement:{tournament_id}",
                "code": "manual_placement",
                "category": MANUAL_PLACEMENT,
                "severity": "unresolved",
                "age_group": tournament.get("age_group"),
                "tournament_id": tournament_id,
                "host_club": tournament.get("host_club"),
                "manual_placement_kind": "calendar",
                "message": (
                    f"{tournament_id} is scheduled but still marked as an unresolved "
                    f"manual placement (calendar)"
                ),
            }
        )
    return out


def _unplaced_findings(
    plan: Mapping[str, Any],
    problem: Optional[Mapping[str, Any]] = None,
    *,
    decisions: Optional[Mapping[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """Findings for genuine unplaced obligations (no tournament exists).

    A tournament the deterministic slot search could not place is not a
    scheduled tournament with a marker; it only exists as structured planning
    work in ``unresolved_tournament_placements``. Own the stable finding id the
    planner assigned it (``placement_findings``), never a synthetic tournament
    id, so the obligation stays visible/actionable without a fake placement.
    """
    out: List[Dict[str, Any]] = []

    proofs = proofs_by_obligation(decisions) if decisions else {}
    capability = unplaced_placement_search_capability()
    for entry in plan.get("unresolved_tournament_placements") or []:
        if not isinstance(entry, Mapping):
            continue
        finding_id = str(entry.get("id") or "")
        if not finding_id:
            # Older/hand-built records without a canonical id: derive the same
            # stable identity shape from the finding's own scope rather than
            # hiding the obligation.
            finding_id = (
                f"unplaced_placement:{entry.get('age_group') or '?'}:"
                f"{entry.get('date') or '?'}"
            )
        coverage = obligation_search_coverage(plan, problem or {}, entry, allow_search=False)
        proof = proofs.get(finding_id)
        if proof is not None:
            is_current, stale_reason = proof_is_current(
                proof,
                plan=plan,
                problem=problem or {},
                obligation=entry,
                current_capability=capability,
            )
            coverage = (
                coverage_from_proof(proof)
                if is_current
                else stale_coverage_from_proof(proof, reason=stale_reason, fallback=coverage)
            )
        out.append(
            {
                "finding_id": finding_id,
                "code": "unplaced_tournament_placement",
                "category": MANUAL_PLACEMENT,
                "severity": "unresolved",
                "age_group": entry.get("age_group"),
                "date": entry.get("date"),
                "host_club": entry.get("responsible_host"),
                "responsible_host": entry.get("responsible_host"),
                "reason": entry.get("reason"),
                "search_attempted": bool(entry.get("search_attempted")),
                "bounded_repair_exhausted": bool(entry.get("bounded_repair_exhausted")),
                # A planner exhaustion claim written by a superseded search is
                # not current evidence: surface it as stale/retryable rather
                # than indistinguishable from a fresh result.
                "bounded_repair_exhausted_stale": bool(coverage.get("capability_stale")),
                "search_capability": dict(entry.get("search_capability") or {}),
                # The planner's own ``bounded_repair_exhausted`` flag describes
                # only the search it actually ran. The supported ladder is
                # broader, so report what this capability can still attempt
                # instead of letting that flag read as global infeasibility.
                "search_coverage": coverage,
                "message": (
                    f"{entry.get('age_group') or '?'} on {entry.get('date') or '?'} could not be "
                    f"placed automatically; responsible host {entry.get('responsible_host') or 'unknown'} "
                    "keeps the hosting obligation"
                ),
            }
        )
    return out


def _movable_capacity_findings(
    problem: Mapping[str, Any], plan: Mapping[str, Any]
) -> List[Dict[str, Any]]:
    """Expose host-controlled ice that is available to a blocked same-host placement.

    The finding is a fact (the responsible host controls movable ice on a
    candidate date), not a committed placement: it always carries
    ``requires_host_confirmation`` and the selected repair option still passes
    full verification. It is deliberately separate from the owning
    ``manual_placement`` finding so a caller can select the ice opportunity
    directly instead of reconstructing it from provider options.
    """
    from .movable_capacity_repair import movable_capacity_opportunities

    out: List[Dict[str, Any]] = []
    for opportunity in movable_capacity_opportunities(plan, problem):
        dates = opportunity.get("movable_dates") or []
        first = dates[0] if dates else {}
        out.append(
            {
                "finding_id": opportunity["finding_id"],
                "code": "movable_capacity_opportunity",
                "category": MOVABLE_CAPACITY,
                "severity": "opportunity",
                "age_group": opportunity.get("age_group"),
                "tournament_id": opportunity.get("tournament_id"),
                "host_club": opportunity.get("host_club"),
                "original_date": opportunity.get("original_date"),
                "movable_date_count": len(dates),
                "earliest_movable_date": first.get("date"),
                "requires_host_confirmation": True,
                "message": (
                    f"{opportunity['tournament_id']} can use host-controlled ice for "
                    f"{opportunity.get('host_club')} on {first.get('date')} "
                    f"(requires host confirmation)"
                ),
            }
        )
    return out


def _shape_findings(verification: Mapping[str, Any]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for index, shape in enumerate(verification.get("input_constrained_shapes") or [], start=1):
        age_group = str(shape.get("age_group") or "")
        out.append(
            {
                "finding_id": f"input_constrained_shape:{age_group}:{index}",
                "code": "input_constrained_shape",
                "category": ROSTER_SHAPE,
                "severity": "strong_goal",
                "age_group": age_group or None,
                "message": shape.get("message") or f"input-constrained tournament shape for {age_group}",
                "facts": dict(shape),
            }
        )
    return out


def _booking_feasibility_findings(verification: Mapping[str, Any]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for warning in verification.get("booking_feasibility_warnings") or []:
        tournament_id = str(warning.get("tournament_id") or "")
        out.append(
            {
                "finding_id": f"ice_time_governing_minimum:{tournament_id or 'booking'}",
                "code": "ice_time_governing_minimum",
                "category": BOOKING_FEASIBILITY,
                "severity": "follow_up",
                "tournament_id": tournament_id or None,
                "age_group": warning.get("age_group"),
                "accepted_booking_interval": True,
                "accepted_exception": warning.get("accepted_exception"),
                "message": warning.get("message")
                or "accepted booking interval below the governing floor",
            }
        )
    return out


def _spacing_findings(
    problem: Mapping[str, Any], plan: Mapping[str, Any]
) -> List[Dict[str, Any]]:
    """Actionable temporal-clustering findings for teams with a tight local cluster.

    A team whose own tournaments fall within a few days of each other is a real
    schedule-quality defect. The finding names the cluster and, crucially,
    allows the bounded search to enumerate *cross-age* placement exchanges for
    the clustered tournaments instead of concluding the cluster is
    structurally unavoidable because same-age moves failed.
    """

    from .team_schedule_quality import (
        CLOSE_GAP_DAYS,
        PREFERRED_GAP_DAYS,
        team_schedule_profile,
    )

    out: List[Dict[str, Any]] = []
    tournaments = [
        dict(tournament)
        for tournament in plan.get("tournaments", []) or []
        if not tournament.get("cancelled")
    ]
    for team in problem.get("teams") or []:
        identity = (
            str(team.get("club") or ""),
            str(team.get("label") or ""),
            str(team.get("age_group") or ""),
        )
        if not identity[0] or not identity[1]:
            continue
        profile = team_schedule_profile(plan, identity, problem=problem)
        gaps = profile.get("spacing") or {}
        min_gap = gaps.get("min_gap_days")
        if min_gap is None or int(min_gap) >= CLOSE_GAP_DAYS:
            continue
        cluster_dates = _clustered_dates(
            profile.get("tournament_dates") or [],
            close_gap=CLOSE_GAP_DAYS,
            neighbor_gap=PREFERRED_GAP_DAYS,
        )
        if len(cluster_dates) < 2:
            continue
        cluster = set(cluster_dates)
        tournament_ids = sorted(
            str(tournament.get("id") or "")
            for tournament in tournaments
            if str(tournament.get("date") or "") in cluster
            and any(
                str(member.get("club") or "") == identity[0]
                and str(member.get("label") or "") == identity[1]
                and not bool(member.get("guest", False))
                for member in tournament.get("teams", []) or []
            )
        )
        if len(tournament_ids) < 2:
            continue
        out.append(
            {
                "finding_id": f"temporal_clustering:{identity[0]}:{identity[1]}:{identity[2]}",
                "code": "temporal_clustering",
                "category": TEMPORAL_CLUSTERING,
                "severity": "strong_goal",
                "age_group": identity[2],
                "club": identity[0],
                "team": identity[1],
                "team_identity": {
                    "club": identity[0],
                    "label": identity[1],
                    "age_group": identity[2],
                },
                "clustered_dates": sorted(cluster),
                "tournament_ids": tournament_ids,
                "tournament_id": tournament_ids[0],
                "min_gap_days": int(min_gap),
                "message": (
                    f"{identity[0]} {identity[1]} plays {len(profile.get('tournament_dates') or [])} "
                    f"tournaments with a {int(min_gap)}-day minimum gap; the cluster around "
                    f"{', '.join(sorted(cluster))} may be repairable by a placement exchange"
                ),
            }
        )
    return out


def _clustered_dates(
    dates: Iterable[str],
    *,
    close_gap: int,
    neighbor_gap: int,
) -> List[str]:
    """Return the tight local cluster around a team's closest tournament dates.

    A cluster is a run of dates whose consecutive gaps are strictly under
    ``close_gap``. Only the tightest such run is reported, widened once to
    include neighbours within ``neighbor_gap`` days of a cluster member, so a
    tight pair plus a tournament a few days later (the eight-day three-in-a-row
    case) is named together without chaining the team's entire season.
    """

    from datetime import date as _date

    parsed: List[tuple[_date, str]] = []
    for value in dates:
        try:
            parsed.append((_date.fromisoformat(str(value)), str(value)))
        except (TypeError, ValueError):
            continue
    parsed.sort()
    components: List[List[tuple[_date, str]]] = []
    run: List[tuple[_date, str]] = []
    for item in parsed:
        if run and (item[0] - run[-1][0]).days < close_gap:
            run.append(item)
            continue
        if len(run) >= 2:
            components.append(run)
        run = [item]
    if len(run) >= 2:
        components.append(run)
    if not components:
        return []
    tightest = min(
        components,
        key=lambda component: (
            min((b[0] - a[0]).days for a, b in zip(component, component[1:])),
            component[0][0],
        ),
    )
    member_dates = [day for day, _value in tightest]
    return [
        value
        for day, value in parsed
        if any(abs((day - member).days) <= neighbor_gap for member in member_dates)
    ]


def _home_representation_findings(
    problem: Mapping[str, Any], plan: Mapping[str, Any]
) -> List[Dict[str, Any]]:
    """Actionable findings for a multi-team club/age pool skewed at home.

    A pool where one sibling team represents the club at nearly every home
    tournament is a real, deterministic schedule-quality fact. It is reported
    as a soft ``quality`` finding, never a hard failure: there may be no safe
    balancing move, and the repair provider is the only thing allowed to
    mutate the schedule.
    """
    from .home_representation import home_representation_rows
    from .home_representation_repair import home_representation_finding_id

    rows = home_representation_rows(
        (problem or {}).get("teams") or [], (plan or {}).get("tournaments") or []
    )
    out: List[Dict[str, Any]] = []
    for row in rows:
        if row.get("balanced"):
            continue
        club = str(row.get("club") or "")
        age_group = str(row.get("age_group") or "")
        out.append(
            {
                "finding_id": home_representation_finding_id(age_group, club),
                "code": "home_representation_skew",
                "category": HOME_REPRESENTATION,
                "severity": "quality",
                "age_group": age_group,
                "club": club,
                "team_count": int(row.get("team_count") or 0),
                "home_tournament_count": int(row.get("home_tournament_count") or 0),
                "home_appearances": dict(row.get("home_appearances") or {}),
                "spread": int(row.get("spread") or 0),
                "material_spread": int(row.get("material_spread") or 0),
                "message": (
                    f"{club} {age_group} home representation is skewed "
                    f"({row.get('evidence')}); sibling teams should be spread "
                    "more evenly across the club's home tournaments"
                ),
            }
        )
    return out


def _intra_club_distribution_findings(
    verification: Mapping[str, Any],
) -> List[Dict[str, Any]]:
    """Actionable findings for aggregate-complete but label-uneven club pools.

    ``participation_targets`` already classifies each club x age-group x scope
    player pool. An ``intra_club_distribution`` pool has enough aggregate
    participation; only the nominal sibling labels are uneven, so it is a soft
    quality finding, never an unresolved participation shortfall. It carries the
    exact sibling counts/targets, the current spread and the scope so the repair
    provider (and the operator) never has to reconstruct the classification.
    """
    from .intra_club_distribution_repair import intra_club_distribution_finding_id

    out: List[Dict[str, Any]] = []
    for pool in verification.get("participation_club_pools") or []:
        if str(pool.get("classification") or "") != INTRA_CLUB_DISTRIBUTION:
            continue
        club = str(pool.get("club") or "")
        age_group = str(pool.get("age_group") or "")
        scope = str(pool.get("scope") or "")
        distribution = [
            {
                "team": str(row.get("team") or ""),
                "actual": int(row.get("actual") or 0),
                "target": int(row.get("target") or 0),
            }
            for row in pool.get("team_distribution") or []
        ]
        actuals = [row["actual"] for row in distribution]
        max_actual = max(actuals) if actuals else 0
        min_actual = min(actuals) if actuals else 0
        out.append(
            {
                "finding_id": intra_club_distribution_finding_id(club, age_group, scope),
                "code": "intra_club_participation_distribution",
                "category": CLUB_DISTRIBUTION,
                "severity": "quality",
                "age_group": age_group,
                "club": club,
                "scope": scope,
                "classification": INTRA_CLUB_DISTRIBUTION,
                "club_pool_target": pool.get("club_pool_target"),
                "club_pool_actual": pool.get("club_pool_actual"),
                "club_pool_deviation": pool.get("club_pool_deviation"),
                "team_distribution": distribution,
                "team_count": int(pool.get("registered_team_count") or len(distribution)),
                "min_actual": min_actual,
                "max_actual": max_actual,
                "spread": max_actual - min_actual,
                "message": (
                    f"{club} {age_group} ({scope}) club pool is aggregate-complete "
                    f"({pool.get('club_pool_actual')}/{pool.get('club_pool_target')}) but the "
                    f"sibling labels are uneven (spread {max_actual - min_actual})"
                ),
            }
        )
    return out


def _count(verification: Mapping[str, Any], key: str) -> int:
    value = verification.get(key)
    return len(value) if isinstance(value, (list, tuple)) else 0


def _manual_count(verification: Mapping[str, Any]) -> int:
    # Participation entries flagged as pure intra-club distribution are not
    # unresolved manual work, so they must not inflate the manual-placement
    # defect dimension.
    participation = [
        item
        for item in (verification.get("manual_participation_placements") or [])
        if isinstance(item, Mapping) and item.get("counts_as_unresolved_shortfall", True)
    ]
    return len(participation) + sum(
        _count(verification, key)
        for key in (
            "manual_calendar_placements",
            "manual_external_conflict_placements",
        )
    )


def _avoidability_count(verification: Mapping[str, Any], avoidability: str) -> int:
    return sum(
        1
        for deviation in verification.get("participation_deviations") or []
        if str(deviation.get("avoidability") or "") == avoidability
    )


def _deviation_is_unresolved(deviation: Mapping[str, Any]) -> bool:
    """False for a pure intra-club label imbalance.

    An aggregate-complete pool split 5+3 is not an unresolved participation
    deficit, so it must not drive repair search or count as an equal-weight
    objective dimension.
    """
    return str(deviation.get("club_pool_classification") or "") != INTRA_CLUB_DISTRIBUTION


def _unresolved_participation_deviation_count(verification: Mapping[str, Any]) -> int:
    return sum(
        1
        for deviation in verification.get("participation_deviations") or []
        if _deviation_is_unresolved(deviation)
    )


def _unresolved_avoidability_count(verification: Mapping[str, Any], avoidability: str) -> int:
    return sum(
        1
        for deviation in verification.get("participation_deviations") or []
        if str(deviation.get("avoidability") or "") == avoidability
        and _deviation_is_unresolved(deviation)
    )
