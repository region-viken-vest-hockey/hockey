"""Sanitized per-run provenance/evidence bundle (issue #264 P0).

Every production export should be reviewable without reconstructing shell
history or trusting a prose commit message: a reviewer should be able to
determine exactly why the final candidate was selected from committed
artifacts alone. This module assembles that evidence from data the pipeline
already produces -- the run manifest's decision log (issue #264 P0's
run-scoping fix makes this trustworthy per-run), the Stage 3 controller's
per-attempt log, Stage 2's calendar-evidence summary, and a fresh
independent :func:`~tournament_scheduler.planning_contract.verify_candidate`
/ :func:`~tournament_scheduler.planning_contract.score_candidate` run over
the final selected candidate -- into one JSON file written alongside the
Stage 4 export.

Deliberately contains no LLM calls and no secrets/credentials: candidate
fingerprints are content hashes (:func:`fingerprints.stable_payload_sha256`),
and every other field is already non-secret operator-facing data the
pipeline persists elsewhere.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

EVIDENCE_BUNDLE_SCHEMA_VERSION = 1

_ATTEMPT_LOG_FILENAME = "stage3_attempt_log.json"


def stage3_attempt_log_path(work_dir: "Path | str") -> Path:
    return Path(work_dir) / _ATTEMPT_LOG_FILENAME


def read_stage3_attempt_log(work_dir: "Path | str") -> list[dict[str, Any]]:
    """Return the accumulated Stage 3 per-attempt evidence log for this run.

    Returns an empty list if the file is missing or unreadable -- a run
    that never reached Stage 3, or one from before this file existed,
    simply has no attempt-level evidence rather than a crash.
    """
    path = stage3_attempt_log_path(work_dir)
    if not path.exists():
        return []
    try:
        import json

        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    return data if isinstance(data, list) else []


def append_stage3_attempt_log_entry(work_dir: "Path | str", entry: dict[str, Any]) -> None:
    """Append one Stage 3 attempt's evidence to the durable per-run log.

    Unlike ``stage3_interactive_state.json`` (cleared once the Stage 3
    optimize/apply/keep-baseline loop resolves), this file is append-only
    for the lifetime of a run and is only reset at the start of a genuinely
    new run (see ``cli.pipeline_orchestrator``'s fresh-run-start handling) --
    so a reviewer can see every attempt that was tried, not just the one
    that was ultimately selected.
    """
    import json

    log = read_stage3_attempt_log(work_dir)
    log.append(entry)
    stage3_attempt_log_path(work_dir).write_text(
        json.dumps(log, indent=2, ensure_ascii=False, default=str), encoding="utf-8"
    )


def clear_stage3_attempt_log(work_dir: "Path | str") -> None:
    try:
        stage3_attempt_log_path(work_dir).unlink(missing_ok=True)
    except Exception:
        pass


def build_stage3_attempt_entry(
    *,
    attempt: int,
    candidate: dict[str, Any],
    problem: dict[str, Any] | None,
) -> dict[str, Any]:
    """Build one Stage 3 attempt-log entry from a candidate that was just produced.

    Independently re-verifies/re-scores *candidate* here (rather than
    trusting whatever summary a caller already computed) so the attempt log
    is authoritative on its own, matching ``planning_contract``'s "no
    planner-trusted shortcuts" philosophy.
    """
    from ..planning_contract import score_candidate, verify_candidate
    from .fingerprints import stable_payload_sha256

    return {
        "attempt": attempt,
        "candidate_fingerprint": stable_payload_sha256(candidate.get("tournaments", [])),
        "candidate_source": candidate.get("source"),
        "verify_result": verify_candidate(candidate, problem),
        "score_result": score_candidate(candidate),
    }


def build_run_evidence_bundle(
    *,
    run_id: str,
    input_fingerprint: dict[str, Any] | None,
    decision_log: list[dict[str, Any]] | None,
    scraping_checkpoint: dict[str, Any] | None,
    stage3_attempt_log: list[dict[str, Any]] | None,
    final_candidate: dict[str, Any] | None,
    final_verify_result: dict[str, Any] | None,
    final_score_result: dict[str, Any] | None,
    export_dir: str | None,
    export_output_files: dict[str, str] | None,
) -> dict[str, Any]:
    """Assemble the sanitized evidence bundle for one pipeline run.

    Pure/deterministic given its inputs (no filesystem/manifest access of
    its own, aside from what callers already read) so it is independently
    testable and reusable from both ``run --interactive`` and the legacy
    ``run`` entrypoint.
    """
    scraping_checkpoint = scraping_checkpoint or {}
    source_summary = {
        "sources_scanned": len(scraping_checkpoint.get("sources", []) or []),
        "blocked_sources": list(scraping_checkpoint.get("blocked", []) or []),
        "club_calendar_status": dict(scraping_checkpoint.get("club_calendar_status", {}) or {}),
    }

    final_candidate_summary: dict[str, Any] | None = None
    if final_candidate is not None:
        from .fingerprints import stable_payload_sha256

        final_candidate_summary = {
            "fingerprint": stable_payload_sha256(final_candidate.get("tournaments", [])),
            "source": final_candidate.get("source"),
        }

    return {
        "schema_version": EVIDENCE_BUNDLE_SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "run_id": run_id,
        "input_fingerprint": input_fingerprint or {},
        "source_summary": source_summary,
        "decision_log": list(decision_log or []),
        "stage3_attempt_log": list(stage3_attempt_log or []),
        "final_candidate": final_candidate_summary,
        "final_verify_result": final_verify_result,
        "final_score_result": final_score_result,
        "export": {
            "dir": export_dir,
            "output_files": dict(export_output_files or {}),
        },
    }
