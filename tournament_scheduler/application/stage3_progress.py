"""Progress- and strategy-aware continuation semantics for interactive Stage 3.

The LLM/controller owns *whether* continuing a Stage 3 search/repair is still
worthwhile and in which direction. This module owns the deterministic facts
that decision needs, plus the loop-safety bounds that keep a runaway or broken
orchestration from searching forever:

- one canonical, candidate-scoped action signature, so "the exact same action
  against the exact same candidate" is an identity rather than a string
  comparison;
- per-attempt progress records (did this action change the candidate or reduce
  hard violations?);
- the concise ``search_history`` projection a DecisionContext exposes so the
  controller decides from prior-attempt evidence instead of a raw attempt
  count;
- a generous emergency circuit breaker that is a *technical safety failure*
  (runaway/broken orchestration), never evidence that the planning problem is
  unsolvable.

It contains no hockey legality, no repair/search selection and no transport
policy. The lifecycle controller applies the signature/bounds; the CLI only
renders the projection.

A raw attempt count is deliberately *not* a continuation gate here: attempting
five materially different strategies is not the same as repeating the same
no-op five times, and only the latter is bounded.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

# Emergency-only circuit breaker. Deliberately far above any plausible
# legitimate Stage 3 exploration: a genuinely useful session may run several
# distinct repair/search/evidence investigations, so tripping this means the
# orchestration is looping or broken. It must be surfaced as a technical
# safety failure, never as "planning exhausted".
EMERGENCY_STAGE3_ACTION_LIMIT = 40

# Action ids whose repetition is bounded by no-progress signature identity.
# Adoption/operator actions are loop terminators, not search strategies, so
# they keep their own validation.
PROGRESS_SCOPED_ACTIONS = frozenset({"optimize_plan", "apply_repair_option"})

# Identity keys carry the exact candidate/revision an action was issued
# against and are validated separately as stale-action scope. They are not
# part of the action's search strategy.
_IDENTITY_ARGUMENT_KEYS = frozenset({"candidate_fingerprint", "candidate_revision"})

# Number of most-recent action ids exposed in ``search_history.last_actions``.
LAST_ACTIONS_WINDOW = 5


def action_signature(action_id: str, arguments: Mapping[str, Any] | None = None) -> str:
    """Return the canonical signature of one Stage 3 action.

    The signature is ``(action_id, normalized non-identity arguments)``; the
    candidate/revision an action targets is tracked separately so the same
    strategy applied to a *different* candidate is a different attempt.
    """
    normalized = {
        str(key): value
        for key, value in dict(arguments or {}).items()
        if str(key) not in _IDENTITY_ARGUMENT_KEYS
    }
    payload = json.dumps(
        {"action": str(action_id), "arguments": normalized},
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def build_attempt_record(
    *,
    action_id: str,
    arguments: Mapping[str, Any] | None,
    candidate_revision: int,
    candidate_fingerprint: str,
    transition: str,
    progress: bool,
    hard_violations: int | None,
) -> dict[str, Any]:
    """Build one concise continuation-evidence record for a Stage 3 attempt."""
    return {
        "action_id": str(action_id),
        "action_signature": action_signature(action_id, arguments),
        "candidate_revision": int(candidate_revision),
        "candidate_fingerprint": str(candidate_fingerprint or ""),
        "transition": str(transition),
        "progress": bool(progress),
        "hard_violations": hard_violations,
        "at": datetime.now(timezone.utc).isoformat(),
    }


def upsert_attempt(
    attempts: Sequence[Mapping[str, Any]], record: Mapping[str, Any]
) -> list[dict[str, Any]]:
    """Insert *record*, replacing a prior record for the same attempt scope.

    An attempt is identified by ``(candidate_revision, action_signature)`` so
    re-emitting the same decision (for example after an in-process arena
    repair) updates one record instead of inflating the action count.
    """
    result = [dict(item) for item in attempts]
    key = (record.get("candidate_revision"), record.get("action_signature"))
    for index, item in enumerate(result):
        if (item.get("candidate_revision"), item.get("action_signature")) == key:
            result[index] = dict(record)
            return result
    result.append(dict(record))
    return result


def repeated_no_progress_signature(
    attempts: Sequence[Mapping[str, Any]],
    *,
    action_id: str,
    signature: str,
    candidate_fingerprint: str,
) -> bool:
    """True when this exact action already ran against this exact candidate
    without producing progress.

    Only progress-scoped actions are bounded here. A *different* signature
    (another repair family, search neighborhood, roster/date hypothesis or
    evidence investigation) is always allowed, and so is the same signature
    against a different candidate.
    """
    if action_id not in PROGRESS_SCOPED_ACTIONS:
        return False
    expected_fingerprint = str(candidate_fingerprint or "")
    for item in reversed(list(attempts)):
        if str(item.get("action_signature") or "") != signature:
            continue
        if str(item.get("candidate_fingerprint") or "") != expected_fingerprint:
            continue
        return not bool(item.get("progress", False))
    return False


def build_search_history(attempts: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Project attempt records into the concise continuation-evidence dict.

    Everything the LLM/controller needs to decide whether another bounded,
    materially different attempt is worthwhile lives here; the harness must
    not reconstruct it from logs, side files or a raw attempt counter.
    """
    records = [dict(item) for item in attempts]
    signatures = [str(item.get("action_signature") or "") for item in records]
    fingerprints = {
        str(item.get("candidate_fingerprint") or "")
        for item in records
        if item.get("candidate_fingerprint")
    }
    hard_values = [
        int(item["hard_violations"])
        for item in records
        if item.get("hard_violations") is not None
    ]
    repeated_no_progress = sum(1 for item in records if not bool(item.get("progress", False)))
    return {
        "actions_used": len(records),
        "unique_action_signatures": len(set(signatures)),
        "candidate_revisions": len(fingerprints),
        "hard_violations_before": hard_values[0] if hard_values else None,
        "hard_violations_now": hard_values[-1] if hard_values else None,
        "repeated_no_progress_actions": repeated_no_progress,
        "last_actions": [str(item.get("action_id") or "") for item in records[-LAST_ACTIONS_WINDOW:]],
        "circuit_breaker_limit": EMERGENCY_STAGE3_ACTION_LIMIT,
        "circuit_breaker_tripped": len(records) >= EMERGENCY_STAGE3_ACTION_LIMIT,
    }
