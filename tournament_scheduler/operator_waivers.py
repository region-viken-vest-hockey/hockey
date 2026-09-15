"""First-class, narrowly scoped, audited operator waivers for hard planning rules.

The planner conflates two different meanings of "hard":

* structural invariants -- the plan/data is malformed or internally
  inconsistent (unknown team identifiers, corrupt serialization, invalid
  tournament identity). These can never be waived by anyone, including an
  operator.
* hard planning rules -- the planner, optimizer and agents must never
  violate these autonomously, but an authorized operator may explicitly
  waive one for a precise scope. :data:`WAIVABLE_RULE_IDS` enumerates the
  rules the verifier currently understands as waivable; everything else is
  treated as structural/not-yet-classified and remains blocking.

Core safety rule: the planner, optimizer and agent decision protocol may
*suggest* an operator waiver, but may never create, broaden or silently
infer one. Only an explicit operator action (the ``rvv-miniputt waiver``
CLI capability) writes to this store. Without a matching active waiver the
verifier's hard behavior is unchanged.

A waiver is tied to the exact state it authorizes (team identity, half,
configured target, accepted actual, and the tournament the extra
participation lands in). If any of those change, the waiver simply stops
matching and normal hard failure returns -- there is no "broaden on later
mutation" path.

Store: ``<work_dir>/operator_waivers.json``.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

SCHEMA_VERSION = 1
STORE_FILENAME = "operator_waivers.json"

# Rules the verifier may downgrade to an explicit operator-waived finding.
# Add a rule here only after classifying it as an operator-waivable planning
# rule (not a structural invariant).
WAIVABLE_RULE_IDS: frozenset[str] = frozenset({"participation_target_exceeded"})

# Named explicitly (and asserted by tests) so the classification is visible:
# these mean the plan/data is malformed or internally inconsistent and must
# stay blocking for everyone, including operators.
NON_WAIVABLE_STRUCTURAL_INVARIANTS: frozenset[str] = frozenset(
    {
        "unregistered_team",
        "duplicate_team_in_tournament",
        "duplicate_participation_same_date",
        "age_group_mismatch",
        "invalid_date",
        "invalid_game_record",
        "invalid_game_round",
        "game_team_not_participant",
        "game_integrity_ambiguous_participants",
        "team_double_booked_in_round",
        "tournament_under_minimum",
        "unknown_team",
        "corrupt_serialization",
    }
)


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def waivers_path(work_dir: "str | os.PathLike[str]") -> Path:
    return Path(work_dir) / STORE_FILENAME


def _canonical_scope(
    *,
    rule: str,
    team: Mapping[str, Any],
    tournament_id: "str | None",
    half: "str | None",
    configured_value: int,
    allowed_value: int,
) -> dict[str, Any]:
    return {
        "rule": rule,
        "team": {
            "club": str(team.get("club", "")),
            "label": str(team.get("label", "")),
            "age_group": str(team.get("age_group", "")),
        },
        "tournament_id": str(tournament_id) if tournament_id else None,
        "half": str(half) if half else None,
        "configured_value": int(configured_value),
        "allowed_value": int(allowed_value),
    }


def scope_fingerprint(
    *,
    rule: str,
    team: Mapping[str, Any],
    tournament_id: "str | None",
    half: "str | None",
    configured_value: int,
    allowed_value: int,
) -> str:
    """Stable hash of the exact state a waiver authorizes.

    Recomputed from the record's own authoritative fields on every load and
    matching pass, so a hand-edited store that claims a different
    fingerprint no longer matches anything.
    """
    payload = json.dumps(
        _canonical_scope(
            rule=rule,
            team=team,
            tournament_id=tournament_id,
            half=half,
            configured_value=configured_value,
            allowed_value=allowed_value,
        ),
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class WaiverError(ValueError):
    """An operator waiver could not be created/revoked as requested."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


def load_waivers(work_dir: "str | os.PathLike[str]") -> list[dict[str, Any]]:
    """Return every stored waiver record (active and revoked)."""
    path = waivers_path(work_dir)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    records = data.get("waivers") if isinstance(data, dict) else None
    if not isinstance(records, list):
        return []
    return [record for record in records if isinstance(record, dict)]


def load_active_waivers(work_dir: "str | os.PathLike[str]") -> list[dict[str, Any]]:
    return [record for record in load_waivers(work_dir) if record.get("active", True)]


