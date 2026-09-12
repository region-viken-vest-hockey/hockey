"""Stage 1 — config parsing and Norwegian-language validation.

Loads ``input.xlsx`` (the canonical pipeline input workbook), validates it, and
writes the parsed, validated configuration to the Stage 1 checkpoint via
:class:`~tournament_scheduler.pipeline.state.PipelineState`.

Workbook input format (all fields required unless marked optional)::

    {
        "start_date": "YYYY-MM-DD",
        "end_date": "YYYY-MM-DD",
        "age_groups": ["U10", "U12", ...],          // optional
        "parallel_games": {"U10": 3, "U7": 4, ...}, // optional
        "round_length_minutes": {"U10": 10, ...},   // optional
        "teams": [                                   // list of teams (roster)
            {"club": "Kongsberg", "label": "Kongsberg U10A", "age_group": "U10"},
            ...
        ]
    }

The workbook ``Lag`` sheet is the standard team roster input. The internal config
shape still supports an external roster path for compatibility with lower-level helpers.

Norwegian error messages are emitted via :func:`validate_config` as a list of
human-readable strings so callers can surface them directly to users.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .fingerprints import build_stage1_fingerprints
from .state import PipelineState, StageName, StageStatus
from .stage1_helpers import _load_workbook_config, _parse_config, validate_config
# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


class Stage1Error(ValueError):
    """Raised when Stage 1 cannot proceed due to validation errors."""

    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__("\n".join(errors))


def load_effective_config(
    state: PipelineState,
    *,
    input_path: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    """Load the effective (merged) config from ``input.xlsx`` + Stage 1 checkpoint.

    Reads the canonical ``input.xlsx`` workbook for human-editable fields
    (``start_date``, ``end_date``, ``age_groups``, ``parallel_games``,
    per-age-group participation targets, global planning knobs, ``sources``) and merges
    in the computed fields (``teams``, ``round_length_minutes``) from the Stage 1 checkpoint.

    Returns a dict with the same shape that downstream stages expect,
    so callers see no API change.
    """
    ckpt = state.read_stage(StageName.CONFIG)
    if not ckpt:
        return {}

    # Resolve input path: checkpoint > parameter > default
    ip = input_path or ckpt.get("input_path", "input.xlsx")
    raw = _load_workbook_config(ip)

    merged: dict[str, Any] = {}

    # From input.xlsx (canonical source)
    merged["start_date"] = raw.get("start_date")
    merged["end_date"] = raw.get("end_date")
    merged["parallel_games"] = raw.get("parallel_games", {})
    # The canonical workbook no longer supports a global
    # `target_tournament_count` / `deltakelser_per_lag` fallback — Stage 1
    # validation rejects it outright (see stage1_helpers.validate_config), so
    # it is intentionally never merged into the effective config here. The
    # per-age-group `participation_targets_by_age_group` values below are the
    # only participation-target source for the canonical path.
    if raw.get("max_hosting_days_per_month") is not None:
        merged["max_hosting_days_per_month"] = raw["max_hosting_days_per_month"]
    if raw.get("participation_targets_by_age_group"):
        merged["participation_targets_by_age_group"] = raw["participation_targets_by_age_group"]
    merged["sources"] = raw.get("sources", [])

    # Age groups: explicit input workbook value only; downstream can fall back
    # to the plan when the input file truly omits the field.
    merged["age_groups"] = raw.get("age_groups", [])
    merged["age_groups_from_input"] = "age_groups" in raw

    # From Stage 1 checkpoint (computed)
    merged["teams"] = ckpt.get("teams", [])
    merged["round_length_minutes"] = ckpt.get("round_length_minutes", {})

    # Preserve other computed/metadata fields from the checkpoint
    if "input_path" in ckpt:
        merged["input_path"] = ckpt["input_path"]

    return merged


def run(
    input_path: str | os.PathLike[str],
    state: PipelineState,
    *,
    strict: bool = True,
) -> dict[str, Any]:
    """Parse and validate *input_path*, write the Stage 1 checkpoint.

    The checkpoint stores the normalized roster/config fields (``teams`` expanded
    from a file reference, ``round_length_minutes`` from the workbook, and any
    per-age-group participation targets parsed from the workbook).
    Human-editable fields (``start_date``, ``end_date``, ``age_groups``,
    ``parallel_games``, global planning knobs, ``sources``) live exclusively in ``input.xlsx``. Use
    :func:`load_effective_config` to merge both sources transparently.

    Parameters
    ----------
    input_path:
        Path to ``input.xlsx`` (canonical pipeline input workbook).
    state:
        :class:`PipelineState` instance managing the work directory.
    strict:
        If ``True`` (default), raise :class:`Stage1Error` on any validation
        error and write the checkpoint with ``status=failed``.  If ``False``,
        continue with warnings logged but not raised.

    Returns
    -------
    dict
        The computed config dict (teams, round_length_minutes, input_path,
        optionally semantic_warnings) that was written to the checkpoint.

    Raises
    ------
    Stage1Error
        When *strict* is ``True`` and validation produces errors.
    FileNotFoundError
        When *input_path* does not exist.
    """
    raw = _load_workbook_config(input_path)
    errors = validate_config(raw, Path(input_path))

    if errors:
        if strict:
            raise Stage1Error(errors)
        # Non-strict: record errors but continue with best-effort parsing
        state.write_stage(
            StageName.CONFIG,
            {"errors": errors, "raw": raw},
            status=StageStatus.FAILED,
        )
        return {"errors": errors}

    # Parse validated config into structured objects
    state.write_stage(StageName.CONFIG, {}, status=StageStatus.RUNNING)
    config = _parse_config(raw, input_path)
    config.update(build_stage1_fingerprints(input_path, raw, config))

    state.write_stage(StageName.CONFIG, config, status=StageStatus.DONE)
    return config


# CLI entry point — supports: python3 -m tournament_scheduler.pipeline.stage1_config
# ---------------------------------------------------------------------------

if __name__ == "__main__":  # pragma: no cover
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="Stage 1: config parsing and validation")
    parser.add_argument("--input", default="input.xlsx", help="Path to input.xlsx workbook")
    parser.add_argument("--work-dir", default=".pipeline", help="Pipeline work directory")
    cli_args = parser.parse_args()

    from .run_log_paths import append_stage_log_line  # noqa: E402
    from .state import PipelineState  # noqa: E402

    _state = PipelineState(cli_args.work_dir)
    try:
        _result = run(cli_args.input, _state)
        _raw = _load_workbook_config(cli_args.input)
        print(f"Stage 1 OK — {len(_result.get('teams', []))} lag, "
              f"{_raw.get('start_date')} til {_raw.get('end_date')}")
        append_stage_log_line(
            _state,
            f"Stage 1 OK: {len(_result.get('teams', []))} teams, "
            f"{_raw.get('start_date')} to {_raw.get('end_date')}",
        )
        sys.exit(0)
    except (Stage1Error, FileNotFoundError) as _e:
        append_stage_log_line(_state, f"Stage 1 FAILED: {_e}")
        print(str(_e), file=sys.stderr)
        sys.exit(1)
