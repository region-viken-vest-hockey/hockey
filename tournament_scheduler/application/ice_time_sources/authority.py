"""Typed attestation authority: who asserted completeness, for which venue/period, until when."""

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
    venue_id: str
    covers_from: datetime
    covers_to: datetime
    document: str = ""  # source document the attestation covers (required for file imports)

    def __post_init__(self) -> None:
        if not self.issuer.strip() or not self.scope.strip():
            raise ValueError("authority needs an issuer and a scope statement")
        if not self.venue_id.strip():
            raise ValueError("authority must name the venue it covers")
        if self.covers_from.tzinfo is None or self.covers_to.tzinfo is None:
            raise ValueError("authority coverage period must be timezone-aware")
        if not self.covers_from < self.covers_to:
            raise ValueError("authority coverage period must have positive duration")
        if self.effective_from.tzinfo is None or self.effective_until.tzinfo is None:
            raise ValueError("authority interval must be timezone-aware")
        if not self.effective_from < self.effective_until:
            raise ValueError("authority interval must have positive duration")

    def covers(self, venue_id: str, from_date: datetime, to_date: datetime) -> bool:
        """True when this authority's structured venue/period encompasses the claim."""
        return venue_id == self.venue_id and self.covers_from <= from_date and to_date <= self.covers_to

    def fingerprint(self) -> str:
        return f"{self.issuer}|{self.document}|{self.scope}|{self.effective_from.isoformat()}|{self.effective_until.isoformat()}|{self.venue_id}|{self.covers_from.isoformat()}|{self.covers_to.isoformat()}"
