"""Tests for tournament_scheduler.season.config."""

from unittest.mock import patch

import pytest

from tournament_scheduler.season.config import ParallelGamesConfig, SeasonConfigError


class TestParallelGamesConfig:
    def test_explicit_values_are_returned_as_is(self):
        config = ParallelGamesConfig.from_dict({"U10": 3, "JU11": {"parallelGames": 2}})
        assert config.parallel_games_for("U10") == 3
        assert config.parallel_games_for("JU11") == 2

    def test_unknown_age_group_is_not_special_cased(self):
        config = ParallelGamesConfig.from_dict({"JU8": 4})
        assert config.parallel_games_for("JU8") == 4

    def test_missing_age_group_raises(self):
        config = ParallelGamesConfig.from_dict({"U10": 3})
        with pytest.raises(SeasonConfigError, match="ikke konfigurert"):
            config.parallel_games_for("U11")

    def test_non_positive_value_raises(self):
        with pytest.raises(SeasonConfigError, match="positivt heltall"):
            ParallelGamesConfig.from_dict({"U10": 0})

    def test_bool_value_raises(self):
        with pytest.raises(SeasonConfigError):
            ParallelGamesConfig.from_dict({"U10": True})

    def test_missing_parallel_games_key_raises(self):
        with pytest.raises(SeasonConfigError, match="Mangler 'parallelGames'"):
            ParallelGamesConfig.from_dict({"U10": {}})

    def test_warning_is_not_emitted_for_explicit_values(self):
        with patch("tournament_scheduler.utils.rich_output.TournamentOutput.print_warning") as mock_warn:
            ParallelGamesConfig.from_dict({"U10": 2})
        mock_warn.assert_not_called()
