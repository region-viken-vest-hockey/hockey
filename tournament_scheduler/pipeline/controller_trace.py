"""Append-only structured controller decision trace for one pipeline run.

The coarse run manifest (``decision_log``) and the Stage 3 per-attempt log
answer *what* was selected, but not *why* the bounded convergence controller
chose one direction/candidate over another, nor which verified improvement a
later epoch replaced. This module owns the missing structurally analyzable
record: a compact, append-only JSONL file of explicit decision inputs/outputs,
stable candidate references and measurable effects.

It deliberately contains no chain-of-thought and no model reasoning. Every
event is written by deterministic repository code at a decision boundary and
carries only:

* an event family name and an optional epoch/direction/finding scope;
* candidate-before/candidate-after fingerprints (stable content identities);
* the option/ref that was selected and the concise explicit rationale;
* the measured objective/verification deltas and Pareto-frontier mutations.

The trace lives outside the export tree (``<work_dir>/logs/<run_id>/``), so it
is never part of a public GitHub Pages bundle. The sanitized evidence bundle
may reference and summarize it, but the detail stays in the run workspace.

The file is append-only and keyed by ``run_id``/sequence number, so it survives
process restarts and resume: a later invocation continues the same file instead
of truncating it.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

CONTROLLER_TRACE_SCHEMA_VERSION = 1
CONTROLLER_TRACE_FILENAME = "controller_trace.jsonl"

# ---------------------------------------------------------------------------
# Event families
# ---------------------------------------------------------------------------
#
# One constant per meaningful controller/harness decision boundary. Keeping
# them named (rather than free-form strings at call sites) makes the trace
# programmatically analyzable and prevents silent vocabulary drift.

EVENT_RUN_START = "run_start"
EVENT_STAGE_GATE = "stage_gate"
EVENT_EPOCH_START = "epoch_start"
EVENT_DIRECTION_SELECTED = "direction_selected"
EVENT_OPTIONS_ENUMERATED = "options_enumerated"
EVENT_FRONTIER_MUTATION = "frontier_mutation"
EVENT_CANDIDATE_MUTATION = "candidate_mutation"
EVENT_EPOCH_END = "epoch_end"
EVENT_TERMINAL = "terminal"
EVENT_PAUSE = "pause"
EVENT_AUDIT_VERDICT = "audit_verdict"
EVENT_OPERATOR_QUESTION = "operator_question"
EVENT_REVIEW_SELECTION = "review_selection"
EVENT_STAGE4_MATERIALIZATION = "stage4_materialization"
EVENT_PROMOTION = "promotion"
EVENT_FRONTIER_ADOPTION = "frontier_adoption"

# Family groups used by the summary.
_FRONTIER_MUTATION_EVENTS = frozenset({EVENT_FRONTIER_MUTATION})
_CANDIDATE_MUTATION_EVENTS = frozenset({EVENT_CANDIDATE_MUTATION, EVENT_FRONTIER_ADOPTION})


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def controller_trace_scope(run_id: str | None) -> str:
    """Directory scope for one run; an unscoped trace is still durable."""
    return str(run_id or "").strip() or "unscoped"


def controller_trace_dir(work_dir: "str | os.PathLike[str]", run_id: str | None) -> Path:
    return Path(work_dir) / "logs" / controller_trace_scope(run_id)


def controller_trace_path(work_dir: "str | os.PathLike[str]", run_id: str | None) -> Path:
    return controller_trace_dir(work_dir, run_id) / CONTROLLER_TRACE_FILENAME


def _last_sequence(path: Path) -> int:
    if not path.exists():
        return 0
    try:
        last_line = ""
        with open(path, "r", encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    last_line = line
        if not last_line:
            return 0
        return int(json.loads(last_line).get("seq") or 0)
    except Exception:
        return 0


@dataclass
class ControllerTrace:
    """Append-only writer for one run's controller decision trace.

    All writes are best-effort: an observability artifact must never fail the
    decision it is describing. A write failure is swallowed so the controller
    keeps operating on the authoritative persisted state.
    """

    work_dir: "str | os.PathLike[str]"
    run_id: str | None = None
    enabled: bool = True

    def __post_init__(self) -> None:
        self.path = controller_trace_path(self.work_dir, self.run_id)
        self.run_id = controller_trace_scope(self.run_id)
        self._seq = _last_sequence(self.path) if self.enabled else 0

    def emit(self, event: str, **fields: Any) -> dict[str, Any]:
        """Append one structured event and return the persisted record."""
        if not self.enabled:
            return {}
        self._seq += 1
        record: dict[str, Any] = {
            "schema_version": CONTROLLER_TRACE_SCHEMA_VERSION,
            "run_id": self.run_id,
            "seq": self._seq,
            "at": _now_iso(),
            "event": str(event),
        }
        for key, value in fields.items():
            if value is None:
                continue
            record[str(key)] = value
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        except Exception:
            # Observability must not break the controller.
            self._seq -= 1
            return {}
        return record

    def reference(self) -> dict[str, Any]:
        """Stable path/identity reference suitable for a report or bundle."""
        return {
            "schema_version": CONTROLLER_TRACE_SCHEMA_VERSION,
            "run_id": self.run_id,
            "path": _display_path(self.path),
            "event_count": self._seq,
        }


def _display_path(path: Path) -> str:
    """Prefer a workspace-relative path; fall back to the absolute path."""
    try:
        return path.relative_to(Path.cwd()).as_posix()
    except Exception:
        return path.as_posix()


def read_controller_trace(
    work_dir: "str | os.PathLike[str]", run_id: str | None
) -> list[dict[str, Any]]:
    """Return every persisted event for one run, skipping malformed lines."""
    path = controller_trace_path(work_dir, run_id)
    if not path.exists():
        return []
    events: list[dict[str, Any]] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                parsed = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                events.append(parsed)
    except OSError:
        return []
    events.sort(key=lambda entry: int(entry.get("seq") or 0))
    return events


def metric_pairs(delta: Mapping[str, Any] | None) -> tuple[dict[str, Any], dict[str, Any]]:
    """Split a season-maintenance metric delta into before/after mappings.

    ``season_maintenance._metric_delta`` records each metric as
    ``<name>_before`` / ``<name>_after``; this projects those into two flat
    dictionaries so a trace consumer does not have to re-derive the pairing.
    """
    delta = delta or {}
    before: dict[str, Any] = {}
    after: dict[str, Any] = {}
    for key, value in delta.items():
        if not key.endswith("_before"):
            continue
        base = key[: -len("_before")]
        before[base] = value
        if f"{base}_after" in delta:
            after[base] = delta.get(f"{base}_after")
    return before, after


def candidate_lineage(events: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Reconstruct the candidate fingerprint chain from mutation events.

    Each entry links a committed mutation (or explicit frontier adoption) to
    the exact before/after fingerprints, direction/finding and chosen option,
    so a repair that was later replaced can be read straight off the trace
    without replaying the harness conversation.
    """
    lineage: list[dict[str, Any]] = []
    for entry in events:
        if str(entry.get("event")) not in _CANDIDATE_MUTATION_EVENTS:
            continue
        before = str(entry.get("candidate_before") or "")
        after = str(entry.get("candidate_after") or "")
        if not after:
            continue
        lineage.append(
            {
                "event": str(entry.get("event")),
                "seq": entry.get("seq"),
                "epoch": entry.get("epoch"),
                "direction": str(entry.get("direction") or ""),
                "finding_id": str(entry.get("finding_id") or ""),
                "option_id": str(entry.get("option_id") or ""),
                "candidate_before": before,
                "candidate_after": after,
                "hard_verification_ok": bool(entry.get("hard_verification_ok", True)),
                "metrics_before": dict(entry.get("metrics_before") or {}),
                "metrics_after": dict(entry.get("metrics_after") or {}),
            }
        )
    return lineage


