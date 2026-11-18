"""Canonical season store port: load, revision-checked atomic write, history.

The legacy filesystem/Git-backed ``infrastructure.canonical_season_store`` is the
reference adapter; a local durable workspace is a second adapter. Mutation,
verification and revision semantics stay in ``CanonicalSeasonService``: a store
only persists a complete snapshot atomically and reports typed failures.
"""

from __future__ import annotations

from typing import Any, Mapping, Protocol, runtime_checkable

from .common import PortCapabilities

STORE_FEATURES = frozenset({"atomic_write", "revision_cas", "revision_history", "evidence_archive"})


@runtime_checkable
class CanonicalStorePort(Protocol):
    """Persistence of one season's complete canonical state.

    Target contract (what the application may rely on once an adapter sits
    behind the phase-2 translation boundary, ADR 0009):

    * ``load`` returns schedule, decisions and export context together or raises
      ``PortError(NOT_FOUND | SCHEMA_INCOMPATIBLE | UNAVAILABLE)``; never a half state.
    * ``write`` installs the whole snapshot atomically. With ``expected_revision``
      it is a compare-and-swap and raises ``PortError(REVISION_CONFLICT)`` when
      the stored revision differs. ``require_absent`` refuses to overwrite.
    * A failed write leaves the previous state intact.

    Legacy status: the unchanged ``CanonicalSeasonStore`` already satisfies the
    structural methods and the atomicity/CAS/preservation *behavior*, but still
    raises its own exceptions (``CanonicalRevisionConflictError``,
    ``SeasonStateError``, ``OSError``). Translating them to ``PortError`` belongs
    to the phase-2 adapter boundary; until then callers must not assume
    ``PortError`` from the legacy store.
    """

    def has_state(self, season: str) -> bool: ...

    def load(self, season: str) -> Any: ...

    def write(
        self,
        snapshot: Any,
        *,
        require_absent: bool = False,
        expected_revision: str | None = None,
    ) -> None: ...


@runtime_checkable
class RevisionHistoryPort(Protocol):
    """Resolve the canonical schedule payload as of a recorded revision.

    Returns ``None`` when the revision cannot be resolved unambiguously; callers
    fail closed and never reconstruct identity from the current plan.
    """

    def schedule_at(self, season: str, revision: str) -> Mapping[str, Any] | None: ...


def store_capabilities(adapter_id: str, *, features: frozenset[str]) -> PortCapabilities:
    unknown = features - STORE_FEATURES
    if unknown:
        raise ValueError(f"unknown store features: {sorted(unknown)}")
    return PortCapabilities(adapter_id=adapter_id, features=features)