def save_waivers(work_dir: "str | os.PathLike[str]", records: Sequence[Mapping[str, Any]]) -> None:
    path = waivers_path(work_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"schema_version": SCHEMA_VERSION, "waivers": [dict(r) for r in records]}
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def create_waiver(
    work_dir: "str | os.PathLike[str]",
    *,
    rule: str,
    team: Mapping[str, Any],
    tournament_id: "str | None" = None,
    half: "str | None" = None,
    configured_value: int,
    allowed_value: int,
    reason: str,
    actor: "str | None" = None,
    evidence: "Mapping[str, Any] | None" = None,
    now: "str | None" = None,
) -> tuple[dict[str, Any], bool]:
    """Persist an operator-authorized waiver for exactly one scoped exception.

    Returns ``(record, created)`` -- ``created`` is ``False`` when an active
    waiver with the identical scope/values already exists (idempotent
    re-authorization). Callers must be genuine operator actions; this
    function performs no agent-facing validation beyond argument sanity.
    """
    if rule not in WAIVABLE_RULE_IDS:
        raise WaiverError(f"rule {rule!r} is not an operator-waivable rule")
    team = dict(team or {})
    label = str(team.get("label", "") or "").strip()
    club = str(team.get("club", "") or "").strip()
    age_group = str(team.get("age_group", "") or "").strip()
    if not label:
        raise WaiverError("team label is required")
    if not club or not age_group:
        raise WaiverError("team club and age_group are required")
    if not str(reason or "").strip():
        raise WaiverError("operator reason is required")
    try:
        configured = int(configured_value)
        allowed = int(allowed_value)
    except (TypeError, ValueError) as exc:
        raise WaiverError("configured_value and allowed_value must be integers") from exc
    if rule == "participation_target_exceeded" and allowed <= configured:
        raise WaiverError(
            "for participation_target_exceeded, allowed_value must exceed configured_value "
            "(this rule authorizes going over the target, not under it)"
        )

    canonical_team = {"club": club, "label": label, "age_group": age_group}
    fingerprint = scope_fingerprint(
        rule=rule,
        team=canonical_team,
        tournament_id=tournament_id,
        half=half,
        configured_value=configured,
        allowed_value=allowed,
    )

    records = load_waivers(work_dir)
    for record in records:
        if record.get("active", True) and record.get("scope_fingerprint") == fingerprint:
            return record, False

    base_id = f"waiver-{fingerprint[:16]}"
    existing_ids = {str(record.get("id")) for record in records}
    waiver_id = base_id
    suffix = 2
    while waiver_id in existing_ids:
        waiver_id = f"{base_id}-{suffix}"
        suffix += 1

    timestamp = now or _now_iso()
    record: dict[str, Any] = {
        "id": waiver_id,
        "rule": rule,
        "scope": {
            "team": canonical_team,
            "tournament_id": str(tournament_id) if tournament_id else None,
            "half": str(half) if half else None,
        },
        "configured_value": configured,
        "allowed_value": allowed,
        "reason": str(reason).strip(),
        "created_at": timestamp,
        "created_by": str(actor or _default_actor()),
        "active": True,
        "revoked_at": None,
        "revoked_by": None,
        "revocation_reason": None,
        "scope_fingerprint": fingerprint,
        "evidence": dict(evidence or {}),
    }
    records.append(record)
    save_waivers(work_dir, records)
    return record, True


def revoke_waiver(
    work_dir: "str | os.PathLike[str]",
    waiver_id: str,
    *,
    reason: "str | None" = None,
    actor: "str | None" = None,
    now: "str | None" = None,
) -> dict[str, Any]:
    """Revoke an active waiver by id; normal hard failure returns immediately."""
    records = load_waivers(work_dir)
    target = next((r for r in records if str(r.get("id")) == str(waiver_id)), None)
    if target is None:
        raise WaiverError(f"unknown waiver id {waiver_id!r}")
    if not target.get("active", True):
        raise WaiverError(f"waiver {waiver_id!r} is already revoked")
    target["active"] = False
    target["revoked_at"] = now or _now_iso()
    target["revoked_by"] = str(actor or _default_actor())
    target["revocation_reason"] = str(reason or "").strip() or None
    save_waivers(work_dir, records)
    return target


def _default_actor() -> str:
    return (
        os.environ.get("RVV_OPERATOR")
        or os.environ.get("USER")
        or os.environ.get("USERNAME")
        or "operator"
    )


# ---------------------------------------------------------------------------
# Matching (consumed by the verifier / repair enumeration)
# ---------------------------------------------------------------------------


