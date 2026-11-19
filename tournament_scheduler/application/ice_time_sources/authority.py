"""Typed manual-attestation authority: who asserted completeness, for what, until when."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class Authority:
    """Explicit issuer, scope statement and effective interval of an attestation.

    A manual (or imported) snapshot may only claim ``COMPLETE`` through one of
    these; its ``effective_until`` is the coverage freshness bound.
    """

    issuer: str
    scope: str
    effective_from: datetime
    effective_until: datetime

    def __post_init__(self) -> None:
        if not self.issuer.strip() or not self.scope.strip():
            raise ValueError("authority needs an issuer and a scope statement")
        if self.effective_from.tzinfo is None or self.effective_until.tzinfo is None:
            raise ValueError("authority interval must be timezone-aware")
        if not self.effective_from < self.effective_until:
            raise ValueError("authority interval must have positive duration")

    def fingerprint(self) -> str:
        return f"{self.issuer}|{self.scope}|{self.effective_from.isoformat()}|{self.effective_until.isoformat()}"
