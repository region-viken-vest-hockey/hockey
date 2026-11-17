"""Reverse event-to-tournament assessment projection."""

from __future__ import annotations

from typing import Any, Mapping

from tournament_scheduler.booking.interval_facts import event_fingerprint


def assessment_events(
    *,
    events: list[dict[str, Any]],
    candidates_by_event: Mapping[str, list[tuple[str, dict[str, Any]]]],
    tournament_by_id: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Project the reverse event-to-tournament crosswalk, preserving raw events."""
    events_rows: list[dict[str, Any]] = []
    for event in events:
        event_fp = str(event.get("fingerprint") or event_fingerprint(event))
        club = str(event.get("club") or "")
        candidate_tournaments: list[dict[str, Any]] = []
        covered_ids: list[str] = []
        contesting_count = 0
        for tournament_id, candidate in candidates_by_event.get(event_fp, []):
            tournament = tournament_by_id[tournament_id]
            if candidate["covers_current_interval"]:
                covered_ids.append(tournament_id)
            if not candidate["season_context_only"]:
                contesting_count += 1
            candidate_tournaments.append(
                {
                    "tournament_id": tournament_id,
                    "age_group": str(tournament.get("age_group") or ""),
                    "arena": str(tournament.get("arena") or ""),
                    "date": str(tournament.get("date") or ""),
                    "start_time": str(tournament.get("start_time") or ""),
                    "relation": candidate["relation"],
                    "date_delta_days": candidate["date_delta_days"],
                    "covers_current_interval": candidate["covers_current_interval"],
                    "age_group_conflict": candidate["age_group_conflict"],
                    "arena_mismatch": candidate["arena_mismatch"],
                    "source_trusted": candidate["source_trusted"],
                    "actionable": candidate["actionable"],
                    "season_context_only": candidate["season_context_only"],
                }
            )
        candidate_tournaments.sort(key=lambda item: (item["date"], item["start_time"], item["tournament_id"]))
        events_rows.append(
            {
                "event_fingerprint": event_fp,
                "club": club,
                "date": str(event.get("date") or ""),
                "start": str(event.get("start") or ""),
                "end": str(event.get("end") or ""),
                "title": str(event.get("calendar_event") or event.get("title") or ""),
                "availability": str(event.get("availability") or ""),
                "candidate_count": len(candidate_tournaments),
                "candidate_tournaments": candidate_tournaments,
                "covered_tournament_ids": sorted(covered_ids),
                "one_to_many": contesting_count > 1,
                "group_booking": len(covered_ids) > 1,
                "unmatched": contesting_count == 0,
            }
        )
    events_rows.sort(key=lambda row: (row["club"], row["date"], row["start"], row["end"], row["event_fingerprint"]))
    return events_rows
