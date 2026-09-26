"""Immutable publication evidence for sealed-season republish.

A publication of an already published/sealed season is a *replacement* of the
public projection, not a replan. Two things must therefore be recoverable from
repository-owned state, not from a mutable ``gh-pages`` branch head:

* the exact public projection that is being replaced (the previous published
  baseline), and
* an immutable reference to that bundle (the Pages run id / commit and the
  public-bundle content fingerprint), so ``operator rollback`` can reach it.

This module owns the evidence vocabulary and the read/write of the retained
before/after record. It never decides *whether* a publication may proceed (that
is the publication boundary) and it never edits canonical schedule facts.

The record is attached to the new immutable baseline in ``decisions.json`` and,
for human/machine readability, also written next to the canonical season state
under ``season/<season>/evidence/publications/<publication_id>/``. It is derived
evidence: it must never be the only copy of an operational fact.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from .export_projection_guard import diff_tournament_projection
from .fingerprints import stable_payload_sha256

PUBLICATION_EVIDENCE_SCHEMA_VERSION = 1
PUBLICATION_EVIDENCE_DIRNAME = "publications"
PUBLICATION_EVIDENCE_JSON = "publication_evidence.json"
PUBLICATION_EVIDENCE_MARKDOWN = "publication_evidence.md"

_PUBLICATION_ID_RE = re.compile(r"^[A-Za-z0-9._-]+$")


class PublicationEvidenceError(RuntimeError):
    """Raised when publication evidence is missing or malformed."""


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).replace(microsecond=0).isoformat()


def _text(value: Any) -> str:
    return str(value or "").strip()


def build_publication_evidence(
    *,
    run_id: str | None,
    canonical_revision: str,
    projection_fingerprint: str,
    bundle_fingerprint: str | None = None,
    export_id: str | None = None,
    export_fingerprint: str | None = None,
    pages_branch: str | None = None,
    pages_commit: str | None = None,
    published_at: str | None = None,
    artifacts: list[str] | None = None,
) -> dict[str, Any]:
    """Build the immutable reference for one successful publication."""

    evidence = {
        "schema_version": PUBLICATION_EVIDENCE_SCHEMA_VERSION,
        "run_id": _text(run_id),
        "canonical_revision": _text(canonical_revision),
        "projection_fingerprint": _text(projection_fingerprint),
        "bundle_fingerprint": _text(bundle_fingerprint),
        "export_id": _text(export_id),
        "export_fingerprint": _text(export_fingerprint),
        "pages_branch": _text(pages_branch),
        "pages_commit": _text(pages_commit),
        "published_at": _text(published_at),
        "artifacts": [str(artifact) for artifact in artifacts or []],
    }
    return validate_publication_evidence(evidence)


def validate_publication_evidence(evidence: Mapping[str, Any] | None) -> dict[str, Any]:
    """Normalize/validate a publication evidence record, failing closed.

    A replacement publication must be able to name the exact canonical revision
    and public projection it replaced. The immutable Pages reference (run id /
    bundle fingerprint / commit) is validated separately by the publication
    boundary, because a legacy first publication may predate it.
    """

    if not isinstance(evidence, Mapping):
        raise PublicationEvidenceError("publication evidence must be an object")
    normalized = {str(key): value for key, value in evidence.items()}
    for field in ("canonical_revision", "projection_fingerprint"):
        if not _text(normalized.get(field)):
            raise PublicationEvidenceError(
                f"publication evidence is missing required field {field!r}"
            )
    if not any(
        _text(normalized.get(field))
        for field in ("run_id", "bundle_fingerprint", "pages_commit", "export_fingerprint")
    ):
        raise PublicationEvidenceError(
            "publication evidence has no artifact reference "
            "(run_id / bundle_fingerprint / pages_commit / export_fingerprint)"
        )
    normalized.setdefault("schema_version", PUBLICATION_EVIDENCE_SCHEMA_VERSION)
    return normalized


def has_immutable_reference(evidence: Mapping[str, Any] | None) -> bool:
    """True when evidence can name an immutable published artifact."""

    if not isinstance(evidence, Mapping):
        return False
    return bool(
        _text(evidence.get("run_id"))
        or _text(evidence.get("bundle_fingerprint"))
        or _text(evidence.get("pages_commit"))
    )


def previous_publication_link(baseline: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """Return the immutable link from a new publication to the previous one."""

    if not isinstance(baseline, Mapping):
        return None
    link: dict[str, Any] = {
        "publication_id": _text(baseline.get("publication_id")),
        "canonical_revision": _text(baseline.get("canonical_revision")),
        "published_at": _text(baseline.get("published_at")),
        "projection_fingerprint": _text(baseline.get("projection_fingerprint")),
        "tournament_count": baseline.get("tournament_count"),
    }
    evidence = baseline.get("publication_evidence")
    if isinstance(evidence, Mapping):
        # The link only needs the immutable reference + facts about the replaced
        # version; the replaced version's own decision snapshot stays on its own
        # baseline record instead of being duplicated into every later link.
        link["publication_evidence"] = {
            key: value for key, value in evidence.items() if key != "decision_snapshot"
        }
    return link


def build_republish_delta(
    published_projection: Mapping[str, Mapping[str, Any]],
    current_projection: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """Compare the exact previously published projection to current canonical.

    Uses the one versioned full operational projection and fails closed on an
    unknown/incomplete schema, so an occupancy/cancellation/guest change can
    never be reported as "unchanged" merely because placement matches.
    """

    delta = diff_tournament_projection(published_projection, current_projection)
    if delta["schema_errors"]:
        raise PublicationEvidenceError(
            "republish delta cannot be trusted: the published or current projection "
            "has an incomplete/legacy schema: "
            + json.dumps(delta["schema_errors"][:5], ensure_ascii=False, sort_keys=True)
        )
    changed_ids = {entry["tournament_id"] for entry in delta["field_changes"]}
    shared_ids = set(published_projection) & set(current_projection)
    return {
        "delta": delta,
        "summary": {
            "published_tournament_count": len(published_projection),
            "current_tournament_count": len(current_projection),
            "added": len(delta["added_tournament_ids"]),
            "removed": len(delta["removed_tournament_ids"]),
            "changed": len(delta["field_changes"]),
            "unchanged": len(shared_ids - changed_ids),
            "schema_errors": len(delta["schema_errors"]),
        },
    }


def _normalized_booking_evidence(decisions: Mapping[str, Any]) -> list[dict[str, str]]:
    """Deterministic, detail-preserving booking-evidence records.

    Status alone loses the event/calendar reference that supports a
    ``confirmed_booked`` conclusion, so a changed reference is invisible. Each
    record keeps the identity and supporting fingerprints and is sorted so the
    snapshot is stable across dictionary ordering.
    """

    records: list[dict[str, str]] = []
    for entry in decisions.get("tournament_booking_evidence") or []:
        if not isinstance(entry, Mapping):
            continue
        records.append(
            {
                "tournament_id": _text(entry.get("tournament_id")),
                "evidence_id": _text(entry.get("id")),
                "status": _text(entry.get("status") or entry.get("reason")),
                "reason": _text(entry.get("reason")),
                "event_fingerprint": _text(entry.get("event_fingerprint")),
                "calendar_fingerprint": _text(entry.get("calendar_fingerprint")),
                "checked_at": _text(entry.get("checked_at")),
            }
        )
    return sorted(
        records,
        key=lambda item: (
            item["tournament_id"],
            item["evidence_id"],
            item["status"],
            item["event_fingerprint"],
            item["checked_at"],
        ),
    )


def _booking_by_tournament(records: Any) -> dict[str, list[dict[str, str]]]:
    grouped: dict[str, list[dict[str, str]]] = {}
    for entry in records or []:
        if not isinstance(entry, Mapping):
            continue
        grouped.setdefault(_text(entry.get("tournament_id")), []).append(dict(entry))
    return grouped


def decision_snapshot(decisions: Mapping[str, Any] | None) -> dict[str, Any]:
    """Compact approval/booking/protection/constraint snapshot.

    Decision-only state is deliberately outside the schedule projection. It is
    snapshotted here so a later republish can report which approvals, booking
    evidence, protections and request constraints changed, without that report
    ever being able to mask a schedule mutation.
    """

    resolved = decisions if isinstance(decisions, Mapping) else {}
    records = resolved.get("decisions") if isinstance(resolved.get("decisions"), Mapping) else {}
    approvals: dict[str, Any] = {}
    for tournament_id, record in records.items():
        if not isinstance(record, Mapping):
            continue
        approvals[str(tournament_id)] = {
            "status": _text(record.get("status")),
            "placement_locked": bool(record.get("placement_locked")),
            "participants_locked": bool(record.get("participants_locked")),
            "approved_fingerprint": record.get("approved_fingerprint"),
        }

    def _active_ids(key: str) -> list[str]:
        entries = resolved.get(key) or []
        return sorted(
            _text(entry.get("id"))
            for entry in entries
            if isinstance(entry, Mapping) and _text(entry.get("status") or "active") == "active"
        )

    return {
        "schema_version": PUBLICATION_EVIDENCE_SCHEMA_VERSION,
        "approvals": approvals,
        "change_protections": _active_ids("change_protections"),
        "request_constraints": _active_ids("request_constraints"),
        "booking_evidence": _normalized_booking_evidence(resolved),
    }


def diff_decision_snapshot(
    before: Mapping[str, Any] | None,
    after: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Compare two decision snapshots, separating evidence-only changes.

    When the previous publication predates the decision snapshot (or was never
    recorded), the comparison is explicitly reported as unavailable rather than
    silently missing, so an operator is never told there were no approval or
    booking changes when none could be checked.
    """

    if not isinstance(before, Mapping):
        return {
            "available": False,
            "reason": "no_previous_decision_snapshot",
            "changed": None,
            "approval_changes": [],
            "booking_changes": [],
            "change_protections": {"added": [], "removed": []},
            "request_constraints": {"added": [], "removed": []},
        }
    after = after if isinstance(after, Mapping) else {}

    before_approvals = before.get("approvals") if isinstance(before.get("approvals"), Mapping) else {}
    after_approvals = after.get("approvals") if isinstance(after.get("approvals"), Mapping) else {}
    approval_changes = [
        {
            "tournament_id": tournament_id,
            "before": before_approvals.get(tournament_id),
            "after": after_approvals.get(tournament_id),
        }
        for tournament_id in sorted(set(before_approvals) | set(after_approvals))
        if before_approvals.get(tournament_id) != after_approvals.get(tournament_id)
    ]

    def _set_diff(key: str) -> dict[str, list[str]]:
        old = set(before.get(key) or [])
        new = set(after.get(key) or [])
        return {"added": sorted(new - old), "removed": sorted(old - new)}

    before_bookings = _booking_by_tournament(before.get("booking_evidence"))
    after_bookings = _booking_by_tournament(after.get("booking_evidence"))
    booking_changes = [
        {
            "tournament_id": tournament_id,
            "before": before_bookings.get(tournament_id, []),
            "after": after_bookings.get(tournament_id, []),
        }
        for tournament_id in sorted(set(before_bookings) | set(after_bookings))
        if before_bookings.get(tournament_id) != after_bookings.get(tournament_id)
    ]

    protections = _set_diff("change_protections")
    constraints = _set_diff("request_constraints")
    return {
        "available": True,
        "reason": None,
        "approval_changes": approval_changes,
        "booking_changes": booking_changes,
        "change_protections": protections,
        "request_constraints": constraints,
        "changed": bool(
            approval_changes
            or booking_changes
            or protections["added"]
            or protections["removed"]
            or constraints["added"]
            or constraints["removed"]
        ),
    }


