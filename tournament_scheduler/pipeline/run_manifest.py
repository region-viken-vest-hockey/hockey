"""Versioned AI-operator run manifest.

The run manifest is the shared data contract that lets an AI operator (or a
human) understand workspace state without parsing console logs: the active
objective, which capability is currently running, input fingerprints, a
timestamped history of capability results, and the final outcome of the run.

It is stored alongside the existing per-stage checkpoint files (see
``pipeline/state.py``) as ``<work_dir>/run_manifest.json`` and does not
replace them — ``PipelineState`` remains the source of truth for stage data.
The manifest is a higher-level, operator-facing summary layered on top.

See ``docs/run-manifest-schema.md`` for the full schema documentation,
versioning policy, and backward-compatibility story.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from .capability_result import CapabilityResult

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DEFAULT_WORK_DIR = ".pipeline"
_MANIFEST_FILENAME = "run_manifest.json"

# Bump only on a breaking change to the manifest shape (removing/renaming a
# field, changing a field's meaning). Additive fields do not require a bump.
# See docs/run-manifest-schema.md for the compatibility policy.
RUN_MANIFEST_SCHEMA_VERSION = 1

_LEGACY_RUN_ID = "legacy"


class ManifestPersistenceError(RuntimeError):
    """A run manifest read or write could not be completed reliably (issue #14).

    A subclass of ``RuntimeError`` so every pre-existing ``except Exception``
    (or ``except RuntimeError``) call site keeps working unchanged; new code
    can catch this specifically to distinguish "the manifest is unreliable"
    from an unrelated bug.
    """


class RunOutcome(str, Enum):
    """Overall outcome of an operator run, mirroring capability statuses."""

    IN_PROGRESS = "in_progress"
    OK = "ok"
    WARNING = "warning"
    BLOCKED = "blocked"
    FAILED = "failed"


_VALID_OUTCOMES = {outcome.value for outcome in RunOutcome}


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _new_run_id() -> str:
    timestamp = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{timestamp}-{uuid.uuid4().hex[:8]}"


def _next_recommended_capability(
    last_completed: str | None, last_entry: dict[str, Any] | None
) -> str | None:
    """What an operator should run next, given the last completed capability
    and its recorded result (issue #15).

    Reuses the canonical stage sequence from ``pipeline.state.StageName``
    (config -> scraping -> planning -> export). A capability outside that
    sequence (e.g. a per-source health check) or a blocked/failed/
    requires-human result is treated as unresolved: the same capability is
    recommended again rather than advancing to the next stage.
    """
    from .state import StageName  # local import: avoid a cycle at module load

    sequence = [stage.value for stage in StageName]

    if last_completed is None:
        return sequence[0]
    if last_entry is not None and (
        last_entry.get("status") in ("blocked", "failed") or last_entry.get("requires_human")
    ):
        return last_completed
    if last_completed not in sequence:
        return None
    idx = sequence.index(last_completed)
    return sequence[idx + 1] if idx + 1 < len(sequence) else None


# ---------------------------------------------------------------------------
# RunManifest
# ---------------------------------------------------------------------------


class RunManifest:
    """Read/write the versioned run manifest for a pipeline work directory.

    Parameters
    ----------
    work_dir:
        Directory where ``run_manifest.json`` is stored (default ``.pipeline``,
        matching :class:`~tournament_scheduler.pipeline.state.PipelineState`).
    """

    def __init__(self, work_dir: str | os.PathLike[str] = _DEFAULT_WORK_DIR) -> None:
        self.work_dir = Path(work_dir)
        self.work_dir.mkdir(parents=True, exist_ok=True)

    @property
    def path(self) -> Path:
        return self.work_dir / _MANIFEST_FILENAME

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start_run(
        self,
        objective: str,
        *,
        input_fingerprint: dict[str, Any] | None = None,
        run_id: str | None = None,
    ) -> dict[str, Any]:
        """Start a new run, overwriting the previous manifest's run history.

        ``pending_questions`` is carried forward from any existing manifest
        rather than reset: escalation questions and their human answers are
        durable workspace state (see ``pipeline/escalation.py``), not
        per-run state — a question answered before an interruption must
        stay answered, and must survive across ``rvv-miniputt run``/
        ``operator run`` invocations for a human to actually be able to
        answer it in between.

        Returns the new manifest dict.
        """
        previous = self.read() if self.exists() else None
        carried_questions = (
            list(previous.get("pending_questions", [])) if isinstance(previous, dict) else []
        )
        now = _now_iso()
        manifest: dict[str, Any] = {
            "schema_version": RUN_MANIFEST_SCHEMA_VERSION,
            "run_id": run_id or _new_run_id(),
            "objective": objective,
            # active_capability: set before a capability executes, cleared once
            # it produces a result or the run finalizes (issue #15).
            "active_capability": None,
            "last_completed_capability": None,
            "next_recommended_capability": _next_recommended_capability(None, None),
            # current_capability: deprecated alias, mirrors last_completed_capability
            # (or the in-progress capability while one is active). Kept so
            # older consumers reading this exact key don't break.
            "current_capability": None,
            "input_fingerprint": input_fingerprint or {},
            "started_at": now,
            "updated_at": now,
            "ended_at": None,
            "final_outcome": RunOutcome.IN_PROGRESS.value,
            "capabilities": [],
            "pending_questions": carried_questions,
            # Per-run history of the observe-decide-act loop (issue #11).
            # Resets each run like `capabilities` — unlike pending_questions,
            # an action transition is only meaningful within the run that
            # produced it.
            "action_log": [],
        }
        self._write(manifest)
        return manifest

    def set_current_capability(self, name: str) -> None:
        """Mark *name* as the active capability, before it has a result (issue #15).

        Sets ``active_capability`` and mirrors it onto the legacy
        ``current_capability`` key. Call this before a capability starts
        executing so an interrupted run still shows what was in progress
        when execution stopped, even though no result was ever recorded
        for it.
        """
        manifest = self.read()
        manifest["active_capability"] = name
        manifest["current_capability"] = name
        manifest["updated_at"] = _now_iso()
        self._write(manifest)

    def record_capability(self, result: CapabilityResult) -> dict[str, Any]:
        """Append a capability result to the run history.

        Clears ``active_capability`` (the capability is no longer in
        progress — it just produced a result, however that turned out) and
        updates ``last_completed_capability`` and
        ``next_recommended_capability`` (issue #15).

        Returns the recorded entry (the result dict plus a ``recorded_at``
        timestamp).
        """
        manifest = self.read()
        entry = result.to_dict()
        entry["recorded_at"] = _now_iso()
        manifest.setdefault("capabilities", []).append(entry)
        if result.capability:
            manifest["active_capability"] = None
            manifest["last_completed_capability"] = result.capability
            manifest["current_capability"] = result.capability
            manifest["next_recommended_capability"] = _next_recommended_capability(
                result.capability, entry
            )
        manifest["updated_at"] = entry["recorded_at"]
        self._write(manifest)
        return entry

    def record_action_transition(
        self,
        *,
        target: str,
        action_id: str,
        arguments: dict[str, Any],
        rationale: str,
        policy_rule: str,
        result: CapabilityResult,
        transition: str,
    ) -> dict[str, Any]:
        """Append one observe-decide-act loop step (issue #11) to ``action_log``.

        Every field the loop's stopping-condition tests rely on is recorded
        explicitly rather than left to be re-derived from ``result``:

        target:
            What the action operated on (e.g. a source name).
        action_id / arguments:
            The exact :class:`~tournament_scheduler.pipeline.operator_action.OperatorAction`
            invoked — always one of the registry's known action IDs.
        rationale:
            Free-text explanation of why this action was chosen.
        policy_rule:
            Stable identifier for the deterministic rule that selected it
            (e.g. ``"blocked+credentials->request_credentials"``), so a
            transition can be attributed to policy without parsing prose.
        result:
            The :class:`CapabilityResult` the action produced.
        transition:
            What the loop did next: ``"resolved"``, ``"retry"``,
            ``"escalate"``, or ``"no_progress_stop"``.

        Returns the recorded entry.
        """
        manifest = self.read()
        now = _now_iso()
        entry = {
            "target": target,
            "action_id": action_id,
            "arguments": dict(arguments),
            "rationale": rationale,
            "policy_rule": policy_rule,
            "result": result.to_dict(),
            "transition": transition,
            "recorded_at": now,
        }
        manifest.setdefault("action_log", []).append(entry)
        manifest["updated_at"] = now
        self._write(manifest)
        return entry

    def record_decision(
        self,
        *,
        context: dict[str, Any],
        action: dict[str, Any],
        result: dict[str, Any],
    ) -> dict[str, Any]:
        """Append one LLM-directed soft decision (issue #260) to ``decision_log``.

        Distinct from :meth:`record_action_transition`'s deterministic
        observe-decide-act loop (issue #11), whose actions are selected by a
        stable ``policy_rule``: entries here originate from an LLM/agent
        controller choosing a ``DecisionAction`` over a ``DecisionContext``
        (``tournament_scheduler.application.decisions``), after deterministic
        validation. Takes plain dicts (each type's ``to_dict()``) rather than
        the application-layer dataclasses themselves, since this module must
        not import the application layer. Only the concise rationale is
        persisted — never chain-of-thought.
        """
        manifest = self.read()
        now = _now_iso()
        entry = {
            "context": dict(context),
            "action": dict(action),
            "result": dict(result),
            "recorded_at": now,
        }
        manifest.setdefault("decision_log", []).append(entry)
        manifest["updated_at"] = now
        self._write(manifest)
        return entry

    def finalize(self, outcome: str) -> None:
        """Mark the run as finished with a terminal outcome.

        *outcome* must be one of ``ok``, ``warning``, ``blocked``, ``failed``
        (not ``in_progress`` — use this only once the run has actually ended).
        """
        outcome_value = outcome.value if isinstance(outcome, RunOutcome) else str(outcome)
        if outcome_value not in _VALID_OUTCOMES or outcome_value == RunOutcome.IN_PROGRESS.value:
            raise ValueError(
                f"Invalid terminal outcome {outcome_value!r}. "
                f"Valid values: ok, warning, blocked, failed"
            )
        manifest = self.read()
        now = _now_iso()
        manifest["final_outcome"] = outcome_value
        # A finalized run always has active_capability: null (issue #15) —
        # cleared here as a safety net even though record_capability already
        # clears it on the normal path, so an early-abort finalize can't
        # leave a stale active capability behind.
        manifest["active_capability"] = None
        manifest["updated_at"] = now
        manifest["ended_at"] = now
        self._write(manifest)

    # ------------------------------------------------------------------
    # Human escalation / approval protocol (pending_questions)
    # ------------------------------------------------------------------
    #
    # See pipeline/escalation.py for the Question shape and the types a
    # capability can raise. RunManifest only owns storage: append-if-new,
    # look up by id, and record an answer.

    def add_pending_question(self, question: dict[str, Any]) -> dict[str, Any]:
        """Append *question* (a dict from ``escalation.Question.to_dict()``)
        unless a question with the same ``id`` was already raised in this
        workspace, answered or not.

        For anything narrower than ``workspace`` scope, the id already bakes
        in ``(scope, scope_key)`` (see ``escalation._question_id``), so a
        context change (new run, new workbook, new season) naturally
        produces a new id rather than reusing an old answer. When that
        happens, any older, still-fresh entry sharing this question's
        ``(type, capability, summary, scope)`` — i.e. the same decision in
        an earlier context — is marked ``stale`` rather than deleted or
        overwritten, preserving its audit history (issue #12).

        Returns the newly stored question, or the existing entry when this
        exact question, in this exact scope context, has already been
        raised — so a capability can call this unconditionally every time
        it blocks without ever asking the same thing twice.
        """
        manifest = self.read()
        existing = manifest.setdefault("pending_questions", [])
        question_id = question.get("id")
        for entry in existing:
            if entry.get("id") == question_id:
                return entry

        scope = question.get("scope", "workspace")
        if scope != "workspace":
            signature = (question.get("type"), question.get("capability"), question.get("summary"), scope)
            for entry in existing:
                entry_signature = (entry.get("type"), entry.get("capability"), entry.get("summary"), entry.get("scope"))
                if (
                    entry_signature == signature
                    and entry.get("scope_key") != question.get("scope_key")
                    and not entry.get("stale")
                ):
                    entry["stale"] = True
                    entry["stale_reason"] = (
                        f"scope_key changed ({entry.get('scope_key')!r} -> {question.get('scope_key')!r})"
                    )

        existing.append(question)
        manifest["updated_at"] = _now_iso()
        self._write(manifest)
        return question

    def promote_question(
        self,
        question_id: str,
        new_scope: str,
        *,
        new_scope_key: str = "",
        decided_by: str | None = None,
    ) -> dict[str, Any]:
        """Promote *question_id* to a broader scope (issue #12).

        Copies the source entry's content and answer into a new entry under
        *new_scope*, which must be strictly broader than the source entry's
        current scope (``run < input_version < season < workspace``). The
        source entry is left untouched — still auditable at its original
        scope — and gains a ``promoted_to`` pointer to the new entry's id.

        Returns the existing promoted entry unchanged if this exact
        promotion was already made (idempotent, like ``add_pending_question``).
        """
        from .escalation import _question_id, scope_order  # local import: avoid a cycle at module load

        manifest = self.read()
        pending = manifest.setdefault("pending_questions", [])
        source = next((entry for entry in pending if entry.get("id") == question_id), None)
        if source is None:
            raise ValueError(f"No question with id {question_id!r} in {self.path}")

        source_scope = source.get("scope", "workspace")
        if scope_order(new_scope) <= scope_order(source_scope):
            raise ValueError(
                f"Cannot promote from {source_scope!r} to {new_scope!r}: "
                "the target scope must be strictly broader than the source scope"
            )

        resolved_key = "" if new_scope == "workspace" else new_scope_key
        promoted_id = _question_id(source["type"], source["capability"], source["summary"], new_scope, resolved_key)

        for entry in pending:
            if entry.get("id") == promoted_id:
                return entry

        now = _now_iso()
        promoted = dict(source)
        promoted.update(
            {
                "id": promoted_id,
                "scope": new_scope,
                "scope_key": resolved_key,
                "stale": False,
                "stale_reason": None,
                "promoted_from": source["id"],
                "promoted_at": now,
            }
        )
        if decided_by is not None:
            promoted["decided_by"] = decided_by

        source["promoted_to"] = promoted_id
        pending.append(promoted)
        manifest["updated_at"] = now
        self._write(manifest)
        return promoted

    def all_questions(self) -> list[dict[str, Any]]:
        """Return every question ever raised in this workspace — answered,
        unanswered, stale, and promoted — the full audit trail."""
        return list(self.read().get("pending_questions", []))

    def answer_question(
        self, question_id: str, answer: str, *, decided_by: str | None = None
    ) -> dict[str, Any]:
        """Record a durable human answer to a previously-raised question.

        Raises ``ValueError`` if no question with *question_id* exists.
        """
        manifest = self.read()
        for entry in manifest.get("pending_questions", []):
            if entry.get("id") == question_id:
                now = _now_iso()
                entry["answered"] = True
                entry["answer"] = answer
                entry["decided_by"] = decided_by
                entry["decided_at"] = now
                manifest["updated_at"] = now
                self._write(manifest)
                return entry
        raise ValueError(f"No pending question with id {question_id!r} in {self.path}")

    def unanswered_questions(self) -> list[dict[str, Any]]:
        """Return every question in this workspace that has no recorded answer."""
        return [q for q in self.read().get("pending_questions", []) if not q.get("answered")]

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def read(self) -> dict[str, Any]:
        """Return the current manifest.

        When no manifest file exists yet (e.g. the work directory was
        populated by a version of the pipeline that predates this schema),
        a read-only manifest is synthesized from the legacy stage checkpoint
        files so callers always get a consistent shape.

        When the file *does* exist but is corrupted (invalid JSON, or valid
        JSON that isn't an object), that is a genuinely different situation
        from "no manifest yet" (issue #14): the corrupted file is backed up
        alongside itself rather than silently discarded, and the returned
        (synthesized) manifest carries a non-``None`` ``manifest_recovery``
        key describing what happened — so ``rvv-miniputt status --json``
        surfaces the corruption as a visible diagnostic instead of masking
        it behind an indistinguishable "legacy workspace" view.
        """
        if not self.path.exists():
            return self._synthesize_from_legacy_checkpoints()
        try:
            raw = self.path.read_text(encoding="utf-8")
        except OSError as exc:
            return self._synthesize_from_legacy_checkpoints(
                recovery_reason="read_error", recovery_detail=str(exc)
            )
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            backup_path = self._backup_corrupted_manifest(raw)
            return self._synthesize_from_legacy_checkpoints(
                recovery_reason="invalid_json", recovery_detail=str(exc), backup_path=backup_path
            )
        if not isinstance(data, dict):
            backup_path = self._backup_corrupted_manifest(raw)
            return self._synthesize_from_legacy_checkpoints(
                recovery_reason="not_a_json_object", recovery_detail=type(data).__name__, backup_path=backup_path
            )
        data.setdefault("manifest_recovery", None)
        self._backfill_capability_state_fields(data)
        return data

    def _backfill_capability_state_fields(self, data: dict[str, Any]) -> None:
        """Add active/last-completed/next-recommended capability fields to a
        manifest written before issue #15, without discarding anything.

        A pre-fix finalized manifest could have a stale non-null
        ``current_capability`` — that was exactly the bug — so
        ``active_capability`` is only backfilled from it while the run is
        still ``in_progress``; a finalized legacy manifest correctly gets
        ``active_capability: None`` here even though it never explicitly
        cleared one.
        """
        if "active_capability" in data and "last_completed_capability" in data and "next_recommended_capability" in data:
            return
        capabilities = data.get("capabilities") or []
        last_entry = capabilities[-1] if capabilities else None
        last_completed = last_entry.get("capability") if last_entry else None
        data.setdefault("last_completed_capability", last_completed)
        if data.get("final_outcome") == RunOutcome.IN_PROGRESS.value:
            data.setdefault("active_capability", data.get("current_capability"))
        else:
            data.setdefault("active_capability", None)
        data.setdefault(
            "next_recommended_capability",
            _next_recommended_capability(last_completed, last_entry),
        )

    def exists(self) -> bool:
        return self.path.exists()

    def check_health(self) -> dict[str, Any]:
        """Round-trip check for the operator-state health check (issue #14).

        Reads the current manifest (or the synthesized legacy view, if none
        exists yet) and writes it straight back — content-neutral either
        way, so a permissions problem, a full disk, or any other write
        failure is caught proactively instead of only being discovered the
        next time something tries to record real state. Never raises —
        failure is reported in the returned dict.
        """
        try:
            manifest = self.read()
            recovery = manifest.get("manifest_recovery")
            self._write(manifest)
            return {
                "healthy": recovery is None,
                "writable": True,
                "manifest_recovery": recovery,
                "detail": "" if recovery is None else f"Manifest was recovered: {recovery.get('reason')}",
            }
        except ManifestPersistenceError as exc:
            return {"healthy": False, "writable": False, "manifest_recovery": None, "detail": str(exc)}

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _backup_corrupted_manifest(self, raw_content: str) -> str | None:
        """Best-effort copy of a corrupted manifest file aside, preserving it
        for inspection instead of silently overwriting it on the next write.

        Returns the backup path, or ``None`` if even the backup couldn't be
        written (still safe — the corruption diagnostic is reported either
        way, this is purely an extra recovery aid).
        """
        backup_path = self.path.with_name(f"{self.path.name}.corrupted-{_new_run_id()}")
        try:
            backup_path.write_text(raw_content, encoding="utf-8")
            return str(backup_path)
        except OSError:
            return None

    def _write(self, manifest: dict[str, Any]) -> None:
        """Atomically replace the manifest file's contents.

        Writes to a temp file in the same directory (so the eventual
        ``os.replace`` is an atomic rename on the same filesystem) and only
        then swaps it into place — a crash or failure mid-write leaves the
        previous valid manifest untouched rather than a half-written file
        (issue #14).
        """
        payload = json.dumps(manifest, indent=2, ensure_ascii=False)
        tmp_path = self.path.with_name(f"{self.path.name}.tmp-{uuid.uuid4().hex[:8]}")
        try:
            tmp_path.write_text(payload, encoding="utf-8")
            os.replace(tmp_path, self.path)
        except (OSError, ValueError) as exc:
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass
            raise ManifestPersistenceError(f"Failed to write run manifest {self.path}: {exc}") from exc

    def _synthesize_from_legacy_checkpoints(
        self,
        *,
        recovery_reason: str | None = None,
        recovery_detail: str | None = None,
        backup_path: str | None = None,
    ) -> dict[str, Any]:
        """Build a manifest-shaped view from ``stage*.json`` checkpoints.

        This is the backward-compatibility path required for work
        directories that were populated before ``run_manifest.json`` existed:
        every field the manifest promises is still populated, just derived
        from data that was already on disk.

        When called because an *existing* manifest file was corrupted
        (``recovery_reason`` set — issue #14), the returned manifest's
        ``manifest_recovery`` key documents what happened instead of the
        corruption being indistinguishable from "no manifest ever existed".
        """
        from .state import PipelineState, StageName

        state = PipelineState(self.work_dir)
        capabilities: list[dict[str, Any]] = []
        current_capability: str | None = None
        latest_updated: str | None = None
        earliest_updated: str | None = None
        any_failed = False
        any_incomplete = False

        for stage in (StageName.CONFIG, StageName.SCRAPING, StageName.PLANNING, StageName.EXPORT):
            checkpoint_path = state.checkpoint_path(stage)
            if not checkpoint_path.exists():
                continue

            envelope = state.read_envelope(stage)
            stage_status = str(envelope.get("status", "pending"))
            is_stale = bool(envelope.get("stale"))

            if stage_status == "done" and not is_stale:
                capability_status = "ok"
            elif stage_status == "failed":
                capability_status = "failed"
                any_failed = True
            else:
                capability_status = "warning"
                any_incomplete = True

            summary = f"Legacy checkpoint for stage '{stage.value}' (status={stage_status})"
            if is_stale:
                summary += f", stale: {envelope.get('stale_reason', '')}"

            result = CapabilityResult(
                status=capability_status,
                summary=summary,
                capability=stage.value,
                problems=[str(envelope["error"])] if envelope.get("error") else [],
            )
            entry = result.to_dict()
            entry["recorded_at"] = envelope.get("updated_at", "")
            capabilities.append(entry)

            current_capability = stage.value
            updated_at = envelope.get("updated_at")
            if updated_at:
                if latest_updated is None or updated_at > latest_updated:
                    latest_updated = updated_at
                if earliest_updated is None or updated_at < earliest_updated:
                    earliest_updated = updated_at

        if not capabilities:
            final_outcome = RunOutcome.IN_PROGRESS.value
        elif any_failed:
            final_outcome = RunOutcome.FAILED.value
        elif any_incomplete or len(capabilities) < 4:
            final_outcome = RunOutcome.WARNING.value
        else:
            final_outcome = RunOutcome.OK.value
        # A corrupted-manifest recovery always makes the workspace state at
        # least suspect, regardless of what the legacy checkpoints alone
        # would otherwise imply.
        if recovery_reason is not None and final_outcome == RunOutcome.OK.value:
            final_outcome = RunOutcome.WARNING.value

        manifest_recovery = (
            None
            if recovery_reason is None
            else {
                "recovered": True,
                "reason": recovery_reason,
                "detail": recovery_detail,
                "backup_path": backup_path,
                "detected_at": _now_iso(),
            }
        )

        last_entry = capabilities[-1] if capabilities else None
        return {
            "schema_version": RUN_MANIFEST_SCHEMA_VERSION,
            "run_id": _LEGACY_RUN_ID,
            "objective": None,
            "active_capability": None,
            "last_completed_capability": current_capability,
            "next_recommended_capability": _next_recommended_capability(current_capability, last_entry),
            "current_capability": current_capability,
            "input_fingerprint": {},
            "started_at": earliest_updated,
            "updated_at": latest_updated,
            "ended_at": None,
            "final_outcome": final_outcome,
            "capabilities": capabilities,
            "pending_questions": [],
            "action_log": [],
            "synthesized_from_legacy_checkpoints": True,
            "manifest_recovery": manifest_recovery,
        }


def is_durable(work_dir: str | os.PathLike[str]) -> bool:
    """Convenience wrapper around ``RunManifest.check_health()`` (issue #14).

    Used as a pre-execution gate for approval-required operator actions
    (see ``pipeline/operator_action.py``): an approved destructive/external
    action must not run if there is no reliable way to record it happened.
    Only ``writable`` matters here — a manifest that recovered from past
    corruption but can currently be written to is still durable enough to
    proceed.
    """
    return bool(RunManifest(work_dir).check_health().get("writable"))
