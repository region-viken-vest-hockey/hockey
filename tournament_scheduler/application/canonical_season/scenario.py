"""Versioned scenario documents for atomic canonical batch maintenance.

A scenario is the typed input to ``batch_maintenance``: an ordered list of
scoped operations plus the declared tournament scope they may touch. Two shapes
are accepted:

* ``canonical-scenario/1`` (preferred): an object with exactly the keys
  ``schema``, ``operations`` and optionally ``scope``.
* A bare JSON list of operations (legacy, unversioned). It carries no scope, so
  the caller must declare one separately.

Unknown schema versions and unknown keys are refused so a document written for
a different contract cannot be applied silently. Operation-level validation
stays with the batch owner; this module only checks the document envelope.
"""

from __future__ import annotations

from typing import Any

from tournament_scheduler.infrastructure.canonical_season_store import SeasonStateError

SCENARIO_SCHEMA = "canonical-scenario/1"

_ALLOWED_KEYS = frozenset({"schema", "operations", "scope"})


def parse_scenario(payload: Any) -> dict[str, Any]:
    """Return ``{"operations": [...], "scope": [...] | None}`` for a scenario document."""

    if isinstance(payload, list):
        return {"operations": _operation_list(payload), "scope": None}
    if not isinstance(payload, dict):
        raise SeasonStateError(
            "Refusing canonical scenario: expected a JSON list of operations or a "
            f"{SCENARIO_SCHEMA} object"
        )
    unknown = sorted(set(payload) - _ALLOWED_KEYS)
    if unknown:
        raise SeasonStateError(f"Refusing canonical scenario: unknown keys {', '.join(unknown)}")
    schema = payload.get("schema")
    if schema != SCENARIO_SCHEMA:
        raise SeasonStateError(
            f"Refusing canonical scenario: unsupported schema {schema!r}; expected {SCENARIO_SCHEMA!r}"
        )
    scope = payload.get("scope")
    if scope is not None:
        if not isinstance(scope, list) or not all(isinstance(item, str) for item in scope):
            raise SeasonStateError("Refusing canonical scenario: scope must be a list of tournament ids")
        scope = [item.strip() for item in scope if item.strip()]
    return {"operations": _operation_list(payload.get("operations")), "scope": scope}


def _operation_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value:
        raise SeasonStateError("Refusing canonical scenario: operations must be a non-empty list")
    return [dict(item) if isinstance(item, dict) else item for item in value]
