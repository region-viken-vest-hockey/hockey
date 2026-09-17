"""``rvv-miniputt stage3 reset`` — restart planning from preserved Stage 1/2 facts."""

from __future__ import annotations

import argparse
import json
from typing import Sequence

from ...application.stage3_reset import Stage3ResetError, reset_stage3_lifecycle
from ...pipeline.evidence_bundle import clear_stage3_attempt_log
from ...pipeline.state import PipelineState
from .stage3_cpsat_cache import clear_cp_sat_cache
from ._shared import _console


def _cmd_stage3_reset(args: argparse.Namespace) -> int:
    work_dir = getattr(args, "work_dir", ".pipeline")
    try:
        result = reset_stage3_lifecycle(work_dir)
        state = PipelineState(work_dir)
        clear_stage3_attempt_log(state.work_dir)
        clear_cp_sat_cache(state)
    except (Stage3ResetError, OSError, RuntimeError) as exc:
        if getattr(args, "json", False):
            print(json.dumps({"ok": False, "error": str(exc)}, indent=2, ensure_ascii=False))
        else:
            _console.print(f"[red]✗[/red] Stage 3 reset avvist: {exc}")
        return 1

    payload = result.to_dict()
    payload.update(
        {
            "ok": True,
            "cleared_auxiliary_state": ["stage3_attempt_log", "stage3_cpsat_cache"],
            "resume_from": 3,
            "next_command": (
                "scripts/rvv-miniputt run --interactive "
                f"--input {result.input_path} --work-dir {result.work_dir} --resume-from 3"
            ),
        }
    )

    if getattr(args, "json", False):
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0

    _console.print("[bold green]✓ Stage 3 reset[/bold green]")
    _console.print(f"  gammel run_id: {result.old_run_id or '(ingen)'}")
    _console.print(f"  ny run_id: {result.new_run_id}")
    _console.print(f"  bevart: {', '.join(result.preserved_checkpoints)}")
    cleared = list(result.cleared_checkpoints) + list(payload["cleared_auxiliary_state"])
    _console.print(f"  nullstilt: {', '.join(cleared) or '(ingen Stage 3/4-filer fantes)'}")
    _console.print("  neste steg:")
    _console.print(f"    {payload['next_command']}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rvv-miniputt stage3 reset",
        description=(
            "Discard the current Stage 3/4 lineage while preserving fresh Stage 1/2 "
            "configuration and scrape evidence. Intended for engineering/lifecycle recovery."
        ),
    )
    parser.add_argument("--work-dir", default=".pipeline", help="Pipeline work directory (default: .pipeline)")
    parser.add_argument("--json", action="store_true", help="Print the reset result as JSON")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    return _cmd_stage3_reset(build_parser().parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
