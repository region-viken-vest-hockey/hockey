"""Deterministic repair options for ``host_team_missing`` findings.

This is the first small, planner-neutral repair-option capability: Python
enumerates concrete local repairs and applies only a selected option id against
an unchanged candidate fingerprint, then reruns the independent verifier.

Every host-club registered team is either turned into a hard-feasible option or
recorded in ``rejected_candidates`` with an explicit reason. Pre-checks reuse
the vocabulary the targeted roster repair already uses
(``already_participating``/``already_plays_same_date``/``at_participation_max``),
and anything they cannot decide locally falls back to the canonical verifier's
own violation code (e.g. ``club_hard_max_exceeded``, ``bye_team_not_allowed``,
``arena_interval_conflict``) -- so no legal option is hidden by an ad-hoc
ranking policy, and no illegal option is exposed.

An option is only exposed when applying it leaves a candidate that passes the
full independent verifier. When a candidate carries several independent
``host_team_missing`` findings, no single local option can satisfy that gate,
so the context reports the rejection evidence and hands control back to the
broader search/escalation loop instead of committing partial progress.
"""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

from . import planning_half
from .application.decisions import DecisionContext
from .effective_tournament_shape import compute_effective_tournament_shape
from .game_generation import generate_tournament_games
from .host_representation import clubs_represent_same_club, constituent_clubs, host_eligible_teams
from .models import Team
from .operator_waivers import find_participation_waiver
from .planning_contract import _parse_date, external_calendar_conflict, verify_candidate

TeamIdentity = Tuple[str, str, str]


@dataclass(frozen=True)
class RepairOption:
    option_id: str
    finding_id: str
    action: str
    tournament_id: str
    arguments: Mapping[str, Any]
    hard_feasible: bool
    effects: Mapping[str, Any] = field(default_factory=dict)
    evidence: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "option_id": self.option_id,
            "finding_id": self.finding_id,
            "action": self.action,
            "tournament_id": self.tournament_id,
            "arguments": dict(self.arguments),
            "hard_feasible": self.hard_feasible,
            "effects": dict(self.effects),
            "evidence": dict(self.evidence),
        }


