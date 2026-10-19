"""Deterministic identity for a reviewed batch scenario.

A revision alone does not identify what a caller reviewed: the same base
revision can receive a different scenario, scope or gate input. The token here
covers everything that determines the candidate and its gates. Preview returns
it, and apply recomputes it from its own inputs and refuses on a mismatch.

``actor`` and ``note`` are deliberately excluded: they change the history
record, not the candidate or the verdict.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping, Sequence

REVIEWED_SCENARIO_TOKEN_PREFIX = "rs-"


def reviewed_scenario_token(
    *,
    season: str,
    base_revision: str,
    candidate_fingerprint: str,
    operations: Sequence[Mapping[str, Any]],
    scope: Sequence[str],
    request_id: str,
    gate_inputs: Mapping[str, Any],
    problem: Mapping[str, Any] | None,
) -> str:
    payload = {
        "season": season,
        "base_revision": base_revision,
        "candidate_fingerprint": candidate_fingerprint,
        "operations": [dict(operation) for operation in operations],
        "scope": sorted(str(item) for item in scope),
        "request_id": request_id,
        "gate_inputs": dict(gate_inputs),
        "problem_sha256": _sha256(problem) if problem else None,
    }
    return REVIEWED_SCENARIO_TOKEN_PREFIX + _sha256(payload)


def _sha256(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
