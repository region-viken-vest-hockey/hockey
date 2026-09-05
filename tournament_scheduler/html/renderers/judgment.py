"""Neutral deterministic end-of-report metric summary for the season-plan report page.

Renders the same deterministic tone/scorecard as before (issue #260 Phase 4:
``html/renderers/judgment.py`` was flagged as Python acting as the authority
for subjective "rough/mixed/strong" framing and "what to fix next"
recommendations). This module now only states measured facts and the
deterministic tone classification (:func:`_score_tone`) — it does not author
an opinion about whether the plan is good enough, and it does not prescribe
a fix order. That judgment belongs to the LLM/agent controller or the
operator, not to this renderer.
"""

from __future__ import annotations

import html as _html
from typing import Any

from tournament_scheduler.models import team_key as _team_key

from ..data_computation import _RVV_CLUBS, canonical_rvv_club_name


def _score_tone(*, gate_status: str, gate_score: int, pairwise: float, diversity: float, month_balance: float, missing_hosts: list[str], spread: int) -> str:
    if gate_status == "fail" or gate_score < 70 or pairwise < 0.75 or spread >= 5:
        return "rough"
    if gate_status == "warn" or missing_hosts or pairwise < 0.9 or diversity < 0.9 or month_balance < 0.9 or spread >= 3:
        return "mixed"
    return "strong"


