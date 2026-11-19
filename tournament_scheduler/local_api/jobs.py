"""Bounded, cancellable local jobs with phase/progress/error/cost data.

Long operations (operate sessions, planner, source acquisition) run here, never on
the request thread. The pool and the retained-job count are bounded; cancellation
is cooperative through the ``JobContext`` the work function receives.
"""

from __future__ import annotations

import threading
import time
import uuid
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable

from .contracts import ApiError, ErrorCode

MAX_WORKERS = 2
MAX_RETAINED = 100
MAX_QUEUED = 16


class JobCancelled(Exception):
    pass


@dataclass
class JobState:
    job_id: str
    kind: str
    request_id: str
    status: str = "queued"  # queued | running | succeeded | failed | cancelled
    phase: str = "queued"
    progress: float = 0.0
    error: dict[str, Any] | None = None
    cost: dict[str, Any] = field(default_factory=lambda: {"provider_calls": 0, "elapsed_s": 0.0})
    result: Any = None
    committed: bool = False  # past the cancellation boundary: the job will finish, cancel is "too late"
    guard: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def view(self) -> dict[str, Any]:
        return {"job_id": self.job_id, "kind": self.kind, "request_id": self.request_id,
                "status": self.status, "phase": self.phase, "progress": self.progress,
                "error": self.error, "cost": dict(self.cost)}


class JobContext:
    def __init__(self, state: JobState, cancel: threading.Event) -> None:
        self._state, self._cancel = state, cancel

    @property
    def job_id(self) -> str:
        return self._state.job_id

    def is_active(self) -> bool:
        return not self._cancel.is_set()

    def try_commit(self) -> bool:
        """The single atomic cancellation boundary for this job.

        Under the job guard either cancellation was already requested (returns False and the
        caller must create nothing) or the job is marked committed (a later cancel is "too late"
        and the job may finish ``succeeded``). A job therefore never both succeeds with a
        registered result and reports cancelled.
        """
        with self._state.guard:
            if self._cancel.is_set():
                return False
            self._state.committed = True
            return True

    def report(self, phase: str, progress: float) -> None:
        self.checkpoint()
        self._state.phase, self._state.progress = phase, progress

    def checkpoint(self) -> None:
        if self._cancel.is_set():
            raise JobCancelled()


class JobManager:
    def __init__(self, workers: int = MAX_WORKERS) -> None:
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="operate-job")
        self._lock = threading.Lock()
        self._jobs: "OrderedDict[str, tuple[JobState, threading.Event, threading.Event]]" = OrderedDict()

    def submit(self, kind: str, request_id: str, work: Callable[[JobContext], Any]) -> JobState:
        with self._lock:
            active = sum(1 for s, _, _ in self._jobs.values() if s.status in ("queued", "running"))
            if active >= MAX_QUEUED:
                raise ApiError(503, ErrorCode.REVIEW_REQUIRED, "too many active jobs; retry later")
            state = JobState(uuid.uuid4().hex, kind, request_id)
            cancel, done = threading.Event(), threading.Event()
            self._jobs[state.job_id] = (state, cancel, done)
            while len(self._jobs) > MAX_RETAINED:
                oldest = next((k for k, (s, _, d) in self._jobs.items() if d.is_set()), None)
                if oldest is None:
                    break
                del self._jobs[oldest]
        self._pool.submit(self._run, state, cancel, done, work)
        return state

    def _run(self, state: JobState, cancel: threading.Event, done: threading.Event, work) -> None:
        started = time.monotonic()
        try:
            if cancel.is_set():
                raise JobCancelled()
            state.status = "running"
            result = work(JobContext(state, cancel))
            with state.guard:  # final cancellation checkpoint, consistent with try_commit
                if cancel.is_set() and not state.committed:
                    raise JobCancelled()
                state.result = result
                state.status, state.phase, state.progress = "succeeded", "done", 1.0
        except JobCancelled:
            state.status, state.phase = "cancelled", "cancelled"
        except ApiError as exc:
            state.status, state.phase, state.error = "failed", "failed", exc.to_dict()
        except Exception as exc:  # job boundary: never leak a traceback to the client
            state.status, state.phase = "failed", "failed"
            state.error = {"code": ErrorCode.INTERNAL.value, "message": type(exc).__name__, "details": {}}
        finally:
            state.cost["elapsed_s"] = round(time.monotonic() - started, 3)
            done.set()

    def get(self, job_id: str) -> JobState:
        with self._lock:
            entry = self._jobs.get(job_id)
        if entry is None:
            raise ApiError(404, ErrorCode.NOT_FOUND, "unknown job")
        return entry[0]

    def wait(self, job_id: str, timeout: float) -> JobState:
        entry = self._jobs.get(job_id)
        if entry is None:
            raise ApiError(404, ErrorCode.NOT_FOUND, "unknown job")
        entry[2].wait(timeout)
        return entry[0]

    def cancel(self, job_id: str) -> tuple[JobState, bool]:
        """Request cancellation. Returns ``(state, accepted)``; ``accepted`` is False when too late."""
        state = self.get(job_id)
        with state.guard:
            if state.committed or state.status in ("succeeded", "failed", "cancelled"):
                return state, False
            self._jobs[job_id][1].set()
            return state, True

    def shutdown(self) -> None:
        with self._lock:
            for _, cancel, _ in self._jobs.values():
                cancel.set()
        self._pool.shutdown(wait=True, cancel_futures=True)
