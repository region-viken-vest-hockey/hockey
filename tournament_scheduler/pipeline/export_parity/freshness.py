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
    canonical_required: bool = False,
    lookup_failed: bool = False,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return ``(failures, not_checkable)`` freshness findings.

    ``canonical_required`` marks publication of an identified canonical season
    export. In that mode freshness must fail closed: the current canonical
    revision has to be determinable, and *each* artifact and the manifest must
    carry it, not merely one matching value among several.
    """

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

    revisions = {"xlsx": primary_revision, "html": secondary_revision, "manifest": manifest_revision}

    if canonical_required:
        if lookup_failed or not required_revision:
            not_checkable.append(
                {
                    "code": "canonical_state_unreadable" if lookup_failed else "missing_canonical_revision",
                    "message": (
                        "The current canonical revision could not be determined for this canonical "
                        "season export, so freshness cannot be proven."
                    ),
                }
            )
        else:
            for kind, revision in revisions.items():
                if not revision:
                    not_checkable.append(
                        {
                            "code": "missing_canonical_revision",
                            "message": (
                                f"{kind} does not carry a canonical revision, so freshness against "
                                f"{required_revision} cannot be verified."
                            ),
                        }
                    )
            stale = sorted({revision for revision in revisions.values() if revision and revision != required_revision})
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
    elif required_revision:
        observed = {revision for revision in revisions.values() if revision}
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
