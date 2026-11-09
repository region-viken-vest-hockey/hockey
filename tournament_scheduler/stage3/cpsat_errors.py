"""Exceptions raised by the Stage 3 CP-SAT participant engine."""

from __future__ import annotations

from typing import Any, Dict, Optional


class CpSatUnavailable(RuntimeError):
    """Raised when OR-Tools is not installed in the current environment."""


class CpSatNoCandidate(RuntimeError):
    """Raised when CP-SAT cannot return a feasible candidate within its budget."""

    def __init__(
        self,
        status: str,
        runtime_seconds: float,
        *,
        baseline_candidate_fingerprint: Optional[str] = None,
        problem_fingerprint: Optional[str] = None,
        diagnostics: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.status = status
        self.runtime_seconds = runtime_seconds
        self.baseline_candidate_fingerprint = baseline_candidate_fingerprint
        self.problem_fingerprint = problem_fingerprint
        # issue #298: solver-size/model diagnostics (team/slot/variable/
        # constraint counts, half, budget) so a no-candidate result is
        # explainable evidence rather than an opaque timeout -- the caller
        # can tell a genuinely oversized/complex model apart from a solver
        # fluke without re-instrumenting anything.
        self.diagnostics = dict(diagnostics or {})
        super().__init__(f"CP-SAT returned no candidate ({status})")
