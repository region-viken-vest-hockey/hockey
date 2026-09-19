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

from datetime import date as _date
from typing import Any, Iterable, Mapping

from tournament_scheduler.canonical_state import REQUEST_CONSTRAINTS_KEY
from tournament_scheduler.pipeline.fingerprints import stable_payload_sha256

ACTIVE = "active"
RELEASED = "released"

TEAM_UNAVAILABLE = "team_unavailable"
MINIMUM_GAP = "minimum_gap"
OPPONENT_AVOIDANCE = "opponent_avoidance"

SUPPORTED_TYPES: tuple[str, ...] = (
    TEAM_UNAVAILABLE,
    MINIMUM_GAP,
    OPPONENT_AVOIDANCE,
)

TEAM_CONSTRAINT_PREFIX = "request"


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
    else:  # opponent_avoidance
        if len(raw_teams) != 2:
            raise RequestConstraintError(
                "Constraint type 'opponent_avoidance' requires exactly two teams"
            )

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

    return violations


def request_constraint_violations(
    plan: Mapping[str, Any],
    decisions: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Return every active request-constraint violation for a plan."""

    violations: list[dict[str, Any]] = []
    for constraint in active_request_constraints(decisions):
        violations.extend(constraint_violations(plan, constraint))
    return violations


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
    "constraint_id",
    "constraint_payload",
    "constraint_violations",
    "request_constraint_records",
    "request_constraint_report",
    "request_constraint_violations",
    "resolve_team_identity",
    "team_identity",
    "validate_and_normalize",
]
