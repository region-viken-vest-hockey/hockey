"""Canonical club-level opponent diversity and exposure analysis.

Opponent identity is a club *within an age group*. Individual squad labels
("Frisk Asker 1", "Frisk Asker 2", ...) are an administrative detail, not a
sporting distinction: a squad that faces "Frisk Asker 1" and a squad that
faces "Frisk Asker 2" have both faced the same opposing club. Earlier
per-team consequence analysis keyed opponent repetition on the exact
``(club, label, age_group)`` triple, so a repair that merely re-labelled
which Frisk Asker squad an opponent met could be blocked as if it had added
a whole new opponent. This module is the single place that decides what
"the same opponent" means so the operator gate, the season scorer and the
Stage 3 objective can all agree.

Two encounter measures are kept explicitly separate and are never mixed:

``games``
    Actual scheduled games (``tournament["games"]``). This is authoritative
    whenever game records are resolvable. A limited-round tournament in
    which two participants never meet contributes no encounter between them.
``co_attendance``
    Participants sharing a tournament without generated game records. This
    is a *planning proxy* only and is reported under this explicit name; it
    must never be presented as a played game.

``measure="auto"`` selects ``games`` only when *every* subject tournament
with more than one participant carries a resolvable game record; a plan with
partial game coverage falls back to ``co_attendance`` so that opponents in
participant-only tournaments are never silently dropped. The chosen
``game_coverage`` (``complete``/``partial``/``none``) is reported so a
consumer comparing two plans can detect a coverage change. In ``games`` mode
only actual games count, even if some tournaments in the plan also list
participants without games.

Diversity is measured per *individual squad*, never aggregated across all
squads of the subject's club, so "Tønsberg Grønn repeatedly faces Frisk
Asker while Tønsberg Grå faces other clubs" stays visible instead of being
hidden by a balanced Tønsberg-wide total.

Exposure is opportunity-aware. For each opposing club the module reports
the raw club-level encounter count, the club's share of the subject's
opponent encounters, and an ``exposure_index``::

    exposure_index = observed_share / expected_share
    expected_share = opposing_club_squads / total_opposing_squads

A club that supplies many squads is therefore expected to be met more
often and is not penalized merely for being available; a subject that meets
one club far more often than its squad supply predicts still shows an index
well above 1.0. Same-club siblings are reported separately and excluded
from opposing-club diversity and exposure. Guest teams are excluded
entirely.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from tournament_scheduler.guest_slots import is_guest_team

SquadIdentity = Tuple[str, str, str]  # (club, label, age_group)
ClubIdentity = Tuple[str, str]  # (club, age_group)

MEASURE_GAMES = "games"
MEASURE_CO_ATTENDANCE = "co_attendance"
_DEFAULT_MEASURE = "auto"


def squad_identity(team: Mapping[str, Any], fallback_age_group: str = "") -> SquadIdentity:
    """Return the canonical ``(club, label, age_group)`` identity for *team*."""

    return (
        str(team.get("club") or ""),
        str(team.get("label") or ""),
        str(team.get("age_group") or fallback_age_group),
    )


def club_identity(identity: SquadIdentity) -> ClubIdentity:
    """Return the ``(club, age_group)`` opponent identity for a squad identity."""

    return (str(identity[0]), str(identity[2]))


@dataclass(frozen=True)
class ClubOpponentRecord:
    """One opposing club's encounters with one subject squad within an age group."""

    club: str
    age_group: str
    encounters: int
    squad_encounters: int
    available_tournaments: int
    opponent_squads: int
    exposure_share: float
    expected_share: float
    exposure_index: float

    def to_dict(self) -> Dict[str, Any]:
        return {
            "club": self.club,
            "age_group": self.age_group,
            "encounters": self.encounters,
            "squad_encounters": self.squad_encounters,
            "available_tournaments": self.available_tournaments,
            "opponent_squads": self.opponent_squads,
            "exposure_share": self.exposure_share,
            "expected_share": self.expected_share,
            "exposure_index": self.exposure_index,
        }


@dataclass(frozen=True)
class SquadOpponentRecord:
    """Exact squad-level repetition retained for diagnostics, not blocking."""

    identity: SquadIdentity
    encounters: int

    def to_dict(self) -> Dict[str, Any]:
        return {
            "club": self.identity[0],
            "label": self.identity[1],
            "age_group": self.identity[2],
            "encounters": self.encounters,
        }