def summarize_controller_trace(events: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Compact, deterministic summary of a controller trace.

    Intended for the sanitized evidence bundle and for quick human inspection:
    it keeps the decision *shape* (which directions, epochs, candidate
    transitions, frontier mutations, terminals) without copying every event.
    """
    events = list(events)
    event_counts: dict[str, int] = {}
    directions_epochs: dict[str, int] = {}
    frontier_adds: list[str] = []
    frontier_prunes: list[str] = []
    operator_questions: list[dict[str, Any]] = []
    audit_statuses: list[str] = []
    terminal_reason = ""
    terminal_detail = ""
    pause_reason = ""
    review_selection: dict[str, Any] | None = None
    stage4_materializations: list[dict[str, Any]] = []
    first_fingerprint = ""
    last_fingerprint = ""

    for entry in events:
        event = str(entry.get("event") or "")
        event_counts[event] = event_counts.get(event, 0) + 1
        if event == EVENT_DIRECTION_SELECTED:
            direction = str(entry.get("direction") or "")
            if direction:
                directions_epochs[direction] = directions_epochs.get(direction, 0) + 1
        elif event == EVENT_FRONTIER_MUTATION:
            if entry.get("accepted"):
                ref = str(entry.get("candidate_ref") or "")
                if ref:
                    frontier_adds.append(ref)
            for ref in entry.get("dominated_refs") or []:
                frontier_prunes.append(str(ref))
            for ref in entry.get("pruned_refs") or []:
                frontier_prunes.append(str(ref))
        elif event == EVENT_CANDIDATE_MUTATION:
            after = str(entry.get("candidate_after") or "")
            if after:
                if not first_fingerprint:
                    first_fingerprint = str(entry.get("candidate_before") or "")
                last_fingerprint = after
        elif event == EVENT_AUDIT_VERDICT:
            status = str(entry.get("status") or "")
            if status:
                audit_statuses.append(status)
        elif event == EVENT_OPERATOR_QUESTION:
            operator_questions.append(
                {
                    "item_id": entry.get("item_id"),
                    "direction": entry.get("direction"),
                    "finding": entry.get("finding"),
                    "question": entry.get("question"),
                }
            )
        elif event == EVENT_TERMINAL:
            terminal_reason = str(entry.get("reason") or "")
            terminal_detail = str(entry.get("detail") or "")
        elif event == EVENT_PAUSE:
            pause_reason = str(entry.get("reason") or "")
        elif event == EVENT_REVIEW_SELECTION:
            review_selection = {
                "selected_ref": entry.get("selected_ref"),
                "recommended_ref": entry.get("recommended_ref"),
                "adopted": bool(entry.get("adopted")),
                "reason": entry.get("reason"),
            }
        elif event == EVENT_STAGE4_MATERIALIZATION:
            stage4_materializations.append(
                {
                    "epoch": entry.get("epoch"),
                    "candidate_fingerprint": entry.get("candidate_fingerprint"),
                    "export_fingerprint": entry.get("export_fingerprint"),
                    "export_dir": entry.get("export_dir"),
                }
            )

    lineage = candidate_lineage(events)
    return {
        "schema_version": CONTROLLER_TRACE_SCHEMA_VERSION,
        "event_count": len(events),
        "event_counts": dict(sorted(event_counts.items())),
        "epoch_count": sum(
            1 for entry in events if str(entry.get("event")) == EVENT_EPOCH_START
        ),
        "directions": dict(sorted(directions_epochs.items())),
        "candidate_transitions": lineage,
        "committed_mutations": len(lineage),
        "frontier_adds": frontier_adds,
        "frontier_prunes": frontier_prunes,
        "audit_statuses": audit_statuses,
        "operator_questions": operator_questions,
        "stage4_materializations": stage4_materializations,
        "terminal_reason": terminal_reason,
        "terminal_detail": terminal_detail,
        "pause_reason": pause_reason,
        "first_candidate_fingerprint": first_fingerprint,
        "last_candidate_fingerprint": last_fingerprint,
        "review_selection": review_selection,
    }


def controller_trace_reference(
    work_dir: "str | os.PathLike[str]", run_id: str | None
) -> dict[str, Any]:
    """Reference + compact summary for a run's trace, for the evidence bundle."""
    events = read_controller_trace(work_dir, run_id)
    reference = {
        "schema_version": CONTROLLER_TRACE_SCHEMA_VERSION,
        "run_id": controller_trace_scope(run_id),
        "path": _display_path(controller_trace_path(work_dir, run_id)),
        "event_count": len(events),
    }
    reference["summary"] = summarize_controller_trace(events)
    return reference


__all__ = [
    "CONTROLLER_TRACE_FILENAME",
    "CONTROLLER_TRACE_SCHEMA_VERSION",
    "EVENT_AUDIT_VERDICT",
    "EVENT_CANDIDATE_MUTATION",
    "EVENT_DIRECTION_SELECTED",
    "EVENT_EPOCH_END",
    "EVENT_EPOCH_START",
    "EVENT_FRONTIER_ADOPTION",
    "EVENT_FRONTIER_MUTATION",
    "EVENT_OPERATOR_QUESTION",
    "EVENT_OPTIONS_ENUMERATED",
    "EVENT_PAUSE",
    "EVENT_PROMOTION",
    "EVENT_REVIEW_SELECTION",
    "EVENT_RUN_START",
    "EVENT_STAGE4_MATERIALIZATION",
    "EVENT_STAGE_GATE",
    "EVENT_TERMINAL",
    "ControllerTrace",
    "candidate_lineage",
    "controller_trace_dir",
    "controller_trace_path",
    "controller_trace_reference",
    "metric_pairs",
    "read_controller_trace",
    "summarize_controller_trace",
]
