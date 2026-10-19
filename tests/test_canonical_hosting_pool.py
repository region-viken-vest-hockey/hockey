"""Initial-pipeline planners read the canonical eligible hosting pool (#511)."""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace

from tournament_scheduler.participation.withdrawals import build_withdrawal_records
from tournament_scheduler.pipeline import makeup_date_policy
from tournament_scheduler.pipeline.makeup_date_policy import restrict_to_canonical_hosting_pool


def _teams() -> list[SimpleNamespace]:
    return [
        SimpleNamespace(club="Echo", label="Echo 1", age_group="U10"),
        SimpleNamespace(club="Echo", label="Echo 2", age_group="U12"),
        SimpleNamespace(club="Alfa", label="Alfa 1", age_group="U10"),
    ]


def _decisions(scope: str) -> dict:
    record = build_withdrawal_records(
        team={"club": "Echo", "label": "Echo 1", "age_group": "U10"},
        tournament_ids=["u10-a"],
        request_id="echo-withdrawal",
        actor="tester",
        note="withdrawn",
        created_at="2026-09-22T00:00:00+00:00",
        source_revision="rev-1",
        effective_from="2026-10-03",
    )[0]
    record["scope"] = scope
    return {"participation_withdrawals": [record]}


def _restricted_pool(state, start, end, teams):
    """Return the pool the planner was restricted to, or ``None`` when untouched."""
    calls: list = []
    planner = SimpleNamespace(restrict_hosting_pool=calls.append)
    assert restrict_to_canonical_hosting_pool(planner, state, start, end, teams) is planner
    return calls[0] if calls else None


def _patch_canonical(monkeypatch, decisions: dict | None) -> None:
    monkeypatch.setattr(makeup_date_policy, "load_effective_config", lambda state: {})
    canonical = None if decisions is None else {"decisions": decisions}
    monkeypatch.setattr(makeup_date_policy, "resolve_canonical_state", lambda config, start, end: canonical)


def test_no_canonical_season_keeps_registered_roster(monkeypatch) -> None:
    _patch_canonical(monkeypatch, None)

    assert _restricted_pool(None, date(2026, 8, 1), date(2027, 6, 1), _teams()) is None


def test_durable_age_group_withdrawal_leaves_hosting_pool(monkeypatch) -> None:
    _patch_canonical(monkeypatch, _decisions("age_group"))

    pool = _restricted_pool(None, date(2026, 8, 1), date(2027, 6, 1), _teams())

    assert {(t["club"], t["label"], t["age_group"]) for t in pool} == {
        ("Echo", "Echo 2", "U12"),
        ("Alfa", "Alfa 1", "U10"),
    }


def test_tournament_scoped_absence_keeps_team_in_hosting_pool(monkeypatch) -> None:
    _patch_canonical(monkeypatch, _decisions("tournament"))

    pool = _restricted_pool(None, date(2026, 8, 1), date(2027, 6, 1), _teams())

    assert len(pool) == 3

class _PoolPlanner:
    """Minimal stand-in for ``SeasonPlanner``'s hosting-pool surface."""

    def __init__(self, roster) -> None:
        self.roster = roster
        self._keys = None

    def restrict_hosting_pool(self, eligible_teams) -> None:
        self._keys = {(t["club"], t["label"], t["age_group"]) for t in eligible_teams}

    def hosting_pool_teams(self):
        return [t for t in self.roster.teams if self._keys is None or (t.club, t.label, t.age_group) in self._keys]


def test_rebuild_hosting_targets_exclude_durably_withdrawn_club(monkeypatch) -> None:
    """After a durable age-group withdrawal, U10 host targets come from the
    eligible pool: Echo's only U10 team has left, so Echo carries no hosting
    share, while the registered roster is unchanged (#511)."""
    from tournament_scheduler.hosting.assignment import hosting_targets_for_age_group
    from tournament_scheduler.models import Roster, Team

    registered = [Team(club=t.club, label=t.label, age_group=t.age_group) for t in _teams()]
    planner = _PoolPlanner(Roster(teams=registered))
    _patch_canonical(monkeypatch, _decisions("age_group"))

    restrict_to_canonical_hosting_pool(planner, None, date(2026, 8, 1), date(2027, 6, 1), registered)

    assert hosting_targets_for_age_group(planner, "U10", 2) == {"Alfa": 2}
    assert len(planner.roster.teams) == 3
