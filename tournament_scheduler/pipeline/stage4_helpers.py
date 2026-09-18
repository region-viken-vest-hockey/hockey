"""Stage 4 export helpers."""

from __future__ import annotations

from ..models import SeasonPlan


def build_tournament_placement_entries(plan: SeasonPlan) -> list[dict[str, str]]:
    """Build manual-placement report rows for `plan.unresolved_tournament_placements`.

    issue #323 P0: a tournament whose participant set (selected first, per
    all hard constraints) had no candidate host among the participants' own
    physical clubs with a free arena/time slot -- no tournament exists to
    attach this to (the point is that nothing was placed), so
    tournament-shaped fields are left blank. The original participant set
    was never mutated to fit an unrelated host.
    """
    entries: list[dict[str, str]] = []
    for item in getattr(plan, "unresolved_tournament_placements", None) or []:
        age_group = str(item.get("age_group", "") or "")
        date_value = str(item.get("date", "") or "")
        candidate_hosts = item.get("candidate_hosts") or []
        participant_clubs = item.get("participant_clubs") or []
        participant_teams = item.get("participant_teams") or []
        # issue #330: prefer the exact roster over the deduplicated club set
        # -- "Participants: Frisk Asker, Jar" reads as a two-team tournament
        # when it may have been several Frisk/Jar teams. Older/hand-built
        # checkpoint records without `participant_teams` fall back to an
        # explicit "unknown" marker rather than guessing a team count from
        # the club list.
        if participant_teams:
            team_count = item.get("participant_team_count", len(participant_teams))
            team_labels = ", ".join(str(t.get("label") or t.get("club") or "?") for t in participant_teams)
            roster_clause = f"Team count: {team_count}. Teams: {team_labels}. "
        else:
            roster_clause = "Team count: ukjent (eldre eksportdata uten lagliste). "
        # issue #329: whether a hosting-deficit-biased alternate roster was
        # tried before giving up -- lets the operator/auditor tell "no
        # alternative composition existed at all" apart from "an alternative
        # was tried and still failed".
        if item.get("required_duration_minutes"):
            duration_clause = (
                f"Required duration: {item.get('required_duration_minutes')} min "
                f"(base ice {item.get('configured_ice_time_minutes', 'ukjent')} min + "
                f"round buffer {item.get('round_buffer_minutes', 'ukjent')} min for "
                f"{item.get('round_count', 'ukjent')} rounds). "
            )
        else:
            duration_clause = ""
        if "alternate_roster_attempted" in item:
            retry_clause = (
                "Alternativ lagsammensetning forsøkt: "
                f"{'ja' if item.get('alternate_roster_attempted') else 'nei'}. "
            )
        else:
            retry_clause = ""
        # Report the hosts and same-host dates that were actually searched,
        # not every participant-derived candidate that merely existed. Older
        # records without `search_hosts_tried` fall back to the candidate
        # list, matching the previous wording.
        searched_hosts = item.get("search_hosts_tried") or candidate_hosts
        same_host_dates_checked = item.get("same_host_dates_checked") or []
        if same_host_dates_checked:
            same_host_date_clause = (
                "Andre datoer søkt for samme vertsklubb: "
                f"{', '.join(str(d) for d in same_host_dates_checked)}. "
            )
        else:
            same_host_date_clause = ""
        responsible_host = item.get("responsible_host") or item.get("search_host")
        if responsible_host:
            responsible_host_clause = f"Responsible host: {responsible_host}. "
        else:
            responsible_host_clause = ""
        entries.append(
            {
                "type": "MANUAL PLACEMENT REQUIRED — ingen vertsklubb blant deltakerne",
                "category": str(item.get("category") or "manual_tournament_placement"),
                # Stable finding identity, independent of any tournament id:
                # an unplaced obligation has no tournament. Used by the
                # manual-work projection to keep parallel same-age-group/
                # same-date obligations as distinct operator work items.
                "finding_id": str(item.get("id") or ""),
                "date": date_value,
                "arena": "",
                # The responsible host still owes this tournament even though
                # no scheduled tournament exists, so the operator sees whose
                # obligation it is rather than an anonymous row.
                "host_club": str(responsible_host or ""),
                "age_group": age_group,
                "tournament_id": "",
                "interval": "",
                "conflicting_tournament_id": "",
                "conflicting_age_group": "",
                "conflicting_interval": "",
                "required_duration_minutes": str(item.get("required_duration_minutes") or ""),
                "configured_ice_time_minutes": str(item.get("configured_ice_time_minutes") or ""),
                "round_count": str(item.get("round_count") or ""),
                "round_buffer_minutes": str(item.get("round_buffer_minutes") or ""),
                "message": (
                    "MANUAL PLACEMENT REQUIRED. "
                    f"Age group: {age_group}. Date: {date_value or 'ukjent'}. "
                    f"Participant clubs: {', '.join(str(c) for c in participant_clubs) or 'ukjent'}. "
                    f"{roster_clause}"
                    f"{duration_clause}"
                    f"{responsible_host_clause}"
                    f"{retry_clause}"
                    f"{same_host_date_clause}"
                    f"Hosts searched: {', '.join(str(c) for c in searched_hosts) or 'ingen'}. "
                    "Reason: none of the selected participants' clubs had a legal, free arena/time slot. "
                    "Action: RVV must manually assign a host/arena/time for this age group and date."
                ),
            }
        )
    return entries


# ---------------------------------------------------------------------------
