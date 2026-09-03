"""Data models for tournament scheduling."""

from dataclasses import dataclass, field
from datetime import datetime, date, timedelta
from typing import Collection, List, Dict, Set, Optional, Tuple
import uuid


@dataclass
class CalendarEvent:
    """Represents a calendar event with scheduling information."""

    date: str  # Format: DD.MM.YYYY
    name: str
    datetime: datetime
    duration_hours: float = 0.0
    location: str = ""  # Raw LOCATION field from the source calendar


@dataclass
class TournamentInfo:
    """Information about a tournament extracted from Excel."""

    date: date
    name: str
    teams: List[str]
    location: Optional[str] = None


@dataclass
class ConflictContext:
    """Context containing all data needed for conflict checking."""

    start_date: datetime
    end_date: datetime
    team_names: List[str] = field(default_factory=list)
    calendar_events: List[CalendarEvent] = field(default_factory=list)
    excel_dates: Set[date] = field(default_factory=set)
    tournament_to_reschedule: Optional[date] = None


@dataclass
class ConflictResult:
    """Result of a conflict check operation."""

    excluded_dates: Set[date]
    reasons: Dict[date, str]
    checker_name: str


@dataclass
class SchedulingResult:
    """Complete result of a scheduling operation."""

    available_dates: List[date]
    excluded_dates: List[date]
    exclusion_breakdown: Dict[str, int]
    detailed_exclusions: List[Tuple[date, str]]
    total_weekends_checked: int
    tournament_info: Optional[TournamentInfo] = None


@dataclass
class Team:
    """A single team belonging to a club, in a specific age group.

    Example: Team(club="Jar", label="Jar 1", age_group="U10")

    """

    club: str
    label: str  # e.g. "Jar 1", "Jar 2"
    age_group: str  # e.g. "U10", "JU11"
    target_tournament_count: Optional[int] = None  # per-team override for the tournament-count target (None = use default)

    @property
    def name(self) -> str:
        """Display name for the team (defaults to its label)."""
        return self.label


def team_key(team: Team, duplicate_labels: Collection[str] | None = None) -> str:
    """Return a stable display/storage key for *team*.

    Most rosters use unique labels, so the plain label is returned. When a
    label appears multiple times in the roster, the key is disambiguated
    with club and age group so per-team metrics stay unique.
    """
    if duplicate_labels and team.label in duplicate_labels:
        return f"{team.label} ({team.club}, {team.age_group})"
    return team.label


@dataclass
class Roster:
    """An ordered collection of teams participating in the season plan."""

    teams: List[Team] = field(default_factory=list)

    def by_age_group(self, age_group: str) -> List[Team]:
        """Return all teams belonging to the given age group, in roster order."""
        return [team for team in self.teams if team.age_group == age_group]

    def age_groups(self) -> List[str]:
        """Return the distinct age groups present in the roster, in first-seen order."""
        seen: List[str] = []
        for team in self.teams:
            if team.age_group not in seen:
                seen.append(team.age_group)
        return seen

    def clubs(self) -> List[str]:
        """Return the distinct club names present in the roster, in first-seen order."""
        seen: List[str] = []
        for team in self.teams:
            if team.club not in seen:
                seen.append(team.club)
        return seen


@dataclass
class Game:
    """A single round-robin game within a tournament."""

    home: Team
    away: Team
    parallel_slot: int = 0  # which parallel timeslot/sheet this game is played in
    round_number: int = 0  # which round of the round-robin this game belongs to (1-based)


@dataclass
class Tournament:
    """A single weekend tournament: one age group/gender, hosted at one arena.

    Holds the ordered list of participating teams and the round-robin game
    schedule generated for them (every participating team plays every other
    participating team once).
    """

    date: date
    arena: str  # host club's home arena, e.g. "Jarhallen"
    age_group: str  # e.g. "U10", "JU11" — one age group/gender per tournament
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    teams: List[Team] = field(default_factory=list)
    games: List[Game] = field(default_factory=list)
    host_club: Optional[str] = None
    cancelled: bool = False
    cancellation_reason: Optional[str] = None
    start_time: Optional[str] = None  # HH:MM string, e.g. "09:00"
    preferanse_vekt: float = 0.0  # subjective preference weight (0.0 = neutral, >0 preferred, <0 avoid)
    scoring_weight_term: float = 0.0  # capped subjective weight term actually applied during scoring
    # Non-empty when this tournament is hosted by a club whose calendar source
    # could not be scraped (blocked/failed), so the assigned start time is a
    # provisional placeholder: hall time must be booked/verified by hand. The
    # tournament still counts towards the club's normal share of tournaments.
    manual_booking_reason: Optional[str] = None

    def duration_minutes(self, round_length: int) -> int:
        """Return the total tournament play time in minutes.

        Computed as ``round_length`` (minutes per round) times the number of
        rounds in the round-robin schedule. Returns 0 if there are no games.
        """
        if not self.games:
            return 0
        max_round = max(g.round_number for g in self.games)
        return round_length * max_round

    def matchday_duration_minutes(self, round_length: int, setup_buffer_minutes: int = 5) -> int:
        """Return the full hall occupancy for the tournament.

        This includes the round-robin play time plus a setup/changeover buffer
        after each round.
        """
        if not self.games:
            return 0
        max_round = max(g.round_number for g in self.games)
        from tournament_scheduler.utils.slot_finder import matchday_duration_minutes as _matchday_duration_minutes

        return _matchday_duration_minutes(round_length, max_round, setup_buffer_minutes)

    def end_time(self, round_length: int) -> Optional[str]:
        """Return the computed end time as an HH:MM string.

        Computed by adding :meth:`duration_minutes` to :attr:`start_time`.
        Returns ``None`` if ``start_time`` is unset.
        """
        if not self.start_time:
            return None
        start = datetime.strptime(self.start_time, "%H:%M")
        end = start + timedelta(minutes=self.duration_minutes(round_length))
        return end.strftime("%H:%M")

    def get_bye_rounds(self) -> Dict[int, List[str]]:
        """Return a mapping of round_number -> list of team labels with a bye.

        A team has a bye in a round if it participates in the tournament but
        does not appear as either home or away in any game for that round.
        This only happens with an odd number of teams; even-sized
        tournaments return an empty dict.
        """
        bye_map: Dict[int, List[str]] = {}
        if len(self.teams) % 2 == 0 or not self.games:
            return bye_map

        max_round = max(g.round_number for g in self.games)
        for r in range(1, max_round + 1):
            playing = {
                g.home.label for g in self.games if g.round_number == r
            } | {g.away.label for g in self.games if g.round_number == r}
            byes = [t.label for t in self.teams if t.label not in playing]
            if byes:
                bye_map[r] = byes
        return bye_map


