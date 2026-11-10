"""Static, public-safe worksheet styling shared by the season-plan exporter.

Only plain cell styles and worksheet properties are used (no formulas,
drawings, hyperlinks, comments or conditional-format parts), so the public XLSX
sanitizer in ``pipeline/pages_bundle.py`` needs no exemption.
"""

from openpyxl.styles import Font, PatternFill
from openpyxl.worksheet.worksheet import Worksheet

HEADER_FILL = PatternFill(start_color="DCE6F1", end_color="DCE6F1", fill_type="solid")
ATTENTION_FILL = PatternFill(start_color="FDE68A", end_color="FDE68A", fill_type="solid")


def style_header_row(sheet: Worksheet, row: int = 1) -> None:
    """Bold and shade the header row."""
    for cell in sheet[row]:
        cell.font = Font(bold=True)
        cell.fill = HEADER_FILL


def finalize_sheet(sheet: Worksheet, header_row: int, *, filterable: bool = False) -> None:
    """Freeze the header, set a landscape fit-to-width print layout and optionally add a filter."""
    sheet.freeze_panes = f"A{header_row + 1}"
    sheet.print_title_rows = f"{header_row}:{header_row}"
    sheet.page_setup.orientation = "landscape"
    sheet.page_setup.fitToWidth = 1
    sheet.page_setup.fitToHeight = 0
    sheet.sheet_properties.pageSetUpPr.fitToPage = True
    if filterable and sheet.max_row > header_row:
        last_column = sheet.cell(row=header_row, column=sheet.max_column).column_letter
        sheet.auto_filter.ref = f"A{header_row}:{last_column}{sheet.max_row}"
