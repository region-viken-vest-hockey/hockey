"""Canonical eligibility/withdrawal reconciliation for promoted seasons.

A mid-season team withdrawal is a durable fact about the *eligible pool* for an
age group, not only a per-tournament roster edit. ``effective_tournament_shape``
derives the legal no-bye shape from the registered pool, so removing a
participant without recording the pool change looks like an avoidable
underfill and is correctly refused. This module owns the explicit,
revision-bound reconciliation record that lets the verifier and game generator
see the correct active pool at the relevant scope.

The registered ``Lag`` roster (and the historical participation of an already
completed tournament) is never rewritten: withdrawal records are additive
canonical decisions. A genuine season/age-group withdrawal is **durable and
age-group scoped**: it reduces the eligible pool for the whole age group so a
later maintenance, rebuild or newly materialized tournament cannot silently
reintroduce the team. A one-tournament participant absence without such a
record keeps the full registered pool and therefore fails closed if the smaller
shape is not independently legal.

Identity mirrors canonical team identity ``(club, label, age_group)``. The
verifier consumes the projection through :func:`project_into_problem`; no other
module re-derives an equivalent scope.

Reversal is explicit. A record is released (status ``released``) by the
canonical release operation (CLI ``season release-withdrawal``). Release is
itself verified: it refuses to write when it would leave the current schedule
invalid (for example a four-team field in a five-team pool), and it may restore
the participant atomically in the same commit. An active record can never be
silently dropped by a roster merely regaining the team; the verifier instead
reports ``withdrawn_team_participating``. Only registration reconciliation
(removing the team from the authoritative pool) or an explicit verified release
ends the ineligibility.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

from tournament_scheduler.canonical_state import PARTICIPATION_WITHDRAWALS_KEY
from tournament_scheduler.pipeline.fingerprints import stable_payload_sha256

ACTIVE = "active"
RELEASED = "released"

#: Durable, season/age-group scoped withdrawal. The reduction applies to every
#: current and future tournament in the age group.
SCOPE_AGE_GROUP = "age_group"
#: Legacy/explicit per-tournament scope. Kept readable so older records (or an
#: intentionally single-tournament decision) still project correctly.
SCOPE_TOURNAMENT = "tournament"

#: Problem field the verifier reads for the presence-reconciled shape pool.
#: Kept as a list of ``{"scope", "tournament_ids", "effective_from", "club",
#: "label", "age_group"}`` entries so a single problem stays a plain,
#: serializable structure.
WITHDRAWN_TEAMS_FIELD = "withdrawn_tournament_teams"

#: Problem field carrying the durable ineligibility projection. Unlike the
#: shape field it is never presence-reconciled, so an active withdrawal keeps
#: the team ineligible for the age group even if a roster regains it.
WITHDRAWN_INELIGIBLE_FIELD = "withdrawn_ineligible_teams"


def team_identity(team: Mapping[str, Any], fallback_age_group: str = "") -> tuple[str, str, str]:
    return (
        str(team.get("club") or ""),
        str(team.get("label") or ""),
        str(team.get("age_group") or fallback_age_group),
    )


def registered_team_identities(
    problem: Mapping[str, Any] | None,
) -> tuple[set[tuple[str, str, str]], set[tuple[str, str]]]:
    """Return the registered eligible pool as identity sets.

    The first set is the full canonical ``(club, label, age_group)`` identity.
    The second contains ``(club, label)`` pairs for legacy teams whose age group
    is missing, so a withdrawal target can still be recognised without
    fabricating an age group.
    """

    if not isinstance(problem, Mapping):
        return set(), set()
    full: set[tuple[str, str, str]] = set()
    ageless: set[tuple[str, str]] = set()
    for team in problem.get("teams", []) or []:
        if not isinstance(team, Mapping):
            continue
        club = str(team.get("club") or "")
        label = str(team.get("label") or "")
        if not club or not label:
            continue
        age_group = str(team.get("age_group") or "")
        if age_group:
            full.add((club, label, age_group))
        else:
            ageless.add((club, label))
    return full, ageless


def is_registered_participant(
    problem: Mapping[str, Any] | None,
    identity: tuple[str, str, str],
) -> bool:
    """Return whether *identity* exists in the authoritative registered pool.

    The full ``(club, label, age_group)`` identity must match a registered team;
    a legacy team without a recorded age group also matches on ``(club, label)``.
    """

    club, label, age_group = (str(part) for part in identity)
    if not club or not label:
        return False
    full, ageless = registered_team_identities(problem)
    if (club, label, age_group) in full:
        return True
    return (club, label) in ageless


def _record_id(
    *,
    team: Mapping[str, Any],
    age_group: str,
    scope: str,
    tournament_ids: Iterable[str],
    effective_from: str,
    request_id: str,
    source_revision: str,
) -> str:
    digest = stable_payload_sha256(
        {
            "kind": "participation_withdrawal",
            "scope": scope,
            "team": {
                "club": str(team.get("club") or ""),
                "label": str(team.get("label") or ""),
                "age_group": str(team.get("age_group") or age_group),
            },
            "age_group": str(age_group or ""),
            "tournament_ids": sorted({str(item) for item in tournament_ids if str(item)}),
            "effective_from": str(effective_from or ""),
            "request_id": str(request_id or ""),
            "source_revision": str(source_revision or ""),
        }
    )
    return f"withdrawal:{digest[:16]}"


def build_withdrawal_records(
    *,
    team: Mapping[str, Any],
    tournament_ids: Iterable[str],
    request_id: str,
    actor: str,
    note: str,
    created_at: str,
    source_revision: str,
    effective_from: str = "",
) -> list[dict[str, Any]]:
    """Build one durable, age-group scoped withdrawal record for a withdrawal.

    The affected tournament ids are retained as provenance, not as the scope of
    the eligibility reduction: a genuine season/age-group withdrawal makes the
    team ineligible for the whole age group until it is explicitly released.
    ``effective_from`` is the earliest affected tournament date, so completed
    tournaments that already happened before the withdrawal keep the team as
    historical provenance.
    """

    resolved_ids = sorted({str(item) for item in tournament_ids if str(item)})
    age_group = str(team.get("age_group") or "")
    if not age_group:
        raise ValueError("A participation withdrawal requires a team age_group")
    record = {
        "kind": "participation_withdrawal",
        "status": ACTIVE,
        "scope": SCOPE_AGE_GROUP,
        "team": {
            "club": str(team.get("club") or ""),
            "label": str(team.get("label") or ""),
            "age_group": age_group,
        },
        "age_group": age_group,
        "tournament_ids": resolved_ids,
        "effective_from": str(effective_from or ""),
        "request_id": str(request_id or ""),
        "created_at": created_at,
        "created_by": actor,
        "note": note or "",
        "source_event": "participant_removal",
        "source_revision": source_revision,
    }
    record["id"] = _record_id(
        team=record["team"],
        age_group=age_group,
        scope=SCOPE_AGE_GROUP,
        tournament_ids=resolved_ids,
        effective_from=str(effective_from or ""),
        request_id=str(request_id or ""),
        source_revision=source_revision,
    )
    return [record]


def active_withdrawals(decisions: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(decisions, Mapping):
        return []
    return [
        dict(record)
        for record in decisions.get(PARTICIPATION_WITHDRAWALS_KEY, []) or []
        if isinstance(record, Mapping) and str(record.get("status") or ACTIVE) == ACTIVE
    ]


def record_scope(record: Mapping[str, Any]) -> str:
    """Resolve a record's scope, normalizing legacy ``tournament_id`` records."""

    scope = str(record.get("scope") or "")
    if scope in (SCOPE_AGE_GROUP, SCOPE_TOURNAMENT):
        return scope
    if record.get("tournament_id"):
        return SCOPE_TOURNAMENT
    return SCOPE_AGE_GROUP if record.get("age_group") else SCOPE_TOURNAMENT


