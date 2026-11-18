"""Compatibility adapter: the RVV ``input.xlsx`` workbook as a :class:`SeasonInputPort`.

Delegates parsing to the existing workbook reader (sheets ``Lag``, ``Kilder``,
``Aldersgrupper``, ``Innstillinger``) and normalizes through the shared
normalizer. The workbook path is injected; nothing here knows the repo root.
A ``Kilder`` sheet is optional, and a row without ``url`` is a valid declaration.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from tournament_scheduler.application.ports.common import PortCapabilities, SourceKind, SourceRef
from tournament_scheduler.application.ports.season_input import (
    DiagnosticCode,
    InputDiagnostic,
    NormalizedSeasonInput,
    SeasonInputError,
    season_input_capabilities,
)
from tournament_scheduler.pipeline.input_workbook import WorkbookInputError, load_workbook_config

from .normalize import normalize_raw_config


class WorkbookSeasonInputAdapter:
    adapter_id = "workbook"

    def __init__(
        self,
        path: str | Path,
        *,
        timezone_name: str = "Europe/Oslo",
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self._path = Path(path)
        self._timezone = timezone_name
        self._clock = clock

    def capabilities(self) -> PortCapabilities:
        return season_input_capabilities(self.adapter_id)

    def read(self) -> NormalizedSeasonInput:
        name = self._path.name
        if not self._path.is_file():
            raise SeasonInputError((InputDiagnostic(DiagnosticCode.MISSING_INPUT, name, "workbook not found"),))
        if self._path.suffix.lower() not in {".xlsx", ".xlsm"}:
            raise SeasonInputError((InputDiagnostic(DiagnosticCode.UNSUPPORTED_FORMAT, name, "expected an .xlsx/.xlsm workbook"),))
        try:
            raw = load_workbook_config(self._path)
        except WorkbookInputError as exc:
            raise SeasonInputError((InputDiagnostic(DiagnosticCode.UNREADABLE, name, str(exc)),)) from exc
        origin = SourceRef(
            source_id=f"{self.adapter_id}:{name}",
            kind=SourceKind.FILE,
            captured_at=self._clock(),
            timezone=self._timezone,
            document=name,
            fingerprint=hashlib.sha256(self._path.read_bytes()).hexdigest(),
        )
        return normalize_raw_config(raw, origin=origin, document=name)
