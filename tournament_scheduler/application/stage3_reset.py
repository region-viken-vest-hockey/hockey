"""Canonical recovery for discarding an untrustworthy interactive Stage 3 lineage.

A Stage 3 reset is an engineering/operator recovery operation, not a planning
strategy.  It deliberately preserves the normalized Stage 1 configuration and
Stage 2 scrape evidence while removing every candidate/checkpoint owned by
Stage 3 and Stage 4.  A fresh run id is created so decisions made against the
superseded Stage 3 lineage cannot be confused with the recovered run.

Search/solver caches and attempt evidence are owned by their respective
modules and are cleared by the CLI recovery facade after this lifecycle reset.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .stage3_session_store import Stage3SessionStore
from ..pipeline.run_manifest import RunManifest
from ..pipeline.state import PipelineState, StageName, StageStatus


class Stage3ResetError(RuntimeError):
    """The workspace cannot safely restart from preserved Stage 1/2 facts."""


@dataclass(frozen=True)
class Stage3ResetResult:
    work_dir: str
    old_run_id: str
    new_run_id: str
    input_path: str
    preserved_checkpoints: tuple[str, ...]
    cleared_checkpoints: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _require_fresh_upstream(state: PipelineState, stage: StageName) -> None:
    envelope = state.read_envelope(stage)
    status = state.status(stage)
    if status != StageStatus.DONE or envelope.get("stale"):
        detail = str(envelope.get("stale_reason") or envelope.get("error") or status.value)
        raise Stage3ResetError(
            f"Cannot reset Stage 3 while {stage.value} is not a fresh DONE checkpoint: {detail}"
        )


def reset_stage3_lifecycle(work_dir: str | Path = ".pipeline") -> Stage3ResetResult:
    """Reset Stage 3/4 lifecycle state while preserving Stage 1/2 evidence.

    Preconditions are intentionally strict: both upstream checkpoints must be
    present, DONE and non-stale.  The operation then removes the canonical
    Stage3Session (including compatibility mirrors), removes Stage 3 and Stage
    4 checkpoints, and starts a fresh run manifest using the previous
    objective/input fingerprint.  The new run id separates the recovered
    decision lineage from the superseded one without re-scraping calendars.
    """

    state = PipelineState(work_dir)
    _require_fresh_upstream(state, StageName.CONFIG)
    _require_fresh_upstream(state, StageName.SCRAPING)

    manifest = RunManifest(work_dir)
    previous: dict[str, Any] = manifest.read() if manifest.exists() else {}
    old_run_id = str(previous.get("run_id") or "")
    objective = str(previous.get("objective") or "Resume Stage 3 from preserved Stage 1/2 evidence")
    input_fingerprint = previous.get("input_fingerprint")
    if not isinstance(input_fingerprint, dict):
        input_fingerprint = {}

    config = state.read_stage(StageName.CONFIG)
    input_path = str(config.get("input_path") or "input.xlsx")

    # The session store is the sole owner of interactive Stage 3 lifecycle
    # state and its legacy compatibility mirrors.
    Stage3SessionStore(state.work_dir).clear()

    cleared: list[str] = []
    for stage in (StageName.PLANNING, StageName.EXPORT):
        path = state.checkpoint_path(stage)
        if path.exists():
            path.unlink()
            cleared.append(path.name)

    fresh_manifest = manifest.start_run(objective, input_fingerprint=input_fingerprint)
    new_run_id = str(fresh_manifest.get("run_id") or "")

    return Stage3ResetResult(
        work_dir=str(state.work_dir),
        old_run_id=old_run_id,
        new_run_id=new_run_id,
        input_path=input_path,
        preserved_checkpoints=(
            state.checkpoint_path(StageName.CONFIG).name,
            state.checkpoint_path(StageName.SCRAPING).name,
        ),
        cleared_checkpoints=tuple(cleared),
    )