def analyze_opinionated_judgment(
    plan: object,
    *,
    team_game_counts: dict[str, int],
    club_stats: dict[str, dict[str, object]],
    team_travel: dict[str, int],
) -> dict[str, object]:
    """Return a structured opinionated synthesis of the plan."""
    tournaments = [t for t in getattr(plan, "tournaments", []) if not getattr(t, "cancelled", False)]
    fairness_gate = getattr(plan, "fairness_gate", {}) if isinstance(getattr(plan, "fairness_gate", {}), dict) else {}

    # Build the same duplicate-label set used by compute_team_game_counts so
    # team_key() lookups against team_game_counts resolve to the correct keys.
    _label_to_identities: dict[str, set[tuple[str, str]]] = {}
    for _t in tournaments:
        for _team in getattr(_t, "teams", []):
            _identity = (getattr(_team, "club", ""), getattr(_team, "age_group", ""))
            _label_to_identities.setdefault(_team.label, set()).add(_identity)
    _plan_duplicate_labels = {lbl for lbl, ids in _label_to_identities.items() if len(ids) > 1}

    gate_status = str(fairness_gate.get("status", "pass")).lower()
    gate_score = int(fairness_gate.get("score", 0) or 0)
    pairwise = float(getattr(plan, "pairwise_matchup_score", 0.0) or 0.0)
    diversity = float(getattr(plan, "diversity_score", 0.0) or 0.0)
    month_balance = float(getattr(plan, "month_balance_score", 0.0) or 0.0)

    host_counts: dict[str, int] = {}
    team_clubs = sorted(
        {
            canonical_rvv_club_name(team.club)
            for tournament in tournaments
            for team in getattr(tournament, "teams", [])
            if getattr(team, "club", None)
        }
    )
    tournaments_by_age: dict[str, list[object]] = {}
    for tournament in tournaments:
        age_group = str(getattr(tournament, "age_group", "") or "")
        tournaments_by_age.setdefault(age_group, []).append(tournament)
        host = canonical_rvv_club_name(getattr(tournament, "host_club", None) or "")
        if host:
            host_counts[host] = host_counts.get(host, 0) + 1
    missing_hosts = [club for club in _RVV_CLUBS if club not in host_counts]
    def _missing_host_label(club: str) -> str:
        return f"{club} (ingen lag i planen)" if club not in team_clubs else club
    top_host = ""
    top_host_count = 0
    if host_counts:
        top_host, top_host_count = max(host_counts.items(), key=lambda item: (item[1], item[0]))
    total_hosted = sum(host_counts.values())
    top_host_share = top_host_count / total_hosted if total_hosted else 0.0

    age_group_host_summaries: list[tuple[str, int, int]] = []
    age_group_game_spreads: list[tuple[str, int, int, int]] = []
    team_age_groups: dict[str, str] = {}
    for tournament in tournaments:
        for team in getattr(tournament, "teams", []):
            team_age_groups.setdefault(team.label, str(getattr(tournament, "age_group", "") or ""))
    for age_group, age_tournaments in sorted(tournaments_by_age.items()):
        age_host_counts: dict[str, int] = {}
        age_team_objs = list({id(team): team for tournament in age_tournaments for team in getattr(tournament, "teams", [])}.values())
        age_team_counts = [team_game_counts.get(_team_key(team, _plan_duplicate_labels), 0) for team in age_team_objs]
        if age_team_counts:
            age_group_game_spreads.append((age_group, min(age_team_counts), max(age_team_counts), max(age_team_counts) - min(age_team_counts)))
        for tournament in age_tournaments:
            host = canonical_rvv_club_name(getattr(tournament, "host_club", None) or "")
            if host:
                age_host_counts[host] = age_host_counts.get(host, 0) + 1
        if age_host_counts and age_tournaments:
            top_age_host, top_age_host_count = max(age_host_counts.items(), key=lambda item: (item[1], item[0]))
            age_group_host_summaries.append((age_group, top_age_host_count, len(age_tournaments)))

    counts = list(team_game_counts.values())
    spread = max(counts) - min(counts) if counts else 0
    max_team = ""
    max_games = 0
    min_team = ""
    min_games = 0
    if team_game_counts:
        max_team, max_games = max(team_game_counts.items(), key=lambda item: (item[1], item[0]))
        min_team, min_games = min(team_game_counts.items(), key=lambda item: (item[1], item[0]))

    farthest_team = ""
    farthest_km = 0
    farthest_age_group = ""
    if team_travel:
        farthest_team, farthest_km = max(team_travel.items(), key=lambda item: (item[1], item[0]))
        farthest_age_group = next((age_group for label, age_group in team_age_groups.items() if label == farthest_team), "")

    busiest_club = ""
    busiest_club_load = 0
    if club_stats:
        busiest_club, busiest_club_load = max(
            (
                (club, int(stats.get("hosted", 0) or 0) + int(stats.get("away", 0) or 0))
                for club, stats in club_stats.items()
            ),
            key=lambda item: (item[1], item[0]),
        )

    tone = _score_tone(
        gate_status=gate_status,
        gate_score=gate_score,
        pairwise=pairwise,
        diversity=diversity,
        month_balance=month_balance,
        missing_hosts=missing_hosts,
        spread=spread,
    )
    tone_label = {
        "strong": "SOLID",
        "mixed": "BLANDET",
        "rough": "IKKE KLAR",
    }[tone]

    # Neutral deterministic status/metric summary (issue #260 Phase 4):
    # state what the thresholds measured, not a first-person opinion about
    # whether the plan is good enough to send, and no recommended fix order —
    # that judgment belongs to the LLM/agent controller (or the operator),
    # not to this renderer. `tone`/`tone_label` remain the one deterministic
    # classification (see `_score_tone`); everything below only reports the
    # facts that fed it.
    verdict = f"Status: {tone_label}."

    matchup_bucket = "bredt" if (pairwise >= 0.9 and diversity >= 0.9) else "moderat bredt" if pairwise >= 0.8 else "snevert"
    matchup_text = f"Motstanderbilde: {int(round(pairwise * 100))}% nye møtepar ({matchup_bucket})."

    if age_group_game_spreads:
        age_spread_text = ", ".join(
            f"{age_group} {min_games}–{max_games}"
            for age_group, min_games, max_games, spread_value in sorted(age_group_game_spreads, key=lambda item: (item[3], item[0]), reverse=True)[:3]
        )
    else:
        age_spread_text = ""

    load_bucket = "jevn" if spread <= 1 else "akseptabel" if spread <= 2 else "ujevn"
    load_text = (
        f"Kampbelastning ({load_bucket}): {max_team or 'ingen lag'} har {max_games} kamper, "
        f"{min_team or 'ingen lag'} har {min_games}, spredning {spread}."
    )
    if age_spread_text:
        load_text += f" Størst spredning per aldersgruppe: {age_spread_text}."

    if missing_hosts:
        hosting_text = f"{len(missing_hosts)} RVV-klubb(er) uten hjemmeturnering: {', '.join(_missing_host_label(club) for club in missing_hosts)}."
        if busiest_club:
            hosting_text += f" Høyest totalbelastning: {busiest_club} ({busiest_club_load} roller)."
    elif top_host_share >= 0.4:
        hosting_text = f"Hjemmeturneringer mest konsentrert hos {top_host}: {top_host_count} av {total_hosted}."
        if busiest_club:
            hosting_text += f" Høyest totalbelastning: {busiest_club} ({busiest_club_load} roller)."
    else:
        hosting_text = "Hjemmeturneringer er balansert på tvers av klubber."
    if age_group_host_summaries:
        top_host_groups = ", ".join(
            f"{age_group} {top_count}/{total_count}"
            for age_group, top_count, total_count in sorted(
                age_group_host_summaries,
                key=lambda item: (item[1] / item[2] if item[2] else 0.0, item[1], item[0]),
                reverse=True,
            )[:3]
        )
        hosting_text += f" Høyest konsentrasjon per aldersgruppe: {top_host_groups}."

    if team_travel and farthest_km:
        travel_age = f" ({farthest_age_group})" if farthest_age_group else ""
        travel_text = f"Lengst total reise: {farthest_team}{travel_age}, {farthest_km} km."
    else:
        travel_text = "Ingen fremtredende reiseavvik registrert."

    # No prescribed fix order here — that is a contextual tradeoff for the
    # LLM/agent controller or operator to decide, not a Python policy. Point
    # at the deterministic capability that lists concrete findings instead.
    action_text = "Se `rvv-miniputt critic` for konkrete funn og forslag til justeringer."

    cards = [
        ("Matchup", matchup_text),
        ("Belastning", load_text),
        ("Hjemmeturneringer", hosting_text),
        ("Reise", travel_text),
    ]

    return {
        "tone": tone,
        "tone_label": tone_label,
        "verdict": verdict,
        "action_text": action_text,
        "cards": cards,
    }


def render_judgment_cards_html(cards: list[tuple[str, str]]) -> str:
    """Render the judgment cards behind an optional expandable summary."""
    if not cards:
        return ""
    card_html = "".join(
        f'<article class="judgment-card"><span>{_html.escape(label)}</span><p>{_html.escape(text)}</p></article>'
        for label, text in cards
    )
    return (
        '<details class="judgment-toggle">'
        '<summary class="judgment-toggle-summary">Vis hvorfor</summary>'
        f'<div class="judgment-grid judgment-grid--hero">{card_html}</div>'
        '</details>'
    )
