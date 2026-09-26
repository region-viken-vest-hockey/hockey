"""Scoped participant removal and season/age-group withdrawal mutations.

This module owns the canonical participant-removal use case. Two related but
deliberately distinct outcomes share one atomic operation:

* **one-tournament participant absence** -- remove one participant from one
  tournament and regenerate its games *without* changing the registered
  eligible pool. If the reduced shape is avoidably underfilled the operation
  fails closed; the operator must make an explicit withdrawal decision.
* **genuine season/age-group withdrawal** -- the same removal, together with a
  revision-bound :mod:`tournament_scheduler.participation_withdrawals` record
  that reduces the *eligible* shape pool for exactly the affected tournaments.
  The registered roster and any historical participation are preserved.

Both paths share the single canonical load -> mutate -> verify -> reconcile ->
history -> revision -> atomic-write lifecycle. No date, host, arena or booked
occupancy interval is changed, and completed tournaments are never rewritten.
"""

from __future__ import annotations

import copy
from typing import Any, Mapping

from tournament_scheduler.canonical_baseline import approval_fingerprint, resolve_approval
from tournament_scheduler.canonical_history_summary import verification_summary
from tournament_scheduler.canonical_state import (
    CHANGE_PROTECTIONS_KEY,
    PARTICIPATION_WITHDRAWALS_KEY,
    canonical_state_revision,
    schedule_fingerprint,
)
from tournament_scheduler.change_protections import (
    ACTIVE as CHANGE_PROTECTION_ACTIVE,
    MUST_NOT_PARTICIPATE,
    build_net_roster_protections,
    protection_violations,
)
from tournament_scheduler.infrastructure.canonical_season_store import (
    SeasonStateError,
)
from tournament_scheduler.participation_withdrawals import (
    ACTIVE as WITHDRAWAL_ACTIVE,
    SCOPE_AGE_GROUP,
    active_withdrawals,
    build_withdrawal_records,
    effective_from_for_tournaments,
    project_into_problem,
    record_scope,
    record_tournament_ids,
    team_identity as _team_identity,
)
from tournament_scheduler.plan_derived_state import reconcile_plan_derived_state
from tournament_scheduler.planning_contract import verify_candidate
from tournament_scheduler.request_constraints import request_constraint_violations

from .removal_policy import (
    evaluate_removal_consequences,
    require_no_avoidable_underfill,
    require_registered_removal_target,
)
from .replacement import _affected_participation_counts
from .shared import (
    _operator_identity,
    _now_iso,
    _append_decision_history,
    _resolve_plan_problem,
    _regenerate_tournament_games,
    _guest_reservation_signature,
)


