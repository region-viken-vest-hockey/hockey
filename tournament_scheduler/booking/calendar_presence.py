"""Whether a club's external calendar evidences its tournament bookings (#853).

A club profile may declare that its calendar shows standing ice use but not its
tournament bookings. That is a documented source limitation: a generic ice-use
block is then not proof of a tournament's identity, and an absent event is not
proof of an absent booking. The fact is advisory; it never changes booking
authority or status.

Known limit: a tournament moved to a new date with only a generic calendar
entry cannot be proven from that entry. Its accepted manual confirmation stays
authoritative until a separately accepted fact supersedes it.
"""

from __future__ import annotations

from tournament_scheduler.clubs.profiles import get_club_profile


def calendar_publishes_tournament_bookings(club: str) -> bool | None:
    """Return whether *club*'s calendar lists its tournament bookings, or ``None`` if undeclared."""

    profile = get_club_profile(club)
    presence = profile.calendar_booking_presence if profile is not None else None
    return None if presence is None else presence.publishes_tournament_bookings


def calendar_omits_tournament_bookings(club: str) -> bool:
    """Return whether *club* has a documented limitation: its calendar omits its tournament bookings."""

    return calendar_publishes_tournament_bookings(club) is False
