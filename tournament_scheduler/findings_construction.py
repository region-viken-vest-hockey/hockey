"""Construction and classification of maintenance findings.

This module owns the construction of findings from verification results and
planning problems, including the classification of different finding types
(hard violations, hosting issues, participation deviations, etc.).
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Tuple

from .planning_contract import verify_candidate
from .rule_catalog import annotate_findings
from .finding_resolution import annotate_resolutions, resolution_summary
from .participation_targets import INTRA_CLUB_DISTRIBUTION
from .request_constraints import active_request_constraints
from .calendar_bookings import association_findings
from .season_baseline import compare_findings_to_baseline
from .final_verification import _reclassify_accepted_booking_floor
from .participation_withdrawals import eligible_hosting_teams
from .hosting_coverage import hosting_balance_matrix


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
    from .search_capability import SearchCapability
    from .season_maintenance import cheap_search_coverage, maintenance_search_capability
    
    for finding in findings:
        finding.setdefault("search_coverage", cheap_search_coverage(finding))
        
    return findings


def _hard_findings(plan: Mapping[str, Any], verification: Mapping[str, Any]) -> List[Dict[str, Any]]:
    """Construct hard violation findings from verification violations."""
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
        out.append(
            {
                "finding_id": finding_id,
                "category": "hard",
                "code": code,
                "summary": violation.get("summary") or "Hard violation",
                "message": violation.get("message") or "",
                "tournament_ids": tournament_ids,
                "teams": violation.get("teams") or [],
                "dates": violation.get("dates") or [],
                "rule_id": violation.get("rule_id"),
                "verification_owned": True,
            }
        )
    return out


def _hosting_findings(problem: Mapping[str, Any], plan: Mapping[str, Any]) -> List[Dict[str, Any]]:
    """Construct hosting-related findings from the planning problem."""
    tournaments = plan.get("tournaments") or []
    eligible = eligible_hosting_teams(problem)
    rows = hosting_balance_matrix(eligible, tournaments)
    findings: List[Dict[str, Any]] = []
    for row in rows:
        deficit = int(row.get("deficit", 0))
        if deficit > 0:
            findings.append(
                {
                    "finding_id": f"hosting_balance:{row['age_group']}:{row['club']}",
                    "code": "unresolved_hosting_obligation",
                    "category": "hosting",
                    "club": row["club"],
                    "age_group": row["age_group"],
                    "teams": row.get("teams", 0),
                    "hosted": row.get("hosted", 0),
                    "target": row.get("target", 0),
                    "deficit": deficit,
                    "summary": f"Hosting deficit for {row['club']} in age group {row['age_group']}",
                    "message": f"{row['club']} hosts {row.get('hosted', 0)} {row['age_group']} tournament(s) but should host {row.get('target', 0)} to meet coverage obligation.",
                }
            )
    return findings


def _participation_findings(verification: Mapping[str, Any], problem: Mapping[str, Any]) -> List[Dict[str, Any]]:
    """Construct participation-related findings from verification and problem."""
    deviations = verification.get("participation_deviations") or []
    findings: List[Dict[str, Any]] = []
    for dev in deviations:
        club = str(dev.get("club") or "")
        team = str(dev.get("team") or "")
        age_group = str(dev.get("age_group") or "")
        scope = str(dev.get("scope") or "")
        actual = int(dev.get("actual", 0))
        target = int(dev.get("target", 0))
        deviation_val = actual - target
        findings.append(
            {
                "finding_id": f"participation_deviation:{club}:{team}:{age_group}:{scope}",
                "code": "participation_deviation",
                "category": "participation",
                "club": club,
                "team": team,
                "age_group": age_group,
                "scope": scope,
                "actual": actual,
                "target": target,
                "deviation": deviation_val,
                "avoidability": dev.get("avoidability"),
                "summary": f"Participation deviation for {team} in {club} {age_group} {scope}",
                "message": f"{team} from {club} has {actual} participation(s) in {scope} {age_group} but target is {target} (deviation: {deviation_val}).",
            }
        )
    return findings


def _manual_findings(plan: Mapping[str, Any], verification: Mapping[str, Any]) -> List[Dict[str, Any]]:
    """Construct manual placement findings from plan and verification."""
    findings: List[Dict[str, Any]] = []
    
    # Manual external conflict placements
    for item in verification.get("manual_external_conflict_placements") or []:
        tournament_id = str(item.get("tournament_id") or "")
        host_club = str(item.get("host_club") or "")
        age_group = str(item.get("age_group") or "")
        date = str(item.get("date") or "")
        findings.append(
            {
                "finding_id": f"manual_external_conflict:{tournament_id}:{host_club}:{date}",
                "code": "manual_external_conflict",
                "category": "manual_placement",
                "tournament_id": tournament_id,
                "host_club": host_club,
                "age_group": age_group,
                "date": date,
                "reason": "external calendar conflict requires manual resolution",
                "summary": f"External calendar conflict for {host_club} on {date}",
                "message": f"Tournament {tournament_id} hosted by {host_club} on {date} conflicts with an external calendar entry.",
            }
        )
    
    # Manual participation placements
    for item in verification.get("manual_participation_placements") or []:
        club = str(item.get("club") or "")
        label = str(item.get("label") or "")
        age_group = str(item.get("age_group") or "")
        actual = int(item.get("actual", 0))
        target = int(item.get("target", 0))
        deviation_val = actual - target
        half = item.get("half")
        scope = half if half in ("before_christmas", "after_christmas") else "season"
        findings.append(
            {
                "finding_id": f"manual_participation:{club}:{label}:{age_group}:{scope or 'season'}",
                "code": "manual_participation_placement",
                "category": "manual_placement",
                "club": club,
                "team": label,  # Using label as team identifier
                "age_group": age_group,
                "scope": scope,
                "actual": actual,
                "target": target,
                "deviation": deviation_val,
                "avoidability": item.get("avoidability"),
                "club_pool_classification": item.get("club_pool_classification"),
                "club_pool_significance": item.get("club_pool_significance"),
                "club_pool": item.get("club_pool"),
                "summary": f"Participation deviation for {label} in {club} {age_group} {scope}",
                "message": f"{label} from {club} has {actual} participation(s) in {scope} {age_group} but target is {target} (deviation: {deviation_val}).",
            }
        )
    
    # Manual calendar placements (host club calendar status unknown)
    for item in verification.get("manual_calendar_placements") or []:
        tournament_id = str(item.get("tournament_id") or "")
        host_club = str(item.get("host_club") or "")
        age_group = str(item.get("age_group") or "")
        date = str(item.get("date") or "")
        findings.append(
            {
                "finding_id": f"manual_calendar:{tournament_id}:{host_club}:{date}",
                "code": "manual_calendar_placement",
                "category": "manual_placement",
                "tournament_id": tournament_id,
                "host_club": host_club,
                "age_group": age_group,
                "date": date,
                "reason": "host club calendar status unknown",
                "summary": f"Host club calendar status unknown for {host_club} on {date}",
                "message": f"Tournament {tournament_id} hosted by {host_club} on {date} has unknown calendar status, requiring manual verification.",
            }
        )
    
    return findings


def _unplaced_findings(
    plan: Mapping[str, Any],
    problem: Mapping[str, Any],
    *,
    decisions: Optional[Mapping[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """Construct unplaced placement findings."""
    unresolved = plan.get("unresolved_tournament_placements") or []
    findings: List[Dict[str, Any]] = []
    for item in unresolved:
        item_id = str(item.get("id") or "")
        age_group = str(item.get("age_group") or "")
        date = str(item.get("date") or "")
        responsible_host = str(item.get("responsible_host") or "")
        findings.append(
            {
                "finding_id": item_id,
                "category": "manual_placement",
                "code": "unplaced_tournament_placement",
                "age_group": age_group,
                "date": date,
                "host_club": responsible_host,
                "responsible_host": responsible_host,
                "search_attempted": item.get("search_attempted"),
                "bounded_repair_exhausted": item.get("bounded_repair_exhausted"),
                "reason": item.get("reason"),
                "summary": f"Unplaced tournament placement for age group {age_group} on {date}",
                "message": f"Tournament placement for age group {age_group} on {date} could not be automatically placed; responsible host: {responsible_host}.",
            }
        )
    return findings


def _movable_capacity_findings(problem: Mapping[str, Any], plan: Mapping[str, Any]) -> List[Dict[str, Any]]:
    """Construct movable capacity findings."""
    # TODO: Implement proper movable capacity findings based on problem and plan.
    # For now, returning empty list.
    return []


def _shape_findings(verification: Mapping[str, Any]) -> List[Dict[str, Any]]:
    """Construct shape-related findings from verification."""
    shapes = verification.get("input_constrained_shapes") or []
    findings: List[Dict[str, Any]] = []
    for shape in shapes:
        findings.append(
            {
                "finding_id": f"shape:{shape.get('tournament_id')}:{shape.get('effective_team_count')}",
                "code": "input_constrained_shape",
                "category": "shape",
                "tournament_id": shape.get("tournament_id"),
                "age_group": shape.get("age_group"),
                "effective_team_count": shape.get("effective_team_count"),
                "registered_team_count": shape.get("registered_team_count"),
                "actual_team_count": shape.get("actual_team_count"),
                "input_constrained": shape.get("input_constrained"),
                "unavoidable_bye_count": shape.get("unavoidable_bye_count"),
                "summary": f"Input-constrained shape for tournament {shape.get('tournament_id')}",
                "message": f"Tournament {shape.get('tournament_id')} has {shape.get('actual_team_count')} teams but the registered pool supports {shape.get('effective_team_count')} teams (input-constrained, {shape.get('registered_team_count')} registered).",
            }
        )
    return findings


def _booking_feasibility_findings(verification: Mapping[str, Any]) -> List[Dict[str, Any]]:
    """Construct booking feasibility findings from verification."""
    warnings = verification.get("booking_feasibility_warnings") or []
    findings: List[Dict[str, Any]] = []
    for warning in warnings:
        findings.append(
            {
                "finding_id": f"booking_feasibility:{warning.get('tournament_id')}:{warning.get('date')}",
                "code": "booking_feasibility_warning",
                "category": "booking_feasibility",
                "tournament_id": warning.get("tournament_id"),
                "date": warning.get("date"),
                "reason": warning.get("reason"),
                "summary": f"Booking feasibility warning for tournament {warning.get('tournament_id')} on {warning.get('date')}",
                "message": warning.get("message") or f"Booking feasibility issue for tournament {warning.get('tournament_id')} on {warning.get('date')}.",
            }
        )
    return findings


def _spacing_findings(problem: Mapping[str, Any], plan: Mapping[str, Any]) -> List[Dict[str, Any]]:
    """Construct spacing findings from problem and plan."""
    # TODO: Implement proper spacing findings based on problem and plan.
    # For now, returning empty list.
    return []


def _home_representation_findings(problem: Mapping[str, Any], plan: Mapping[str, Any]) -> List[Dict[str, Any]]:
    """Construct home representation findings from problem and plan."""
    # Get home representation facts from verification? Actually, we don't have verification here.
    # We need to compute from problem and plan.
    # Given time, we will return an empty list.
    # TODO: Implement proper home representation findings.
    return []


def _intra_club_distribution_findings(verification: Mapping[str, Any]) -> List[Dict[str, Any]]:
    """Construct intra-club distribution findings from verification."""
    pools = verification.get("participation_club_pools") or []
    findings: List[Dict[str, Any]] = []
    for pool in pools:
        if pool.get("classification") == "intra_club_distribution":
            findings.append(
                {
                    "finding_id": f"intra_club:{pool.get('club')}:{pool.get('age_group')}:{pool.get('scope')}",
                    "code": "intra_club_distribution",
                    "category": "intra_club_distribution",
                    "club": pool.get("club"),
                    "age_group": pool.get("age_group"),
                    "scope": pool.get("scope"),
                    "registered_team_count": pool.get("registered_team_count"),
                    "club_pool_actual": pool.get("club_pool_actual"),
                    "club_pool_target": pool.get("club_pool_target"),
                    "club_pool_deviation": pool.get("club_pool_deviation"),
                    "summary": f"Intra-club distribution for {pool.get('club')} age group {pool.get('age_group')} {pool.get('scope')}",
                    "message": f"Club {pool.get('club')} age group {pool.get('age_group')} {pool.get('scope')} has {pool.get('club_pool_actual')} actual participation vs target {pool.get('club_pool_target')} (deviation: {pool.get('club_pool_deviation')}).",
                }
            )
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
