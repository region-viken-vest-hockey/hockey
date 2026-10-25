"""Detect a publication whose retained evidence record is missing or unusable.

A seal commits the baseline before its evidence record is written, so a failed
write leaves history that looks complete. This module answers one read-only
question for the operator: does the active publication have a retained record
that is present, matches the baseline, and still matches its own fingerprint?
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from tournament_scheduler.pipeline.fingerprints import stable_payload_sha256

from tournament_scheduler.publication.evidence import (
    PUBLICATION_EVIDENCE_JSON,
    PublicationEvidenceError,
    evidence_directory,
    read_publication_evidence,
)

# Mirrors the payload that ``write_publication_evidence`` fingerprints.
_RECORD_FINGERPRINT_KEYS = (
    "schema_version",
    "season",
    "publication_id",
    "canonical_revision",
    "publication_evidence",
    "previous_publication",
    "republish_delta",
    "decision_changes",
)


def retained_record_is_intact(record: Mapping[str, Any]) -> bool:
    """True when a retained record still matches the fingerprint written with it."""

    payload = {key: record.get(key) for key in _RECORD_FINGERPRINT_KEYS}
    return str(record.get("record_fingerprint") or "") == stable_payload_sha256(payload)


def retained_evidence_gap(
    season_root: str | Path, season: str, baseline: Mapping[str, Any]
) -> dict[str, str] | None:
    """Name the active publication when its retained evidence is missing or invalid."""

    publication_id = str(baseline.get("publication_id") or "")

    def _gap(reason: str) -> dict[str, str]:
        return {"publication_id": publication_id, "reason": reason}

    try:
        record_path = evidence_directory(season_root, season, publication_id) / PUBLICATION_EVIDENCE_JSON
    except PublicationEvidenceError:
        return _gap("invalid_retained_evidence")
    if not record_path.exists():
        return _gap("missing_retained_evidence")
    record = read_publication_evidence(season_root, season, publication_id)
    if (
        record is None
        or record.get("publication_id") != publication_id
        or record.get("canonical_revision") != baseline.get("canonical_revision")
        or not retained_record_is_intact(record)
    ):
        return _gap("invalid_retained_evidence")
    return None