def candidate_fingerprint(candidate: Mapping[str, Any]) -> str:
    payload = json.dumps(candidate, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def enumerate_host_team_missing_repairs(
    candidate: Mapping[str, Any],
    problem: Mapping[str, Any],
    *,
    run_id: str = "",
) -> Dict[str, Any]:
    """Return legal repair options and rejected evidence for host-team findings."""
    verification = verify_candidate(dict(candidate), dict(problem))
    findings = [v for v in verification.get("violations", []) if v.get("code") == "host_team_missing"]
    fingerprint = candidate_fingerprint(candidate)
    options: List[RepairOption] = []
    rejected: List[Dict[str, Any]] = []
    for index, finding in enumerate(findings, start=1):
        tournament_id = str(finding.get("tournament_id") or "")
        finding_id = f"host_team_missing:{tournament_id or index}"
        tournament = _find_tournament(candidate, tournament_id)
        if not tournament:
            rejected.append({"finding_id": finding_id, "reason": "tournament_not_found"})
            continue
        p_options, p_rejected = _participant_options(candidate, problem, tournament, finding_id, fingerprint)
        r_options, r_rejected = _rehost_options(candidate, problem, tournament, finding_id, fingerprint)
        options.extend(p_options)
        options.extend(r_options)
        rejected.extend(p_rejected)
        rejected.extend(r_rejected)
        # Deletion is deliberately the next local family after participant
        # repair and represented rehost have both failed for this finding. It
        # is not offered as a substitute for a legal lower-impact local repair.
        if not p_options and not r_options:
            remove_option, remove_rejected = _remove_tournament_option(
                candidate, problem, tournament, finding_id, fingerprint, verification
            )
            if remove_option is not None:
                options.append(remove_option)
            rejected.extend(remove_rejected)
    return {
        "run_id": run_id,
        "candidate_fingerprint": fingerprint,
        "verification": verification,
        "options": [o.to_dict() for o in options],
        "rejected_candidates": rejected,
    }


def build_host_team_missing_decision_context(
    candidate: Mapping[str, Any],
    problem: Mapping[str, Any],
    *,
    run_id: str,
    candidate_ref: Optional[str] = None,
) -> DecisionContext:
    repair_set = enumerate_host_team_missing_repairs(candidate, problem, run_id=run_id)
    legal_ids = [o["option_id"] for o in repair_set["options"]]
    verification = repair_set["verification"]
    return DecisionContext(
        run_id=run_id,
        capability="host_team_missing_repair",
        stage="stage3",
        objective="Choose one repository-generated local repair option for host_team_missing, or escalate if none is legal.",
        facts={
            "candidate_fingerprint": repair_set["candidate_fingerprint"],
            "repair_options": repair_set["options"],
            "rejected_candidates": repair_set["rejected_candidates"],
        },
        baseline_hard_violations=tuple(
            f"{v.get('code')}: {v.get('message')}" for v in verification.get("violations", [])
        ),
        warnings=() if legal_ids else ("No legal local host_team_missing repair option was found; rejected_candidates explains why.",),
        candidate_ref=candidate_ref,
        available_actions=("apply_repair_option", "optimize_plan", "request_operator") if legal_ids else ("optimize_plan", "request_operator"),
        action_parameters={
            "apply_repair_option": {
                "option_id": {"type": "string", "enum": legal_ids, "description": "Repository-generated repair option id to apply."},
                "candidate_fingerprint": {"type": "string", "enum": [repair_set["candidate_fingerprint"]]},
            }
        } if legal_ids else {},
    )


def apply_host_team_missing_repair_option(
    candidate: Mapping[str, Any],
    problem: Mapping[str, Any],
    *,
    option_id: str,
    expected_fingerprint: str,
    run_id: str = "",
) -> Dict[str, Any]:
    """Atomically apply a selected option id and verify the resulting candidate."""
    before = candidate_fingerprint(candidate)
    if before != expected_fingerprint:
        return {"ok": False, "reason": "stale_candidate_fingerprint", "before_fingerprint": before, "expected_fingerprint": expected_fingerprint}
    repair_set = enumerate_host_team_missing_repairs(candidate, problem, run_id=run_id)
    option = next((o for o in repair_set["options"] if o["option_id"] == option_id), None)
    if option is None:
        return {"ok": False, "reason": "unknown_or_stale_option", "before_fingerprint": before}

    mutated = copy.deepcopy(candidate)
    if option["action"] in {"append_participant", "replace_participant"}:
        _apply_participant_option(mutated, problem, option)
    elif option["action"] == "rehost":
        _apply_rehost_option(mutated, option)
    elif option["action"] == "remove_tournament":
        _apply_remove_tournament_option(mutated, option)
    else:
        return {"ok": False, "reason": "unsupported_action", "before_fingerprint": before}

    verification = verify_candidate(mutated, dict(problem))
    if not verification.get("ok"):
        return {"ok": False, "reason": "verification_failed", "before_fingerprint": before, "verification": verification}
    after = candidate_fingerprint(mutated)
    return {
        "ok": True,
        "candidate": mutated,
        "option_id": option_id,
        "before_fingerprint": before,
        "after_fingerprint": after,
        "verification": verification,
        "effects": option.get("effects", {}),
    }


def _participant_options(candidate, problem, tournament, finding_id: str, fingerprint: str):
    """Legal participant repairs for one finding, plus explicit rejections."""
    out: List[RepairOption] = []
    rejected: List[Dict[str, Any]] = []
    age_group = tournament.get("age_group")
    host = tournament.get("host_club")
    teams = list(tournament.get("teams") or [])
    current = {_identity(t) for t in teams}
    t_date = _parse_date(tournament.get("date"))
    required = _effective_size(problem, age_group)
    existing_counts = _participation_counts(candidate)
    half_counts = _participations_by_half(candidate, problem)
    same_date = _same_date_identities(candidate, t_date, except_tournament_id=tournament.get("id"))
    registered = list(problem.get("teams", []))
    # Every registered team whose club label shares a constituent with the host
    # is considered, not just the exact-age-group subset: a host-club sibling
    # in the wrong age group is still useful rejection evidence.
    host_registered = [
        team for team in registered if clubs_represent_same_club(team.get("club", ""), host or "")
    ]
    candidates = host_eligible_teams(registered, host, age_group)
    ineligible = [team for team in host_registered if team.get("age_group") != age_group]

    if str(tournament.get("id")) in {str(item) for item in (problem.get("manual_adjustments") or {}).get("pinned_tournament_ids", [])}:
        return [], [
            {**_participant_base(finding_id, tournament, add), "reason": "manual_restriction_forbids_mutation"}
            for add in host_registered
        ]

    for add in ineligible:
        rejected.append(
            {
                **_participant_base(finding_id, tournament, add),
                "reason": "age_group_mismatch",
                "registered_age_group": add.get("age_group"),
            }
        )

    # A tournament already at its configured/effective size can only be
    # repaired by a replacement. Without an explicit target both growth and
    # replacement are attempted, so an append that would break the legal
    # roster shape still leaves the replacement family available.
    if required is not None and len(teams) >= required:
        remove_pool: List[Any] = list(teams)
    elif required is not None:
        remove_pool = [None]
    else:
        remove_pool = [None, *teams]

    for add in candidates:
        add_id = _identity(add)
        base = _participant_base(finding_id, tournament, add)
        if add_id in current:
            rejected.append({**base, "reason": "already_participating"})
            continue
        if add_id in same_date:
            rejected.append({**base, "reason": "already_plays_same_date"})
            continue
        limit = _participation_limit_reason(
            add_id, existing_counts, half_counts, problem, t_date, tournament.get("id")
        )
        if limit is not None:
            rejected.append({**base, "reason": limit})
            continue
        for remove in remove_pool:
            entry = dict(base)
            if remove is not None:
                entry["remove_team"] = _team_ref(remove)
            trial = copy.deepcopy(candidate)
            action = "replace_participant" if remove else "append_participant"
            _mutate_participants(trial, tournament.get("id"), remove, add, problem)
            result = verify_candidate(trial, dict(problem))
            if not result.get("ok"):
                rejected.append(
                    {**entry, "reason": _primary_violation_reason(result), "violations": _codes(result)}
                )
                continue
            suffix = f"{_slug(add_id)}" + (f":for:{_slug(_identity(remove))}" if remove else "")
            deficit = _participation_deficit(add_id, existing_counts.get(add_id, 0), problem, t_date)
            out.append(RepairOption(
                option_id=f"{fingerprint[:12]}:{finding_id}:{action}:{suffix}",
                finding_id=finding_id,
                action=action,
                tournament_id=str(tournament.get("id")),
                arguments={"add_team": _team_ref(add), **({"remove_team": _team_ref(remove)} if remove else {})},
                hard_feasible=True,
                effects={
                    "host_team_missing": -1,
                    "participation_deficit_delta": -1 if deficit > 0 else 0,
                    "participation_deficit": deficit,
                    "roster_size_delta": 0 if remove else 1,
                },
                evidence={"verification_ok": True},
            ))
    return out, rejected


def _participant_base(finding_id: str, tournament, team) -> Dict[str, Any]:
    return {"finding_id": finding_id, "tournament_id": tournament.get("id"), "team": _team_ref(team)}


def _remove_tournament_option(candidate, problem, tournament, finding_id: str, fingerprint: str, before_verification):
    """Verified local delete option for a surplus host-missing tournament.

    The option is exposed only when deleting the tournament leaves the full
    candidate hard-valid and does not create a new unresolved hosting
    obligation. Participation shortfalls remain soft/manual evidence and are
    reported as effects for the harness/operator to weigh.
    """
    pinned_ids = {str(item) for item in (problem.get("manual_adjustments") or {}).get("pinned_tournament_ids", [])}
    base = {
        "finding_id": finding_id,
        "tournament_id": tournament.get("id"),
        "host_club": tournament.get("host_club"),
        "age_group": tournament.get("age_group"),
    }
    if str(tournament.get("id")) in pinned_ids:
        return None, [{**base, "action": "remove_tournament", "reason": "manual_restriction_forbids_mutation"}]

    trial = copy.deepcopy(candidate)
    _apply_remove_tournament_option(trial, {"tournament_id": str(tournament.get("id"))})
    after_verification = verify_candidate(trial, dict(problem))
    before_unresolved = _unresolved_hosting_keys(before_verification)
    after_unresolved = _unresolved_hosting_keys(after_verification)
    new_unresolved = sorted(after_unresolved - before_unresolved)
    if new_unresolved:
        return None, [
            {
                **base,
                "action": "remove_tournament",
                "reason": "hosting_obligation_would_be_unresolved",
                "unresolved_hosting_obligations": [
                    dict(row) for row in after_verification.get("unresolved_hosting_obligations", [])
                ],
            }
        ]
    if not after_verification.get("ok"):
        return None, [
            {
                **base,
                "action": "remove_tournament",
                "reason": _primary_violation_reason(after_verification),
                "violations": _codes(after_verification),
            }
        ]

    effects = _remove_tournament_effects(candidate, trial, tournament, before_verification, after_verification)
    option = RepairOption(
        option_id=f"{fingerprint[:12]}:{finding_id}:remove_tournament:{_slug(tournament.get('id'))}",
        finding_id=finding_id,
        action="remove_tournament",
        tournament_id=str(tournament.get("id")),
        arguments={"tournament_id": str(tournament.get("id"))},
        hard_feasible=True,
        effects=effects,
        evidence={
            "verification_ok": True,
            "verification_preview": "pass",
            "removed_host_club": tournament.get("host_club"),
            "removed_age_group": tournament.get("age_group"),
            "removed_team_count": len(tournament.get("teams", []) or []),
        },
    )
    return option, []


def _rehost_options(candidate, problem, tournament, finding_id: str, fingerprint: str):
    out: List[RepairOption] = []
    rejected: List[Dict[str, Any]] = []
    current_host = tournament.get("host_club")
    t_date = _parse_date(tournament.get("date"))
    represented = sorted({part for team in tournament.get("teams", []) for part in constituent_clubs(team.get("club", ""))})
    # The canonical planning problem exposes club -> arena as ``clubs``
    # (see planning_contract.build_planning_problem and stage3_optimizer);
    # ``club_arenas`` is the same map under the name older/hand-built test
    # problems used. Accept either so a rehost is never rejected as
    # ``arena_not_configured`` just because the problem used the other key.
    club_arenas = dict(problem.get("club_arenas") or problem.get("clubs") or {})
    statuses = dict(problem.get("club_calendar_status") or {})
    busy = problem.get("club_busy_intervals") or {}
    duration = _duration_minutes(tournament, problem)
    if str(tournament.get("id")) in {str(item) for item in (problem.get("manual_adjustments") or {}).get("pinned_tournament_ids", [])}:
        return [], [
            {
                "finding_id": finding_id,
                "tournament_id": tournament.get("id"),
                "host_club": host,
                "reason": "manual_restriction_forbids_mutation",
            }
            for host in represented
        ]
    for host in represented:
        base = {"finding_id": finding_id, "tournament_id": tournament.get("id"), "host_club": host}
        if clubs_represent_same_club(host, current_host or ""):
            rejected.append({**base, "reason": "current_invalid_host"})
            continue
        arena = club_arenas.get(host)
        if not arena:
            rejected.append({**base, "reason": "arena_not_configured"})
            continue
        if statuses and statuses.get(host, "unknown") != "known":
            rejected.append({**base, "reason": "calendar_evidence_not_trusted"})
            continue
        for start in _candidate_start_times(tournament):
            if external_calendar_conflict(busy, host, t_date, start, duration):
                rejected.append({**base, "arena": arena, "start_time": start, "reason": "external_calendar_conflict"})
                continue
            trial = copy.deepcopy(candidate)
            tt = _find_tournament(trial, tournament.get("id"))
            tt.update({"host_club": host, "arena": arena, "start_time": start})
            result = verify_candidate(trial, dict(problem))
            if result.get("ok"):
                out.append(RepairOption(
                    option_id=f"{fingerprint[:12]}:{finding_id}:rehost:{_slug((host, arena, start))}",
                    finding_id=finding_id,
                    action="rehost",
                    tournament_id=str(tournament.get("id")),
                    arguments={"host_club": host, "arena": arena, "start_time": start},
                    hard_feasible=True,
                    effects={"host_team_missing": -1},
                    evidence={"date": tournament.get("date"), "start_time": start, "end_time": _end_time(start, duration), "calendar_status": statuses.get(host, "known")},
                ))
            else:
                rejected.append(
                    {
                        **base,
                        "arena": arena,
                        "start_time": start,
                        "reason": _primary_violation_reason(result),
                        "violations": _codes(result),
                    }
                )
    return out, rejected


def _apply_participant_option(candidate, problem, option):
    args = option["arguments"]
    _mutate_participants(candidate, option["tournament_id"], args.get("remove_team"), args["add_team"], problem)


def _apply_rehost_option(candidate, option):
    t = _find_tournament(candidate, option["tournament_id"])
    t.update(option["arguments"])


def _apply_remove_tournament_option(candidate, option):
    tournament_id = str(option["tournament_id"])
    candidate["tournaments"] = [
        t for t in candidate.get("tournaments", []) if str(t.get("id")) != tournament_id
    ]
    _refresh_candidate_derived_state(candidate, removed_tournament_id=tournament_id)


def _mutate_participants(candidate, tournament_id, remove, add, problem):
    t = _find_tournament(candidate, tournament_id)
    teams = [dict(team) for team in t.get("teams", [])]
    if remove:
        rid = _identity(remove)
        teams = [team for team in teams if _identity(team) != rid]
    teams.append(dict(add))
    t["teams"] = teams
    _regenerate_games(t, problem)


def _regenerate_games(tournament, problem):
    teams = [Team(team["club"], team["label"], team["age_group"], team.get("target_tournament_count")) for team in tournament.get("teams", [])]
    pg = int((problem.get("parallel_games") or {}).get(tournament.get("age_group"), 1) or 1)
    rounds = (problem.get("rounds_per_tournament") or {}).get(tournament.get("age_group"))
    tournament["games"] = [{"home": g.home.label, "away": g.away.label, "parallel_slot": g.parallel_slot, "round_number": g.round_number} for g in generate_tournament_games(teams, pg, rounds)]


def _effective_size(problem, age_group):
    if not age_group:
        return None
    registered = sum(1 for t in problem.get("teams", []) if t.get("age_group") == age_group)
    shape = compute_effective_tournament_shape(age_group, registered, configured_rounds=(problem.get("rounds_per_tournament") or {}).get(age_group), parallel_game_capacity=(problem.get("parallel_games") or {}).get(age_group))
    return shape.effective_team_count if shape.has_explicit_target else None


def _duration_minutes(tournament, problem):
    value = tournament.get("duration_minutes")
    if isinstance(value, int) and value > 0:
        return value
    ice = (problem.get("ice_time_minutes") or problem.get("round_length_minutes") or {}).get(tournament.get("age_group"))
    return int(ice or 120)


def _candidate_start_times(tournament):
    seen = []
    for item in (tournament.get("start_time"), "10:00", "10:30", "11:00", "11:30", "12:00", "12:30", "13:00", "13:30", "14:00", "14:30", "15:00"):
        if item and item not in seen:
            seen.append(item)
    return seen


def _find_tournament(candidate, tournament_id):
    return next((t for t in candidate.get("tournaments", []) if str(t.get("id")) == str(tournament_id)), None)


def _identity(team) -> TeamIdentity:
    return (team.get("club", ""), team.get("label", ""), team.get("age_group", ""))


def _team_ref(team) -> Dict[str, Any]:
    return {"club": team.get("club", ""), "label": team.get("label", ""), "age_group": team.get("age_group", "")}


def _participation_counts(candidate) -> Dict[TeamIdentity, int]:
    counts: Dict[TeamIdentity, int] = {}
    for t in candidate.get("tournaments", []):
        if t.get("cancelled"):
            continue
        for team in t.get("teams", []) or []:
            ident = _identity(team)
            counts[ident] = counts.get(ident, 0) + 1
    return counts


def _unresolved_hosting_keys(verification) -> set[Tuple[str, str]]:
    return {
        (str(row.get("club", "")), str(row.get("age_group", "")))
        for row in verification.get("unresolved_hosting_obligations", []) or []
    }


def _remove_tournament_effects(candidate, trial, tournament, before_verification, after_verification) -> Dict[str, Any]:
    effects: Dict[str, Any] = {
        "host_team_missing": _count_code(after_verification, "host_team_missing")
        - _count_code(before_verification, "host_team_missing"),
        "tournament_count": -1,
        "new_hard_violations": len(after_verification.get("violations", []) or []),
        "manual_participation_placements_delta": len(
            after_verification.get("manual_participation_placements", []) or []
        )
        - len(before_verification.get("manual_participation_placements", []) or []),
        "unresolved_hosting_obligations_delta": len(
            after_verification.get("unresolved_hosting_obligations", []) or []
        )
        - len(before_verification.get("unresolved_hosting_obligations", []) or []),
    }
    host = tournament.get("host_club")
    age_group = tournament.get("age_group")
    if host and age_group:
        effects[f"hosting.{host}.{age_group}"] = -1
    before_counts = _participation_counts(candidate)
    after_counts = _participation_counts(trial)
    for team in tournament.get("teams", []) or []:
        ident = _identity(team)
        delta = after_counts.get(ident, 0) - before_counts.get(ident, 0)
        label = team.get("label") or " / ".join(part for part in ident if part)
        effects[f"participation.{label}"] = delta
    return effects


def _count_code(verification, code: str) -> int:
    return sum(1 for violation in verification.get("violations", []) or [] if violation.get("code") == code)


def _refresh_candidate_derived_state(candidate, *, removed_tournament_id: str) -> None:
    tournaments = [t for t in candidate.get("tournaments", []) if not t.get("cancelled")]
    arena_counts: Dict[str, int] = {}
    participations: Dict[str, int] = {}
    game_counts: Dict[str, int] = {}
    for t in tournaments:
        arena = t.get("arena")
        if arena:
            arena_counts[str(arena)] = arena_counts.get(str(arena), 0) + 1
        for team in t.get("teams", []) or []:
            label = str(team.get("label", ""))
            if label:
                participations[label] = participations.get(label, 0) + 1
        for game in t.get("games", []) or []:
            for side in ("home", "away"):
                label = str(game.get(side, ""))
                if label:
                    game_counts[label] = game_counts.get(label, 0) + 1
    if "arena_counts" in candidate:
        candidate["arena_counts"] = arena_counts
    if "team_tournament_participations" in candidate:
        candidate["team_tournament_participations"] = participations
    if "team_game_counts" in candidate:
        candidate["team_game_counts"] = game_counts
    for key in (
        "arena_day_collisions",
        "unresolved_hosting_obligations",
        "unresolved_external_conflicts",
        "unresolved_participation_shortfalls",
        "unresolved_tournament_placements",
        "targeted_roster_repairs",
        "same_age_hosting_repairs",
        "cross_age_hosting_repairs",
        "operator_waived_violations",
    ):
        if isinstance(candidate.get(key), list):
            candidate[key] = [
                entry for entry in candidate[key] if not _entry_mentions_tournament(entry, removed_tournament_id)
            ]


def _entry_mentions_tournament(entry: Any, tournament_id: str) -> bool:
    if isinstance(entry, Mapping):
        for key, value in entry.items():
            if "tournament" in str(key) and str(value) == tournament_id:
                return True
            if _entry_mentions_tournament(value, tournament_id):
                return True
    elif isinstance(entry, list):
        return any(_entry_mentions_tournament(item, tournament_id) for item in entry)
    return False


def _same_date_identities(candidate, t_date, *, except_tournament_id) -> set[TeamIdentity]:
    out: set[TeamIdentity] = set()
    for t in candidate.get("tournaments", []):
        if str(t.get("id")) == str(except_tournament_id) or _parse_date(t.get("date")) != t_date:
            continue
        out.update(_identity(team) for team in t.get("teams", []) or [])
    return out


def _split_date(problem) -> Optional[date]:
    """Before/after-Christmas cut-off, resolved exactly like the verifier does."""
    configured = problem.get("christmas_split_date")
    if configured:
        return _parse_date(configured)
    start = _parse_date(problem.get("start_date"))
    end = _parse_date(problem.get("end_date"))
    if start and end:
        return planning_half.christmas_split_date(start, end)
    return None


def _participations_by_half(candidate, problem) -> Dict[str, Dict[TeamIdentity, int]]:
    split = _split_date(problem)
    counts: Dict[str, Dict[TeamIdentity, int]] = {"before_christmas": {}, "after_christmas": {}}
    for t in candidate.get("tournaments", []):
        t_date = _parse_date(t.get("date"))
        if t_date is None:
            continue
        bucket = counts.get(planning_half.tournament_half(t_date, split))
        if bucket is None:
            continue
        for team in t.get("teams", []) or []:
            ident = _identity(team)
            bucket[ident] = bucket.get(ident, 0) + 1
    return counts


def _participation_limit_reason(
    identity, existing_counts, half_counts, problem, t_date, tournament_id
) -> Optional[str]:
    """Why *identity* may not take one more participation, or ``None``.

    Covers both an explicit season-wide target (``at_participation_max``) and
    the authoritative per-half participation target the verifier enforces when
    no explicit target is configured (``incompatible_half_target``). A valid,
    matching operator waiver for this exact tournament/half/resulting count
    turns the half-target rejection into an allowed addition -- the verifier
    then reports the resulting overage as operator-waived rather than
    blocking, and the repair option is exposed for the operator to apply.
    """
    team = next((t for t in problem.get("teams", []) if _identity(t) == identity), {})
    target = team.get("target_tournament_count", problem.get("target_tournament_count"))
    if isinstance(target, int) and not isinstance(target, bool):
        if existing_counts.get(identity, 0) >= target:
            return "at_participation_max"
    half_targets = (problem.get("participation_targets_by_age_group") or {}).get(identity[2]) or {}
    if not half_targets or t_date is None:
        return None
    half = planning_half.tournament_half(t_date, _split_date(problem))
    half_target = half_targets.get(half)
    if isinstance(half_target, int) and not isinstance(half_target, bool):
        if half_counts.get(half, {}).get(identity, 0) >= half_target:
            resulting = half_counts.get(half, {}).get(identity, 0) + 1
            waiver = find_participation_waiver(
                problem,
                identity=identity,
                half=half,
                actual=resulting,
                configured=half_target,
                tournament_ids=[str(tournament_id or "")],
            )
            if waiver is None:
                return "incompatible_half_target"
    return None


def _primary_violation_reason(result) -> str:
    """Canonical verifier code that blocked a trial, as an explicit reason."""
    codes = _codes(result)
    return codes[0] if codes else "verification_failed"


def _participation_deficit(identity, current_count: int, problem, t_date) -> int:
    team = next((t for t in problem.get("teams", []) if _identity(t) == identity), {})
    target = team.get("target_tournament_count", problem.get("target_tournament_count"))
    return max(0, int(target) - current_count) if isinstance(target, int) else 0


def _codes(result) -> List[str]:
    return [str(v.get("code")) for v in result.get("violations", [])]


def _slug(parts: Iterable[Any]) -> str:
    if isinstance(parts, tuple):
        raw = "-".join(str(p) for p in parts)
    else:
        raw = str(parts)
    return "".join(ch if ch.isalnum() else "_" for ch in raw).strip("_")


def _end_time(start: str, duration_minutes: int) -> str:
    dt = datetime.strptime(start, "%H:%M") + timedelta(minutes=duration_minutes)
    return dt.strftime("%H:%M")
