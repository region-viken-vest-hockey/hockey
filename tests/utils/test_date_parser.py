"""Tests for DateParser utility."""

from datetime import datetime, date
from tournament_scheduler.utils.date_parser import DateParser


class TestDateParser:
    """Test suite for DateParser."""

    def test_parse_dd_mm_yyyy_dot(self):
        """Test parsing DD.MM.YYYY format."""
        result = DateParser.parse('17.01.2026')
        assert result == datetime(2026, 1, 17)

    def test_parse_dd_mm_yyyy_slash(self):
        """Test parsing DD/MM/YYYY format."""
        result = DateParser.parse('17/01/2026')
        assert result == datetime(2026, 1, 17)

    def test_parse_yyyy_mm_dd(self):
        """Test parsing YYYY-MM-DD format."""
        result = DateParser.parse('2026-01-17')
        assert result == datetime(2026, 1, 17)

    def test_parse_leap_year(self):
        """Test parsing leap year date."""
        result = DateParser.parse('29.02.2024')
        assert result == datetime(2024, 2, 29)

    def test_parse_invalid_leap_year(self):
        """Test parsing invalid leap year date."""
        result = DateParser.parse('29.02.2023')
        assert result is None

    def test_parse_none(self):
        """Test parsing None returns None."""
        result = DateParser.parse(None)
        assert result is None

    def test_parse_empty_string(self):
        """Test parsing empty string returns None."""
        result = DateParser.parse('')
        assert result is None

    def test_parse_whitespace(self):
        """Test parsing whitespace returns None."""
        result = DateParser.parse('   ')
        assert result is None

    def test_parse_invalid_format(self):
        """Test parsing invalid format returns None."""
        result = DateParser.parse('invalid-date')
        assert result is None

    def test_parse_invalid_date(self):
        """Test parsing invalid date like 32.01.2026 returns None."""
        result = DateParser.parse('32.01.2026')
        assert result is None

    def test_parse_with_whitespace(self):
        """Test parsing with surrounding whitespace."""
        result = DateParser.parse('  17.01.2026  ')
        assert result == datetime(2026, 1, 17)

    def test_parse_datetime_cell_datetime_object(self):
        """Test parsing datetime object."""
        dt = datetime(2026, 1, 17, 14, 30)
        result = DateParser.parse_datetime_cell(dt)
        assert result == dt

    def test_parse_datetime_cell_date_object(self):
        """Test parsing date object."""
        d = date(2026, 1, 17)
        result = DateParser.parse_datetime_cell(d)
        assert result == datetime(2026, 1, 17, 0, 0)

    def test_parse_datetime_cell_string(self):
        """Test parsing string in datetime cell."""
        result = DateParser.parse_datetime_cell('17.01.2026')
        assert result == datetime(2026, 1, 17)

    def test_parse_datetime_cell_none(self):
        """Test parsing None cell returns None."""
        result = DateParser.parse_datetime_cell(None)
        assert result is None

    def test_parse_datetime_cell_invalid_type(self):
        """Test parsing invalid type returns None."""
        result = DateParser.parse_datetime_cell(12345)
        assert result is None

    def test_parse_datetime_cell_empty_string(self):
        """Test parsing empty string cell returns None."""
        result = DateParser.parse_datetime_cell('')
        assert result is None