def evidence_directory(
    season_root: str | Path,
    season: str,
    publication_id: str,
) -> Path:
    """Resolve the repository-owned publication evidence directory."""

    if not _PUBLICATION_ID_RE.match(publication_id or ""):
        raise PublicationEvidenceError(
            f"unsafe publication id for evidence path: {publication_id!r}"
        )
    return Path(season_root) / season / "evidence" / PUBLICATION_EVIDENCE_DIRNAME / publication_id


def _atomic_write_text(path: Path, content: str) -> None:
    """Write *content* to *path* atomically (temp file + rename)."""

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    try:
        tmp_path.write_text(content, encoding="utf-8")
        os.replace(tmp_path, path)
    finally:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)


def write_publication_evidence(
    *,
    season_root: str | Path,
    season: str,
    publication_id: str,
    evidence: Mapping[str, Any],
    previous_publication: Mapping[str, Any] | None,
    republish_delta: Mapping[str, Any] | None,
    canonical_revision: str,
    decision_changes: Mapping[str, Any] | None = None,
) -> dict[str, str]:
    """Write the retained before/after evidence for one publication.

    The durable baseline in ``decisions.json`` is committed just before this
    runs, so the write must be recoverable: both files are written atomically and
    a retry for the same publication id is idempotent. A retry that would
    overwrite a *different* evidence record for the same publication id fails
    closed instead of silently replacing it.

    Returns the written paths. Callers must treat a write failure as an
    incomplete publication, not as a successful replacement.
    """

    target = evidence_directory(season_root, season, publication_id)
    json_path = target / PUBLICATION_EVIDENCE_JSON
    markdown_path = target / PUBLICATION_EVIDENCE_MARKDOWN
    fingerprint_payload = {
        "schema_version": PUBLICATION_EVIDENCE_SCHEMA_VERSION,
        "season": season,
        "publication_id": publication_id,
        "canonical_revision": canonical_revision,
        "publication_evidence": dict(evidence),
        "previous_publication": dict(previous_publication) if previous_publication else None,
        "republish_delta": dict(republish_delta) if republish_delta else None,
        "decision_changes": dict(decision_changes) if decision_changes else None,
    }
    record_fingerprint = stable_payload_sha256(fingerprint_payload)

    existing = read_publication_evidence(season_root, season, publication_id)
    if existing is not None:
        if str(existing.get("record_fingerprint") or "") != record_fingerprint:
            raise PublicationEvidenceError(
                "refusing to overwrite conflicting publication evidence for "
                f"{publication_id!r} in {target}"
            )
        return {"json": str(json_path), "markdown": str(markdown_path)}

    record = {
        **fingerprint_payload,
        "recorded_at": _now_iso(),
        "record_fingerprint": record_fingerprint,
    }
    _atomic_write_text(
        json_path, json.dumps(record, indent=2, ensure_ascii=False, default=str)
    )
    _atomic_write_text(markdown_path, _render_markdown(record))
    return {"json": str(json_path), "markdown": str(markdown_path)}


