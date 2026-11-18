"""Strict CSV table reading with located, typed diagnostics.

A *missing* optional file is fine (nothing supplied). A *present* file must be
readable, have its required headers, and every row must have exactly the header's
field count; anything else is reported with file, row and column, never skipped.
"""

from __future__ import annotations

import csv
from pathlib import Path

from tournament_scheduler.application.ports.season_input import DiagnosticCode as Code, InputDiagnostic


def read_table(
    path: Path, required: tuple[str, ...], found: list[InputDiagnostic], *, optional_file: bool
) -> list[tuple[int, dict[str, str]]] | None:
    """Return ``(row_number, cells)`` pairs, or ``None`` when absent/unusable.

    Problems are appended to *found*; row numbers are 1-based data rows.
    """
    name = path.name
    if not path.is_file():
        if not optional_file:
            found.append(InputDiagnostic(Code.MISSING_INPUT, name, "required file not found"))
        return None
    try:
        with path.open(newline="", encoding="utf-8-sig") as handle:
            reader = csv.reader(handle, strict=True)
            header = next(reader, None)
            body = list(reader)
    except (OSError, UnicodeDecodeError, csv.Error) as exc:
        found.append(InputDiagnostic(Code.UNREADABLE, name, str(exc)))
        return None
    if header is None:
        found.append(InputDiagnostic(Code.MISSING_FIELD, f"{name}#header", f"empty file; expected columns {', '.join(required)}"))
        return None
    columns = [h.strip() for h in header]
    lacking = [c for c in required if c not in columns]
    if lacking:
        found.append(InputDiagnostic(Code.MISSING_FIELD, f"{name}#header", f"missing column(s): {', '.join(lacking)}"))
        return None
    repeated = sorted({c for c in columns if c and columns.count(c) > 1})
    if repeated:
        found.append(InputDiagnostic(Code.DUPLICATE, f"{name}#header", f"repeated column(s): {', '.join(repeated)}"))
        return None
    rows: list[tuple[int, dict[str, str]]] = []
    ok = True
    for n, cells in enumerate(body, start=1):
        if not any(c.strip() for c in cells):
            continue
        if len(cells) != len(columns):
            found.append(InputDiagnostic(Code.INVALID_VALUE, f"{name}#{n}", f"expected {len(columns)} field(s), found {len(cells)}"))
            ok = False
            continue
        rows.append((n, {c: v.strip() for c, v in zip(columns, cells) if c}))
    return rows if ok else None
