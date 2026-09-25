"""Revision/baseline freshness rules for an export artifact pair.

Artifact parity alone is not freshness: two stale artifacts can agree with each
other while describing an old canonical revision. This module makes that
distinction explicit (a FAIL/``NOT_CHECKABLE`` reason), independently of the
comparator.
"""

from __future__ import annotations

from typing import Any

from .records import STATUS_FAIL, STATUS_NOT_CHECKABLE


def evaluate_freshness(
    *,
    primary_revision: str,
    secondary_revision: str,
    manifest_revision: str,
    required_revision: str = "",
    requires_fresh_export: bool = False,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return ``(failures, not_checkable)`` freshness findings."""

    failures: list[dict[str, Any]] = []
    not_checkable: list[dict[str, Any]] = []

    if requires_fresh_export:
        failures.append(
            {
                "code": "requires_fresh_export",
                "message": (
                    "Canonical season state records that a fresh export is required; "
                    "regenerate the export before publishing."
                ),
            }
        )

    if primary_revision and secondary_revision and primary_revision != secondary_revision:
        failures.append(
            {
                "code": "artifact_revision_mismatch",
                "message": (
                    "The two artifacts embed different canonical revisions: "
                    f"xlsx={primary_revision}, html={secondary_revision}"
                ),
            }
        )

    if manifest_revision:
        for kind, revision in (("xlsx", primary_revision), ("html", secondary_revision)):
            if revision and revision != manifest_revision:
                failures.append(
                    {
                        "code": "manifest_revision_mismatch",
                        "message": (
                            f"{kind} artifact revision {revision} differs from the export "
                            f"manifest revision {manifest_revision}"
                        ),
                    }
                )

    if required_revision:
        observed = {revision for revision in (primary_revision, secondary_revision, manifest_revision) if revision}
        if not observed:
            not_checkable.append(
                {
                    "code": "missing_canonical_revision",
                    "message": (
                        "No artifact or manifest carries a canonical revision, so freshness "
                        f"against {required_revision} cannot be verified."
                    ),
                }
            )
        else:
            stale = sorted(revision for revision in observed if revision != required_revision)
            if stale:
                failures.append(
                    {
                        "code": "stale_canonical_revision",
                        "message": (
                            "Export artifacts do not match the current canonical revision "
                            f"{required_revision}; stale revisions: {', '.join(stale)}"
                        ),
                    }
                )

    return failures, not_checkable


def status_from_findings(failures: list[dict[str, Any]], not_checkable: list[dict[str, Any]]) -> str:
    if failures:
        return STATUS_FAIL
    if not_checkable:
        return STATUS_NOT_CHECKABLE
    return ""
