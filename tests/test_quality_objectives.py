"""Unit coverage for the shared planner-independent quality objectives."""

from __future__ import annotations

from tournament_scheduler.quality_objectives import (
    QUALITY_METRIC_PATHS,
    QUALITY_OBJECTIVE_DIMENSIONS,
    compare_quality_scores,
    get_metric_path,
    quality_objective_vector,
    with_unresolved_obligations_count,
)


def _score() -> dict:
    return {
        "participation": {
            "spread": 2,
            "season_total_absolute_deviation": 5,
            "avoidable_deviation_count": 1,
        },
        "opponent_diversity": {
            "max_pair_repeat": 3,
            "same_club_pairing_count": 4,
            "inter_club_diversity": 0.75,
            "unique_pairs": 10,
        },
        "turnaround": {"gaps_under_days": {7: 2, 14: 3}},
        "hosting": {"spread": 1, "unresolved_obligations": [{"club": "A"}]},
        "temporal": {"max_gap_days": 20},
    }


def test_quality_objective_vector_is_uniformly_lower_is_better() -> None:
    vector = quality_objective_vector(_score())

    assert set(vector) == set(QUALITY_OBJECTIVE_DIMENSIONS)
    assert vector["participation_season_deviation"] == 5.0
    assert vector["participation_avoidable"] == 1.0
    assert vector["max_pair_repeat"] == 3.0
    assert vector["same_club_pairing_count"] == 4.0
    assert vector["gaps_under_7"] == 2.0
    assert vector["gaps_under_14"] == 3.0
    assert vector["hosting_spread"] == 1.0
    assert vector["temporal_max_gap_days"] == 20.0
    # A "higher is better" fraction is inverted so one dominance check works.
    assert vector["inter_club_diversity_inverted"] == 0.25


def test_quality_objective_vector_defaults_missing_metrics_to_zero() -> None:
    vector = quality_objective_vector({})

    assert set(vector) == set(QUALITY_OBJECTIVE_DIMENSIONS)
    # Every missing numeric metric defaults to 0, so the inverted
    # "higher is better" fraction defaults to 1.0 (fully diverse).
    assert vector["inter_club_diversity_inverted"] == 1.0
    assert all(
        value == 0.0
        for dimension, value in vector.items()
        if dimension != "inter_club_diversity_inverted"
    )


def test_get_metric_path_resolves_int_keyed_thresholds() -> None:
    report = _score()

    assert get_metric_path(report, "turnaround.gaps_under_days.7") == 2
    assert get_metric_path(report, "participation.spread") == 2
    assert get_metric_path(report, "participation.missing") is None


def test_compare_quality_scores_flags_only_wrong_direction_moves() -> None:
    before = with_unresolved_obligations_count(_score())
    after = with_unresolved_obligations_count(_score())
    after["participation"]["spread"] = 3  # lower is better -> regression
    after["opponent_diversity"]["unique_pairs"] = 11  # higher is better -> improvement
    after["turnaround"]["gaps_under_days"][7] = 2  # equal -> no regression

    comparison = compare_quality_scores(before, after)

    assert comparison["regressions"] == ["participation.spread"]
    by_metric = {metric["metric"]: metric for metric in comparison["metrics"]}
    assert by_metric["opponent_diversity.unique_pairs"]["regressed"] is False
    assert by_metric["opponent_diversity.unique_pairs"]["delta"] == 1
    assert by_metric["turnaround.gaps_under_days.7"]["regressed"] is False


def test_with_unresolved_obligations_count_folds_list_only() -> None:
    folded = with_unresolved_obligations_count(_score())

    assert folded["hosting"]["unresolved_obligations_count"] == 1
    assert get_metric_path(folded, "hosting.unresolved_obligations_count") == 1
    # The original report is not mutated.
    assert "unresolved_obligations_count" not in _score()["hosting"]


def test_quality_metric_paths_cover_each_declared_objective_family() -> None:
    families = {path.split(".")[0] for path, _direction in QUALITY_METRIC_PATHS}

    assert {"participation", "opponent_diversity", "turnaround", "hosting", "temporal"} <= families
    assert all(direction in {"higher", "lower"} for _path, direction in QUALITY_METRIC_PATHS)
