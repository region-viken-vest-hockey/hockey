"""Structured refusals for participant replacement.

Each refusal carries a stable code and the canonical owner that decided it, so
dry-run previews and explanations can attribute a block without parsing prose.
Codes that the rule catalog declares resolve their owner through the catalog.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from tournament_scheduler.infrastructure.canonical_season_store import SeasonStateError
from tournament_scheduler.rules import catalog
from tournament_scheduler.season.state import canonical_state_revision

PRECONDITION_OWNER = "canonical_season.replacement_preconditions"
VERIFICATION_OWNER = "planning.verification"
LOCK_OWNER = "season.baseline_overlay"
HOSTING_OWNER = "hosting.responsibility"
PARTICIPANTS_LOCK_CODE = "canonical_participants_locked"
HOSTING_TRANSFER_CODE = "unexplained_hosting_responsibility_transfer"
INCOMPLETE_VERIFICATION_CODE = "incomplete_verification"


def catalog_owner(code: str, default: str) -> str:
    """Canonical owner of a verifier or finding code declared in the rule catalog."""

    for entry in catalog.RULE_CATALOG:
        if code in entry.verifier_codes or code in entry.finding_codes:
            return entry.canonical_owner
    return default


class ReplacementRefusal(SeasonStateError):
    """A canonical refusal with a stable code and the owner that decided it."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        owner: str = PRECONDITION_OWNER,
        evidence: Sequence[Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.owner = owner
        self.evidence = list(evidence or [])

    def record(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "owner": self.owner,
            "message": str(self),
            "evidence": list(self.evidence),
        }


def participant_lock_refusal(tournament_id: str, message: str) -> ReplacementRefusal:
    return ReplacementRefusal(
        PARTICIPANTS_LOCK_CODE,
        message,
        owner=catalog_owner(PARTICIPANTS_LOCK_CODE, LOCK_OWNER),
        evidence=[{"tournament_id": tournament_id}],
    )


def lock_violation_refusals(violations: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "code": str(violation.get("code") or "canonical_lock_violation"),
            "owner": catalog_owner(str(violation.get("code") or ""), LOCK_OWNER),
            "message": str(violation.get("message") or ""),
            "evidence": [dict(violation)],
        }
        for violation in violations
    ]


def verification_refusals(
    violations: Sequence[Mapping[str, Any]], message: str
) -> list[dict[str, Any]]:
    if not violations:
        return [
            {
                "code": INCOMPLETE_VERIFICATION_CODE,
                "owner": catalog_owner(INCOMPLETE_VERIFICATION_CODE, VERIFICATION_OWNER),
                "message": message,
                "evidence": [],
            }
        ]
    return [
        {
            "code": str(violation.get("code") or INCOMPLETE_VERIFICATION_CODE),
            "owner": catalog_owner(
                str(violation.get("code") or ""), VERIFICATION_OWNER
            ),
            "message": str(violation.get("message") or violation.get("code")),
            "evidence": [dict(violation)],
        }
        for violation in violations
    ]


def hosting_transfer_refusals(transfers: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "code": HOSTING_TRANSFER_CODE,
            "owner": catalog_owner(HOSTING_TRANSFER_CODE, HOSTING_OWNER),
            "message": str(entry.get("message")),
            "evidence": [dict(entry)],
        }
        for entry in transfers
    ]


__all__ = [
    "PRECONDITION_OWNER",
    "ReplacementRefusal",
    "blocked_replacement_preview",
    "catalog_owner",
    "hosting_transfer_refusals",
    "lock_violation_refusals",
    "participant_lock_refusal",
    "verification_refusals",
]


def blocked_replacement_preview(
    *,
    season: str,
    schedule: Mapping[str, Any],
    decisions: Mapping[str, Any],
    tournament_id: str,
    remove_team_label: str,
    add_team_label: str,
    refusals: Sequence[Mapping[str, Any]],
    request_id: str | None = None,
    verification_result: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return the stable dry-run shape for a replacement that cannot be built.

    Dry-run is an operational validation interface, not a mutation probe. Domain
    infeasibility therefore reports a blocked verdict in the normal preview
    contract instead of forcing callers to catch exceptions and parse messages.
    Non-dry-run apply still raises and writes nothing.
    """

    before_revision = str(schedule.get("revision") or schedule.get("fingerprint") or "")
    reasons = [str(item["message"]) for item in refusals]
    details = {
        "tournament_id": tournament_id,
        "removed_team": {"label": remove_team_label},
        "added_team": {"label": add_team_label},
        "before_fingerprint": before_revision,
        "candidate_revision": None,
        "request_id": str(request_id or ""),
        "can_apply_unchanged": False,
        "refusal_reasons": reasons,
        "refusals": [dict(item) for item in refusals],
    }
    if verification_result is not None:
        details["verification_result"] = dict(verification_result)
    return {
        "season": season,
        "dry_run": True,
        "current_revision": schedule.get("revision"),
        "current_fingerprint": schedule.get("fingerprint"),
        "current_canonical_revision": canonical_state_revision(schedule, decisions),
        "candidate_revision": None,
        "candidate_fingerprint": None,
        "verification_result": {"ok": False, "violations": []},
        "change_cost": None,
        "verdict": {
            "status": "blocked",
            "applicable": False,
            "checks": {"candidate_constructed": False},
            "blockers": reasons,
        },
        "replacement": details,
    }
