"""Assembles the read-only evidence inventory a harness-led semantic audit
needs to review the current Stage 4 export (issue #325 Phase 1).

:func:`build_audit_context` never decides PASS/FAIL/REVIEW_REQUIRED itself —
it only gathers facts already produced by the pipeline (Stage 4's own
verification/fingerprint, the run evidence bundle, source/calendar
evidence) so the harness (or the headless judge in
:mod:`tournament_scheduler.llm_judge.audit`) can reason without re-deriving
policy or doing fresh live scraping, per the issue's explicit non-goals.
"""

from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from ..effective_tournament_shape import compute_effective_tournament_shape, shape_violation
from ..host_representation import clubs_represent_same_club
from ..plan_derived_state import (
    publication_readiness_with_plan_placements,
    reconcile_plan_derived_state,
)
from ..planning_contract import HARD_MAX_CLUB_TEAMS_PER_TOURNAMENT
from . import audit_evidence
from .audit_result import current_export_fingerprint, current_run_id
from .fingerprints import stable_payload_sha256
from .state import PipelineState, StageName

AUDIT_PROMPT_VERSION = 2

AUDIT_MISSION: dict[str, Any] = {
    "purpose": (
        "Simulate an independent human reviewer of the exported season plan: find "
        "inconsistencies, operationally suspicious patterns, missing rules, and likely "
        "planner/export bugs that deterministic verification has not encoded yet."
    ),
    "use_evidence_to": [
        "reconstruct important facts from the persisted export/checkpoints",
        "cross-check exported formats against the selected plan",
        "look for contradictions between source evidence, verification, readiness and exports",
        "surface possible new planner rules with concrete examples",
    ],
    "do_not": [
        "treat deterministic verification passing as proof that the plan is correct",
        "turn the audit into a second deterministic rules engine",
        "ignore suspicious outliers just because they are technically allowed today",
        "perform fresh live scraping or rely on unstated assumptions",
    ],
}

# The canonical 9-item operator checklist (issue #325). Item 9 is the
# open-ended, most-important item — the audit's entire reason for existing
# is to surface problems/missing rules no existing check already covers.
# This constant is the single source of truth: SKILL.md's prose copy must
# match it verbatim (see tests/test_skill_ownership.py).
AUDIT_CHECKLIST: tuple[dict[str, Any], ...] = (
    {"item_id": 1, "question": "Antall cuper pr lag?"},
    {"item_id": 2, "question": "Antall hjemmeturneringer pr lag?"},
    {"item_id": 3, "question": "Lengde på turneringer?"},
    {"item_id": 4, "question": "Er det faktisk ledig tid på is?"},
    {"item_id": 5, "question": "Deltar vertsklubben i samme turnering?"},
    {"item_id": 6, "question": "Deltar hvert lag maksimalt én gang per dag?"},
    {"item_id": 7, "question": "Er det normalt maks 2 lag fra samme klubb, med 3 kun som synlig unntak?"},
    {"item_id": 8, "question": "Er eksportformatene konsistente?"},
    {
        "item_id": 9,
        "question": (
            "Ser harnesset andre materielle problemer eller manglende regler vi ikke "
            "allerede har tenkt på?"
        ),
    },
)

_SKILL_MD_PATH = Path(__file__).resolve().parents[2] / ".agents" / "skills" / "rvv" / "SKILL.md"
_AUDIT_POLICY_HEADING = "## Semantic safety-net audit (post-export, pre-publication)"


def _runbook_version() -> str:
    """Hash of the canonical SKILL.md audit-policy section, or of the whole
    file if the section can't be located (keeps this functional even if the
    heading text drifts, at the cost of a coarser version signal)."""
    try:
        markdown = _SKILL_MD_PATH.read_text(encoding="utf-8")
    except OSError:
        return "unavailable"
    lines = markdown.splitlines()
    try:
        start = next(i for i, line in enumerate(lines) if line.strip() == _AUDIT_POLICY_HEADING)
    except StopIteration:
        return stable_payload_sha256(markdown)[:16]
    body: list[str] = []
    for line in lines[start + 1 :]:
        stripped = line.strip()
        if stripped.startswith("## "):
            break
        body.append(line)
    return stable_payload_sha256("\n".join(body))[:16]


def _read_evidence_bundle(export_dir: "str | None") -> dict[str, Any] | None:
    if not export_dir:
        return None
    path = Path(export_dir) / "evidence_bundle.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _bound_final_operator_evidence(
    evidence_bundle: dict[str, Any] | None,
    *,
    run_id: str | None,
    export_fingerprint: str | None,
) -> dict[str, Any] | None:
    """Return canonical final operator evidence only when it is bound to
    this exact run/export fingerprint.

    Older exports do not contain this field; mismatched fields are ignored so
    stale reconciled state cannot silently contaminate a newer audit context.
    """
    if not isinstance(evidence_bundle, dict):
        return None
    evidence = evidence_bundle.get("final_operator_evidence")
    if not isinstance(evidence, dict):
        return None
    if str(evidence.get("run_id") or "") != str(run_id or ""):
        return None
    if str(evidence.get("export_fingerprint") or "") != str(export_fingerprint or ""):
        return None
    return evidence


