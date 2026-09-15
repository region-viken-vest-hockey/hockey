from tournament_scheduler.effective_tournament_shape import compute_effective_tournament_shape, shape_violation


def test_full_pool_supports_preferred_shape_with_capacity_cap():
    shape = compute_effective_tournament_shape("U10", 8, configured_rounds=5, parallel_game_capacity=3)
    assert not shape.input_constrained
    assert shape.preferred_no_bye_team_count == 6
    assert shape.effective_team_count == 6
    assert shape.effective_round_count == 5
    assert shape.unavoidable_bye_count == 0


def test_exact_pool_matches_preferred_shape():
    shape = compute_effective_tournament_shape("U10", 6, configured_rounds=5)
    assert not shape.input_constrained
    assert shape.effective_team_count == 6
    assert shape.effective_round_count == 5
    assert shape.unavoidable_bye_count == 0


def test_odd_registered_pool_adapts_with_unavoidable_rests():
    shape = compute_effective_tournament_shape("JU10", 5, configured_rounds=5)
    assert shape.input_constrained
    assert shape.reason == "registered_pool_too_small"
    assert shape.effective_team_count == 5
    assert shape.effective_round_count == 5
    assert shape.unavoidable_bye_count == 5


def test_small_even_pool_adapts_round_count_down():
    shape = compute_effective_tournament_shape("JU8", 4, configured_rounds=5)
    assert shape.input_constrained
    assert shape.effective_team_count == 4
    assert shape.effective_round_count == 3
    assert shape.unavoidable_bye_count == 0


def test_u12_exact_four_unchanged_when_pool_sufficient():
    shape = compute_effective_tournament_shape("U12", 8)
    assert not shape.input_constrained
    assert shape.preferred_no_bye_team_count == 4
    assert shape.effective_team_count == 4


def test_u12_exact_four_adapts_when_pool_too_small():
    shape = compute_effective_tournament_shape("JU12", 3)
    assert shape.input_constrained
    assert shape.effective_team_count == 3
    assert shape.effective_round_count == 3
    assert shape.unavoidable_bye_count == 3


def test_unconfigured_rounds_has_no_underfill_floor():
    # No configured round count and no exact-size override: a season spreads
    # its registered pool across many tournament instances, so there is no
    # single-instance target -- any even subset stays fully legal.
    shape = compute_effective_tournament_shape("U10", 10)
    assert not shape.has_explicit_target
    assert not shape.input_constrained
    assert shape.effective_team_count == 10


def test_unconfigured_rounds_odd_full_pool_is_input_constrained():
    shape = compute_effective_tournament_shape("U10", 3)
    assert not shape.has_explicit_target
    assert shape.input_constrained
    assert shape.effective_team_count == 3
    assert shape.effective_round_count == 3
    assert shape.unavoidable_bye_count == 3


def test_registered_pool_below_two_is_not_a_tournament():
    shape = compute_effective_tournament_shape("U10", 1, configured_rounds=5)
    assert shape.input_constrained
    assert shape.reason == "registered_pool_too_small_for_tournament"
    assert shape.effective_team_count == 1
    assert shape.effective_round_count == 0
    assert shape.unavoidable_bye_count == 0


class TestShapeViolationWithoutExplicitTarget:
    """No configured rounds/exact override: a season draws a subset of the
    registered pool per instance, so underfill alone is never avoidable --
    only an odd count that could have been made even is."""

    def test_small_even_subset_of_a_large_pool_is_not_a_violation(self):
        shape = compute_effective_tournament_shape("U11", 6)
        assert not shape_violation(shape, 2, 0)

    def test_odd_full_pool_is_not_a_violation(self):
        shape = compute_effective_tournament_shape("U10", 3)
        assert not shape_violation(shape, 3, 0)

    def test_odd_subset_smaller_than_the_full_pool_is_a_violation(self):
        shape = compute_effective_tournament_shape("U10", 8)
        assert shape_violation(shape, 3, 0)
