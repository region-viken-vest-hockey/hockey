"""Unit tests for the shared Pareto/dominance arithmetic.

Both Stage 3's multi-objective search and the promoted-season maintenance loop
delegate to :mod:`tournament_scheduler.pareto`, so the relation and the bounded
down-select are tested once here with synthetic vectors instead of only through
either application surface.
"""

from __future__ import annotations

from tournament_scheduler.pareto import (
    dominates,
    non_dominated_indices,
    representative_indices,
    vectors_equal,
)


def test_dominates_requires_weakly_better_everywhere_and_strictly_somewhere() -> None:
    assert dominates({"a": 1.0, "b": 2.0}, {"a": 2.0, "b": 2.0})
    assert dominates({"a": 1.0, "b": 1.0}, {"a": 2.0, "b": 2.0})
    # Equal vectors do not dominate each other.
    assert not dominates({"a": 1.0, "b": 2.0}, {"a": 1.0, "b": 2.0})
    # A trade-off is not a domination.
    assert not dominates({"a": 1.0, "b": 3.0}, {"a": 2.0, "b": 2.0})
    assert not dominates({"a": 2.0, "b": 2.0}, {"a": 1.0, "b": 2.0})


def test_vectors_equal_uses_tolerance() -> None:
    assert vectors_equal({"a": 1.0}, {"a": 1.0 + 1e-12})
    assert not vectors_equal({"a": 1.0}, {"a": 1.01})


def test_non_dominated_indices_drops_dominated_vectors() -> None:
    vectors = [
        {"x": 1.0, "y": 5.0},
        {"x": 2.0, "y": 4.0},
        {"x": 3.0, "y": 6.0},  # dominated by both of the above
    ]

    assert non_dominated_indices(vectors) == [0, 1]


def test_non_dominated_indices_keeps_only_the_first_exact_duplicate() -> None:
    vectors = [
        {"x": 1.0, "y": 1.0},
        {"x": 1.0, "y": 1.0},
    ]

    assert non_dominated_indices(vectors) == [0]


def test_representative_indices_bounds_a_large_front_and_keeps_extremes() -> None:
    vectors = [
        {"x": 1.0, "y": 4.0},
        {"x": 2.0, "y": 3.0},
        {"x": 3.0, "y": 2.0},
        {"x": 4.0, "y": 1.0},
    ]

    selected = representative_indices(vectors, 2)

    assert len(selected) == 2
    # The x-extreme (index 0) and y-extreme (index 3) must both survive.
    assert selected == [0, 3]


def test_representative_indices_is_a_noop_within_budget() -> None:
    vectors = [{"x": 1.0}, {"x": 2.0}]

    assert representative_indices(vectors, 5) == [0, 1]
    assert representative_indices(vectors, 0) == []


def test_representative_indices_is_deterministic_when_filling_remaining_budget() -> None:
    vectors = [
        {"x": 1.0, "y": 10.0},
        {"x": 2.0, "y": 9.0},
        {"x": 10.0, "y": 1.0},
        {"x": 9.0, "y": 2.0},
    ]

    assert representative_indices(vectors, 3) == representative_indices(vectors, 3)
