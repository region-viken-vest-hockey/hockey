"""Participant-derived host-candidate ranking for `SeasonPlanner`.

Split out of `season_planner.py` to keep that module under the file-length
guideline -- this is a single, self-contained ranking helper with no other
coupling to the rest of the build-plan loop.
"""

from __future__ import annotations

from datetime import date
from typing import Dict, List, Set, Tuple

from tournament_scheduler.host_representation import constituent_clubs as _constituent_clubs
from tournament_scheduler.models import Team


def participant_derived_host_candidates(
    planner,
    participants: List[Team],
    age_group: str,
    original_host: str,
    tournament_date: date,
    host_targets_by_age: Dict[str, Dict[str, int]],
    host_counts_by_age: Dict[str, Dict[str, int]],
) -> List[str]:
    """Rank candidate host clubs derived only from *participants* own
    physical clubs (issue #323 P0).

    Unlike the pre-#323 host ranking, this never proposes a club that
    isn't represented by the tournament's own selected participants --
    a joint/shared registration (e.g. "Jutul/Jar") expands to each of
    its physical constituent clubs via `constituent_clubs`, either of
    which is a legal host. The fairness/hosting-target ranking itself
    (original host preferred first when represented, then lowest
    actual-minus-target deficit) is unchanged from the prior
    `_ordered_host_candidates`.
    """
    candidates: List[str] = []
    seen: Set[str] = set()
    for participant in participants:
        for club in _constituent_clubs(participant.club):
            if club in seen:
                continue
            candidates.append(club)
            seen.add(club)
    if not candidates:
        return []

    target_counts = host_targets_by_age.get(age_group, {})
    actual_counts = host_counts_by_age.setdefault(age_group, {})

    def score(club: str) -> Tuple[int, int, int, int]:
        actual_minus_target = actual_counts.get(club, 0) - target_counts.get(club, 0)
        return (
            0 if club == original_host else 1,
            actual_minus_target,
            -target_counts.get(club, 0),
            candidates.index(club),
        )

    return sorted(candidates, key=score)
