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

from ..planning_contract import HARD_MAX_CLUB_TEAMS_PER_TOURNAMENT
from .audit_result import current_export_fingerprint, current_run_id
from .fingerprints import stable_payload_sha256
from .state import PipelineState, StageName

AUDIT_PROMPT_VERSION = 1

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


def _count_bye_rows(tournament: dict[str, Any]) -> int:
    """Count CSV pause/bye rows implied by the persisted tournament games."""
    team_labels = {
        str(team.get("label"))
        for team in tournament.get("teams") or []
        if isinstance(team, dict) and team.get("label")
    }
    if not team_labels:
        return 0
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
    return sum(len(team_labels - played) for played in played_by_round.values())


def _summarize_plan_for_audit(
    *,
    plan_dict: dict[str, Any] | None,
    config_checkpoint: dict[str, Any] | None,
) -> dict[str, Any]:
    """Derive compact cross-check facts from the persisted selected plan.

    This is evidence packaging, not a second policy engine: it exposes simple
    counts/examples so the semantic judge can answer checklist items that the
    export paths alone cannot establish.
    """
    if not isinstance(plan_dict, dict):
        return {}

    tournaments = [t for t in plan_dict.get("tournaments") or [] if isinstance(t, dict)]
    round_lengths = {}
    if isinstance(config_checkpoint, dict):
        round_lengths = dict(config_checkpoint.get("round_length_minutes") or {})

    duration_examples: list[dict[str, Any]] = []
    missing_duration: list[dict[str, Any]] = []
    duration_values: list[int] = []
    bye_row_count = sum(_count_bye_rows(tournament) for tournament in tournaments)
    for tournament in tournaments:
        games = [g for g in tournament.get("games") or [] if isinstance(g, dict)]
        round_count = max([int(g.get("round_number") or 0) for g in games] or [0])
        round_length = round_lengths.get(str(tournament.get("age_group") or ""))
        start_time = tournament.get("start_time")
        duration_minutes = None
        end_time = None
        if round_count > 0 and isinstance(round_length, int) and round_length > 0:
            from ..utils.slot_finder import matchday_duration_minutes

            duration_minutes = matchday_duration_minutes(round_length, round_count)
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
                "round_length_minutes": round_length,
                "duration_minutes": duration_minutes,
                "game_count": len(games),
            }
        )

    host_missing: list[dict[str, Any]] = []
    club_count_over_two: list[dict[str, Any]] = []
    club_count_over_hard_max: list[dict[str, Any]] = []
    max_club_count_by_tournament = Counter()
    team_day_counts: Counter[tuple[str, str]] = Counter()
    for tournament in tournaments:
        teams = [team for team in tournament.get("teams") or [] if isinstance(team, dict)]
        host_club = tournament.get("host_club")
        participant_clubs = [team.get("club") for team in teams]
        if host_club and host_club not in participant_clubs:
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
        "duration_summary": {
            "min_minutes": min(duration_values) if duration_values else None,
            "max_minutes": max(duration_values) if duration_values else None,
            "missing_duration_count": len(missing_duration),
            "missing_duration_examples": missing_duration[:10],
            "longest_examples": duration_examples[:10],
        },
        "host_participation_summary": {
            "tournaments_where_host_club_not_in_participants": len(host_missing),
            "examples": host_missing[:20],
        },
        "team_daily_participation_summary": {
            "duplicate_team_day_count": len(duplicate_team_days),
            "examples": duplicate_team_days[:20],
        },
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
        # issue #328: cross-age hosting-coverage repairs already attempted
        # (repaired or rejected-with-reason) before this plan's remaining
        # `unresolved_hosting_obligations` were accepted.
        "cross_age_hosting_repairs": list(plan_dict.get("cross_age_hosting_repairs") or []),
        "same_club_per_tournament_summary": {
            "tournaments_with_more_than_two_from_same_club": len(club_count_over_two),
            "max_club_count_distribution": dict(sorted(max_club_count_by_tournament.items())),
            "examples": club_count_over_two[:20],
            # issue #326: teams from one club above HARD_MAX_CLUB_TEAMS_PER_TOURNAMENT
            # is an invalid plan, not a quality preference -- any non-empty
            # list here must drive a FAIL verdict, never REVIEW_REQUIRED.
            "tournaments_over_hard_max": len(club_count_over_hard_max),
            "hard_max": HARD_MAX_CLUB_TEAMS_PER_TOURNAMENT,
            "hard_max_examples": club_count_over_hard_max[:20],
        },
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


