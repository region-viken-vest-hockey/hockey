"""Stage 3 interactive/shared-host state file read-write-clear helpers."""

from __future__ import annotations

from pathlib import Path
from typing import Any

_MAX_INTERACTIVE_STAGE3_ATTEMPTS = 3


def _stage3_interactive_state_path(state: "Any") -> Path:
    return state.work_dir / "stage3_interactive_state.json"


def _current_run_id(state: "Any") -> str:
    """Return the active run manifest's ``run_id``, or ``""`` if unreadable."""
    try:
        from ...pipeline.run_manifest import RunManifest

        return str(RunManifest(state.work_dir).read().get("run_id") or "")
    except Exception:
        return ""


def _read_stage3_interactive_state(state: "Any", expected_run_id: str | None = None) -> dict[str, Any]:
    """Read the Stage 3 interactive side-state, scoped to *expected_run_id*.

    issue #264 P0: a Stage 3 controller run must not inherit attempt
    counters, a pending candidate, or a "best plan so far" from a
    prior/superseded run sharing the same work directory. When
    *expected_run_id* is given and the persisted state was written under a
    *different* run_id (or carries none at all -- e.g. a file left over from
    before this scoping existed), it is treated as stale/foreign and
    discarded here rather than silently resumed, so a fresh run always
    starts Stage 3 at attempt 1 instead of inheriting an old run's attempt
    count towards :data:`_MAX_INTERACTIVE_STAGE3_ATTEMPTS`.
    """
    path = _stage3_interactive_state_path(state)
    if not path.exists():
        return {}
    try:
        import json as _json

        data = _json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    if expected_run_id and data.get("run_id") != expected_run_id:
        return {}
    return data


def _write_stage3_interactive_state(state: "Any", data: dict[str, Any]) -> None:
    import json as _json

    _stage3_interactive_state_path(state).write_text(
        _json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def _clear_stage3_interactive_state(state: "Any") -> None:
    try:
        _stage3_interactive_state_path(state).unlink(missing_ok=True)
    except Exception:
        pass


def _shared_host_state_path(state: "Any") -> Path:
    return state.work_dir / "shared_host_decision_state.json"


def _read_shared_host_state(state: "Any", expected_run_id: str | None = None) -> dict[str, Any]:
    path = _shared_host_state_path(state)
    if not path.exists():
        return {}
    try:
        import json as _json

        data = _json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    if expected_run_id and data.get("run_id") != expected_run_id:
        return {}
    return data


def _write_shared_host_state(state: "Any", data: dict[str, Any]) -> None:
    import json as _json

    _shared_host_state_path(state).write_text(
        _json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def _clear_shared_host_state(state: "Any") -> None:
    try:
        _shared_host_state_path(state).unlink(missing_ok=True)
    except Exception:
        pass