def record_tournament_ids(record: Mapping[str, Any]) -> list[str]:
    """Return a record's affected tournament ids as durable provenance."""

    raw = record.get("tournament_ids")
    if isinstance(raw, (list, tuple, set)):
        ids = [str(item) for item in raw if str(item)]
    else:
        ids = []
    tournament_id = str(record.get("tournament_id") or "")
    if tournament_id and tournament_id not in ids:
        ids.append(tournament_id)
    return sorted(dict.fromkeys(ids))


def _record_age_group(record: Mapping[str, Any]) -> str:
    age_group = str(record.get("age_group") or "")
    if age_group:
        return age_group
    team = record.get("team")
    if isinstance(team, Mapping):
        return str(team.get("age_group") or "")
    return ""


def _record_entry(record: Mapping[str, Any]) -> dict[str, Any] | None:
    team = record.get("team")
    if not isinstance(team, Mapping):
        return None
    age_group = _record_age_group(record)
    return {
        "scope": record_scope(record),
        "tournament_ids": record_tournament_ids(record),
        "effective_from": str(record.get("effective_from") or ""),
        "club": str(team.get("club") or ""),
        "label": str(team.get("label") or ""),
        "age_group": age_group,
    }


def _record_entries(records: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str, tuple[str, ...]]] = set()
    for record in records:
        if not isinstance(record, Mapping):
            continue
        entry = _record_entry(record)
        if entry is None:
            continue
        identity = (
            entry["scope"],
            entry["club"],
            entry["label"],
            entry["age_group"],
            tuple(entry["tournament_ids"]),
        )
        if identity in seen:
            continue
        seen.add(identity)
        entries.append(entry)
    return entries