def _apply_remove_participant_to_plan(
    plan: dict[str, Any],
    *,
    tournament_id: str,
    remove_team_label: str,
    problem: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Remove one RVV participant from one tournament and regenerate its games."""

    tournaments = plan.get("tournaments", []) or []
    target = next((t for t in tournaments if str(t.get("id") or "") == tournament_id), None)
    if target is None:
        raise SeasonStateError(f"Unknown tournament id in canonical schedule: {tournament_id}")
    if target.get("cancelled"):
        raise SeasonStateError(f"Tournament {tournament_id} is cancelled and cannot participate in a roster removal")
    age_group = str(target.get("age_group") or "")

    matches = [
        (index, team)
        for index, team in enumerate(target.get("teams", []) or [])
        if str(team.get("label") or "") == remove_team_label
    ]
    if not matches:
        raise SeasonStateError(f"Team {remove_team_label!r} is not a participant in tournament {tournament_id}")
    if len(matches) > 1:
        raise SeasonStateError(f"Team label {remove_team_label!r} is ambiguous in tournament {tournament_id}")
    index, removed_team = matches[0]
    if bool(removed_team.get("guest", False)):
        raise SeasonStateError(
            f"Team {remove_team_label!r} in tournament {tournament_id} is a guest participant; "
            "use the guest-slot lifecycle instead"
        )

    removed_identity = _team_identity(removed_team, age_group)
    if removed_identity[2] != age_group:
        raise SeasonStateError(
            f"Cannot remove {remove_team_label!r}: participant age group "
            f"{removed_identity[2] or '<missing>'} does not match tournament {age_group}"
        )

    before_fingerprint = approval_fingerprint(target)
    del target["teams"][index]
    _regenerate_tournament_games(target, problem)
    return {
        "tournament": target,
        "removed_team": copy.deepcopy(removed_team),
        "removed_identity": removed_identity,
        "age_group": age_group,
        "before_fingerprint": before_fingerprint,
    }


def withdrawal_report(
    service,
    season: str,
    *,
    include_released: bool = False,
) -> dict[str, Any]:
    """Return the canonical participation-withdrawal ledger for a season.

    A genuine withdrawal is durable and age-group scoped, so an active record
    keeps reducing the eligible pool even if a roster regains the team; the
    verifier then reports ``withdrawn_team_participating`` and the operator must
    reverse the withdrawal explicitly. ``restored`` reports that the team is
    back on a scoped roster (an unauthorized reintroduction), while
    ``superseded`` is only true once registration reconciliation removes the
    team from the authoritative pool and the record can no longer reduce it.
    """

    snapshot = service.load(season)
    schedule, decisions = snapshot.schedule, snapshot.decisions
    problem = _resolve_plan_problem(schedule, None, decisions)
    plan = schedule.get("plan") or {}
    from tournament_scheduler.participation_withdrawals import (
        is_registered_participant,
        team_identity,
    )

    present: set[tuple[str, str, str, str, str]] = set()
    for tournament in plan.get("tournaments", []) or []:
        if tournament.get("cancelled"):
            continue
        tournament_id = str(tournament.get("id") or "")
        tournament_date = str(tournament.get("date") or "")
        age_group = str(tournament.get("age_group") or "")
        for team in tournament.get("teams", []) or []:
            identity = team_identity(team, age_group)
            present.add(
                (tournament_id, tournament_date, identity[0], identity[1], identity[2])
            )

    entries: list[dict[str, Any]] = []
    for record in decisions.get(PARTICIPATION_WITHDRAWALS_KEY, []) or []:
        if not isinstance(record, Mapping):
            continue
        entry = dict(record)
        status = str(entry.get("status") or WITHDRAWAL_ACTIVE)
        entry["status"] = status
        entry["scope"] = record_scope(record)
        entry["tournament_ids"] = record_tournament_ids(record)
        if status == WITHDRAWAL_ACTIVE:
            team = entry.get("team") if isinstance(entry.get("team"), Mapping) else {}
            identity = (
                str(team.get("club") or ""),
                str(team.get("label") or ""),
                str(team.get("age_group") or ""),
            )
            effective_from = str(entry.get("effective_from") or "")
            scoped_ids = set(entry["tournament_ids"])
            restored = False
            for tournament_id, tournament_date, club, label, age_group in present:
                if (club, label, age_group) != identity:
                    continue
                if effective_from and tournament_date and tournament_date < effective_from:
                    continue
                if entry["scope"] == SCOPE_AGE_GROUP or tournament_id in scoped_ids:
                    restored = True
                    break
            registered = is_registered_participant(problem, identity)
            entry["restored"] = restored
            entry["registered"] = registered
            entry["superseded"] = not registered
        entries.append(entry)

    visible = [entry for entry in entries if include_released or entry["status"] == WITHDRAWAL_ACTIVE]
    return {
        "season": season,
        "canonical_state_revision": canonical_state_revision(schedule, decisions),
        "active_count": sum(1 for entry in visible if entry["status"] == WITHDRAWAL_ACTIVE),
        "superseded_count": sum(1 for entry in visible if entry.get("superseded")),
        "withdrawals": visible,
    }


def _apply_add_participant_to_plan(
    plan: dict[str, Any],
    *,
    tournament_id: str,
    team: Mapping[str, Any],
    problem: Mapping[str, Any] | None,
) -> None:
    """Add one registered participant back to a tournament and regenerate its games."""

    tournaments = plan.get("tournaments", []) or []
    target = next((t for t in tournaments if str(t.get("id") or "") == tournament_id), None)
    if target is None:
        raise SeasonStateError(f"Unknown tournament id in canonical schedule: {tournament_id}")
    if target.get("cancelled"):
        raise SeasonStateError(
            f"Tournament {tournament_id} is cancelled and cannot have a participant restored"
        )
    age_group = str(target.get("age_group") or "")
    club = str(team.get("club") or "")
    label = str(team.get("label") or "")
    team_age_group = str(team.get("age_group") or age_group)
    if team_age_group != age_group:
        raise SeasonStateError(
            f"Cannot restore {label!r}: participant age group {team_age_group or '<missing>'} "
            f"does not match tournament {age_group}"
        )
    teams = target.setdefault("teams", [])
    if any(
        str(entry.get("club") or "") == club and str(entry.get("label") or "") == label
        for entry in teams
    ):
        return
    teams.append(
        {"club": club, "label": label, "age_group": team_age_group, "guest": False}
    )
    _regenerate_tournament_games(target, problem)


def release_participation_withdrawals(
    service,
    *,
    season: str,
    withdrawal_ids: list[str] | None = None,
    request_id: str | None = None,
    actor: str | None = None,
    note: str = "",
    restore_participants: bool = False,
) -> dict[str, Any]:
    """Explicitly, *and verifiably*, release withdrawal records.

    Release is not a silent decision-only write: the current schedule (or an
    explicitly restored one) is re-verified against the post-release eligible
    pool before anything is written, so a premature release that would leave
    e.g. four-team fields in a now-five-team pool is refused with no canonical
    write.

    ``restore_participants`` is the authorized reversal path. It runs through
    the same complete canonical mutation boundary as apply/batch: a typed,
    replayable ``participant_restoration`` authorization is reproduced from the
    current revision, the candidate is checked for hard verification, request
    constraints, locks, protections, guest integrity, operational
    acceptability and hosting responsibility, and the sealed published-baseline
    replay runs at the apply boundary. The record release and the removal's
    ``must_not_participate`` guards are released in the same atomic commit.
    Provenance is never erased -- only the record's ``status`` changes.
    """

    wanted_ids = {str(value) for value in (withdrawal_ids or []) if str(value)}
    wanted_request = str(request_id or "")
    if not wanted_ids and not wanted_request:
        raise SeasonStateError(
            "Refusing withdrawal release: provide --withdrawal-id and/or --request-id"
        )

    snapshot = service.load(season)
    decisions = snapshot.decisions
    now = _now_iso()
    resolved_actor = _operator_identity(actor)
    released: list[str] = []
    released_requests: set[str] = set()
    restorations: list[tuple[str, dict[str, Any]]] = []
    for record in decisions.get(PARTICIPATION_WITHDRAWALS_KEY, []) or []:
        if not isinstance(record, Mapping):
            continue
        if str(record.get("status") or WITHDRAWAL_ACTIVE) != WITHDRAWAL_ACTIVE:
            continue
        matches_id = str(record.get("id") or "") in wanted_ids
        matches_request = bool(wanted_request) and str(record.get("request_id") or "") == wanted_request
        if not (matches_id or matches_request):
            continue
        released.append(str(record.get("id") or ""))
        released_requests.add(str(record.get("request_id") or ""))
        if restore_participants and isinstance(record.get("team"), Mapping):
            team = dict(record["team"])
            for tournament_id in record_tournament_ids(record):
                restorations.append((tournament_id, team))

    if not released:
        raise SeasonStateError("No active participation withdrawals matched the release request")

    released_protections = _matching_must_not_participate_protections(
        decisions,
        released_requests=released_requests,
        restorations=restorations,
    )

    if not restore_participants:
        return _release_withdrawals_decision_only(
            service,
            season=season,
            snapshot=snapshot,
            released=released,
            released_protections=released_protections,
            actor=resolved_actor,
            note=note,
            now=now,
        )

    resume = _restore_participants(
        service,
        season=season,
        snapshot=snapshot,
        restorations=restorations,
        released=released,
        released_protections=released_protections,
        request_id=wanted_request,
        actor=resolved_actor,
        note=note,
    )
    return resume


def _matching_must_not_participate_protections(
    decisions: Mapping[str, Any],
    *,
    released_requests: set[str],
    restorations: list[tuple[str, dict[str, Any]]],
) -> list[str]:
    """Return the removal guards the release must also release."""

    restoration_keys = {
        (
            str(tournament_id),
            (
                str((team or {}).get("club") or ""),
                str((team or {}).get("label") or ""),
                str((team or {}).get("age_group") or ""),
            ),
        )
        for tournament_id, team in restorations
    }
    released: list[str] = []
    for protection in decisions.get(CHANGE_PROTECTIONS_KEY, []) or []:
        if not isinstance(protection, Mapping):
            continue
        if (
            str(protection.get("status") or CHANGE_PROTECTION_ACTIVE)
            != CHANGE_PROTECTION_ACTIVE
        ):
            continue
        if str(protection.get("kind") or "") != MUST_NOT_PARTICIPATE:
            continue
        if str(protection.get("request_id") or "") not in released_requests:
            continue
        protection_team = protection.get("team") or {}
        key = (
            str(protection.get("tournament_id") or ""),
            (
                str(protection_team.get("club") or ""),
                str(protection_team.get("label") or ""),
                str(protection_team.get("age_group") or ""),
            ),
        )
        if key not in restoration_keys:
            continue
        released.append(str(protection.get("id") or ""))
    return released


def _release_withdrawals_decision_only(
    service,
    *,
    season: str,
    snapshot,
    released: list[str],
    released_protections: list[str],
    actor: str,
    note: str,
    now: str,
) -> dict[str, Any]:
    """Verified decision-only release for a schedule that does not change."""

    from .candidates import _with_released_records

    working = _with_released_records(
        snapshot.decisions,
        withdrawal_ids=released,
        protection_ids=released_protections,
        actor=actor,
        now=now,
        note=note,
    )
    problem = _resolve_plan_problem(snapshot.schedule, None, working)
    plan = copy.deepcopy(snapshot.schedule.get("plan") or {})
    result = (
        verify_candidate(copy.deepcopy(plan), problem)
        if problem
        else verify_candidate(copy.deepcopy(plan))
    )
    if not result.get("ok", True):
        messages = "; ".join(
            str(v.get("message") or v.get("code")) for v in result.get("violations", [])
        )
        raise SeasonStateError(
            "Refusing withdrawal release: the current schedule would not verify once the "
            "withdrawal is released; restore the participant atomically or reconcile the "
            "registration first: " + messages
        )

    from tournament_scheduler.canonical_baseline import (
        build_canonical_baseline,
        verify_canonical_locks,
    )

    baseline = build_canonical_baseline(snapshot.schedule, snapshot.decisions)
    lock_violations = verify_canonical_locks(baseline, plan)
    if lock_violations:
        messages = "; ".join(str(v.get("message")) for v in lock_violations)
        raise SeasonStateError(
            f"Refusing withdrawal release: candidate violates canonical locks: {messages}"
        )
    constraint_violations = request_constraint_violations(plan, working)
    if constraint_violations:
        messages = "; ".join(str(item.get("message")) for item in constraint_violations)
        raise SeasonStateError(
            "Refusing withdrawal release: it violates an active request constraint: " + messages
        )

    _append_decision_history(
        working,
        event="release_participation_withdrawal",
        tournament_id="",
        actor=actor,
        now=now,
        note=note,
        details={
            "released_withdrawal_ids": released,
            "released_protection_ids": released_protections,
            "restored_participants": False,
        },
    )
    committed = service._commit(snapshot.with_decisions(working))
    return {
        "season": season,
        "canonical_state_revision": canonical_state_revision(
            committed.schedule, committed.decisions
        ),
        "released_withdrawal_ids": released,
        "released_protection_ids": released_protections,
        "restored_participants": False,
        "active_count": len(active_withdrawals(committed.decisions)),
    }


def _restore_participants(
    service,
    *,
    season: str,
    snapshot,
    restorations: list[tuple[str, dict[str, Any]]],
    released: list[str],
    released_protections: list[str],
    request_id: str,
    actor: str,
    note: str,
) -> dict[str, Any]:
    """Atomically reverse a withdrawal through the complete apply boundary."""

    from tournament_scheduler.participation_withdrawals import (
        is_registered_participant,
    )

    identities = {
        (
            str((team or {}).get("club") or ""),
            str((team or {}).get("label") or ""),
            str((team or {}).get("age_group") or ""),
        )
        for _tournament_id, team in restorations
    }
    if len(identities) != 1:
        raise SeasonStateError(
            "Refusing withdrawal release: --restore-participant requires the released records "
            "to share one participant identity"
        )
    identity = next(iter(identities))
    registered_problem = _resolve_plan_problem(snapshot.schedule, None, snapshot.decisions)
    if registered_problem is None or not is_registered_participant(registered_problem, identity):
        raise SeasonStateError(
            f"Refusing withdrawal release: cannot restore {identity[1]!r} because it is no longer "
            "part of the registered eligible pool"
        )

    team = {"club": identity[0], "label": identity[1], "age_group": identity[2]}
    restoration_tournament_ids = sorted(
        {str(tournament_id) for tournament_id, _team in restorations if str(tournament_id)}
    )
    plan = copy.deepcopy(snapshot.schedule.get("plan") or {})
    for tournament_id in restoration_tournament_ids:
        _apply_add_participant_to_plan(
            plan, tournament_id=tournament_id, team=team, problem=registered_problem
        )

    from .scoped_mutation import authorize_participant_restoration

    authorization = authorize_participant_restoration(
        schedule=snapshot.schedule,
        decisions=snapshot.decisions,
        candidate=plan,
        tournament_ids=restoration_tournament_ids,
        team=team,
        released_withdrawal_ids=released,
        request_id=request_id or (released[0] if released else ""),
        actor=actor,
        note=note,
    )
    updated_schedule, updated_decisions, _cost = service.apply_candidate(
        season=season,
        candidate=plan,
        problem=None,
        actor=actor,
        operation="participant_restoration",
        _scoped_authorization=authorization,
        _history_event={
            "event": "participant_restoration",
            "tournament_id": "",
            "note": note,
            "details": {
                "released_withdrawal_ids": released,
                "released_protection_ids": released_protections,
                "restored_participants": True,
            },
        },
        _release_participation_withdrawal_ids=released,
        _release_change_protection_ids=released_protections,
    )
    return {
        "season": season,
        "canonical_state_revision": canonical_state_revision(
            updated_schedule, updated_decisions
        ),
        "released_withdrawal_ids": released,
        "released_protection_ids": released_protections,
        "restored_participants": True,
        "active_count": len(active_withdrawals(updated_decisions)),
    }



def remove_participant(
    service,
    *,
    season: str,
    tournament_ids: list[str],
    remove_team_label: str,
    reconcile_withdrawal: bool = False,
    problem: dict[str, Any] | None = None,
    actor: str | None = None,
    note: str = "",
    dry_run: bool = False,
    request_id: str | None = None,
    accept_regressions: list[Any] | None = None,
    accept_regression_reason: str | None = None,
) -> dict[str, Any]:
    """Remove one participant from one or more same-age canonical tournaments."""

    resolved_request_id = str(request_id or "").strip()
    if not resolved_request_id:
        raise SeasonStateError(
            "Refusing canonical participant removal: a stable --request-id is required so the "
            "operation is auditable and revision-bound"
        )
    resolved_tournament_ids = [str(item).strip() for item in tournament_ids if str(item).strip()]
    if not resolved_tournament_ids:
        raise SeasonStateError("Refusing canonical participant removal: at least one --tournament-id is required")
    if len(set(resolved_tournament_ids)) != len(resolved_tournament_ids):
        raise SeasonStateError("Refusing canonical participant removal: duplicate --tournament-id values")

    snapshot = service.load(season)
    schedule, decisions = snapshot.schedule, snapshot.decisions
    resolved_problem = _resolve_plan_problem(schedule, problem, decisions)
    if resolved_problem is None:
        raise SeasonStateError(
            "Participant removal requires a promoted verification-context problem so the "
            "candidate shape, hosting responsibility and pool legality can be verified"
        )
    before_plan = schedule.get("plan") or {}
    plan = copy.deepcopy(before_plan)
    before_canonical_revision = canonical_state_revision(schedule, decisions)

    by_id = {
        str(tournament.get("id") or ""): tournament
        for tournament in plan.get("tournaments", []) or []
        if tournament.get("id")
    }
    age_groups: set[str] = set()
    for tournament_id in resolved_tournament_ids:
        tournament = by_id.get(tournament_id)
        if tournament is None:
            raise SeasonStateError(f"Unknown tournament id in canonical schedule: {tournament_id}")
        if tournament.get("cancelled"):
            raise SeasonStateError(
                f"Tournament {tournament_id} is cancelled and cannot participate in a roster removal"
            )
        age_groups.add(str(tournament.get("age_group") or ""))
        resolved = resolve_approval(
            decisions.get("decisions", {}).get(tournament_id, {}),
            tournament,
        )
        if resolved["participants_locked"]:
            raise SeasonStateError(
                f"Tournament {tournament_id} has an active participant lock; unapprove it explicitly first"
            )
    if len(age_groups) != 1:
        raise SeasonStateError(
            "Refusing canonical participant removal: all affected tournaments must share one age "
            "group so the removed team identity is unambiguous"
        )
    age_group = next(iter(age_groups))

    removals = [
        _apply_remove_participant_to_plan(
            plan,
            tournament_id=tournament_id,
            remove_team_label=remove_team_label,
            problem=resolved_problem,
        )
        for tournament_id in resolved_tournament_ids
    ]
    removed_identities = {removal["removed_identity"] for removal in removals}
    if len(removed_identities) != 1:
        raise SeasonStateError(
            "Refusing canonical participant removal: the label resolves to different team "
            "identities across the affected tournaments"
        )
    removed_identity = next(iter(removed_identities))
    removed_team = removals[0]["removed_team"]

    if reconcile_withdrawal:
        require_registered_removal_target(
            resolved_problem,
            removed_identity=removed_identity,
            remove_team_label=remove_team_label,
        )
    else:
        # Fail closed before the expensive gates: without a withdrawal record the
        # full registered pool is used, so an avoidably underfilled result is
        # never silently accepted as a one-event absence.
        require_no_avoidable_underfill(plan, resolved_problem, resolved_tournament_ids)

    request_actor = _operator_identity(actor)
    created_at = _now_iso()
    withdrawal_records = (
        build_withdrawal_records(
            team=removed_team,
            tournament_ids=resolved_tournament_ids,
            request_id=resolved_request_id,
            actor=request_actor,
            note=note,
            created_at=created_at,
            source_revision=before_canonical_revision,
            effective_from=effective_from_for_tournaments(plan, resolved_tournament_ids),
        )
        if reconcile_withdrawal
        else []
    )
    verification_problem = project_into_problem(resolved_problem, records=withdrawal_records, plan=plan)

    from tournament_scheduler.canonical_baseline import (
        build_canonical_baseline,
        change_cost,
        verify_canonical_locks,
    )

    baseline = build_canonical_baseline(schedule, decisions)
    lock_violations = verify_canonical_locks(baseline, plan)
    if lock_violations:
        messages = "; ".join(str(v.get("message")) for v in lock_violations)
        raise SeasonStateError(
            f"Refusing canonical participant removal: candidate violates canonical locks: {messages}"
        )

    result = verify_candidate(plan, verification_problem)
    if not result.get("ok", True):
        messages = "; ".join(str(v.get("message") or v.get("code")) for v in result.get("violations", []))
        raise SeasonStateError(f"Refusing canonical participant removal: candidate fails hard verification: {messages}")

    from tournament_scheduler.hosting_responsibility import (
        unexplained_responsibility_transfers,
    )

    transfers = unexplained_responsibility_transfers(
        before_plan,
        plan,
        verification_problem,
    )
    if transfers:
        messages = "; ".join(str(entry.get("message")) for entry in transfers)
        raise SeasonStateError(
            f"Refusing canonical participant removal: candidate transfers hosting responsibility: {messages}"
        )

    before_guest_signature = _guest_reservation_signature(before_plan)
    after_guest_signature = _guest_reservation_signature(plan)
    guest_integrity_ok = before_guest_signature == after_guest_signature
    reconcile_plan_derived_state(plan, result, problem=verification_problem)
    existing_protection_violations = protection_violations(plan, decisions)
    constraint_violations = request_constraint_violations(plan, decisions)
    candidate_revision = schedule_fingerprint(plan)
    cost = change_cost(baseline, plan)

    from tournament_scheduler.team_schedule_quality import (
        RegressionAcceptanceError,
        evaluate_regression_acceptances,
        parse_regression_acceptances,
        regression_acceptance_refusals,
    )

    try:
        regression_acceptances = parse_regression_acceptances(accept_regressions, accept_regression_reason)
    except RegressionAcceptanceError as exc:
        raise SeasonStateError(f"Refusing canonical participant removal: {exc}") from exc

    # The withdrawn team's own participation shortfall is the operator's
    # deliberate decision, reported for audit but not a schedule-quality
    # regression. Only the *remaining* teams in the affected scope can block the
    # operation.
    removed_consequences, team_consequences = evaluate_removal_consequences(
        before_plan,
        plan,
        problem=verification_problem,
        removed_identities=[removed_identity],
        tournament_ids=resolved_tournament_ids,
    )
    removed_consequence = next(iter(removed_consequences.values()), {})
    regression_acceptance = evaluate_regression_acceptances(team_consequences, regression_acceptances)
    consequence_acceptable = bool(regression_acceptance["acceptable"])

    participation_counts = _affected_participation_counts(
        before_plan,
        plan,
        (removed_identity,),
        verification_problem,
    )
    new_protections = build_net_roster_protections(
        before_plan=before_plan,
        after_plan=plan,
        tournament_ids=resolved_tournament_ids,
        request_id=resolved_request_id,
        actor=request_actor,
        note=note,
        created_at=created_at,
        source_revision=before_canonical_revision,
        source_event="participant_removal",
    )
    details = {
        "tournament_ids": list(resolved_tournament_ids),
        "age_group": age_group,
        "removed_team": {"club": removed_identity[0], "label": removed_identity[1]},
        "reconcile_withdrawal": bool(reconcile_withdrawal),
        "regenerated_games": {
            removal["tournament"].get("id"): len(removal["tournament"].get("games") or []) for removal in removals
        },
        "candidate_revision": candidate_revision,
        "verification_summary": verification_summary(result, tournament_id=resolved_tournament_ids[0]),
        "guest_reservation_integrity": {
            "ok": guest_integrity_ok,
            "before": before_guest_signature,
            "after": after_guest_signature,
        },
        "hosting_responsibility_transfers": transfers,
        "hosting_responsibility_ok": not transfers,
        "removed_team_consequence": removed_consequence,
        "team_consequences": team_consequences,
        "participation_counts": participation_counts,
        "consequence_acceptable": consequence_acceptable,
        "regression_acceptance": regression_acceptance,
        "existing_change_protection_violations": existing_protection_violations,
        "change_protection_acceptable": not existing_protection_violations,
        "request_constraint_violations": constraint_violations,
        "request_constraint_acceptable": not constraint_violations,
        "protections_to_add": new_protections,
        "withdrawals_to_add": withdrawal_records,
        "request_id": resolved_request_id,
        "can_apply_unchanged": bool(
            result.get("ok", True)
            and guest_integrity_ok
            and not transfers
            and not existing_protection_violations
            and not constraint_violations
            and consequence_acceptable
        ),
    }

    if dry_run:
        preview_details = dict(details)
        preview_details["verification_result"] = result
        return {
            "season": season,
            "dry_run": True,
            "current_revision": schedule.get("revision"),
            "current_fingerprint": schedule.get("fingerprint"),
            "candidate_revision": candidate_revision,
            "candidate_fingerprint": candidate_revision,
            "verification_result": result,
            "change_cost": cost,
            "removal": preview_details,
        }

    if not guest_integrity_ok:
        raise SeasonStateError("Refusing canonical participant removal: it would change reserved guest slots")
    if existing_protection_violations:
        messages = "; ".join(str(item.get("message")) for item in existing_protection_violations)
        raise SeasonStateError("Refusing canonical participant removal: it would undo an accepted change: " + messages)
    if constraint_violations:
        messages = "; ".join(str(item.get("message")) for item in constraint_violations)
        raise SeasonStateError(
            "Refusing canonical participant removal: it violates an active request constraint: " + messages
        )
    if not consequence_acceptable:
        reasons = regression_acceptance_refusals(regression_acceptance)
        raise SeasonStateError("Refusing canonical participant removal: " + "; ".join(reasons))

    from .scoped_mutation import authorize_participant_removal

    scoped_authorization = authorize_participant_removal(
        schedule=schedule,
        decisions=decisions,
        candidate=plan,
        tournament_ids=resolved_tournament_ids,
        remove_team_label=remove_team_label,
        team=removed_team,
        reconcile_withdrawal=bool(reconcile_withdrawal),
        request_id=resolved_request_id,
        actor=request_actor,
        note=note,
        created_at=created_at,
    )
    updated_schedule, updated_decisions, applied_cost = service.apply_candidate(
        season=season,
        candidate=plan,
        problem=verification_problem,
        actor=actor,
        operation="targeted_mutation",
        _scoped_authorization=scoped_authorization,
        _new_change_protections=new_protections,
        _new_participation_withdrawals=withdrawal_records,
        _history_event={
            "event": "participant_removal",
            "tournament_id": resolved_tournament_ids[0],
            "previous_fingerprint": removals[0]["before_fingerprint"],
            "note": note,
            "details": details,
        },
    )
    return {
        "season": season,
        "dry_run": False,
        "revision": updated_schedule.get("revision"),
        "canonical_state_revision": canonical_state_revision(updated_schedule, updated_decisions),
        "verification_result": result,
        "change_cost": applied_cost,
        "removal": details,
    }
