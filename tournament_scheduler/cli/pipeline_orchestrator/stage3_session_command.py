"""``rvv-miniputt stage3 session`` — compact Stage 3 session/status facade.

An agent debugging a Stage 3 resume problem must be able to see where the
run is, which candidate revision/fingerprint is current, what decision is
pending, which decisions were already resolved, and which transition types
are legal next -- from one machine-readable view, without inspecting and
reconciling five side files.
"""

from __future__ import annotations

import argparse
import json
from typing import Any

from ._shared import _console


def _active_run_id(work_dir: str) -> str:
    try:
        from ...pipeline.run_manifest import RunManifest

        return str(RunManifest(work_dir).read().get("run_id") or "")
    except Exception:
        return ""


def _cmd_stage3_session(args: argparse.Namespace) -> int:
    from ...application.stage3_session_store import Stage3SessionStore, status_for_session

    work_dir = getattr(args, "work_dir", ".pipeline")
    run_id = _active_run_id(work_dir)
    session = Stage3SessionStore(work_dir).load(expected_run_id=run_id or None)
    view: dict[str, Any] = status_for_session(session)
    view["work_dir"] = str(work_dir)

    if getattr(args, "json", False):
        print(json.dumps(view, indent=2, ensure_ascii=False))
        return 0

    _console.print(f"[bold]Stage 3-sesjon[/bold] ({work_dir})")
    _console.print(f"  run_id: {view['run_id'] or '(ingen aktiv kjøring)'}")
    _console.print(f"  status: {view['status']}")
    _console.print(
        f"  kandidat: revisjon {view['candidate_revision']} "
        f"({(view['candidate_fingerprint'] or '-')[:12]})"
    )
    pending = view["pending_decision"]
    if pending:
        _console.print(
            f"  venter på: {pending['capability']} ({pending['scope']}, "
            f"revisjon {pending['candidate_revision']})"
        )
    else:
        _console.print("  venter på: (ingen)")
    _console.print(f"  løste delt-vertskap: {view['shared_host_decisions']}")
    if view["finalized_revision"] is not None:
        _console.print(
            f"  ferdigstilt: revisjon {view['finalized_revision']} "
            f"({(view['finalized_fingerprint'] or '-')[:12]})"
        )
    _console.print(f"  lovlige overganger: {', '.join(view['legal_transitions']) or '(ingen)'}")
    return 0