def _normalize_entry(raw: Mapping[str, Any]) -> dict[str, Any] | None:
    club = str(raw.get("club") or "")
    label = str(raw.get("label") or "")
    if not club or not label:
        return None
    age_group = str(raw.get("age_group") or "")
    scope = str(raw.get("scope") or "")
    tournament_ids: list[str] = []
    if isinstance(raw.get("tournament_ids"), (list, tuple, set)):
        tournament_ids = [str(item) for item in raw["tournament_ids"] if str(item)]
    tournament_id = str(raw.get("tournament_id") or "")
    if tournament_id and tournament_id not in tournament_ids:
        tournament_ids.append(tournament_id)
    if scope not in (SCOPE_AGE_GROUP, SCOPE_TOURNAMENT):
        scope = SCOPE_TOURNAMENT if tournament_ids else SCOPE_AGE_GROUP
    return {
        "scope": scope,
        "tournament_ids": sorted(dict.fromkeys(tournament_ids)),
        "effective_from": str(raw.get("effective_from") or ""),
        "club": club,
        "label": label,
        "age_group": age_group,
    }


def _entries_from_field(
    problem: Mapping[str, Any] | None, field: str
) -> list[dict[str, Any]]:
    if not isinstance(problem, Mapping):
        return []
    raw = problem.get(field)
    if not isinstance(raw, list):
        return []
    entries: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str, tuple[str, ...], str]] = set()
    for item in raw:
        if not isinstance(item, Mapping):
            continue
        entry = _normalize_entry(item)
        if entry is None:
            continue
        key = (
            entry["scope"],
            entry["club"],
            entry["label"],
            entry["age_group"],
            tuple(entry["tournament_ids"]),
            str(entry.get("effective_from") or ""),
        )
        if key in seen:
            continue
        seen.add(key)
        entries.append(entry)
    return entries


