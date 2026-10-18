"""Library-level failure contract for the Excel tournament reader.

The reader is reusable core code, so a missing or unusable source must raise a
typed error that the caller can handle, never terminate the process.
"""

from datetime import date

import openpyxl
import pytest

from tournament_scheduler.excel.tournament_reader import ExcelTournamentError, ExcelTournamentReader
from tournament_scheduler.utils.date_parser import DateParser


def test_missing_file_raises_typed_error_not_system_exit(tmp_path) -> None:
    reader = ExcelTournamentReader(str(tmp_path / "missing.xlsx"), DateParser())

    with pytest.raises(ExcelTournamentError, match="ikke funnet"):
        reader.get_all_tournament_dates()


def test_date_without_tournament_raises_typed_error(tmp_path) -> None:
    path = tmp_path / "empty.xlsx"
    workbook = openpyxl.Workbook()
    workbook.save(path)
    reader = ExcelTournamentReader(str(path), DateParser())

    with pytest.raises(ExcelTournamentError, match="Ingen turnering funnet på 2026-12-19"):
        reader.get_tournament_info(date(2026, 12, 19))
