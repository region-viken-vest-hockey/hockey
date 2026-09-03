from __future__ import annotations

import pytest

from tournament_scheduler.cli.plan_command import _parse_weight_overrides


def test_parse_weight_overrides_empty():
    assert _parse_weight_overrides(None) == {}
    assert _parse_weight_overrides([]) == {}


def test_parse_weight_overrides_parses_multiple():
    result = _parse_weight_overrides(["gap_under_7=8.0", "same_club_pairing=1.5"])
    assert result == {"gap_under_7": 8.0, "same_club_pairing": 1.5}


def test_parse_weight_overrides_rejects_malformed():
    with pytest.raises(ValueError):
        _parse_weight_overrides(["gap_under_7"])