def _build_checklist_evidence_guide(
    *,
    plan_audit_summary: dict[str, Any],
    calendar_evidence_summary: dict[str, Any],
    export_consistency_summary: dict[str, Any],
    deterministic_verify_result: dict[str, Any],
    publication_readiness: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Point the semantic judge at the strongest persisted evidence per checklist item."""
    question_by_id = {int(item["item_id"]): item["question"] for item in AUDIT_CHECKLIST}
    readiness_reasons = (publication_readiness or {}).get("reasons")
    if not isinstance(readiness_reasons, dict):
        readiness_reasons = {}
    return [
        {
            "item_id": 1,
            "question": question_by_id[1],
            "primary_evidence": [
                "publication_readiness.reasons.participation_shortfalls",
                "deterministic_verify_result.manual_participation_placements",
                "plan_audit_summary.club_participation_fairness",
                "plan_audit_summary.unresolved_participation_shortfalls",
            ],
            "summary": {
                "participation_shortfall_reasons": readiness_reasons.get("participation_shortfalls"),
                # issue #327: a per-team shortfall next to a club whose
                # `actual_share` is close to its `target_share` (with a
                # small `sibling_spread`) is a capacity-limited proportional
                # outcome, not necessarily a planner defect.
                "club_participation_fairness": plan_audit_summary.get("club_participation_fairness"),
                # issue #327: each shortfall's own `category`/`reason` --
                # `participation_under_target_club_share_ok` already reflects
                # the club-share cross-check above; a plain
                # `participation_under_target` entry does not and deserves
                # closer scrutiny.
                "unresolved_participation_shortfalls": plan_audit_summary.get(
                    "unresolved_participation_shortfalls"
                ),
            },
        },
        {
            "item_id": 2,
            "question": question_by_id[2],
            "primary_evidence": [
                "deterministic_verify_result.unresolved_hosting_obligations",
                "publication_readiness.reasons.unresolved_hosting",
                "plan_audit_summary.cross_age_hosting_repairs",
            ],
            "summary": {
                "unresolved_hosting": len(deterministic_verify_result.get("unresolved_hosting_obligations") or []),
                # issue #328: an unresolved obligation with a non-empty
                # `candidate_reallocation_slots` is exactly the "deficit with
                # reusable surplus" defect pattern this checklist item exists
                # to catch.
                "unresolved_with_untried_reallocation_candidates": [
                    {"club": item.get("club"), "age_group": item.get("age_group")}
                    for item in (deterministic_verify_result.get("unresolved_hosting_obligations") or [])
                    if item.get("candidate_reallocation_slots")
                ],
                "cross_age_hosting_repairs": plan_audit_summary.get("cross_age_hosting_repairs"),
            },
        },
        {
            "item_id": 3,
            "question": question_by_id[3],
            "primary_evidence": ["plan_audit_summary.duration_summary"],
            "summary": plan_audit_summary.get("duration_summary"),
        },
        {
            "item_id": 4,
            "question": question_by_id[4],
            "primary_evidence": [
                "calendar_evidence_summary",
                "deterministic_verify_result.manual_external_conflict_placements",
            ],
            "summary": {
                "sources_scanned": calendar_evidence_summary.get("sources_scanned"),
                "blocked_sources": calendar_evidence_summary.get("blocked_sources"),
                "external_conflict_placements": len(
                    deterministic_verify_result.get("manual_external_conflict_placements") or []
                ),
            },
        },
        {
            "item_id": 5,
            "question": question_by_id[5],
            "primary_evidence": ["plan_audit_summary.host_participation_summary"],
            "summary": plan_audit_summary.get("host_participation_summary"),
        },
        {
            "item_id": 6,
            "question": question_by_id[6],
            "primary_evidence": ["plan_audit_summary.team_daily_participation_summary"],
            "summary": plan_audit_summary.get("team_daily_participation_summary"),
        },
        {
            "item_id": 7,
            "question": question_by_id[7],
            "primary_evidence": ["plan_audit_summary.same_club_per_tournament_summary"],
            "summary": plan_audit_summary.get("same_club_per_tournament_summary"),
        },
        {
            "item_id": 8,
            "question": question_by_id[8],
            "primary_evidence": ["export_consistency_summary"],
            "summary": export_consistency_summary,
        },
        {
            "item_id": 9,
            "question": question_by_id[9],
            "primary_evidence": [
                "publication_readiness",
                "deterministic_verify_result",
                "plan_audit_summary",
                "calendar_evidence_summary",
            ],
            "summary": {"publication_readiness": publication_readiness},
        },
    ]


def build_audit_context(*, work_dir: "str | Path") -> dict[str, Any]:
    """Assemble the audit evidence inventory for the current run's export.

    Pure read of already-persisted checkpoints/artifacts: no fresh scraping,
    no re-deriving deterministic policy (the deterministic verify result is
    read as Stage 4 already computed it, not recomputed here).
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

    deterministic_verify_result = export_checkpoint.get("verify_result") or {}
    publication_readiness: dict[str, Any] | None = None
    if isinstance(plan_dict, dict) and plan_dict.get("publication_readiness"):
        publication_readiness = plan_dict.get("publication_readiness")
    else:
        from ..final_verification import publication_readiness as _compute_readiness

        publication_readiness = _compute_readiness(deterministic_verify_result)

    evidence_bundle = _read_evidence_bundle(export_dir)
    calendar_evidence_summary = (evidence_bundle or {}).get("source_summary") or {}
    if not calendar_evidence_summary:
        calendar_evidence_summary = _summarize_scraping_checkpoint(scraping_checkpoint)
    plan_audit_summary = _summarize_plan_for_audit(
        plan_dict=plan_dict,
        config_checkpoint=config_checkpoint,
    )
    export_consistency_summary = _summarize_export_consistency(
        output_files=output_files,
        plan_audit_summary=plan_audit_summary,
        plan_dict=plan_dict,
    )
    checklist_evidence_guide = _build_checklist_evidence_guide(
        plan_audit_summary=plan_audit_summary,
        calendar_evidence_summary=calendar_evidence_summary,
        export_consistency_summary=export_consistency_summary,
        deterministic_verify_result=deterministic_verify_result,
        publication_readiness=publication_readiness,
    )

    return {
        "audit_prompt_version": AUDIT_PROMPT_VERSION,
        "hard_max_club_teams_per_tournament": HARD_MAX_CLUB_TEAMS_PER_TOURNAMENT,
        "runbook_version": _runbook_version(),
        "audit_mission": AUDIT_MISSION,
        "checklist": [dict(item) for item in AUDIT_CHECKLIST],
        "run_id": current_run_id(work_dir),
        "export_fingerprint": current_export_fingerprint(work_dir),
        "source_fingerprints": {
            "input_fingerprint": manifest.get("input_fingerprint"),
            "effective_config_fingerprint": manifest.get("effective_config_fingerprint"),
        },
        "export_dir": export_dir,
        "output_files": output_files,
        "deterministic_verify_result": deterministic_verify_result,
        "publication_readiness": publication_readiness,
        "evidence_bundle": evidence_bundle,
        "calendar_evidence_summary": calendar_evidence_summary,
        "plan_audit_summary": plan_audit_summary,
        "export_consistency_summary": export_consistency_summary,
        "checklist_evidence_guide": checklist_evidence_guide,
    }
