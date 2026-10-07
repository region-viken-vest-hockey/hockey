"""Typed, durable request constraints for promoted-season maintenance.

Club feedback usually states *intent* ("we cannot play 2027-02-21", "keep at
least 21 days between our tournaments", "do not place us with opponent X that
weekend"), not an exact replacement schedule. Granular
:mod:`tournament_scheduler.change_protections` guard the *result* of one
accepted mutation; request constraints preserve the higher-level semantic
requirement and are enforced by every later canonical schedule-changing commit
until an operator explicitly releases/supersedes them.

The module is planner-independent. It owns the typed model, its deterministic
identity/validation, and one pure verifier over a canonical plan. It never
searches, never writes state and never inspects a generator.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date as _date
from typing import Any, Iterable, Mapping

from tournament_scheduler.canonical_state import REQUEST_CONSTRAINTS_KEY
from tournament_scheduler.pipeline.fingerprints import stable_payload_sha256

ACTIVE = "active"
RELEASED = "released"

TEAM_UNAVAILABLE = "team_unavailable"
MINIMUM_GAP = "minimum_gap"
OPPONENT_AVOIDANCE = "opponent_avoidance"
HOST_SIBLING_PARTICIPATION_PREFERENCE = "host_sibling_participation_preference"

SUPPORTED_TYPES: tuple[str, ...] = (
    TEAM_UNAVAILABLE,
    MINIMUM_GAP,
    OPPONENT_AVOIDANCE,
    HOST_SIBLING_PARTICIPATION_PREFERENCE,
)

TEAM_CONSTRAINT_PREFIX = "request"

# Soft preference constant for ranking - not a hard violation code
HOST_SIBLING_PREFERENCE_VIOLATION = "host_sibling_preference_violated"


class RequestConstraintError(ValueError):
    """Raised when a request-constraint definition is malformed or ambiguous."""


def team_identity(team: Mapping[str, Any], fallback_age_group: str = "") -> tuple[str, str, str]:
    """Return the canonical team identity ``(club, label, age_group)``."""

    return (
        str(team.get("club") or ""),
        str(team.get("label") or ""),
        str(team.get("age_group") or fallback_age_group),
    )


def _normalized_team(team: Mapping[str, Any]) -> dict[str, str]:
    return {
        "club": str(team.get("club") or ""),
        "label": str(team.get("label") or ""),
        "age_group": str(team.get("age_group") or ""),
    }


def _parse_iso_date(value: Any, field: str) -> _date:
    try:
        return _date.fromisoformat(str(value))
    except (TypeError, ValueError) as exc:
        raise RequestConstraintError(
            f"Invalid {field}: {value!r}; expected YYYY-MM-DD"
        ) from exc


def _plan_tournaments(plan: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [
        dict(tournament)
        for tournament in plan.get("tournaments", []) or []
        if tournament.get("id") and not tournament.get("cancelled")
    ]


def _plan_team_identities(plan: Mapping[str, Any]) -> set[tuple[str, str, str]]:
    identities: set[tuple[str, str, str]] = set()
    for tournament in _plan_tournaments(plan):
        age_group = str(tournament.get("age_group") or "")
        for team in tournament.get("teams", []) or []:
            if bool(team.get("guest", False)):
                continue
            identity = team_identity(team, age_group)
            if identity[1]:
                identities.add(identity)
    return identities


def resolve_team_identity(
    plan: Mapping[str, Any],
    team: Mapping[str, Any],
) -> dict[str, str]:
    """Resolve a (possibly age-group-omitting) team reference to one identity.

    The canonical identity is ``(club, label, age_group)``. When the caller
    supplies only ``club``/``label`` and exactly one age group in the plan
    matches, that age group is filled in; zero matches is an unknown team and
    more than one match is ambiguous, so both are rejected instead of guessing.
    """

    club = str(team.get("club") or "").strip()
    label = str(team.get("label") or "").strip()
    age_group = str(team.get("age_group") or "").strip()
    if not label:
        raise RequestConstraintError("Team identity requires a label")
    if age_group:
        identity = (club, label, age_group)
        if identity not in _plan_team_identities(plan):
            raise RequestConstraintError(
                f"Unknown team identity: club={club!r} label={label!r} age_group={age_group!r}"
            )
        return _normalized_team(team)
    matches = sorted(
        age
        for (candidate_club, candidate_label, age) in _plan_team_identities(plan)
        if candidate_club == club and candidate_label == label
    )
    if not matches:
        raise RequestConstraintError(
            f"Unknown team identity: club={club!r} label={label!r}"
        )
    if len(matches) > 1:
        raise RequestConstraintError(
            f"Ambiguous team identity: club={club!r} label={label!r} matches age groups "
            f"{', '.join(matches)}; specify the age group"
        )
    return {"club": club, "label": label, "age_group": matches[0]}


def constraint_payload(constraint: Mapping[str, Any]) -> dict[str, Any]:
    """Return the semantic payload that defines one constraint.

    The payload excludes write-time metadata (actor, timestamps, the stored
    status) so a retried identical request is idempotent and a later schedule
    change never rewrites the constraint identity.
    """

    return {
        "type": str(constraint.get("type") or ""),
        "request_id": str(constraint.get("request_id") or ""),
        "teams": [_normalized_team(team) for team in (constraint.get("teams") or [])],
        "date_from": constraint.get("date_from"),
        "date_to": constraint.get("date_to"),
        "min_days": constraint.get("min_days"),
    }


def constraint_id(constraint: Mapping[str, Any]) -> str:
    """Return the deterministic id for a semantic constraint payload."""

    digest = stable_payload_sha256(constraint_payload(constraint))
    return f"{TEAM_CONSTRAINT_PREFIX}:{digest[:16]}"


def validate_and_normalize(
    definition: Mapping[str, Any],
    plan: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate a requested constraint and return its normalized record (no metadata).

    Malformed/ambiguous definitions are rejected here, at creation time, rather
    than becoming an unsatisfiable canonical requirement.
    """

    constraint_type = str(definition.get("type") or "").strip()
    if constraint_type not in SUPPORTED_TYPES:
        raise RequestConstraintError(
            f"Unsupported request-constraint type: {constraint_type!r}; "
            f"supported types are {', '.join(SUPPORTED_TYPES)}"
        )
    request_id = str(definition.get("request_id") or "").strip()
    if not request_id:
        raise RequestConstraintError("A request constraint requires a stable --request-id")

    raw_teams = list(definition.get("teams") or [])
    date_from = definition.get("date_from")
    date_to = definition.get("date_to")
    min_days = definition.get("min_days")

    if constraint_type in (TEAM_UNAVAILABLE, MINIMUM_GAP):
        if len(raw_teams) != 1:
            raise RequestConstraintError(
                f"Constraint type {constraint_type!r} requires exactly one team"
            )
    elif constraint_type == OPPONENT_AVOIDANCE:
        if len(raw_teams) != 2:
            raise RequestConstraintError(
                "Constraint type 'opponent_avoidance' requires exactly two teams"
            )
    elif constraint_type == HOST_SIBLING_PARTICIPATION_PREFERENCE:
        if len(raw_teams) != 1:
            raise RequestConstraintError(
                f"Constraint type {constraint_type!r} requires exactly one team (the host club's sibling team reference)"
            )
        if date_from is None:
            raise RequestConstraintError(
                f"Constraint type {constraint_type!r} requires --date-from for the age group scope"
            )
        if min_days is not None:
            raise RequestConstraintError(
                f"Constraint type {constraint_type!r} does not accept --min-days"
            )
    else:
        raise RequestConstraintError(f"Unhandled constraint type: {constraint_type!r}")

    teams = [resolve_team_identity(plan, team) for team in raw_teams]

    record: dict[str, Any] = {
        "type": constraint_type,
        "request_id": request_id,
        "teams": teams,
        "date_from": None,
        "date_to": None,
        "min_days": None,
    }

    if constraint_type == MINIMUM_GAP:
        if date_from is not None or date_to is not None:
            raise RequestConstraintError(
                "Constraint type 'minimum_gap' does not accept a date range"
            )
        try:
            resolved_min_days = int(min_days)
        except (TypeError, ValueError) as exc:
            raise RequestConstraintError(
                f"Invalid min_days: {min_days!r}; expected a positive integer"
            ) from exc
        if resolved_min_days < 1:
            raise RequestConstraintError("minimum_gap requires a positive --min-days")
        record["min_days"] = resolved_min_days
    elif constraint_type == HOST_SIBLING_PARTICIPATION_PREFERENCE:
        # For host sibling preference, the team identifies the club+age_group scope.
        # The date_from/date_to represent the age group's season span (or a specific window).
        parsed_from = _parse_iso_date(date_from, "date_from")
        parsed_to = _parse_iso_date(date_to if date_to is not None else date_from, "date_to")
        if parsed_to < parsed_from:
            raise RequestConstraintError(
                f"Invalid date range: {parsed_from.isoformat()} is after {parsed_to.isoformat()}"
            )
        record["date_from"] = parsed_from.isoformat()
        record["date_to"] = parsed_to.isoformat()
    else:
        if min_days is not None:
            raise RequestConstraintError(
                f"Constraint type {constraint_type!r} does not accept --min-days"
            )
        if date_from is None:
            raise RequestConstraintError(
                f"Constraint type {constraint_type!r} requires --date-from"
            )
        parsed_from = _parse_iso_date(date_from, "date_from")
        parsed_to = _parse_iso_date(date_to if date_to is not None else date_from, "date_to")
        if parsed_to < parsed_from:
            raise RequestConstraintError(
                f"Invalid date range: {parsed_from.isoformat()} is after {parsed_to.isoformat()}"
            )
        record["date_from"] = parsed_from.isoformat()
        record["date_to"] = parsed_to.isoformat()

    if constraint_type == OPPONENT_AVOIDANCE:
        first = team_identity(teams[0])
        second = team_identity(teams[1])
        if first == second:
            raise RequestConstraintError(
                "opponent_avoidance requires two distinct teams; a team cannot avoid itself"
            )

    record["id"] = constraint_id(record)
    return record