def waiver_matches_participation(
    waiver: Mapping[str, Any],
    *,
    identity: Sequence[str],
    half: "str | None",
    actual: int,
    configured: int,
    tournament_ids: Iterable[str],
) -> bool:
    """Whether *waiver* authorizes exactly this (team, half, actual) overage."""
    if not waiver.get("active", True):
        return False
    if waiver.get("rule") != "participation_target_exceeded":
        return False
    if not _fingerprint_consistent(waiver):
        return False
    scope = waiver.get("scope") or {}
    team = scope.get("team") or {}
    record_identity = (str(team.get("club", "")), str(team.get("label", "")), str(team.get("age_group", "")))
    if record_identity != tuple(str(part) for part in identity):
        return False
    if (scope.get("half") or None) != (half or None):
        return False
    try:
        if int(waiver.get("allowed_value")) != int(actual):
            return False
        if int(waiver.get("configured_value")) != int(configured):
            return False
    except (TypeError, ValueError):
        return False
    tournament_id = scope.get("tournament_id")
    if tournament_id and str(tournament_id) not in {str(t) for t in tournament_ids}:
        return False
    return True


def _fingerprint_consistent(waiver: Mapping[str, Any]) -> bool:
    scope = waiver.get("scope") or {}
    team = scope.get("team") or {}
    try:
        expected = scope_fingerprint(
            rule=str(waiver.get("rule")),
            team=team,
            tournament_id=scope.get("tournament_id"),
            half=scope.get("half"),
            configured_value=waiver.get("configured_value"),  # type: ignore[arg-type]
            allowed_value=waiver.get("allowed_value"),  # type: ignore[arg-type]
        )
    except (TypeError, ValueError):
        return False
    recorded = waiver.get("scope_fingerprint")
    if recorded and str(recorded) != expected:
        return False
    return True


def find_participation_waiver(
    problem: "Mapping[str, Any] | None",
    *,
    identity: Sequence[str],
    half: "str | None",
    actual: int,
    configured: int,
    tournament_ids: Iterable[str],
) -> "dict[str, Any] | None":
    """First active waiver in *problem* matching this overage, if any."""
    waivers = (problem or {}).get("operator_waivers") or []
    if not isinstance(waivers, list):
        return None
    for waiver in waivers:
        if not isinstance(waiver, dict):
            continue
        if waiver_matches_participation(
            waiver,
            identity=identity,
            half=half,
            actual=actual,
            configured=configured,
            tournament_ids=tournament_ids,
        ):
            return waiver
    return None


def attach_active_waivers(problem: "dict[str, Any]", work_dir: "str | os.PathLike[str]") -> "dict[str, Any]":
    """Attach the work dir's active waivers to a ``planning_problem`` dict.

    Pure data attachment: the verifier remains a pure function over the
    problem contract, and the exact waiver set is captured in the persisted
    problem snapshot rather than read implicitly at verification time.
    """
    problem["operator_waivers"] = load_active_waivers(work_dir)
    return problem


def waiver_audit_rows(problem: "Mapping[str, Any] | None") -> list[dict[str, Any]]:
    """Operator-facing audit rows for every waiver attached to *problem*."""
    rows: list[dict[str, Any]] = []
    waivers = (problem or {}).get("operator_waivers") or []
    if not isinstance(waivers, list):
        return rows
    for waiver in waivers:
        if not isinstance(waiver, dict):
            continue
        scope = waiver.get("scope") or {}
        rows.append(
            {
                "id": waiver.get("id"),
                "rule": waiver.get("rule"),
                "team": dict(scope.get("team") or {}),
                "tournament_id": scope.get("tournament_id"),
                "half": scope.get("half"),
                "configured_value": waiver.get("configured_value"),
                "allowed_value": waiver.get("allowed_value"),
                "reason": waiver.get("reason"),
                "created_at": waiver.get("created_at"),
                "created_by": waiver.get("created_by"),
                "active": bool(waiver.get("active", True)),
                "scope_fingerprint": waiver.get("scope_fingerprint"),
            }
        )
    return rows


__all__ = [
    "SCHEMA_VERSION",
    "STORE_FILENAME",
    "WAIVABLE_RULE_IDS",
    "NON_WAIVABLE_STRUCTURAL_INVARIANTS",
    "WaiverError",
    "waivers_path",
    "scope_fingerprint",
    "load_waivers",
    "load_active_waivers",
    "save_waivers",
    "create_waiver",
    "revoke_waiver",
    "waiver_matches_participation",
    "find_participation_waiver",
    "attach_active_waivers",
    "waiver_audit_rows",
]
