"""Independent, deterministic season-plan artifact parity.

This package verifies the *bytes* of generated ``season_plan.xlsx``,
``season_plan.html`` and ``season_plan_spond.xlsx`` artifacts against each other and against
the canonical revision they claim. It is deliberately separate from the
exporters: the readers parse on-disk artifacts, the comparator is pure, and the
freshness check is owned by :mod:`freshness`. Nothing here plans, mutates
canonical state or reinterprets booking authority.

Public API:

- :func:`verify_export_parity` — read-only verification of one export directory
- :func:`write_parity_report` — persist the bounded ``export_parity.json`` result
- :func:`verify_published_export_parity` — inspect current ``gh-pages:latest`` bytes
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
from .presentation import verify_proposed_presentation
from .published import verify_published_export_parity
from .verify import verify_export_parity, write_parity_report

__all__ = [
    "ArtifactProjection",
    "PARITY_FILENAME",
    "STATUS_FAIL",
    "STATUS_NOT_CHECKABLE",
    "STATUS_PASS",
    "TournamentRecord",
    "verify_export_parity",
    "verify_proposed_presentation",
    "verify_published_export_parity",
    "write_parity_report",
]
