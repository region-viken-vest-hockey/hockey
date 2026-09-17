"""Interactive Stage 3 state access for the CLI adapter.

Historically this module read and wrote three ad-hoc side files
(``stage3_interactive_state.json``, ``shared_host_decision_state.json``,
``arena_conflict_decision_state.json``) directly, and every caller had to
coordinate which of them to read, preserve, rewrite or clear. That is exactly
the ownership the explicit :class:`~...application.stage3_session.Stage3Session`
was introduced to remove.

These helpers are now a thin compatibility facade over the canonical
:class:`~...application.stage3_session_store.Stage3SessionStore`: reads project
the session, writes fold into the session, and clears affect the session. The
store keeps writing the legacy files as non-authoritative mirrors during the
migration window so existing work directories and external observers keep
working, but the session is the single source of truth. New callers should use
the store (or ``rvv-miniputt stage3 session``) directly.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def _current_run_id(state: "Any") -> str:
    """Return the active run manifest's ``run_id``, or ``""`` if unreadable."""
    try:
        from ...pipeline.run_manifest import RunManifest

        return str(RunManifest(state.work_dir).read().get("run_id") or "")
    except Exception:
        return ""


def _store(state: "Any"):
    from ...application.stage3_session_store import Stage3SessionStore

    return Stage3SessionStore(state.work_dir)


# -- legacy paths (compatibility artifacts / migration inputs) -------------


def _stage3_interactive_state_path(state: "Any") -> Path:
    return state.work_dir / "stage3_interactive_state.json"


def _shared_host_state_path(state: "Any") -> Path:
    return state.work_dir / "shared_host_decision_state.json"


def _arena_conflict_state_path(state: "Any") -> Path:
    return state.work_dir / "arena_conflict_decision_state.json"


# -- interactive attempt state --------------------------------------------


def _read_stage3_interactive_state(state: "Any", expected_run_id: str | None = None) -> dict[str, Any]:
    """Return the session's interactive-attempt projection, scoped to the run.

    A finalized or never-populated session reports no interactive state, so a
    superseded run's attempt count / "best plan so far" cannot resurface as
    this run's state.
    """
    return _store(state).interactive_view(expected_run_id)


def _write_stage3_interactive_state(state: "Any", data: dict[str, Any]) -> None:
    _store(state).record_emission(data, run_id=_current_run_id(state))


def _clear_stage3_interactive_state(state: "Any") -> None:
    # The attempt projection is transient; the canonical session keeps the
    # finalized revision/fingerprint. Removing the compatibility mirror is a
    # best-effort cleanup only.
    try:
        _stage3_interactive_state_path(state).unlink(missing_ok=True)
    except Exception:
        pass


# -- shared-host sub-decision state ---------------------------------------


def _read_shared_host_state(state: "Any", expected_run_id: str | None = None) -> dict[str, Any]:
    return _store(state).shared_host_view(expected_run_id)


def _write_shared_host_state(state: "Any", data: dict[str, Any]) -> None:
    _store(state).record_shared_host(data, run_id=_current_run_id(state))


def _clear_shared_host_state(state: "Any") -> None:
    _store(state).clear_shared_host(run_id=_current_run_id(state))


# -- arena-conflict sub-decision state ------------------------------------


def _read_arena_conflict_state(state: "Any", expected_run_id: str | None = None) -> dict[str, Any]:
    return _store(state).arena_view(expected_run_id)


def _write_arena_conflict_state(state: "Any", data: dict[str, Any]) -> None:
    _store(state).record_arena(data, run_id=_current_run_id(state))


def _clear_arena_conflict_state(state: "Any") -> None:
    _store(state).clear_arena(run_id=_current_run_id(state))