def _plan_dict_with_final_operator_evidence(
    plan_dict: dict[str, Any] | None,
    final_operator_evidence: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if not isinstance(plan_dict, dict) or not isinstance(final_operator_evidence, dict):
        return plan_dict
    merged = dict(plan_dict)
    for key in (
        "publication_readiness",
        "unresolved_hosting_obligations",
        "unresolved_external_conflicts",
        "unresolved_participation_shortfalls",
        "unresolved_tournament_placements",
        "operator_waivers",
        "operator_waived_violations",
        "approval_status",
    ):
        if key in final_operator_evidence:
            merged[key] = final_operator_evidence.get(key)
    return merged


def _bound_lists(payload: dict[str, Any] | None, *, cap: int | None = None) -> dict[str, Any]:
    """Replace any over-long list value with capped examples plus a ``*_count``.

    Used to keep the *bounded* audit context small; the complete payload stays
    available through ``operator audit-evidence``.
    """
    if not isinstance(payload, dict):
        return payload or {}
    limit = cap if isinstance(cap, int) and cap > 0 else audit_evidence.EVIDENCE_OVERVIEW_MAX_EXAMPLES
    bounded: dict[str, Any] = {}
    for key, value in payload.items():
        if isinstance(value, list) and len(value) > limit:
            bounded[key] = value[:limit]
            bounded[f"{key}_count"] = len(value)
        else:
            bounded[key] = value
    return bounded


def _bound_verify_result(verify_result: dict[str, Any] | None) -> dict[str, Any]:
    """Return a size-bounded view of the deterministic verify result."""
    return _bound_lists(verify_result)


def _summarize_scraping_checkpoint(scraping_checkpoint: dict[str, Any] | None) -> dict[str, Any]:
    """Small source/calendar inventory for the semantic audit.

    The audit must not perform fresh scraping, but it should be able to see
    whether persisted calendar evidence was present and usable.  Keep this to
    counts/status/provenance rather than embedding thousands of raw events.
    """
    if not isinstance(scraping_checkpoint, dict):
        return {}

    sources = scraping_checkpoint.get("sources") or []
    per_source: list[dict[str, Any]] = []
    for source in sources if isinstance(sources, list) else []:
        if not isinstance(source, dict):
            continue
        per_source.append(
            {
                "name": source.get("name"),
                "type": source.get("type"),
                "event_count": source.get("event_count", len(source.get("events") or [])),
                "blocked": bool(source.get("blocked")),
                "block_reason": source.get("block_reason"),
                "from_cache": bool(source.get("from_cache")),
                "cache_age_hours": source.get("cache_age_hours"),
                "llm_fallback": bool(source.get("llm_fallback")),
            }
        )

    events_by_club = scraping_checkpoint.get("events_by_club") or {}
    event_counts_by_club = {
        str(club): len(events or [])
        for club, events in events_by_club.items()
        if isinstance(events_by_club, dict)
    }

    return {
        "sources_scanned": len(sources) if isinstance(sources, list) else 0,
        "blocked_sources": list(scraping_checkpoint.get("blocked") or []),
        "empty_sources": list(scraping_checkpoint.get("empty_sources") or []),
        "cached_sources": list(scraping_checkpoint.get("cached") or []),
        "club_calendar_status": dict(scraping_checkpoint.get("club_calendar_status") or {}),
        "event_counts_by_club": event_counts_by_club,
        "event_expectation_warnings": list(scraping_checkpoint.get("event_expectation_warnings") or []),
        "window": {
            "start_date": scraping_checkpoint.get("start_date"),
            "end_date": scraping_checkpoint.get("end_date"),
        },
        "per_source": per_source,
    }


def _team_key(team: dict[str, Any]) -> str:
    label = str(team.get("label") or "")
    club = str(team.get("club") or "")
    age_group = str(team.get("age_group") or "")
    return f"{label} ({club}, {age_group})" if club or age_group else label


def _tournament_ref(tournament: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": tournament.get("id"),
        "date": tournament.get("date"),
        "age_group": tournament.get("age_group"),
        "arena": tournament.get("arena"),
        "host_club": tournament.get("host_club"),
    }


def _bye_rounds(tournament: dict[str, Any]) -> dict[int, list[str]]:
    """Return pause/bye teams by round implied by the persisted tournament games."""
    team_labels = {
        str(team.get("label"))
        for team in tournament.get("teams") or []
        if isinstance(team, dict) and team.get("label")
    }
    if not team_labels:
        return {}
    played_by_round: dict[int, set[str]] = defaultdict(set)
    for game in tournament.get("games") or []:
        if not isinstance(game, dict):
            continue
        try:
            round_number = int(game.get("round_number") or 0)
        except (TypeError, ValueError):
            continue
        if round_number <= 0:
            continue
        for side in ("home", "away"):
            label = game.get(side)
            if isinstance(label, dict):
                label = label.get("label")
            if label:
                played_by_round[round_number].add(str(label))
    return {
        round_number: sorted(team_labels - played)
        for round_number, played in sorted(played_by_round.items())
        if team_labels - played
    }


def _count_bye_rows(tournament: dict[str, Any]) -> int:
    """Count CSV pause/bye rows implied by the persisted tournament games."""
    return sum(len(labels) for labels in _bye_rounds(tournament).values())


def _collect_plan_audit_facts(
    *,
    plan_dict: dict[str, Any] | None,
    config_checkpoint: dict[str, Any] | None,
) -> dict[str, Any]:
    """Derive the complete cross-check fact set from the persisted selected plan.

    This is evidence packaging, not a second policy engine: it exposes simple
    counts/examples so the semantic judge can answer checklist items that the
    export paths alone cannot establish. It deliberately returns the *full*
    lists (never truncated); :func:`_summarize_plan_facts` derives the
    bounded overview from it and the selective-evidence index keeps the rest
    retrievable on demand.
    """
    if not isinstance(plan_dict, dict):
        return {}

    tournaments = [t for t in plan_dict.get("tournaments") or [] if isinstance(t, dict)]
    ice_times = {}
    # Effective-shape rule: the complete registered pool per age group, needed to tell
    # an avoidable bye/underscheduling shape apart from a genuine
    # input-constrained one -- degrades to the unconditional previous check
    # when unavailable (audit evidence, not a gate, so a graceful fallback is
    # fine here).
    registered_count_by_age_group: Counter[str] = Counter()
    rounds_per_tournament: dict[str, Any] = {}
    if isinstance(config_checkpoint, dict):
        ice_times = dict(config_checkpoint.get("ice_time_minutes") or {})
        rounds_per_tournament = dict(config_checkpoint.get("rounds_per_tournament") or {})
        for team in config_checkpoint.get("teams") or []:
            if isinstance(team, dict) and team.get("age_group"):
                registered_count_by_age_group[str(team["age_group"])] += 1

    duration_examples: list[dict[str, Any]] = []
    missing_duration: list[dict[str, Any]] = []
    duration_values: list[int] = []
    bye_row_count = sum(_count_bye_rows(tournament) for tournament in tournaments)
    utilisation_examples: list[dict[str, Any]] = []
    blocking_byes: list[dict[str, Any]] = []
    input_constrained_shape_examples: list[dict[str, Any]] = []
    underfilled_by_age: Counter[str] = Counter()
    for tournament in tournaments:
        games = [g for g in tournament.get("games") or [] if isinstance(g, dict)]
        round_count = max([int(g.get("round_number") or 0) for g in games] or [0])
        age_group = str(tournament.get("age_group") or "")
        ice_time = ice_times.get(age_group)
        round_buffer_minutes = 5 * round_count
        start_time = tournament.get("start_time")
        duration_minutes = None
        end_time = None
        if round_count > 0 and isinstance(ice_time, int) and ice_time > 0:
            duration_minutes = ice_time + round_buffer_minutes
            duration_values.append(duration_minutes)
            if start_time:
                try:
                    start = datetime.strptime(str(start_time), "%H:%M")
                    end = start + timedelta(minutes=duration_minutes)
                    end_time = end.strftime("%H:%M")
                except ValueError:
                    end_time = None
        else:
            missing_duration.append(_tournament_ref(tournament))
        duration_examples.append(
            {
                **_tournament_ref(tournament),
                "start_time": start_time,
                "end_time": end_time,
                "round_count": round_count,
                "configured_ice_time_minutes": ice_time,
                "round_buffer_minutes": round_buffer_minutes,
                "required_duration_minutes": duration_minutes,
                "duration_minutes": duration_minutes,
                "game_count": len(games),
            }
        )
        teams = [team for team in tournament.get("teams") or [] if isinstance(team, dict)]
        team_count = len(teams)
        configured_capacity = None
        if isinstance(config_checkpoint, dict):
            pg = (config_checkpoint.get("parallel_games") or {}).get(age_group)
            if isinstance(pg, int) and pg > 0:
                configured_capacity = pg * 2
        full_team_count = configured_capacity or team_count
        max_full_game_count = full_team_count * (full_team_count - 1) // 2 if full_team_count > 1 else 0
        bye_rounds = _bye_rounds(tournament)
        utilisation_row = {
            **_tournament_ref(tournament),
            "team_count": team_count,
            "game_count": len(games),
            "configured_parallel_game_capacity": configured_capacity,
            "full_capacity_game_count": max_full_game_count,
            "bye_rounds": {str(k): v for k, v in bye_rounds.items()},
            "pause_team_count": sum(len(v) for v in bye_rounds.values()),
            "underfilled": bool(team_count and max_full_game_count and len(games) < max_full_game_count),
        }
        utilisation_examples.append(utilisation_row)
        if utilisation_row["underfilled"]:
            underfilled_by_age[age_group] += 1
        if registered_count_by_age_group:
            shape = compute_effective_tournament_shape(
                age_group,
                registered_count_by_age_group.get(age_group, 0),
                configured_rounds=rounds_per_tournament.get(age_group),
                parallel_game_capacity=configured_capacity // 2 if configured_capacity else None,
            )
            bye_round_count = len(bye_rounds)
            if shape_violation(shape, team_count, bye_round_count):
                blocking_byes.append({**utilisation_row, "required_team_count": shape.effective_team_count})
            elif shape.input_constrained and team_count == shape.effective_team_count:
                input_constrained_shape_examples.append({**utilisation_row, **shape.as_dict()})
        elif team_count % 2 == 1 or bye_rounds:
            # No registered-pool evidence available this run -- fall back to
            # the unconditional previous check (audit evidence only).
            blocking_byes.append({**utilisation_row, "required_team_count": None})

    host_missing: list[dict[str, Any]] = []
    club_count_over_two: list[dict[str, Any]] = []
    club_count_over_hard_max: list[dict[str, Any]] = []
    max_club_count_by_tournament = Counter()
    team_day_counts: Counter[tuple[str, str]] = Counter()
    for tournament in tournaments:
        teams = [team for team in tournament.get("teams") or [] if isinstance(team, dict)]
        host_club = tournament.get("host_club")
        participant_clubs = [team.get("club") for team in teams]
        if host_club and not any(
            clubs_represent_same_club(str(participant_club), str(host_club))
            for participant_club in participant_clubs
            if participant_club
        ):
            host_missing.append(
                {
                    **_tournament_ref(tournament),
                    "participant_clubs": sorted({str(club) for club in participant_clubs if club}),
                }
            )

        club_counts = Counter(str(team.get("club") or "") for team in teams if team.get("club"))
        if club_counts:
            max_count = max(club_counts.values())
            max_club_count_by_tournament[max_count] += 1
            offenders = {club: count for club, count in club_counts.items() if count > 2}
            if offenders:
                club_count_over_two.append({**_tournament_ref(tournament), "club_counts_over_two": offenders})
            hard_max_offenders = {
                club: count for club, count in club_counts.items() if count > HARD_MAX_CLUB_TEAMS_PER_TOURNAMENT
            }
            if hard_max_offenders:
                club_count_over_hard_max.append(
                    {**_tournament_ref(tournament), "club_counts_over_hard_max": hard_max_offenders}
                )

        date = str(tournament.get("date") or "")
        for team in teams:
            team_day_counts[(_team_key(team), date)] += 1

    duplicate_team_days = [
        {"team": team, "date": date, "participations": count}
        for (team, date), count in team_day_counts.items()
        if date and count > 1
    ]
    duplicate_team_days.sort(key=lambda item: (-int(item["participations"]), item["date"], item["team"]))

    duration_examples.sort(
        key=lambda item: (
            -(item.get("duration_minutes") or 0),
            str(item.get("date") or ""),
            str(item.get("age_group") or ""),
        )
    )

    return {
        "tournament_count": len(tournaments),
        "game_count": sum(len(t.get("games") or []) for t in tournaments),
        "csv_pause_row_count": bye_row_count,
        "expected_csv_game_rows": sum(len(t.get("games") or []) for t in tournaments) + bye_row_count,
        "team_day_entry_count": sum(team_day_counts.values()),
        "duration_values": duration_values,
        "missing_duration": missing_duration,
        "duration_examples": duration_examples,
        "utilisation_examples": utilisation_examples,
        "blocking_byes": blocking_byes,
        "input_constrained_shape_examples": input_constrained_shape_examples,
        "underfilled_by_age": dict(sorted(underfilled_by_age.items())),
        "host_missing": host_missing,
        "duplicate_team_days": duplicate_team_days,
        "club_count_over_two": club_count_over_two,
        "club_count_over_hard_max": club_count_over_hard_max,
        "max_club_count_by_tournament": dict(sorted(max_club_count_by_tournament.items())),
        # issue #327: proportional club-share fairness evidence, computed by
        # SeasonPlanner from the full registered roster (not re-derivable
        # from `tournaments` alone, which omits zero-participation teams)
        # and passed through the checkpoint unchanged -- lets the semantic
        # judge tell an acceptable, capacity-limited proportional shortfall
        # apart from a genuine planner fairness defect.
        "club_participation_fairness": list(plan_dict.get("club_participation_fairness") or []),
        # issue #327: per-team shortfall category/reason (e.g.
        # `participation_under_target_club_share_ok` vs the generic
        # `participation_under_target`), reconciled onto the plan by Stage 4
        # (`_reconcile_verified_manual_state`) -- lets the judge cross-check
        # an individual team's miss against the club aggregate above instead
        # of only seeing an unlabeled actual/target pair.
        "unresolved_participation_shortfalls": list(plan_dict.get("unresolved_participation_shortfalls") or []),
        # issue #329: same-age hosting-coverage repairs already attempted
        # (repaired or rejected-with-reason), before cross-age repair below,
        # for this plan's remaining `unresolved_hosting_obligations`.
        "same_age_hosting_repairs": list(plan_dict.get("same_age_hosting_repairs") or []),
        # issue #328: cross-age hosting-coverage repairs already attempted
        # (repaired or rejected-with-reason) before this plan's remaining
        # `unresolved_hosting_obligations` were accepted.
        "cross_age_hosting_repairs": list(plan_dict.get("cross_age_hosting_repairs") or []),
        # issue #330: concrete tournament-placement search failures (a real
        # roster/date that could not get a participant-host arena/time) --
        # distinct from `unresolved_hosting_obligations`'
        # (a club x age-group hosting deficit with no tournament object
        # behind it yet). Kept in full here so the selective-evidence API can
        # retrieve an exact roster on demand.
        "unresolved_tournament_placements": list(plan_dict.get("unresolved_tournament_placements") or []),
    }


def _summarize_plan_facts(facts: dict[str, Any]) -> dict[str, Any]:
    """Derive the *bounded* cross-check summary placed in the audit context.

    Large collections (participation shortfalls, hosting repairs, unresolved
    placements) are represented here by counts plus a capped set of
    representative examples and an ``evidence_ref`` query command -- the
    complete detail stays in the selective-evidence index.
    """
    if not facts:
        return {}
    duration_values: list[int] = facts["duration_values"]
    return {
        "tournament_count": facts["tournament_count"],
        "game_count": facts["game_count"],
        "csv_pause_row_count": facts["csv_pause_row_count"],
        "expected_csv_game_rows": facts["expected_csv_game_rows"],
        "team_day_entry_count": facts["team_day_entry_count"],
        "tournament_utilisation_summary": {
            "tournaments_with_byes_or_invalid_no_bye_roster": len(facts["blocking_byes"]),
            "bye_examples": facts["blocking_byes"][:audit_evidence.EVIDENCE_OVERVIEW_MAX_EXAMPLES],
            # Effective-shape rule: tournaments whose odd/bye-having shape is a
            # legitimate input-constrained adaptation (the registered pool
            # was too small for the preferred shape), not a planner defect.
            "input_constrained_shape_count": len(facts["input_constrained_shape_examples"]),
            "input_constrained_shape_examples": facts["input_constrained_shape_examples"][
                :audit_evidence.EVIDENCE_OVERVIEW_MAX_EXAMPLES
            ],
            "underfilled_by_age_group": facts["underfilled_by_age"],
            "underfilled_count": sum(1 for row in facts["utilisation_examples"] if row.get("underfilled")),
            "examples": facts["utilisation_examples"][:audit_evidence.EVIDENCE_OVERVIEW_MAX_EXAMPLES],
            "evidence_ref": "operator audit-evidence --category blocking_byes",
        },
        "duration_summary": {
            "min_minutes": min(duration_values) if duration_values else None,
            "max_minutes": max(duration_values) if duration_values else None,
            "missing_duration_count": len(facts["missing_duration"]),
            "missing_duration_examples": facts["missing_duration"][:audit_evidence.EVIDENCE_OVERVIEW_MAX_EXAMPLES],
            "longest_examples": facts["duration_examples"][:audit_evidence.EVIDENCE_OVERVIEW_MAX_EXAMPLES],
            "evidence_ref": "operator audit-evidence --category tournament_duration",
        },
        "host_participation_summary": {
            "tournaments_where_host_club_not_in_participants": len(facts["host_missing"]),
            "examples": facts["host_missing"][:audit_evidence.EVIDENCE_OVERVIEW_MAX_EXAMPLES],
            "evidence_ref": "operator audit-evidence --category host_participation",
        },
        "team_daily_participation_summary": {
            "duplicate_team_day_count": len(facts["duplicate_team_days"]),
            "examples": facts["duplicate_team_days"][:audit_evidence.EVIDENCE_OVERVIEW_MAX_EXAMPLES],
            "evidence_ref": "operator audit-evidence --category duplicate_team_days",
        },
        "same_club_per_tournament_summary": {
            "tournaments_with_more_than_two_from_same_club": len(facts["club_count_over_two"]),
            "max_club_count_distribution": facts["max_club_count_by_tournament"],
            "examples": facts["club_count_over_two"][:audit_evidence.EVIDENCE_OVERVIEW_MAX_EXAMPLES],
            # issue #326: teams from one club above HARD_MAX_CLUB_TEAMS_PER_TOURNAMENT
            # is an invalid plan, not a quality preference -- any non-empty
            # list here must drive a FAIL verdict, never REVIEW_REQUIRED.
            "tournaments_over_hard_max": len(facts["club_count_over_hard_max"]),
            "hard_max": HARD_MAX_CLUB_TEAMS_PER_TOURNAMENT,
            "hard_max_examples": facts["club_count_over_hard_max"][:audit_evidence.EVIDENCE_OVERVIEW_MAX_EXAMPLES],
            "evidence_ref": "operator audit-evidence --category same_club_participants",
        },
        "unresolved_participation_shortfall_count": len(facts["unresolved_participation_shortfalls"]),
        "club_participation_fairness_count": len(facts["club_participation_fairness"]),
        "same_age_hosting_repair_count": len(facts["same_age_hosting_repairs"]),
        "cross_age_hosting_repair_count": len(facts["cross_age_hosting_repairs"]),
        "unresolved_tournament_placement_count": len(facts["unresolved_tournament_placements"]),
    }


def _count_csv_rows(path: Path) -> int | None:
    try:
        with path.open(encoding="utf-8-sig", newline="") as fh:
            reader = csv.reader(fh)
            next(reader, None)
            return sum(1 for _ in reader)
    except OSError:
        return None


def _summarize_export_consistency(
    *,
    output_files: dict[str, Any] | None,
    plan_audit_summary: dict[str, Any],
    plan_dict: dict[str, Any] | None,
) -> dict[str, Any]:
    output_files = output_files or {}
    expected_tournaments = plan_audit_summary.get("tournament_count")
    expected_games = plan_audit_summary.get("game_count")
    expected_csv_game_rows = plan_audit_summary.get("expected_csv_game_rows")
    expected_pause_rows = plan_audit_summary.get("csv_pause_row_count")
    expected_team_counts = len((plan_dict or {}).get("team_game_counts") or {}) if isinstance(plan_dict, dict) else None

    checks: dict[str, Any] = {}
    csv_games = output_files.get("csv_games")
    if csv_games:
        rows = _count_csv_rows(Path(csv_games))
        checks["csv_games"] = {
            "rows": rows,
            "expected_rows_including_pause_rows": expected_csv_game_rows,
            "plan_game_rows": expected_games,
            "expected_pause_rows": expected_pause_rows,
            "matches_plan": rows == expected_csv_game_rows,
        }
    csv_overview = output_files.get("csv_overview")
    if csv_overview:
        rows = _count_csv_rows(Path(csv_overview))
        checks["csv_overview"] = {
            "rows": rows,
            "expected_tournament_rows": expected_tournaments,
            "matches_plan": rows == expected_tournaments,
        }

    export_dir = Path(str(output_files.get("html") or "")).parent if output_files.get("html") else None
    if export_dir:
        team_counts_path = export_dir / "season_plan_team_counts.csv"
        rows = _count_csv_rows(team_counts_path)
        checks["csv_team_counts"] = {
            "path": str(team_counts_path),
            "rows": rows,
            "expected_team_rows": expected_team_counts,
            "matches_plan": rows == expected_team_counts,
        }

    ical = output_files.get("ical")
    if ical:
        try:
            event_count = Path(ical).read_text(encoding="utf-8").count("BEGIN:VEVENT")
        except OSError:
            event_count = None
        checks["ical"] = {
            "events": event_count,
            "expected_tournament_events": expected_tournaments,
            "matches_plan": event_count == expected_tournaments,
        }

    excel = output_files.get("excel")
    if excel:
        excel_check: dict[str, Any] = {"path": excel}
        try:
            import openpyxl  # type: ignore[import-not-found]

            workbook = openpyxl.load_workbook(excel, read_only=True, data_only=True)
            overview = workbook["Sesongoversikt"] if "Sesongoversikt" in workbook.sheetnames else None
            excel_check.update(
                {
                    "sheet_count": len(workbook.sheetnames),
                    "overview_rows": (overview.max_row - 1) if overview is not None else None,
                    "expected_tournament_rows": expected_tournaments,
                    "matches_plan": (overview.max_row - 1) == expected_tournaments if overview is not None else None,
                }
            )
            workbook.close()
        except Exception as exc:  # pragma: no cover - depends on optional reader availability/corrupt files
            excel_check.update({"read_error": str(exc), "matches_plan": None})
        checks["excel"] = excel_check

    mismatches = [name for name, check in checks.items() if check.get("matches_plan") is False]
    unknown = [name for name, check in checks.items() if check.get("matches_plan") is None]
    return {"checks": checks, "mismatches": mismatches, "unknown": unknown}


def _build_checklist_evidence_guide(*, evidence_overview: dict[str, Any]) -> list[dict[str, Any]]:
    """Point the semantic judge at the queryable evidence categories per checklist item.

    Deliberately compact: it lists the canonical categories, counts, unresolved
    counts and the exact ``operator audit-evidence`` command per item rather
    than embedding the same large lists the overview already references.
    """
    by_item: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for category in evidence_overview.get("categories") or []:
        item_id = category.get("checklist_item")
        if isinstance(item_id, int):
            by_item[item_id].append(category)

    guide: list[dict[str, Any]] = []
    for item in AUDIT_CHECKLIST:
        item_id = int(item["item_id"])
        categories = by_item.get(item_id, [])
        guide.append(
            {
                "item_id": item_id,
                "question": item["question"],
                "categories": [category["category"] for category in categories],
                "counts": {category["category"]: category["count"] for category in categories},
                "unresolved_counts": {
                    category["category"]: category["unresolved_count"]
                    for category in categories
                    if category.get("unresolved_count")
                },
                "evidence_commands": [category["evidence_ref"] for category in categories],
            }
        )
    return guide


def _assemble_raw_audit_evidence(*, work_dir: "str | Path") -> dict[str, Any]:
    """Read persisted run artifacts and assemble the raw audit evidence base.

    Pure read of already-persisted checkpoints/artifacts: no fresh scraping,
    no re-deriving deterministic policy (the deterministic verify result is
    read as Stage 4 already computed it, not recomputed here). The returned
    dict keeps the full finding lists so the selective-evidence index can
    serve exact detail on demand; :func:`build_audit_context` derives the
    bounded overview from it.
    """
    from .run_manifest import RunManifest

    state = PipelineState(work_dir)
    export_checkpoint = state.read_stage(StageName.EXPORT)
    planning_checkpoint = state.read_stage(StageName.PLANNING)
    config_checkpoint = state.read_stage(StageName.CONFIG)
    scraping_checkpoint = state.read_stage(StageName.SCRAPING)
    plan_dict = planning_checkpoint.get("plan") if isinstance(planning_checkpoint, dict) else None

    manifest = RunManifest(work_dir).read()
    export_dir = export_checkpoint.get("export_dir")
    output_files = export_checkpoint.get("output_files") or {}
    run_id = current_run_id(work_dir)
    export_fingerprint = current_export_fingerprint(work_dir)

    evidence_bundle = _read_evidence_bundle(export_dir)
    final_operator_evidence = _bound_final_operator_evidence(
        evidence_bundle,
        run_id=run_id,
        export_fingerprint=export_fingerprint,
    )
    deterministic_verify_result = export_checkpoint.get("verify_result") or {}
    plan_dict = _plan_dict_with_final_operator_evidence(plan_dict, final_operator_evidence)
    # The fresh verifier result (or the fingerprint-bound final operator
    # evidence derived from it) is authoritative for the descriptive
    # hosting/readiness snapshot. The plan checkpoint's own snapshot can be
    # stale after a canonical mutation (e.g. `season move`) changed
    # `plan.tournaments` without recomputing those derived fields.
    bound_readiness = (
        final_operator_evidence.get("publication_readiness")
        if isinstance(final_operator_evidence, dict)
        and isinstance(final_operator_evidence.get("publication_readiness"), dict)
        else None
    )
    authoritative_state = final_operator_evidence if bound_readiness else deterministic_verify_result
    plan_dict = reconcile_plan_derived_state(plan_dict, authoritative_state, readiness=bound_readiness)
    operator_waived_violations = list((final_operator_evidence or {}).get("operator_waived_violations") or [])
    if not operator_waived_violations:
        operator_waived_violations = list(deterministic_verify_result.get("waived_violations") or [])
    operator_waivers: list[dict[str, Any]] = []
    if isinstance(final_operator_evidence, dict) and final_operator_evidence.get("operator_waivers"):
        operator_waivers = list(final_operator_evidence.get("operator_waivers") or [])
    elif isinstance(plan_dict, dict) and plan_dict.get("operator_waivers"):
        operator_waivers = list(plan_dict.get("operator_waivers") or [])
    else:
        from ..operator_waivers import load_active_waivers, waiver_audit_rows

        operator_waivers = waiver_audit_rows({"operator_waivers": load_active_waivers(work_dir)})
    publication_readiness = plan_dict.get("publication_readiness") if isinstance(plan_dict, dict) else None
    if not publication_readiness:
        publication_readiness = publication_readiness_with_plan_placements(
            deterministic_verify_result, plan_dict
        )

    approval_status: dict[str, Any] | None = None
    if isinstance(final_operator_evidence, dict) and isinstance(final_operator_evidence.get("approval_status"), dict):
        approval_status = final_operator_evidence.get("approval_status")
    elif isinstance(plan_dict, dict) and isinstance(plan_dict.get("approval_status"), dict):
        approval_status = plan_dict.get("approval_status")
    elif isinstance(deterministic_verify_result.get("stale_approvals"), list) and deterministic_verify_result.get("stale_approvals"):
        approval_status = {"stale_approvals": deterministic_verify_result.get("stale_approvals")}

    calendar_evidence_summary = (evidence_bundle or {}).get("source_summary") or {}
    if not calendar_evidence_summary:
        calendar_evidence_summary = _summarize_scraping_checkpoint(scraping_checkpoint)
    plan_audit_facts = _collect_plan_audit_facts(
        plan_dict=plan_dict,
        config_checkpoint=config_checkpoint,
    )
    plan_audit_summary = _summarize_plan_facts(plan_audit_facts)
    export_consistency_summary = _summarize_export_consistency(
        output_files=output_files,
        plan_audit_summary=plan_audit_summary,
        plan_dict=plan_dict,
    )

    return {
        "run_id": run_id,
        "export_fingerprint": export_fingerprint,
        "source_fingerprints": {
            "input_fingerprint": manifest.get("input_fingerprint"),
            "effective_config_fingerprint": manifest.get("effective_config_fingerprint"),
        },
        "export_dir": export_dir,
        "output_files": output_files,
        "deterministic_verify_result": deterministic_verify_result,
        "operator_waivers": operator_waivers,
        "operator_waived_violations": operator_waived_violations,
        "approval_status": approval_status,
        "publication_readiness": publication_readiness,
        "calendar_evidence_summary": calendar_evidence_summary,
        "plan_audit_facts": plan_audit_facts,
        "plan_audit_summary": plan_audit_summary,
        "export_consistency_summary": export_consistency_summary,
    }


def build_audit_context(*, work_dir: "str | Path") -> dict[str, Any]:
    """Assemble the *bounded* audit evidence overview for the current export.

    The default context intentionally never embeds the wholesale
    ``evidence_bundle`` or any unbounded finding list: it carries compact
    summaries (counts, distributions, worst/top-N examples, fingerprints) plus
    an ``evidence_index`` that tells the judge which selective
    ``operator audit-evidence`` queries are available. Full detail remains
    retrievable through :func:`build_audit_evidence`.
    """
    raw = _assemble_raw_audit_evidence(work_dir=work_dir)
    index = audit_evidence.build_evidence_index(raw)
    evidence_overview = audit_evidence.build_evidence_overview(index)
    context = {
        "audit_prompt_version": AUDIT_PROMPT_VERSION,
        "hard_max_club_teams_per_tournament": HARD_MAX_CLUB_TEAMS_PER_TOURNAMENT,
        "runbook_version": _runbook_version(),
        "audit_mission": AUDIT_MISSION,
        "checklist": [dict(item) for item in AUDIT_CHECKLIST],
        "run_id": raw["run_id"],
        "export_fingerprint": raw["export_fingerprint"],
        "source_fingerprints": raw["source_fingerprints"],
        "export_dir": raw["export_dir"],
        "output_files": raw["output_files"],
        "deterministic_verify_result": _bound_verify_result(raw["deterministic_verify_result"]),
        "operator_waivers": raw["operator_waivers"],
        "operator_waived_violations": raw["operator_waived_violations"],
        "approval_status": _bound_lists(raw["approval_status"]),
        "publication_readiness": raw["publication_readiness"],
        "calendar_evidence_summary": raw["calendar_evidence_summary"],
        "plan_audit_summary": raw["plan_audit_summary"],
        "export_consistency_summary": raw["export_consistency_summary"],
        "checklist_evidence_guide": _build_checklist_evidence_guide(evidence_overview=evidence_overview),
        "evidence_index": evidence_overview,
    }
    context["evidence_metrics"] = audit_evidence.build_overview_metrics(context, index)
    return context


def build_audit_evidence_index(*, work_dir: "str | Path") -> dict[str, Any]:
    """Return the full (unbounded) selective-evidence index for the current run.

    Used by the headless judge to resolve requested detail; it is never placed
    in the bounded context itself.
    """
    raw = _assemble_raw_audit_evidence(work_dir=work_dir)
    return audit_evidence.build_evidence_index(raw)


def build_audit_evidence(
    *,
    work_dir: "str | Path",
    item: int | None = None,
    tournament: str | None = None,
    club: str | None = None,
    age_group: str | None = None,
    category: str | None = None,
    unresolved: bool = False,
    limit: int | None = None,
) -> dict[str, Any]:
    """Return detailed audit evidence matching the given selectors.

    Reconstruction of the raw evidence is deterministic and read-only, and the
    result is bound to the same ``run_id``/``export_fingerprint`` as the
    overview that advertised these selectors, so stale evidence from another
    run/export can never be mixed in silently.
    """
    raw = _assemble_raw_audit_evidence(work_dir=work_dir)
    index = audit_evidence.build_evidence_index(raw)
    return audit_evidence.query_evidence(
        index,
        item=item,
        tournament=tournament,
        club=club,
        age_group=age_group,
        category=category,
        unresolved=unresolved,
        limit=limit,
    )