def _is_active(record: Mapping[str, Any]) -> bool:
    return str(record.get("status") or ACTIVE) == ACTIVE


def active_request_constraints(decisions: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Return the active request constraints stored in canonical decisions."""

    return [
        dict(record)
        for record in decisions.get(REQUEST_CONSTRAINTS_KEY, []) or []
        if isinstance(record, Mapping) and _is_active(record)
    ]


def request_constraint_records(
    decisions: Mapping[str, Any],
    *,
    include_released: bool = False,
) -> list[dict[str, Any]]:
    """Return stored request constraints, optionally including released ones."""

    records = [
        dict(record)
        for record in decisions.get(REQUEST_CONSTRAINTS_KEY, []) or []
        if isinstance(record, Mapping)
    ]
    if include_released:
        return records
    return [record for record in records if _is_active(record)]


def _date_within(value: Any, start: _date, end: _date) -> bool:
    try:
        parsed = _date.fromisoformat(str(value))
    except (TypeError, ValueError):
        return False
    return start <= parsed <= end


def _team_present(tournament: Mapping[str, Any], identity: tuple[str, str, str]) -> bool:
    age_group = str(tournament.get("age_group") or "")
    return any(
        team_identity(team, age_group) == identity
        for team in tournament.get("teams", []) or []
        if not bool(team.get("guest", False))
    )


def constraint_violations(
    plan: Mapping[str, Any],
    constraint: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Return the structured violations of one constraint against a plan.

    This is a pure function of the plan and the constraint definition: it is
    independent of whatever generator produced the plan, and it derives the
    current satisfaction result instead of trusting a stored flag.
    """

    constraint_type = str(constraint.get("type") or "")
    constraint_identifier = str(constraint.get("id") or "")
    request_id = str(constraint.get("request_id") or "")
    teams = [_normalized_team(team) for team in (constraint.get("teams") or [])]
    tournaments = _plan_tournaments(plan)
    violations: list[dict[str, Any]] = []

    def base(**kwargs: Any) -> dict[str, Any]:
        return {
            "constraint_id": constraint_identifier,
            "request_id": request_id,
            "type": constraint_type,
            **kwargs,
        }

    if constraint_type == TEAM_UNAVAILABLE:
        identity = team_identity(teams[0])
        start = _parse_iso_date(constraint.get("date_from"), "date_from")
        end = _parse_iso_date(constraint.get("date_to") or constraint.get("date_from"), "date_to")
        for tournament in tournaments:
            if not _date_within(tournament.get("date"), start, end):
                continue
            if not _team_present(tournament, identity):
                continue
            violations.append(
                base(
                    code=TEAM_UNAVAILABLE,
                    team=teams[0],
                    tournament_id=str(tournament.get("id") or ""),
                    date=tournament.get("date"),
                    message=(
                        f"{teams[0]['label']} is unavailable on {tournament.get('date')} "
                        f"but participates in {tournament.get('id')}"
                    ),
                )
            )
        return violations

    if constraint_type == MINIMUM_GAP:
        identity = team_identity(teams[0])
        minimum = int(constraint.get("min_days") or 0)
        participations: list[tuple[_date, str]] = []
        for tournament in tournaments:
            if not _team_present(tournament, identity):
                continue
            try:
                when = _date.fromisoformat(str(tournament.get("date")))
            except (TypeError, ValueError):
                continue
            participations.append((when, str(tournament.get("id") or "")))
        participations.sort()
        for (earlier_date, earlier_id), (later_date, later_id) in zip(
            participations, participations[1:]
        ):
            gap = (later_date - earlier_date).days
            if gap < minimum:
                violations.append(
                    base(
                        code=MINIMUM_GAP,
                        team=teams[0],
                        tournament_ids=[earlier_id, later_id],
                        dates=[earlier_date.isoformat(), later_date.isoformat()],
                        gap_days=gap,
                        min_days=minimum,
                        message=(
                            f"{teams[0]['label']} has only {gap} day(s) between "
                            f"{earlier_date.isoformat()} and {later_date.isoformat()}; "
                            f"at least {minimum} required"
                        ),
                    )
                )
        return violations

    if constraint_type == OPPONENT_AVOIDANCE:
        first = team_identity(teams[0])
        second = team_identity(teams[1])
        start = _parse_iso_date(constraint.get("date_from"), "date_from")
        end = _parse_iso_date(constraint.get("date_to") or constraint.get("date_from"), "date_to")
        for tournament in tournaments:
            if not _date_within(tournament.get("date"), start, end):
                continue
            if not (_team_present(tournament, first) and _team_present(tournament, second)):
                continue
            violations.append(
                base(
                    code=OPPONENT_AVOIDANCE,
                    teams=teams,
                    tournament_id=str(tournament.get("id") or ""),
                    date=tournament.get("date"),
                    message=(
                        f"{teams[0]['label']} and {teams[1]['label']} must not meet around "
                        f"{tournament.get('date')} but both participate in {tournament.get('id')}"
                    ),
                )
            )
        return violations

    if constraint_type == HOST_SIBLING_PARTICIPATION_PREFERENCE:
        # This is a soft operational preference, not a hard violation.
        # It returns no hard violations but provides evidence for repair ranking.
        # The preference evidence is derived by the caller (e.g., repair providers)
        # using the constraint definition: club + age_group + scope.
        return violations

    return violations


def count_host_sibling_preference_violations(
    plan: Mapping[str, Any],
    decisions: Mapping[str, Any],
) -> int:
    """Count host sibling preference violations in a plan.

    Returns the number of host tournaments where a host club's sibling team
    participates in away tournaments but not in its own host tournament.
    This is a soft preference metric for ranking repair options.
    """
    evidence = host_sibling_preference_evidence(plan, decisions)
    total_violations = 0
    for entry in evidence:
        total_violations += len(entry.get("violations", []))
    return total_violations


def request_constraint_violations(
    plan: Mapping[str, Any],
    decisions: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Return every active request-constraint violation for a plan."""

    violations: list[dict[str, Any]] = []
    for constraint in active_request_constraints(decisions):
        violations.extend(constraint_violations(plan, constraint))
    return violations


# --- baseline-aware acceptability ------------------------------------------
#
# ``request_constraint_violations`` answers "does this plan satisfy the
# constraint?". Canonical mutation acceptability is a different, baseline
# question: "does this candidate introduce or worsen a violation relative to the
# promoted plan?". A pre-existing unchanged violation is real, visible debt that
# must not veto an unrelated maintenance change, while a candidate that touches
# the constrained facts is still re-evaluated so a new or worsened violation is
# rejected. This classifier is the single policy owner for that comparison, so
# direct mutation, dry-run, repair adoption and bounded search cannot drift.

UNCHANGED = "unchanged"
IMPROVED = "improved"
INTRODUCED = "introduced"
WORSENED = "worsened"
RESOLVED = "resolved"


def violation_identity(violation: Mapping[str, Any]) -> tuple[str, ...]:
    """Return the stable semantic identity of one structured violation.

    The identity is scoped to the constraint and to the exact entities/facts it
    covers, so a candidate that changes a tournament or date in the constraint's
    scope produces a different identity and is classified as a new violation
    instead of being inherited from the baseline. ``minimum_gap`` debt is
    aggregated per constraint (see :func:`_constraint_debt_view`): the identity
    of whichever adjacent pair currently carries the shortfall is not a stable
    debt identity, so it collapses to the constraint itself.
    """

    constraint_identifier = str(violation.get("constraint_id") or "")
    code = str(violation.get("code") or "")
    if code == TEAM_UNAVAILABLE:
        team = violation.get("team") or {}
        return (
            constraint_identifier,
            code,
            str(team.get("club") or ""),
            str(team.get("label") or ""),
            str(team.get("age_group") or ""),
            str(violation.get("tournament_id") or ""),
            str(violation.get("date") or ""),
        )
    if code == OPPONENT_AVOIDANCE:
        return (
            constraint_identifier,
            code,
            str(violation.get("tournament_id") or ""),
            str(violation.get("date") or ""),
        )
    return (constraint_identifier, code)


def violation_severity(violation: Mapping[str, Any]) -> float:
    """Return a deterministic higher-is-worse score for one violation.

    Entity-scoped violations (an unavailable team, an avoided opponent) are
    binary and score ``1.0``. A minimum-gap violation scores its shortfall in
    days; the constraint-level comparison sums those shortfalls, so moving the
    shortfall to a neighbouring pair is the same debt unless the total grows.
    """

    if str(violation.get("code") or "") == MINIMUM_GAP:
        minimum = int(violation.get("min_days") or 0)
        gap = int(violation.get("gap_days") or 0)
        return float(max(0, minimum - gap))
    return 1.0


def _constraint_debt_view(
    constraint: Mapping[str, Any],
    violations: list[dict[str, Any]],
) -> tuple[dict[tuple[str, ...], float], dict[tuple[str, ...], list[dict[str, Any]]]]:
    """Return one constraint's debt keys -> severity and keys -> evidence.

    Entity-scoped constraints key each violation by the exact entity/fact it
    covers, so touching that fact is a new violation. A ``minimum_gap``
    constraint keys its whole spacing shortfall to one aggregate, so a candidate
    that removes one violating adjacent pair while creating another is compared
    on the team's total shortfall rather than on pair identity.
    """

    if not violations:
        return {}, {}
    if str(constraint.get("type") or "") == MINIMUM_GAP:
        key = (str(constraint.get("id") or ""), MINIMUM_GAP)
        severity = sum(violation_severity(item) for item in violations)
        return {key: severity}, {key: violations}
    debt = {violation_identity(item): violation_severity(item) for item in violations}
    by_key = {violation_identity(item): [dict(item)] for item in violations}
    return debt, by_key


def compare_constraint_violations(
    baseline_plan: Mapping[str, Any],
    candidate_plan: Mapping[str, Any],
    constraints: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    """Classify candidate request-constraint violations against a baseline.

    Returns the full candidate and baseline violation evidence plus explicit
    ``introduced``/``worsened``/``unchanged``/``improved``/``resolved`` buckets.
    ``acceptable`` is true exactly when the candidate introduces no new
    violation and worsens no existing one; pre-existing unchanged violations
    remain in the result as unresolved, actionable debt.

    Classification is per constraint debt, not per raw violation dict: entity
    facts are keyed individually, while a ``minimum_gap`` constraint compares
    its total spacing shortfall so debt can migrate between adjacent pairs
    without being misread as a brand-new violation.
    """

    constraint_list = [dict(constraint) for constraint in constraints]
    baseline_violations: list[dict[str, Any]] = []
    candidate_violations: list[dict[str, Any]] = []

    introduced: list[dict[str, Any]] = []
    worsened: list[dict[str, Any]] = []
    unchanged: list[dict[str, Any]] = []
    improved: list[dict[str, Any]] = []
    resolved: list[dict[str, Any]] = []

    for constraint in constraint_list:
        base = constraint_violations(baseline_plan, constraint)
        cand = constraint_violations(candidate_plan, constraint)
        baseline_violations.extend(base)
        candidate_violations.extend(cand)
        baseline_debt, baseline_by_key = _constraint_debt_view(constraint, base)
        candidate_debt, candidate_by_key = _constraint_debt_view(constraint, cand)
        for key, severity in candidate_debt.items():
            entries = candidate_by_key.get(key, [])
            previous = baseline_debt.get(key)
            if previous is None:
                introduced.extend(entries)
            elif severity > previous:
                worsened.extend(entries)
            elif severity < previous:
                improved.extend(entries)
            else:
                unchanged.extend(entries)
        for key, entries in baseline_by_key.items():
            if key not in candidate_debt:
                resolved.extend(entries)

    regressions = introduced + worsened
    return {
        "acceptable": not regressions,
        "regressions": regressions,
        "introduced": introduced,
        "worsened": worsened,
        "unchanged": unchanged,
        "improved": improved,
        "resolved": resolved,
        "baseline_violations": baseline_violations,
        "candidate_violations": candidate_violations,
    }


def compare_request_constraint_violations(
    baseline_plan: Mapping[str, Any],
    candidate_plan: Mapping[str, Any],
    decisions: Mapping[str, Any],
) -> dict[str, Any]:
    """Baseline-aware comparison over every active request constraint."""

    return compare_constraint_violations(
        baseline_plan,
        candidate_plan,
        active_request_constraints(decisions),
    )


def request_constraint_report(
    plan: Mapping[str, Any],
    decisions: Mapping[str, Any],
    *,
    include_released: bool = False,
) -> list[dict[str, Any]]:
    """Return per-constraint lifecycle + derived satisfaction status."""

    report: list[dict[str, Any]] = []
    for constraint in request_constraint_records(decisions, include_released=include_released):
        violations = (
            constraint_violations(plan, constraint) if _is_active(constraint) else []
        )
        report.append(
            {
                **constraint,
                "status": str(constraint.get("status") or ACTIVE),
                "satisfied": not violations if _is_active(constraint) else None,
                "violations": violations,
            }
        )
    return report


def host_sibling_preference_evidence(
    plan: Mapping[str, Any],
    decisions: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Evaluate active host-sibling participation preferences against a plan.

    Returns a list of evidence entries, one per active preference constraint.
    Each entry indicates whether the preference is currently honored or violated.
    This is a soft preference check, not a hard violation.
    """
    evidence: list[dict[str, Any]] = []
    for constraint in active_request_constraints(decisions):
        if constraint.get("type") != HOST_SIBLING_PARTICIPATION_PREFERENCE:
            continue

        team = constraint.get("teams", [{}])[0]
        club = str(team.get("club") or "")
        age_group = str(team.get("age_group") or "")
        date_from = constraint.get("date_from")
        date_to = constraint.get("date_to")

        if not club or not age_group:
            continue

        # Find all tournaments in the age group where this club is the host
        host_tournaments = []
        for tournament in _plan_tournaments(plan):
            if str(tournament.get("age_group") or "") != age_group:
                continue
            if str(tournament.get("host_club") or "") != club:
                continue
            if date_from or date_to:
                try:
                    t_date = _date.fromisoformat(str(tournament.get("date") or ""))
                    start = _date.fromisoformat(date_from) if date_from else None
                    end = _date.fromisoformat(date_to) if date_to else None
                    if start and t_date < start:
                        continue
                    if end and t_date > end:
                        continue
                except (TypeError, ValueError):
                    continue
            host_tournaments.append(tournament)

        # For each host tournament, check if any of the host club's sibling teams
        # are participating (which is the desired state) or if they are absent
        # while participating elsewhere as donors (which violates the preference)
        sibling_teams = []
        for tournament in _plan_tournaments(plan):
            if str(tournament.get("age_group") or "") != age_group:
                continue
            for t in tournament.get("teams", []) or []:
                if str(t.get("club") or "") == club and not t.get("guest"):
                    sibling_teams.append((t, tournament))

        # Group by team identity
        team_tournaments = defaultdict(list)
        for t, tour in sibling_teams:
            ident = (t.get("club"), t.get("label"), t.get("age_group"))
            team_tournaments[ident].append(tour)

        violations = []
        honored = []

        for host_tour in host_tournaments:
            host_id = str(host_tour.get("id") or "")
            host_date = host_tour.get("date")

            # Check which sibling teams are in this host tournament
            siblings_in_host = []
            for t in host_tour.get("teams", []) or []:
                if str(t.get("club") or "") == club and not t.get("guest"):
                    siblings_in_host.append(t)

            if not siblings_in_host:
                # No sibling teams in the host tournament - check if they
                # are participating elsewhere (away tournaments) instead
                for ident, tours in team_tournaments.items():
                    in_host = any(str(t.get("id") or "") == host_id for t in tours)
                    if not in_host and tours:
                        # This sibling team is participating in away tournaments
                        # but not in the host tournament - potential preference violation
                        violations.append({
                            "constraint_id": constraint.get("id"),
                            "request_id": constraint.get("request_id"),
                            "type": HOST_SIBLING_PARTICIPATION_PREFERENCE,
                            "code": HOST_SIBLING_PREFERENCE_VIOLATION,
                            "club": club,
                            "age_group": age_group,
                            "host_tournament_id": host_id,
                            "host_tournament_date": host_date,
                            "missing_sibling_team": {"club": ident[0], "label": ident[1], "age_group": ident[2]},
                            "away_tournaments": [str(t.get("id") or "") for t in tours],
                            "message": (
                                f"Host club {club} sibling team {ident[1]} participates in away "
                                f"tournaments {', '.join(str(t.get('id') or '') for t in tours)} "
                                f"but not in its own host tournament {host_id} on {host_date}"
                            ),
                        })
            else:
                for t in siblings_in_host:
                    honored.append({
                        "club": club,
                        "age_group": age_group,
                        "host_tournament_id": host_id,
                        "host_tournament_date": host_date,
                        "sibling_team": {"club": t.get("club"), "label": t.get("label"), "age_group": t.get("age_group")},
                        "message": f"Sibling team {t.get('label')} participates in host tournament {host_id}",
                    })

        evidence.append({
            "constraint_id": constraint.get("id"),
            "request_id": constraint.get("request_id"),
            "club": club,
            "age_group": age_group,
            "date_from": date_from,
            "date_to": date_to,
            "host_tournament_count": len(host_tournaments),
            "sibling_team_count": len(team_tournaments),
            "violations": violations,
            "honored": honored,
            "preference_honored": len(violations) == 0,
        })
    return evidence


def append_request_constraints(
    decisions: dict[str, Any],
    constraints: Iterable[Mapping[str, Any]],
) -> list[str]:
    """Append constraints idempotently; return the ids that were not already present."""

    records = decisions.setdefault(REQUEST_CONSTRAINTS_KEY, [])
    existing_ids = {
        str(record.get("id") or "")
        for record in records
        if isinstance(record, Mapping)
    }
    added: list[str] = []
    for constraint in constraints:
        const_id = str(constraint.get("id") or "")
        if not const_id or const_id in existing_ids:
            continue
        records.append(dict(constraint))
        existing_ids.add(const_id)
        added.append(const_id)
    return added


__all__ = [
    "ACTIVE",
    "MINIMUM_GAP",
    "OPPONENT_AVOIDANCE",
    "RELEASED",
    "RequestConstraintError",
    "SUPPORTED_TYPES",
    "TEAM_CONSTRAINT_PREFIX",
    "TEAM_UNAVAILABLE",
    "active_request_constraints",
    "append_request_constraints",
    "compare_constraint_violations",
    "compare_request_constraint_violations",
    "constraint_id",
    "constraint_payload",
    "constraint_violations",
    "request_constraint_records",
    "request_constraint_report",
    "request_constraint_violations",
    "violation_identity",
    "violation_severity",
    "resolve_team_identity",
    "team_identity",
    "host_sibling_preference_evidence",
    "count_host_sibling_preference_violations",
    "validate_and_normalize",
]