@dataclass(frozen=True)
class OpponentDiversity:
    """Canonical club-level opponent facts for one individual subject squad."""

    identity: SquadIdentity
    measure: str
    game_coverage: str
    tournament_count: int
    distinct_clubs: int
    total_opponent_encounters: int
    same_club_encounters: int
    clubs: Tuple[ClubOpponentRecord, ...]
    squads: Tuple[SquadOpponentRecord, ...]

    def club_record(self, club: str) -> Optional[ClubOpponentRecord]:
        for record in self.clubs:
            if record.club == club:
                return record
        return None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "team": {
                "club": self.identity[0],
                "label": self.identity[1],
                "age_group": self.identity[2],
            },
            "measure": self.measure,
            "game_coverage": self.game_coverage,
            "tournament_count": self.tournament_count,
            "distinct_clubs": self.distinct_clubs,
            "total_opponent_encounters": self.total_opponent_encounters,
            "same_club_encounters": self.same_club_encounters,
            "club_counts": [record.to_dict() for record in self.clubs],
            "exact_squad_counts": [record.to_dict() for record in self.squads],
        }


def _participants(tournament: Mapping[str, Any], age_group: str) -> List[SquadIdentity]:
    identities: List[SquadIdentity] = []
    for team in tournament.get("teams", []) or []:
        if is_guest_team(team):
            continue
        identity = squad_identity(team, age_group)
        if identity[1]:
            identities.append(identity)
    return identities


def _resolvable_games(
    tournament: Mapping[str, Any], age_group: str
) -> List[Tuple[SquadIdentity, SquadIdentity]]:
    """Return resolvable ``(home, away)`` game identities, or ``[]``."""

    identity_by_label = {
        identity[1]: identity for identity in _participants(tournament, age_group)
    }
    games: List[Tuple[SquadIdentity, SquadIdentity]] = []
    for game in tournament.get("games", []) or []:
        home = identity_by_label.get(str(game.get("home")))
        away = identity_by_label.get(str(game.get("away")))
        if home is not None and away is not None:
            games.append((home, away))
    return games


def _subject_age_group(plan: Mapping[str, Any], identity: SquadIdentity) -> str:
    if identity[2]:
        return identity[2]
    for tournament in plan.get("tournaments", []) or []:
        if tournament.get("cancelled"):
            continue
        if identity in _participants(tournament, str(tournament.get("age_group") or "")):
            return str(tournament.get("age_group") or "")
    return ""


def _game_coverage(
    subject_tournaments: Sequence[Mapping[str, Any]], age_group: str
) -> str:
    """Classify how completely the subject's tournaments carry game records."""

    eligible = [
        tournament
        for tournament in subject_tournaments
        if len(_participants(tournament, age_group)) > 1
    ]
    if not eligible:
        return "none"
    with_games = sum(1 for tournament in eligible if _resolvable_games(tournament, age_group))
    if with_games == len(eligible):
        return "complete"
    if with_games == 0:
        return "none"
    return "partial"


def _selected_measure(
    subject_tournaments: Sequence[Mapping[str, Any]],
    age_group: str,
    measure: str,
) -> Tuple[str, str]:
    """Return the chosen measure and its game coverage.

    ``auto`` uses ``games`` only under *complete* game coverage; partial
    coverage falls back to the co-attendance proxy so participant-only
    tournaments are not silently ignored.
    """

    if measure not in {_DEFAULT_MEASURE, MEASURE_GAMES, MEASURE_CO_ATTENDANCE}:
        raise ValueError(f"Unknown opponent-diversity measure: {measure!r}")
    coverage = _game_coverage(subject_tournaments, age_group)
    if measure == MEASURE_GAMES:
        return MEASURE_GAMES, coverage
    if measure == MEASURE_CO_ATTENDANCE:
        return MEASURE_CO_ATTENDANCE, coverage
    return (
        MEASURE_GAMES if coverage == "complete" else MEASURE_CO_ATTENDANCE
    ), coverage


