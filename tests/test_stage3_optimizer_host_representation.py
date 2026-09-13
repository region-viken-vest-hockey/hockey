"""Regression tests for issue #322's Stage 3 local-search host-representation
guard: neither a team swap nor a host move may leave a tournament's host
unrepresented when the host has an eligible registered team in the age
group."""

from __future__ import annotations

from datetime import date

from tournament_scheduler.stage3_optimizer import _Slot, _host_move_is_valid, _swap_is_valid


def _problem() -> dict:
    return {
        "teams": [
            {"club": "Jar", "label": "Jar 1", "age_group": "U10"},
            {"club": "Kongsberg", "label": "Kongsberg 1", "age_group": "U10"},
            {"club": "Ringerike", "label": "Ringerike 1", "age_group": "U10"},
            {"club": "Holmen", "label": "Holmen 1", "age_group": "U10"},
        ],
        "clubs": {
            "Jar": "Jarhallen",
            "Kongsberg": "Kongsberghallen",
            "Ringerike": "Ringerikshallen",
            "Holmen": "Holmenkollen ishall",
        },
    }


def _slot(host_club: str, team_ids: list, *, arena: str = "Arena", d: date = date(2026, 1, 5)) -> _Slot:
    return _Slot(
        tournament={},
        date=d,
        age_group="U10",
        host_club=host_club,
        parallel_games=1,
        arena=arena,
        team_ids=list(team_ids),
    )


class TestSwapGuard:
    def test_swap_removing_sole_host_representative_is_rejected(self):
        # t1 hosted by Jar with only one Jar-representing team; t2 has none
        # of the two teams to be swapped representing Jar either.
        a = _slot("Jar", [("Jar", "Jar 1", "U10"), ("Kongsberg", "Kongsberg 1", "U10")])
        b = _slot("Ringerike", [("Ringerike", "Ringerike 1", "U10"), ("Holmen", "Holmen 1", "U10")], d=date(2026, 1, 12))
        slots = [a, b]
        problem = _problem()
        # Swap a's Jar (pos 0) with b's Holmen (pos 1): would strand t1 without Jar.
        assert not _swap_is_valid(slots, 0, 0, 1, 1, None, problem, {})

    def test_swap_not_touching_the_host_representative_is_allowed(self):
        a = _slot("Jar", [("Jar", "Jar 1", "U10"), ("Kongsberg", "Kongsberg 1", "U10")])
        b = _slot("Ringerike", [("Ringerike", "Ringerike 1", "U10"), ("Holmen", "Holmen 1", "U10")], d=date(2026, 1, 12))
        slots = [a, b]
        problem = _problem()
        # Swap a's Kongsberg (pos 1, not the host) with b's Holmen (pos 1):
        # Jar stays put in t1, so representation is untouched.
        assert _swap_is_valid(slots, 0, 1, 1, 1, None, problem, {})

    def test_swap_allowed_when_incoming_team_also_represents_host(self):
        # Shared/joint registration "Jar/Kongsberg" also represents Jar, so
        # swapping the plain Jar team out for it keeps Jar represented.
        a = _slot("Jar", [("Jar", "Jar 1", "U10"), ("Kongsberg", "Kongsberg 1", "U10")])
        b = _slot(
            "Ringerike",
            [("Jar/Kongsberg", "Jar/Kongsberg Kittens", "U10"), ("Holmen", "Holmen 1", "U10")],
            d=date(2026, 1, 12),
        )
        slots = [a, b]
        problem = _problem()
        assert _swap_is_valid(slots, 0, 0, 1, 0, None, problem, {})

    def test_swap_allowed_when_host_has_no_eligible_registered_team(self):
        a = _slot("Skien", [("Jar", "Jar 1", "U10"), ("Kongsberg", "Kongsberg 1", "U10")])
        b = _slot("Ringerike", [("Ringerike", "Ringerike 1", "U10"), ("Holmen", "Holmen 1", "U10")], d=date(2026, 1, 12))
        slots = [a, b]
        problem = _problem()  # "Skien" is not a registered club at all here.
        assert _swap_is_valid(slots, 0, 0, 1, 1, None, problem, {})

    def test_guard_is_a_no_op_without_a_problem_contract(self):
        a = _slot("Jar", [("Jar", "Jar 1", "U10"), ("Kongsberg", "Kongsberg 1", "U10")])
        b = _slot("Ringerike", [("Ringerike", "Ringerike 1", "U10"), ("Holmen", "Holmen 1", "U10")], d=date(2026, 1, 12))
        slots = [a, b]
        assert _swap_is_valid(slots, 0, 0, 1, 1)


class TestHostMoveGuard:
    def test_move_to_a_host_with_no_representative_in_slot_is_rejected(self):
        slot = _slot("Jar", [("Jar", "Jar 1", "U10"), ("Kongsberg", "Kongsberg 1", "U10")])
        problem = _problem()
        assert not _host_move_is_valid(
            [slot], 0, "Ringerike", problem["clubs"], {}, None, {}, problem, {}
        )

    def test_move_to_a_host_already_represented_in_slot_is_allowed(self):
        slot = _slot("Jar", [("Jar", "Jar 1", "U10"), ("Kongsberg", "Kongsberg 1", "U10")])
        problem = _problem()
        assert _host_move_is_valid(
            [slot], 0, "Kongsberg", problem["clubs"], {}, None, {}, problem, {}
        )

    def test_move_to_a_host_with_no_eligible_registered_team_is_rejected(self):
        # issue #323 P0: unlike the swap guard, a host move has no "host
        # has no eligible team anywhere in the roster" exception -- the
        # destination must be represented by this slot's own participants,
        # full stop, regardless of roster-wide eligibility.
        slot = _slot("Jar", [("Jar", "Jar 1", "U10"), ("Kongsberg", "Kongsberg 1", "U10")])
        problem = {
            "teams": [
                {"club": "Jar", "label": "Jar 1", "age_group": "U10"},
                {"club": "Kongsberg", "label": "Kongsberg 1", "age_group": "U10"},
            ],
            "clubs": {"Jar": "Jarhallen", "Kongsberg": "Kongsberghallen", "Skien": "Skienhallen"},
        }
        assert not _host_move_is_valid([slot], 0, "Skien", problem["clubs"], {}, None, {}, problem, {})

    def test_guard_applies_even_without_a_problem_contract(self):
        # issue #323 P0: representation is derived purely from slot.team_ids,
        # so the guard is never a no-op even when no problem contract is
        # supplied -- an unrepresented host move is always rejected.
        slot = _slot("Jar", [("Jar", "Jar 1", "U10"), ("Kongsberg", "Kongsberg 1", "U10")])
        clubs = {"Jar": "Jarhallen", "Ringerike": "Ringerikshallen"}
        assert not _host_move_is_valid([slot], 0, "Ringerike", clubs, {})
