"""Single-tournament participant replacement mutations."""

from __future__ import annotations

import copy
from datetime import date as _date
from typing import Any, Mapping

from tournament_scheduler.canonical_baseline import approval_fingerprint, resolve_approval
from tournament_scheduler.canonical_history_summary import verification_summary
from tournament_scheduler.canonical_state import (
    canonical_state_revision,
    schedule_fingerprint,
)
from tournament_scheduler.change_protections import (
    build_participant_replacement_protections,
    protection_violations,
)
from tournament_scheduler.request_constraints import (
    compare_request_constraint_violations,
)
from tournament_scheduler.infrastructure.canonical_season_store import (
    SeasonStateError,
)
from tournament_scheduler.plan_derived_state import reconcile_plan_derived_state
from tournament_scheduler.final_verification import verify_canonical_candidate

from .shared import (
    _operator_identity,
    _now_iso,
    _resolve_plan_problem,
    _regenerate_tournament_games,
    _guest_reservation_signature,
)

def _apply_replace_participant_to_plan(
    plan: dict[str, Any],
    *,
    tournament_id: str,
    remove_team_label: str,
    add_team_label: str,
    problem: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Replace one RVV participant in one tournament and regenerate games."""

    tournaments = plan.get("tournaments", []) or []
    target = next((t for t in tournaments if str(t.get("id") or "") == tournament_id), None)
    if target is None:
        raise SeasonStateError(f"Unknown tournament id in canonical schedule: {tournament_id}")
    if target.get("cancelled"):
        raise SeasonStateError(
            f"Tournament {tournament_id} is cancelled and cannot participate in a roster replacement"
        )
    age_group = str(target.get("age_group") or "")

    matches = [
        (index, team)
        for index, team in enumerate(target.get("teams", []) or [])
        if str(team.get("label") or "") == remove_team_label
    ]
    if not matches:
        raise SeasonStateError(
            f"Team {remove_team_label!r} is not a participant in tournament {tournament_id}"
        )
    if len(matches) > 1:
        raise SeasonStateError(
            f"Team label {remove_team_label!r} is ambiguous in tournament {tournament_id}"
        )
    index, removed_team = matches[0]
    if bool(removed_team.get("guest", False)):
        raise SeasonStateError(
            f"Team {remove_team_label!r} in tournament {tournament_id} is a guest participant; "
            "use the guest-slot lifecycle instead"
        )

    def team_identity(team: Mapping[str, Any], fallback_age_group: str) -> tuple[str, str, str]:
        return (
            str(team.get("club") or ""),
            str(team.get("label") or ""),
            str(team.get("age_group") or fallback_age_group),
        )

    removed_identity = team_identity(removed_team, age_group)
    if removed_identity[2] != age_group:
        raise SeasonStateError(
            f"Cannot replace {remove_team_label!r}: participant age group "
            f"{removed_identity[2] or '<missing>'} does not match tournament {age_group}"
        )

    registered_matches = [
        team
        for team in (problem or {}).get("teams", []) or []
        if isinstance(team, Mapping)
        and str(team.get("label") or "") == add_team_label
        and str(team.get("age_group") or "") == age_group
    ]
    if not registered_matches:
        raise SeasonStateError(
            f"Incoming team {add_team_label!r} is not a registered {age_group} participant"
        )
    if len(registered_matches) > 1:
        raise SeasonStateError(
            f"Incoming team label {add_team_label!r} is ambiguous among registered {age_group} teams"
        )
    added_team = copy.deepcopy(registered_matches[0])
    if bool(added_team.get("guest", False)):
        raise SeasonStateError(
            f"Incoming team {add_team_label!r} is a guest participant; use the guest-slot lifecycle instead"
        )
    added_identity = team_identity(added_team, age_group)
    if added_identity == removed_identity:
        raise SeasonStateError("Participant replacement would be a no-op")

    for existing_index, existing in enumerate(target.get("teams", []) or []):
        if existing_index == index:
            continue
        if team_identity(existing, age_group) == added_identity:
            raise SeasonStateError(
                f"Cannot add {add_team_label!r} to {tournament_id}: that team already participates there"
            )

    before_fingerprint = approval_fingerprint(target)
    target["teams"][index] = added_team
    _regenerate_tournament_games(target, problem)
    return {
        "tournament": target,
        "removed_team": copy.deepcopy(removed_team),
        "added_team": copy.deepcopy(added_team),
        "removed_identity": removed_identity,
        "added_identity": added_identity,
        "age_group": age_group,
        "before_fingerprint": before_fingerprint,
    }


def _affected_participation_counts(
    before_plan: Mapping[str, Any],
    after_plan: Mapping[str, Any],
    identities: tuple[tuple[str, str, str], ...],
    problem: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Return full-season and half-season counts for affected identities."""

    from tournament_scheduler import planning_half

    def parse_date(value: Any) -> _date | None:
        if not value:
            return None
        try:
            return _date.fromisoformat(str(value))
        except ValueError:
            return None

    start = parse_date(before_plan.get("start_date") or (problem or {}).get("start_date"))
    end = parse_date(before_plan.get("end_date") or (problem or {}).get("end_date"))
    split = None
    if (problem or {}).get("christmas_split_date"):
        split = parse_date((problem or {}).get("christmas_split_date"))
    if split is None and start is not None and end is not None:
        split = planning_half.christmas_split_date(start, end)

    def empty_counts() -> dict[str, int]:
        return {"season": 0, "before_christmas": 0, "after_christmas": 0, "unsplit": 0}

    def counts_for(plan: Mapping[str, Any]) -> dict[tuple[str, str, str], dict[str, int]]:
        counts = {identity: empty_counts() for identity in identities}
        for tournament in plan.get("tournaments", []) or []:
            if not isinstance(tournament, Mapping) or tournament.get("cancelled"):
                continue
            tournament_date = parse_date(tournament.get("date"))
            half = "unsplit"
            if tournament_date is not None and split is not None:
                value = planning_half.tournament_half(tournament_date, split)
                if value in ("before_christmas", "after_christmas"):
                    half = value
            fallback_age_group = str(tournament.get("age_group") or "")
            for team in tournament.get("teams", []) or []:
                if bool((team or {}).get("guest", False)):
                    continue
                identity = (
                    str((team or {}).get("club") or ""),
                    str((team or {}).get("label") or ""),
                    str((team or {}).get("age_group") or fallback_age_group),
                )
                if identity not in counts:
                    continue
                counts[identity]["season"] += 1
                counts[identity][half] += 1
        return counts

    before_counts = counts_for(before_plan)
    after_counts = counts_for(after_plan)
    return {
        ":".join(identity): {
            "identity": {
                "club": identity[0],
                "label": identity[1],
                "age_group": identity[2],
            },
            "before": before_counts.get(identity, empty_counts()),
            "after": after_counts.get(identity, empty_counts()),
        }
        for identity in identities
    }


def blocked_replacement_preview(
    *,
    season: str,
    schedule: Mapping[str, Any],
    decisions: Mapping[str, Any],
    tournament_id: str,
    remove_team_label: str,
    add_team_label: str,
    reason: str,
    request_id: str | None = None,
) -> dict[str, Any]:
    """Return the stable dry-run shape for a replacement that cannot be built.

    Dry-run is an operational validation interface, not a mutation probe. Domain
    infeasibility therefore reports a blocked verdict in the normal preview
    contract instead of forcing callers to catch exceptions and parse messages.
    Non-dry-run apply still raises and writes nothing.
    """

    before_revision = str(schedule.get("revision") or schedule.get("fingerprint") or "")
    details = {
        "tournament_id": tournament_id,
        "removed_team": {"label": remove_team_label},
        "added_team": {"label": add_team_label},
        "before_fingerprint": before_revision,
        "candidate_revision": None,
        "request_id": str(request_id or ""),
        "can_apply_unchanged": False,
        "refusal_reasons": [reason],
    }
    return {
        "season": season,
        "dry_run": True,
        "current_revision": schedule.get("revision"),
        "current_fingerprint": schedule.get("fingerprint"),
        "current_canonical_revision": canonical_state_revision(schedule, decisions),
        "candidate_revision": None,
        "candidate_fingerprint": None,
        "verification_result": {"ok": False, "violations": []},
        "change_cost": None,
        "verdict": {
            "status": "blocked",
            "applicable": False,
            "checks": {"candidate_constructed": False},
            "blockers": [reason],
        },
        "replacement": details,
    }


def replacement_verdict(
    details: Mapping[str, Any],
    *,
    verification_ok: bool,
) -> dict[str, Any]:
    """Return the stable domain verdict for a participant-replacement preview.

    Callers (CLI, harness, candidate discovery) consume this instead of
    re-deriving applicability from individual gate fields.
    """

    guest_integrity = details.get("guest_reservation_integrity") or {}
    checks = {
        "schedule_acceptable": bool(verification_ok),
        "consequences_acceptable": bool(details.get("consequence_acceptable")),
        "change_protection_acceptable": bool(details.get("change_protection_acceptable")),
        "request_constraints_acceptable": bool(details.get("request_constraint_acceptable")),
        "guest_reservation_integrity": bool(guest_integrity.get("ok")),
        "hosting_responsibility_acceptable": bool(details.get("hosting_responsibility_ok")),
    }
    blockers = [name for name, ok in checks.items() if not ok]
    return {
        "status": "safe_to_apply" if not blockers else "blocked",
        "applicable": not blockers,
        "checks": checks,
        "blockers": blockers,
    }


def replace_participant(
    service,
    *,
    season: str,
    tournament_id: str,
    remove_team_label: str,
    add_team_label: str,
    problem: dict[str, Any] | None = None,
    actor: str | None = None,
    note: str = "",
    dry_run: bool = False,
    request_id: str | None = None,
) -> dict[str, Any]:
    """Replace one RVV participant in one canonical tournament."""

    snapshot = service.load(season)
    schedule, decisions = snapshot.schedule, snapshot.decisions
    resolved_problem = _resolve_plan_problem(schedule, problem, decisions)
    if resolved_problem is None:
        raise SeasonStateError(
            "Participant replacement requires a promoted verification-context problem "
            "so the incoming team can be validated against registered teams"
        )
    before_plan = schedule.get("plan") or {}
    plan = copy.deepcopy(before_plan)
    before_canonical_revision = canonical_state_revision(schedule, decisions)

    by_id = {
        str(tournament.get("id") or ""): tournament
        for tournament in plan.get("tournaments", []) or []
        if tournament.get("id")
    }
    tournament = by_id.get(tournament_id)
    if tournament is None:
        raise SeasonStateError(f"Unknown tournament id in canonical schedule: {tournament_id}")
    if tournament.get("cancelled"):
        raise SeasonStateError(
            f"Tournament {tournament_id} is cancelled and cannot participate in a roster replacement"
        )
    resolved = resolve_approval(
        decisions.get("decisions", {}).get(tournament_id, {}),
        tournament,
    )
    if resolved["participants_locked"]:
        message = f"Tournament {tournament_id} has an active participant lock; unapprove it explicitly first"
        if dry_run:
            return blocked_replacement_preview(
                season=season,
                schedule=schedule,
                decisions=decisions,
                tournament_id=tournament_id,
                remove_team_label=remove_team_label,
                add_team_label=add_team_label,
                reason=message,
                request_id=request_id,
            )
        raise SeasonStateError(message)

    try:
        replacement = _apply_replace_participant_to_plan(
            plan,
            tournament_id=tournament_id,
            remove_team_label=remove_team_label,
            add_team_label=add_team_label,
            problem=resolved_problem,
        )
    except SeasonStateError as exc:
        if dry_run:
            return blocked_replacement_preview(
                season=season,
                schedule=schedule,
                decisions=decisions,
                tournament_id=tournament_id,
                remove_team_label=remove_team_label,
                add_team_label=add_team_label,
                reason=str(exc),
                request_id=request_id,
            )
        raise
    candidate_tournament = replacement["tournament"]
    removed_team = replacement["removed_team"]
    added_team = replacement["added_team"]
    removed_identity = replacement["removed_identity"]
    added_identity = replacement["added_identity"]

    from tournament_scheduler.canonical_baseline import (
        build_canonical_baseline,
        change_cost,
        verify_canonical_locks,
    )

    baseline = build_canonical_baseline(schedule, decisions)
    lock_violations = verify_canonical_locks(baseline, plan)
    if lock_violations:
        messages = "; ".join(str(v.get("message")) for v in lock_violations)
        message = f"candidate violates canonical locks: {messages}"
        if dry_run:
            return blocked_replacement_preview(
                season=season,
                schedule=schedule,
                decisions=decisions,
                tournament_id=tournament_id,
                remove_team_label=remove_team_label,
                add_team_label=add_team_label,
                reason=message,
                request_id=request_id,
            )
        raise SeasonStateError(f"Refusing canonical participant replacement: {message}")

    result = verify_canonical_candidate(plan, resolved_problem)
    if not result.get("ok", True):
        messages = "; ".join(
            str(v.get("message") or v.get("code"))
            for v in result.get("violations", [])
        )
        message = f"candidate fails hard verification: {messages}"
        if dry_run:
            return blocked_replacement_preview(
                season=season,
                schedule=schedule,
                decisions=decisions,
                tournament_id=tournament_id,
                remove_team_label=remove_team_label,
                add_team_label=add_team_label,
                reason=message,
                request_id=request_id,
            )
        raise SeasonStateError(f"Refusing canonical participant replacement: {message}")

    from tournament_scheduler.hosting_responsibility import (
        unexplained_responsibility_transfers,
    )

    transfers = unexplained_responsibility_transfers(
        before_plan,
        plan,
        resolved_problem,
    )
    if transfers:
        messages = "; ".join(str(entry.get("message")) for entry in transfers)
        message = f"candidate transfers hosting responsibility: {messages}"
        if dry_run:
            return blocked_replacement_preview(
                season=season,
                schedule=schedule,
                decisions=decisions,
                tournament_id=tournament_id,
                remove_team_label=remove_team_label,
                add_team_label=add_team_label,
                reason=message,
                request_id=request_id,
            )
        raise SeasonStateError(f"Refusing canonical participant replacement: {message}")

    before_guest_signature = _guest_reservation_signature(before_plan)
    after_guest_signature = _guest_reservation_signature(plan)
    guest_integrity_ok = before_guest_signature == after_guest_signature
    reconcile_plan_derived_state(plan, result, problem=resolved_problem)
    existing_protection_violations = protection_violations(plan, decisions)
    constraint_comparison = compare_request_constraint_violations(
        before_plan, plan, decisions
    )
    candidate_revision = schedule_fingerprint(plan)
    cost = change_cost(baseline, plan)

    from tournament_scheduler.team_schedule_quality import (
        compare_team_schedule_consequence,
    )

    team_consequences = {
        "removed_team": compare_team_schedule_consequence(
            before_plan,
            plan,
            removed_identity,
            problem=resolved_problem,
        ),
        "added_team": compare_team_schedule_consequence(
            before_plan,
            plan,
            added_identity,
            problem=resolved_problem,
        ),
    }

    def replacement_regressions(team_key: str, analysis: Mapping[str, Any]) -> list[dict[str, Any]]:
        regressions: list[dict[str, Any]] = []
        for regression in analysis.get("material_regressions", []) or []:
            code = str((regression or {}).get("code") or "")
            if code == "participation_count_changed":
                before_count = int((regression or {}).get("before") or 0)
                after_count = int((regression or {}).get("after") or 0)
                if team_key == "removed_team" and after_count < before_count:
                    continue
                if team_key == "added_team" and after_count > before_count:
                    continue
            if code == "travel_materially_worse" and team_key == "added_team":
                # A one-tournament substitution intentionally gives the incoming
                # team another tournament.  Report the travel increase, but do
                # not treat the added trip itself as a regression.
                continue
            regressions.append(dict(regression))
        return regressions

    for key, analysis in team_consequences.items():
        filtered = replacement_regressions(key, analysis)
        analysis["replacement_material_regressions"] = filtered
        analysis["replacement_acceptable"] = not filtered
    consequence_acceptable = all(
        analysis.get("replacement_acceptable", False)
        for analysis in team_consequences.values()
    )
    participation_counts = _affected_participation_counts(
        before_plan,
        plan,
        (removed_identity, added_identity),
        resolved_problem,
    )
    protection_request_id = str(request_id or "")
    protection_created_at = _now_iso()
    new_protections = build_participant_replacement_protections(
        removed_team=removed_team,
        added_team=added_team,
        tournament_id=tournament_id,
        request_id=protection_request_id,
        actor=_operator_identity(actor),
        note=note,
        created_at=protection_created_at,
        source_revision=before_canonical_revision,
    )
    details = {
        "tournament_id": tournament_id,
        "date": candidate_tournament.get("date"),
        "age_group": replacement["age_group"],
        "removed_team": {"club": removed_identity[0], "label": removed_identity[1]},
        "added_team": {"club": added_identity[0], "label": added_identity[1]},
        "before_fingerprint": replacement["before_fingerprint"],
        "candidate_revision": candidate_revision,
        # Bounded summary instead of the full whole-season verification result;
        # the exact result stays in the dry-run/CLI response below.
        "verification_summary": verification_summary(result, tournament_id=tournament_id),
        "regenerated_games_count": len(candidate_tournament.get("games") or []),
        "guest_reservation_integrity": {
            "ok": guest_integrity_ok,
            "before": before_guest_signature,
            "after": after_guest_signature,
        },
        "hosting_responsibility_transfers": transfers,
        "hosting_responsibility_ok": not transfers,
        "team_consequences": team_consequences,
        "participation_counts": participation_counts,
        "consequence_acceptable": consequence_acceptable,
        "existing_change_protection_violations": existing_protection_violations,
        "change_protection_acceptable": not existing_protection_violations,
        "request_constraint_violations": constraint_comparison["candidate_violations"],
        "request_constraint_regressions": constraint_comparison["regressions"],
        "request_constraint_unchanged_violations": constraint_comparison["unchanged"],
        "request_constraint_acceptable": constraint_comparison["acceptable"],
        "protections_to_add": new_protections,
        "request_id": protection_request_id,
        "can_apply_unchanged": bool(
            result.get("ok", True)
            and guest_integrity_ok
            and not transfers
            and not existing_protection_violations
            and constraint_comparison["acceptable"]
            and consequence_acceptable
        ),
    }

    if dry_run:
        # The dry-run/CLI response carries the exact full verification result
        # for review; the committed history stores only the bounded summary.
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
            "verdict": replacement_verdict(
                preview_details, verification_ok=bool(result.get("ok", True))
            ),
            "replacement": preview_details,
        }

    if not guest_integrity_ok:
        raise SeasonStateError(
            "Refusing canonical participant replacement: it would change reserved guest slots"
        )
    if existing_protection_violations:
        messages = "; ".join(
            str(item.get("message")) for item in existing_protection_violations
        )
        raise SeasonStateError(
            "Refusing canonical participant replacement: it would undo an accepted change: "
            + messages
        )
    if constraint_comparison["regressions"]:
        messages = "; ".join(
            str(item.get("message")) for item in constraint_comparison["regressions"]
        )
        raise SeasonStateError(
            "Refusing canonical participant replacement: it violates an active request constraint: "
            + messages
        )
    if not consequence_acceptable:
        regressions = []
        for team_name, analysis in team_consequences.items():
            for regression in analysis.get("material_regressions", []):
                regressions.append(f"{team_name}:{regression.get('code')}")
        raise SeasonStateError(
            "Refusing canonical participant replacement: it materially worsens an affected "
            "team's schedule: " + ", ".join(regressions)
        )

    from .scoped_mutation import authorize_participant_replacement

    scoped_authorization = authorize_participant_replacement(
        schedule=schedule,
        decisions=decisions,
        candidate=plan,
        tournament_id=tournament_id,
        remove_team_label=remove_team_label,
        add_team_label=add_team_label,
    )
    updated_schedule, updated_decisions, applied_cost = service.apply_candidate(
        season=season,
        candidate=plan,
        problem=resolved_problem,
        actor=actor,
        operation="targeted_mutation",
        _scoped_authorization=scoped_authorization,
        _new_change_protections=new_protections,
        _history_event={
            "event": "participant_replacement",
            "tournament_id": tournament_id,
            "previous_fingerprint": replacement["before_fingerprint"],
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
        "verdict": replacement_verdict(
            details, verification_ok=bool(result.get("ok", True))
        ),
        "replacement": details,
    }
