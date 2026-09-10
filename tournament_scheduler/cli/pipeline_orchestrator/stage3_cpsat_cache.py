"""Run-scoped cache of automatic/explicit CP-SAT Stage 3 results (issue #310).

Follows the same run_id-scoped JSON side-file pattern as
:mod:`interactive_state_io`'s Stage 3 interactive state: a fingerprint of the
inputs that actually determine a CP-SAT solve (problem, baseline candidate,
request options) is the cache key, so an unchanged (problem, baseline,
request) triple never re-solves -- whether the repeat call is another
automatic shadow evaluation for the same attempt, or a later attempt that
happens to leave Stage 3 unchanged.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ...pipeline.fingerprints import stable_payload_sha256


def _stage3_cpsat_cache_path(state: "Any") -> Path:
    return state.work_dir / "stage3_cpsat_cache.json"


def cp_sat_cache_key(
    problem: "dict[str, Any] | None",
    baseline_candidate: "dict[str, Any] | None",
    request: "dict[str, Any]",
) -> str:
    """Deterministic cache key for one (problem, baseline, request) triple.

    Reuses :func:`stable_payload_sha256` (already used inside
    :mod:`stage3_cpsat` for candidate/problem provenance fingerprints)
    rather than inventing a second hashing scheme -- the three fingerprints
    are combined into one key so a change to *any* of them invalidates reuse.
    """
    problem_fp = stable_payload_sha256(problem) if problem is not None else None
    baseline_fp = stable_payload_sha256(baseline_candidate) if baseline_candidate is not None else None
    request_fp = stable_payload_sha256(request)
    return stable_payload_sha256(
        {"problem": problem_fp, "baseline": baseline_fp, "request": request_fp}
    )


def candidate_ref_for_key(cache_key: str) -> str:
    return f"stage3_cp_sat:{cache_key[:16]}"


def _read_cache_file(state: "Any", expected_run_id: str | None) -> dict[str, Any]:
    path = _stage3_cpsat_cache_path(state)
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


def _write_cache_file(state: "Any", data: dict[str, Any]) -> None:
    import json as _json

    _stage3_cpsat_cache_path(state).write_text(
        _json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def read_cp_sat_cache_entry(
    state: "Any", run_id: str | None, cache_key: str
) -> "dict[str, Any] | None":
    """Return the cached entry for *cache_key*, or ``None`` on a miss/foreign run."""
    data = _read_cache_file(state, run_id)
    entries = data.get("entries")
    if not isinstance(entries, dict):
        return None
    entry = entries.get(cache_key)
    return dict(entry) if isinstance(entry, dict) else None


def write_cp_sat_cache_entry(
    state: "Any",
    run_id: str | None,
    cache_key: str,
    *,
    candidate: "dict[str, Any]",
    report: "dict[str, Any]",
) -> "dict[str, Any]":
    """Store *candidate*/*report* under *cache_key* and return the stored entry.

    Increments the file-level ``invocation_count`` -- this is the actual
    solver-invocation counter the issue asks for; a cache *hit* never reaches
    this function, so it only counts real solves.
    """
    data = _read_cache_file(state, run_id)
    data.setdefault("run_id", run_id or "")
    entries = data.setdefault("entries", {})
    entry = {
        "candidate": candidate,
        "report": report,
        "candidate_ref": candidate_ref_for_key(cache_key),
        "computed_at": datetime.now(tz=timezone.utc).isoformat(),
    }
    entries[cache_key] = entry
    data["invocation_count"] = int(data.get("invocation_count", 0)) + 1
    _write_cache_file(state, data)
    return entry


def resolve_candidate_ref(
    state: "Any", run_id: str | None, candidate_ref: str
) -> "dict[str, Any] | None":
    """Look up a cached candidate by the ``candidate_ref`` string a decision
    context handed back to the caller, without needing the original
    (problem, baseline, request) triple that produced it."""
    data = _read_cache_file(state, run_id)
    entries = data.get("entries")
    if not isinstance(entries, dict):
        return None
    for entry in entries.values():
        if isinstance(entry, dict) and entry.get("candidate_ref") == candidate_ref:
            return dict(entry)
    return None


def clear_cp_sat_cache(state: "Any") -> None:
    try:
        _stage3_cpsat_cache_path(state).unlink(missing_ok=True)
    except Exception:
        pass
