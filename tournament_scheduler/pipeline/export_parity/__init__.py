"""Independent, deterministic XLSX/HTML season-plan artifact parity.

This package verifies the *bytes* of an already generated export pair
(``season_plan.xlsx`` and ``season_plan.html``) against each other and against
the canonical revision they claim. It is deliberately separate from the
exporters: the readers parse on-disk artifacts, the comparator is pure, and the
freshness check is owned by :mod:`freshness`. Nothing here plans, mutates
canonical state or reinterprets booking authority.

Public API:

- :func:`verify_export_parity` — read-only verification of one export directory
- :func:`write_parity_report` — persist the bounded ``export_parity.json`` result
- :data:`PARITY_FILENAME`, :data:`STATUS_PASS`, :data:`STATUS_FAIL`,
  :data:`STATUS_NOT_CHECKABLE`
"""

from __future__ import annotations

from .records import (
    PARITY_FILENAME,
    STATUS_FAIL,
    STATUS_NOT_CHECKABLE,
    STATUS_PASS,
    ArtifactProjection,
    TournamentRecord,
)
from .verify import verify_export_parity, write_parity_report

__all__ = [
    "ArtifactProjection",
    "PARITY_FILENAME",
    "STATUS_FAIL",
    "STATUS_NOT_CHECKABLE",
    "STATUS_PASS",
    "TournamentRecord",
    "verify_export_parity",
    "write_parity_report",
]