def compute_opponent_diversity(
    plan: Mapping[str, Any],
    identity: SquadIdentity,
    *,
    measure: str = _DEFAULT_MEASURE,
) -> OpponentDiversity:
    """Compute canonical club-level opponent diversity for one subject *identity*.

    *plan* is any mapping with a ``tournaments`` list (a candidate, a canonical
    plan payload or a Stage 3 checkpoint plan). *identity* is the individual
    squad whose own schedule is being evaluated.
    """

    subject = tuple(identity)
    subject_club, _, age_group = subject
    age_group = _subject_age_group(plan, subject) or age_group

    all_tournaments = [
        tournament
        for tournament in plan.get("tournaments", []) or []
        if not tournament.get("cancelled")
    ]
    age_group_tournaments = [
        tournament
        for tournament in all_tournaments
        if str(tournament.get("age_group") or "") == age_group
    ]

    club_squads: Dict[str, set] = {}
    club_available_tournaments: Counter = Counter()
    for tournament in age_group_tournaments:
        clubs_here: set = set()
        for participant in _participants(tournament, age_group):
            if participant[0] == subject_club:
                continue
            club_squads.setdefault(participant[0], set()).add(participant)
            clubs_here.add(participant[0])
        for club in clubs_here:
            club_available_tournaments[club] += 1
    total_opposing_squads = sum(len(squads) for squads in club_squads.values())

    subject_tournaments = [
        tournament
        for tournament in age_group_tournaments
        if subject in _participants(tournament, age_group)
    ]
    resolved_measure, game_coverage = _selected_measure(subject_tournaments, age_group, measure)

    club_encounters: Counter = Counter()
    club_squad_encounters: Counter = Counter()
    squad_encounters: Counter = Counter()
    same_club_encounters = 0

    for tournament in subject_tournaments:
        if resolved_measure == MEASURE_GAMES:
            games = _resolvable_games(tournament, age_group)
            for home, away in games:
                if subject == home:
                    opponent = away
                elif subject == away:
                    opponent = home
                else:
                    continue
                squad_encounters[opponent] += 1
                if opponent[0] == subject_club:
                    same_club_encounters += 1
                else:
                    club_encounters[opponent[0]] += 1
                    club_squad_encounters[opponent[0]] += 1
        else:
            participants = _participants(tournament, age_group)
            seen_clubs: set = set()
            for opponent in participants:
                if opponent == subject:
                    continue
                squad_encounters[opponent] += 1
                if opponent[0] == subject_club:
                    same_club_encounters += 1
                    continue
                club_squad_encounters[opponent[0]] += 1
                if opponent[0] not in seen_clubs:
                    seen_clubs.add(opponent[0])
                    club_encounters[opponent[0]] += 1

    total_opponent_encounters = sum(club_encounters.values())
    distinct_clubs = sum(1 for value in club_encounters.values() if value > 0)

    club_records: List[ClubOpponentRecord] = []
    for club in sorted(club_squads):
        encounters = int(club_encounters.get(club, 0) or 0)
        opponent_squads = len(club_squads[club])
        exposure_share = (encounters / total_opponent_encounters) if total_opponent_encounters else 0.0
        expected_share = (opponent_squads / total_opposing_squads) if total_opposing_squads else 0.0
        exposure_index = (exposure_share / expected_share) if expected_share else 0.0
        club_records.append(
            ClubOpponentRecord(
                club=club,
                age_group=age_group,
                encounters=encounters,
                squad_encounters=int(club_squad_encounters.get(club, 0) or 0),
                available_tournaments=int(club_available_tournaments.get(club, 0) or 0),
                opponent_squads=opponent_squads,
                exposure_share=round(exposure_share, 6),
                expected_share=round(expected_share, 6),
                exposure_index=round(exposure_index, 6),
            )
        )

    squad_records = tuple(
        SquadOpponentRecord(identity=opponent, encounters=count)
        for opponent, count in sorted(
            squad_encounters.items(), key=lambda item: (-item[1], item[0])
        )
    )

    return OpponentDiversity(
        identity=subject,
        measure=resolved_measure,
        game_coverage=game_coverage,
        tournament_count=len(subject_tournaments),
        distinct_clubs=distinct_clubs,
        total_opponent_encounters=total_opponent_encounters,
        same_club_encounters=same_club_encounters,
        clubs=tuple(club_records),
        squads=squad_records,
    )


__all__ = [
    "ClubIdentity",
    "ClubOpponentRecord",
    "MEASURE_CO_ATTENDANCE",
    "MEASURE_GAMES",
    "OpponentDiversity",
    "SquadIdentity",
    "SquadOpponentRecord",
    "club_identity",
    "compute_opponent_diversity",
    "squad_identity",
]
