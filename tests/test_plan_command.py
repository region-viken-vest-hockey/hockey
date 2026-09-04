from __future__ import annotations

import pytest

from tournament_scheduler.cli.plan_command import _parse_weight_overrides


def test_parse_weight_overrides_empty():
    assert _parse_weight_overrides(None) == ({}, {})
    assert _parse_weight_overrides([]) == ({}, {})


def test_parse_weight_overrides_parses_multiple():
    weights, per_age_group = _parse_weight_overrides(["gap_under_7=8.0", "same_club_pairing=1.5"])
    assert weights == {"gap_under_7": 8.0, "same_club_pairing": 1.5}
    assert per_age_group == {}


def test_parse_weight_overrides_parses_per_age_group():
    weights, per_age_group = _parse_weight_overrides(
        ["gap_under_7=8.0", "JU12:same_club_pairing=1.5", "JU12:gap_under_7=2.0", "JU14:pair_repeat=4.0"]
    )
    assert weights == {"gap_under_7": 8.0}
    assert per_age_group == {
        "JU12": {"same_club_pairing": 1.5, "gap_under_7": 2.0},
        "JU14": {"pair_repeat": 4.0},
    }


def test_parse_weight_overrides_rejects_malformed():
    with pytest.raises(ValueError):
        _parse_weight_overrides(["gap_under_7"])
    with pytest.raises(ValueError):
        _parse_weight_overrides(["JU12:gap_under_7"])
