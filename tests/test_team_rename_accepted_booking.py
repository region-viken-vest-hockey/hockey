"""A label-only rename is not blocked by an accepted short calendar booking (#572, #569, #429).

The 2026-2027 season has accepted calendar bookings below the 120-minute governing
floor. Those are durable, source-confirmed facts. Renaming a team identity moves no
placement and changes no booking, so the rename's own verification must classify the
unchanged accepted interval the same way the other canonical writers do, rather than
rejecting the rename on the planning floor.
"""

from __future__ import annotations

import copy
import json

import pytest

from tournament_scheduler.infrastructure.canonical_season_store import SeasonStateError
from tournament_scheduler.application.canonical_season.store_facade import (
    load_decisions,
    load_schedule,
    rename_teams,
)
from tournament_scheduler.season.ice_time_overrides import active_overrides
from tests.test_accepted_exact_duration import _accept

MAPPING = [{"club": "A", "age_group": "U10", "from_label": "A1", "to_label": "A Gul"}]


def test_identity_rename_succeeds_beside_an_accepted_short_booking(tmp_path):
    root, _problem, _event, _teams, _result = _accept(tmp_path, planned=155, booked=100)

    preview = rename_teams(
        season="2026-2027",
        root=root,
        mappings=MAPPING,
        request_id="rename-beside-accepted-booking",
        dry_run=True,
    )
    assert preview["dry_run"] is True
    assert preview["placement_neutrality"]["placement_changed_ids"] == []
    assert preview["placement_neutrality"]["tournaments_added"] == []
    assert preview["placement_neutrality"]["tournaments_removed"] == []

    applied = rename_teams(
        season="2026-2027",
        root=root,
        mappings=MAPPING,
        request_id="rename-beside-accepted-booking",
        actor="tester",
    )
    assert applied["dry_run"] is False

    schedule = load_schedule("2026-2027", root=root)
    tournament = schedule["plan"]["tournaments"][0]
    assert tournament["id"] == "rvv-0900"
    assert tournament["teams"][0] == {"club": "A", "label": "A Gul", "age_group": "U10"}
    # The accepted 100-minute booking is kept exactly; the rename neither extends nor re-approves it.
    assert active_overrides(load_decisions("2026-2027", root=root)) == {"rvv-0900": 100}


def test_identity_rename_still_refused_by_an_independent_hard_violation(tmp_path):
    root, _problem, _event, _teams, _result = _accept(tmp_path, planned=155, booked=100)
    schedule_path = root / "2026-2027" / "schedule.json"
    schedule = json.loads(schedule_path.read_text(encoding="utf-8"))
    # A second tournament on the same date and arena that reuses team A1: a genuine collision,
    # with no accepted booking evidence behind it.
    clash = copy.deepcopy(schedule["plan"]["tournaments"][0])
    clash["id"] = "rvv-0901"
    schedule["plan"]["tournaments"].append(clash)
    schedule_path.write_text(json.dumps(schedule, ensure_ascii=False, indent=2), encoding="utf-8")

    with pytest.raises(SeasonStateError, match="rvv-0901"):
        rename_teams(
            season="2026-2027",
            root=root,
            mappings=MAPPING,
            request_id="rename-with-independent-violation",
            dry_run=True,
        )