def withdrawn_entries(problem: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    """Return the presence-reconciled shape projection already on a problem."""

    return _entries_from_field(problem, WITHDRAWN_TEAMS_FIELD)


def withdrawn_ineligible_entries(
    problem: Mapping[str, Any] | None,
) -> list[dict[str, Any]]:
    """Return the durable ineligibility projection already on a problem.

    This projection is never presence-reconciled: an active withdrawal keeps the
    team ineligible for the age group even if a roster regains it, so the
    verifier can refuse a silent reintroduction.
    """

    return _entries_from_field(problem, WITHDRAWN_INELIGIBLE_FIELD)


def _entry_scopes_tournament(entry: Mapping[str, Any], tournament_id: str) -> bool:
    """Return whether an entry's eligibility reduction covers a tournament."""

    if entry.get("scope") == SCOPE_AGE_GROUP:
        return True
    return str(tournament_id) in (entry.get("tournament_ids") or [])


def _scoped_identities(
    entries: Iterable[Mapping[str, Any]],
    tournament_id: str,
    age_group: str,
    tournament_date: str | None,
) -> set[tuple[str, str, str]]:
    resolved_id = str(tournament_id or "")
    resolved_age = str(age_group or "")
    resolved_date = str(tournament_date or "")
    identities: set[tuple[str, str, str]] = set()
    for entry in entries:
        if resolved_age and entry["age_group"] and entry["age_group"] != resolved_age:
            continue
        effective_from = str(entry.get("effective_from") or "")
        if resolved_date and effective_from and resolved_date < effective_from:
            continue
        if not _entry_scopes_tournament(entry, resolved_id):
            continue
        identities.add((entry["club"], entry["label"], entry["age_group"]))
    return identities


def withdrawn_team_identities_for_tournament(
    problem: Mapping[str, Any] | None,
    tournament_id: str,
    age_group: str,
    tournament_date: str | None = None,
) -> set[tuple[str, str, str]]:
    """Return the durable ineligible identities that scope one tournament.

    An age-group scoped withdrawal covers every tournament in the age group; a
    tournament scoped withdrawal covers only the tournaments it names. A
    tournament dated before the withdrawal's ``effective_from`` is historical
    and keeps the team as provenance.
    """

    return _scoped_identities(
        withdrawn_ineligible_entries(problem), tournament_id, age_group, tournament_date
    )


def _plan_roster_identities(
    plan: Mapping[str, Any] | None,
) -> set[tuple[str, str, str, str, str]]:
    """Return ``(tournament_id, date, club, label, age_group)`` for participants."""

    present: set[tuple[str, str, str, str, str]] = set()
    if not isinstance(plan, Mapping):
        return present
    for tournament in plan.get("tournaments", []) or []:
        if not isinstance(tournament, Mapping):
            continue
        if tournament.get("cancelled"):
            continue
        tournament_id = str(tournament.get("id") or "")
        if not tournament_id:
            continue
        tournament_date = str(tournament.get("date") or "")
        age_group = str(tournament.get("age_group") or "")
        for team in tournament.get("teams", []) or []:
            if not isinstance(team, Mapping):
                continue
            present.add(
                (
                    tournament_id,
                    tournament_date,
                    str(team.get("club") or ""),
                    str(team.get("label") or ""),
                    str(team.get("age_group") or age_group),
                )
            )
    return present


def _entry_is_restored(
    entry: Mapping[str, Any],
    present: set[tuple[str, str, str, str, str]],
) -> bool:
    """Return whether a team is back on a scoped roster on/after effective_from."""

    effective_from = str(entry.get("effective_from") or "")
    identity = (entry["club"], entry["label"], entry["age_group"])
    for tournament_id, tournament_date, club, label, age_group in present:
        if (club, label, age_group) != identity:
            continue
        if effective_from and tournament_date and tournament_date < effective_from:
            continue
        if entry.get("scope") == SCOPE_AGE_GROUP:
            return True
        if tournament_id in (entry.get("tournament_ids") or []):
            return True
    return False


def _dedupe_entries(entries: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str, tuple[str, ...], str]] = set()
    for entry in entries:
        key = (
            str(entry.get("scope") or ""),
            str(entry.get("club") or ""),
            str(entry.get("label") or ""),
            str(entry.get("age_group") or ""),
            tuple(sorted({str(item) for item in entry.get("tournament_ids") or [] if str(item)})),
            str(entry.get("effective_from") or ""),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(dict(entry))
    return deduped


def _filter_registered_entries(
    entries: list[dict[str, Any]],
    *,
    problem: Mapping[str, Any] | None,
) -> list[dict[str, Any]]:
    """Drop entries whose team is no longer in the registered eligible pool."""

    registered_full, registered_ageless = registered_team_identities(problem)
    if not (registered_full or registered_ageless):
        return entries
    return [
        entry
        for entry in entries
        if is_registered_participant(
            problem, (entry["club"], entry["label"], entry["age_group"])
        )
    ]


def _filter_scoped_entries(
    entries: list[dict[str, Any]],
    *,
    plan: Mapping[str, Any] | None,
    problem: Mapping[str, Any] | None,
) -> list[dict[str, Any]]:
    """Reconcile the *shape* pool projection against the final roster.

    A record stops reducing the eligible shape pool once its team is restored
    to a scoped roster on/after the withdrawal's effective date, or once
    registration reconciliation removes the team from the authoritative pool.
    Durability is enforced independently by the un-filtered ineligibility
    projection, so this presence-based reduction never lets a withdrawn team
    silently rejoin.
    """

    registered = _filter_registered_entries(entries, problem=problem)
    present = _plan_roster_identities(plan) if plan is not None else set()
    if not present:
        return registered
    return [entry for entry in registered if not _entry_is_restored(entry, present)]


def project_into_problem(
    problem: Mapping[str, Any] | None,
    *,
    decisions: Mapping[str, Any] | None = None,
    records: Iterable[Mapping[str, Any]] | None = None,
    plan: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a copy of *problem* carrying the active withdrawal scopes.

    Two additive projections are produced:

    * :data:`WITHDRAWN_TEAMS_FIELD` is the *shape* pool reduction. When a
      *plan* is supplied it is reconciled against that final roster, so a
      candidate that deliberately restores a participant does not also carry a
      stale eligibility reduction.
    * :data:`WITHDRAWN_INELIGIBLE_FIELD` is the durable ineligibility
      projection. It is never presence-reconciled, so the verifier refuses a
      silent reintroduction until the record is explicitly released.
    """

    resolved: dict[str, Any] = dict(problem) if isinstance(problem, Mapping) else {}
    if not resolved:
        return resolved
    combined = list(active_withdrawals(decisions))
    if records:
        combined.extend(dict(record) for record in records if isinstance(record, Mapping))
    new_entries = _record_entries(combined)
    # When the caller supplies authoritative *decisions*, those are the complete
    # active set: rebuild the fields from them instead of unioning whatever
    # projections an earlier pass left on the problem. Otherwise stale entries
    # (e.g. a record that was just released) would survive re-projection. When
    # only *records* are supplied for a candidate under construction, the
    # existing authoritative projections must be preserved and extended.
    base_shape = [] if decisions is not None else withdrawn_entries(resolved)
    base_ineligible = (
        [] if decisions is not None else withdrawn_ineligible_entries(resolved)
    )
    shape_entries = _dedupe_entries([*base_shape, *new_entries])
    ineligible_entries = _dedupe_entries([*base_ineligible, *new_entries])
    if plan is not None:
        shape_entries = _filter_scoped_entries(
            shape_entries, plan=plan, problem=resolved
        )
    ineligible_entries = _filter_registered_entries(
        ineligible_entries, problem=resolved
    )
    resolved[WITHDRAWN_TEAMS_FIELD] = shape_entries
    resolved[WITHDRAWN_INELIGIBLE_FIELD] = ineligible_entries
    return resolved


def withdrawn_team_count_for_tournament(
    problem: Mapping[str, Any] | None,
    tournament_id: str,
    age_group: str,
    tournament_date: str | None = None,
) -> int:
    """Return how many distinct withdrawn teams reduce one tournament's shape pool."""

    return len(
        _scoped_identities(
            withdrawn_entries(problem), tournament_id, age_group, tournament_date
        )
    )


def effective_from_for_tournaments(
    plan: Mapping[str, Any] | None,
    tournament_ids: Iterable[str],
) -> str:
    """Return the earliest date of the named tournaments, or ``""``."""

    wanted = {str(item) for item in tournament_ids if str(item)}
    dates = [
        str(tournament.get("date") or "")
        for tournament in (plan or {}).get("tournaments", []) or []
        if str(tournament.get("id") or "") in wanted and tournament.get("date")
    ]
    return min(dates) if dates else ""


def append_withdrawal_records(
    decisions: dict[str, Any],
    records: list[dict[str, Any]],
) -> None:
    existing = decisions.setdefault(PARTICIPATION_WITHDRAWALS_KEY, [])
    existing_ids = {str(record.get("id") or "") for record in existing if isinstance(record, Mapping)}
    for record in records:
        record_id = str(record.get("id") or "")
        if record_id and record_id not in existing_ids:
            existing.append(dict(record))
            existing_ids.add(record_id)


__all__ = [
    "ACTIVE",
    "PARTICIPATION_WITHDRAWALS_KEY",
    "RELEASED",
    "SCOPE_AGE_GROUP",
    "SCOPE_TOURNAMENT",
    "WITHDRAWN_INELIGIBLE_FIELD",
    "WITHDRAWN_TEAMS_FIELD",
    "active_withdrawals",
    "append_withdrawal_records",
    "build_withdrawal_records",
    "effective_from_for_tournaments",
    "is_registered_participant",
    "project_into_problem",
    "record_scope",
    "record_tournament_ids",
    "registered_team_identities",
    "team_identity",
    "withdrawn_entries",
    "withdrawn_ineligible_entries",
    "withdrawn_team_count_for_tournament",
    "withdrawn_team_identities_for_tournament",
]