def read_publication_evidence(
    season_root: str | Path,
    season: str,
    publication_id: str,
) -> dict[str, Any] | None:
    """Read one retained publication evidence record, or ``None``."""

    path = evidence_directory(season_root, season, publication_id) / PUBLICATION_EVIDENCE_JSON
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def list_publication_evidence(season_root: str | Path, season: str) -> list[dict[str, Any]]:
    """List retained publication evidence records, newest first."""

    root = Path(season_root) / season / "evidence" / PUBLICATION_EVIDENCE_DIRNAME
    if not root.is_dir():
        return []
    records: list[dict[str, Any]] = []
    for path in sorted(root.glob(f"*/{PUBLICATION_EVIDENCE_JSON}")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, dict):
            data["evidence_path"] = str(path)
            records.append(data)
    records.sort(key=lambda item: str(item.get("recorded_at") or item.get("publication_id") or ""), reverse=True)
    return records


def _render_markdown(record: Mapping[str, Any]) -> str:
    evidence = record.get("publication_evidence") or {}
    previous = record.get("previous_publication") or {}
    delta = record.get("republish_delta") or {}
    summary = delta.get("summary") or {}
    changes = record.get("decision_changes") or {}
    lines = [
        f"# Publication evidence: {record.get('season')} / {record.get('publication_id')}",
        "",
        f"- Canonical revision: `{record.get('canonical_revision')}`",
        f"- Published run: `{evidence.get('run_id') or 'unknown'}`",
        f"- Public bundle fingerprint: `{evidence.get('bundle_fingerprint') or 'unknown'}`",
        f"- Public branch/commit: `{evidence.get('pages_branch') or 'unknown'}` `{evidence.get('pages_commit') or 'unknown'}`",
        f"- Recorded at: {record.get('recorded_at')}",
        "",
        "## Previous publication (retained for rollback)",
        "",
    ]
    if previous:
        lines += [
            f"- Publication id: `{previous.get('publication_id')}`",
            f"- Canonical revision: `{previous.get('canonical_revision')}`",
            f"- Published at: {previous.get('published_at')}",
            f"- Projection fingerprint: `{previous.get('projection_fingerprint')}`",
            f"- Published run: `{(previous.get('publication_evidence') or {}).get('run_id') or 'unknown'}`",
        ]
    else:
        lines.append("- First publication for this season (no previous version to retain).")
    lines += ["", "## Published -> canonical delta", ""]
    if summary:
        lines += [
            f"- Added: {summary.get('added', 0)}",
            f"- Removed: {summary.get('removed', 0)}",
            f"- Changed: {summary.get('changed', 0)}",
            f"- Unchanged: {summary.get('unchanged', 0)}",
            f"- Projection schema errors: {summary.get('schema_errors', 0)}",
        ]
    else:
        lines.append("- Not applicable on the first publication.")
    lines += ["", "## Decision-only changes (approvals / bookings / protections)", ""]
    if changes:
        lines += [
            f"- Approval changes: {len(changes.get('approval_changes') or [])}",
            f"- Booking-evidence changes: {len(changes.get('booking_changes') or [])}",
            f"- Change protections added/removed: "
            f"{len((changes.get('change_protections') or {}).get('added') or [])}/"
            f"{len((changes.get('change_protections') or {}).get('removed') or [])}",
            f"- Request constraints added/removed: "
            f"{len((changes.get('request_constraints') or {}).get('added') or [])}/"
            f"{len((changes.get('request_constraints') or {}).get('removed') or [])}",
        ]
    else:
        lines.append("- No decision snapshot available for this publication.")
    lines.append("")
    return "\n".join(lines)


__all__ = [
    "PUBLICATION_EVIDENCE_DIRNAME",
    "PUBLICATION_EVIDENCE_JSON",
    "PUBLICATION_EVIDENCE_MARKDOWN",
    "PUBLICATION_EVIDENCE_SCHEMA_VERSION",
    "PublicationEvidenceError",
    "build_publication_evidence",
    "build_republish_delta",
    "decision_snapshot",
    "diff_decision_snapshot",
    "evidence_directory",
    "has_immutable_reference",
    "list_publication_evidence",
    "previous_publication_link",
    "read_publication_evidence",
    "validate_publication_evidence",
    "write_publication_evidence",
]
