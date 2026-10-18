"""Structured, harness-neutral progress for Stage 2 calendar collection.

The tracker records per-source lifecycle facts while sources are being
collected and persists a small snapshot to the work directory so any
interactive surface can poll it without parsing log prose.

This is operator feedback only. Source success, event counts, blocked state
and trust remain owned by the Stage 2 result and its source-integrity
contract; the snapshot never replaces or contradicts them.
"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

STAGE2_PROGRESS_FILENAME = "stage2_progress.json"

SOURCE_PENDING = "pending"
SOURCE_ACTIVE = "active"
SOURCE_COMPLETED = "completed"
SOURCE_CACHED = "cached"
SOURCE_BLOCKED = "blocked"
SOURCE_FAILED = "failed"

_TERMINAL_STATES = frozenset({SOURCE_COMPLETED, SOURCE_CACHED, SOURCE_BLOCKED, SOURCE_FAILED})


class Stage2ProgressTracker:
    """Thread-safe per-source progress recorder for one Stage 2 run.

    Each transition rewrites ``stage2_progress.json`` atomically (temp file +
    ``os.replace``) so a concurrent reader sees either the previous or the new
    snapshot, never a partial file. Persistence failures are swallowed: progress
    is best-effort and must never fail the scrape it describes.

    The file is rewritten on lifecycle transitions only, so it does not carry a
    live elapsed value for an active source. Instead every active source carries
    a wall-clock ``started_at``; a poller derives current elapsed time as
    ``now - started_at`` on each read.
    """

    def __init__(self, work_dir: Path, source_names: list[str]) -> None:
        self._path = Path(work_dir) / STAGE2_PROGRESS_FILENAME
        self._lock = threading.Lock()
        self._started_at = _now_iso()
        self._order = list(source_names)
        self._sources: dict[str, dict[str, Any]] = {
            name: {"name": name, "state": SOURCE_PENDING, "elapsed_s": None}
            for name in self._order
        }
        self._source_started: dict[str, float] = {}
        self._write(finished=False)

    @property
    def path(self) -> Path:
        return self._path

    def mark_active(self, name: str) -> None:
        with self._lock:
            entry = self._entry(name)
            entry["state"] = SOURCE_ACTIVE
            self._source_started[name] = time.monotonic()
            entry["started_at"] = _now_iso()
            self._write(finished=False)

    def mark_cached(self, name: str, *, event_count: int) -> None:
        self._finish(name, SOURCE_CACHED, event_count=event_count)

    def mark_result(self, name: str, source_result: dict[str, Any]) -> None:
        """Record a finished source from its Stage 2 source result dict."""
        if source_result.get("blocked"):
            state = SOURCE_BLOCKED
        else:
            state = SOURCE_COMPLETED
        self._finish(
            name,
            state,
            event_count=int(source_result.get("event_count") or 0),
            block_reason=str(source_result.get("block_reason") or "") or None,
        )

    def mark_failed(self, name: str, error: str) -> None:
        self._finish(name, SOURCE_FAILED, block_reason=error)

    def finish(self) -> dict[str, Any]:
        """Persist the final snapshot once every source has been recorded."""
        with self._lock:
            return self._write(finished=True)

    def snapshot(self) -> dict[str, Any]:
        """Return the current progress as a JSON-serialisable dict."""
        with self._lock:
            return self._build(finished=False)

    # ------------------------------------------------------------------

    def _entry(self, name: str) -> dict[str, Any]:
        if name not in self._sources:
            self._order.append(name)
            self._sources[name] = {"name": name, "state": SOURCE_PENDING, "elapsed_s": None}
        return self._sources[name]

    def _finish(
        self,
        name: str,
        state: str,
        *,
        event_count: int | None = None,
        block_reason: str | None = None,
    ) -> None:
        with self._lock:
            entry = self._entry(name)
            started = self._source_started.pop(name, None)
            entry.pop("started_at", None)
            entry["state"] = state
            entry["elapsed_s"] = (
                round(time.monotonic() - started, 1) if started is not None else 0.0
            )
            if event_count is not None:
                entry["event_count"] = event_count
            if block_reason:
                entry["block_reason"] = block_reason
            self._write(finished=False)

    def _build(self, *, finished: bool) -> dict[str, Any]:
        sources = [dict(self._sources[name]) for name in self._order]
        return {
            "started_at": self._started_at,
            "updated_at": _now_iso(),
            "finished": finished,
            "total": len(sources),
            "completed": sum(1 for s in sources if s["state"] in _TERMINAL_STATES),
            "sources": sources,
        }

    def _write(self, *, finished: bool) -> dict[str, Any]:
        snapshot = self._build(finished=finished)
        payload = json.dumps(snapshot, indent=2, ensure_ascii=False)
        tmp_path = self._path.with_name(f"{self._path.name}.tmp-{uuid.uuid4().hex[:8]}")
        try:
            tmp_path.write_text(payload, encoding="utf-8")
            os.replace(tmp_path, self._path)
        except OSError:
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass
        return snapshot


def read_stage2_progress(work_dir: Path) -> dict[str, Any] | None:
    """Return the latest persisted Stage 2 progress snapshot, if any."""
    path = Path(work_dir) / STAGE2_PROGRESS_FILENAME
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _now_iso() -> str:
    return datetime.now(tz=UTC).isoformat()
