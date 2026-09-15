from datetime import date

from tournament_scheduler.arena_conflicts import (
    arena_interval_collisions,
    find_arena_interval_collisions,
    tournament_interval,
)
from tournament_scheduler.models import Game, Team, Tournament


def _teams(age_group="U10"):
    return [
        Team(club="Jar", label="Jar", age_group=age_group),
        Team(club="Jutul", label="Jutul", age_group=age_group),
    ]


def _games(age_group="U10", rounds=1):
    teams = _teams(age_group)
    return [Game(home=teams[0], away=teams[1], round_number=idx) for idx in range(1, rounds + 1)]


def _tournament(tid, start_time, *, arena="Jar Isforum", day=date(2026, 9, 5), age_group="U10", rounds=1):
    return Tournament(
        id=tid,
        date=day,
        arena=arena,
        age_group=age_group,
        teams=_teams(age_group),
        games=_games(age_group, rounds),
        start_time=start_time,
        host_club="Jar",
    )


def test_tournament_interval_uses_full_datetime_and_can_cross_midnight():
    tournament = _tournament("late", "23:30", rounds=2)

    interval = tournament_interval(tournament, {"U10": 20})

    assert interval is not None
    assert interval.start.isoformat() == "2026-09-05T23:30:00"
    # configured 20 min base ice + 2 × 5 min setup/changeover crosses midnight.
    assert interval.end.isoformat() == "2026-09-06T00:00:00"
    assert interval.interval_label == "2026-09-05 23:30–2026-09-06 00:00"


def test_adjacent_intervals_do_not_collide():
    first = _tournament("first", "10:00", rounds=1)
    second = _tournament("second", "10:25", rounds=1)

    collisions = find_arena_interval_collisions([first, second], {"U10": 20})

    assert collisions == []


def test_overlapping_intervals_collide_with_actionable_details():
    first = _tournament("first", "10:00", rounds=2)
    second = _tournament("second", "10:20", rounds=1)

    collisions = find_arena_interval_collisions([first, second], {"U10": 20})

    assert len(collisions) == 1
    collision = collisions[0]
    assert collision["arena"] == "Jar Isforum"
    assert collision["date"] == "2026-09-05"
    assert collision["tournament_id"] == "first"
    assert collision["conflicting_tournament_id"] == "second"
    assert collision["interval"] == "2026-09-05 10:00–2026-09-05 10:30"
    assert collision["conflicting_interval"] == "2026-09-05 10:20–2026-09-05 10:45"
    assert "first" in collision["message"]
    assert "second" in collision["message"]


def test_overnight_interval_collides_with_next_day_tournament():
    overnight = _tournament("overnight", "23:30", rounds=4)
    next_day = _tournament("next-day", "00:00", day=date(2026, 9, 6), rounds=1)

    collisions = find_arena_interval_collisions([overnight, next_day], {"U10": 20})

    assert len(collisions) == 1
    assert collisions[0]["tournament_id"] == "overnight"
    assert collisions[0]["conflicting_tournament_id"] == "next-day"
    assert collisions[0]["interval"] == "2026-09-05 23:30–2026-09-06 00:10"


def test_same_time_in_different_arenas_does_not_collide():
    first = _tournament("first", "10:00", arena="Jar Isforum")
    second = _tournament("second", "10:00", arena="Kongsberghallen")

    assert find_arena_interval_collisions([first, second], {"U10": 20}) == []


def test_arena_interval_collisions_accepts_prebuilt_intervals():
    first = tournament_interval(_tournament("first", "10:00"), {"U10": 20})
    second = tournament_interval(_tournament("second", "10:01"), {"U10": 20})

    collisions = arena_interval_collisions([first, second])  # type: ignore[list-item]

    assert len(collisions) == 1