@dataclass
class SeasonPlan:
    """A full proposed season plan: an ordered sequence of tournaments plus metadata."""

    tournaments: List[Tournament] = field(default_factory=list)
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    # Opponent-variety score: for each team that has played at least one
    # game this season, the fraction of its eligible opponents (other teams
    # in the same age group, excluding its own club) that it has actually
    # faced, averaged across all such teams (1.0 = every team has played
    # every eligible opponent at least once; lower values indicate teams
    # repeatedly facing a narrow set of opponents). Distinct from
    # `pairwise_matchup_score`, which measures the fraction of *games* that
    # are first-time pairings rather than opponent-pool coverage per team.
    diversity_score: float = 0.0
    # Fraction of scheduled matchups (pairwise team-vs-team games) that are
    # first-time pairings, grounded in actual `_opponent_history` counts
    # rather than mere tournament co-attendance (1.0 = every scheduled game
    # is a fresh matchup; lower values indicate more repeat matchups).
    pairwise_matchup_score: float = 0.0
    # How evenly tournaments are spread across the season's months: 1.0
    # means every month carries exactly its expected share of the season's
    # tournament load; lower values indicate more uneven month-to-month
    # distribution (derived from `_month_counts` vs. the expected average).
    month_balance_score: float = 0.0
    # Maps arena/host-club name -> number of tournaments scheduled there
    arena_counts: Dict[str, int] = field(default_factory=dict)
    # Maps team label -> total number of round-robin games played across
    # the entire season (computed by SeasonPlanner.build_plan).
    team_game_counts: Dict[str, int] = field(default_factory=dict)
    # Difference between the team with the most games and the team with
    # the fewest games (max - min of team_game_counts values).
    game_count_spread: int = 0
    # Per-age-group game count spread: same metric as game_count_spread but
    # computed independently for each age group so that the critic can flag
    # fairness issues without cross-age-group noise.
    # Keys are age-group strings (e.g. "U10"); values are the max-min spread
    # for teams within that age group.
    game_count_spread_by_age_group: Dict[str, int] = field(default_factory=dict)
    # Structured fairness gate summarising pass/warn/fail status for the
    # main roster-based season fairness metrics.
    fairness_gate: Dict[str, object] = field(default_factory=dict)
    # Maps team label -> date of their last round-robin game in the season.
    # Used for early-finish detection (teams that finish weeks before others).
    team_last_game_dates: Dict[str, date] = field(default_factory=dict)
    # Age groups intentionally skipped because they have fewer than
    # MIN_TEAMS_PER_TOURNAMENT configured teams. Each entry is a dict:
    # {"age_group": str, "team_count": int, "reason": str}.
    skipped_age_groups: List[Dict[str, object]] = field(default_factory=list)
    # Manual operator adjustments preserved across checkpoint round-trips.
    # Keys are small string lists such as locked_dates, banned_dates,
    # forced_host_clubs, excluded_host_clubs, and pinned_tournament_ids.
    manual_adjustments: Dict[str, list[str]] = field(default_factory=dict)
    # Same-arena same-day collisions that could not be avoided while
    # assigning hosts. Each entry is a small dict with date/arena/age-group
    # details for reporting.
    arena_day_collisions: List[Dict[str, str]] = field(default_factory=list)
    # Global date-range preferences that were active during planning.
    # Each entry mirrors a DatePreference: {"fra": ISO date, "til": ISO date, "vekt": float}.
    # Populated from the Datopreferanser sheet; empty when no preferences were configured.
    date_preference_weights: List[Dict[str, object]] = field(default_factory=list)


# Mapping of age groups whose player pools are known to overlap (e.g. a player
# might play in both groups). The planner should avoid scheduling tournaments
# for overlapping age groups on the same weekend to prevent double-booking.
# The mapping is symmetric — each key's value list should also list the key
# back, so lookups work in either direction.
AGE_GROUP_OVERLAP: Dict[str, List[str]] = {
    "U10": ["JU11"],
    "JU11": ["U10"],
    "U11": ["JU12"],
    "JU12": ["U11"],
    "U12": ["JU13"],
    "JU13": ["U12"],
}


def overlapping_age_groups(age_group: str) -> List[str]:
    """Return the list of age groups whose player pools overlap with the given one."""
    return AGE_GROUP_OVERLAP.get(age_group, [])


@dataclass
class DatePreference:
    """A global date-range scoring preference from the Datopreferanser sheet.

    Positive ``vekt`` penalises dates within the range (e.g. Easter weekend);
    negative ``vekt`` rewards them (e.g. a preferred hosting window).
    Ranges are inclusive on both ends.
    """

    fra: date   # range start (inclusive)
    til: date   # range end (inclusive)
    vekt: float = 0.0  # preference weight: >0 penalise, <0 reward, 0 neutral
