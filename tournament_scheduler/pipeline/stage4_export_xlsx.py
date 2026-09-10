"""XLSX metadata/ZIP normalization for reproducible Stage 4 exports."""

from __future__ import annotations

import re
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path


def _zip_datetime(build_timestamp: datetime) -> tuple[int, int, int, int, int, int]:
    """Return a ZIP-compatible UTC timestamp tuple.

    ZIP stores local DOS timestamps and cannot represent years before 1980;
    reproducible builds using earlier epochs are clamped to that minimum.
    """
    moment = build_timestamp.astimezone(timezone.utc).replace(microsecond=0)
    if moment.year < 1980:
        moment = moment.replace(year=1980, month=1, day=1, hour=0, minute=0, second=0)
    return (moment.year, moment.month, moment.day, moment.hour, moment.minute, moment.second)


def _normalize_xlsx_core_properties(xml_bytes: bytes, build_timestamp: datetime) -> bytes:
    fixed = build_timestamp.astimezone(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")
    text = xml_bytes.decode("utf-8")
    for field in ("created", "modified"):
        pattern = rf"(<dcterms:{field}[^>]*>)(.*?)(</dcterms:{field}>)"
        replacement = rf"\g<1>{fixed}\g<3>"
        text, count = re.subn(pattern, replacement, text)
        if count == 0:
            insert_at = text.find("</cp:coreProperties>")
            if insert_at != -1:
                text = (
                    text[:insert_at]
                    + f'<dcterms:{field} xsi:type="dcterms:W3CDTF">{fixed}</dcterms:{field}>'
                    + text[insert_at:]
                )
    return text.encode("utf-8")


def _normalize_xlsx(path: Path, build_timestamp: datetime) -> None:
    """Normalize an XLSX workbook's embedded and ZIP metadata in place."""
    import openpyxl

    workbook = openpyxl.load_workbook(path)
    workbook.properties.created = build_timestamp.replace(tzinfo=None)
    workbook.properties.modified = build_timestamp.replace(tzinfo=None)
    workbook.save(path)

    fixed_date_time = _zip_datetime(build_timestamp)
    with tempfile.NamedTemporaryFile(delete=False, dir=path.parent, suffix=".xlsx") as handle:
        tmp_path = Path(handle.name)

    try:
        with zipfile.ZipFile(path, "r") as source, zipfile.ZipFile(tmp_path, "w", compression=zipfile.ZIP_DEFLATED) as dest:
            for name in sorted(source.namelist()):
                original_info = source.getinfo(name)
                info = zipfile.ZipInfo(filename=name, date_time=fixed_date_time)
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = original_info.external_attr
                info.comment = original_info.comment
                info.create_system = original_info.create_system
                data = source.read(name)
                if name == "docProps/core.xml":
                    data = _normalize_xlsx_core_properties(data, build_timestamp)
                dest.writestr(info, data)
        tmp_path.replace(path)
    finally:
        tmp_path.unlink(missing_ok=True)


def _normalize_export_workbooks(primary_export_path: Path, build_timestamp: datetime) -> None:
    for workbook_path in sorted(primary_export_path.rglob("*.xlsx")):
        _normalize_xlsx(workbook_path, build_timestamp)
