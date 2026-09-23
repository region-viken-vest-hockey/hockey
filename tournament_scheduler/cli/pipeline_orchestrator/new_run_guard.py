"""Guard a plain full-pipeline run from silently invalidating a reviewed candidate.

After Stage 4 exports a hard-valid, unpromoted candidate and a semantic audit
has reviewed it, the safe way to improve it is ``stage3 refine``. A plain
``rvv-miniputt run`` that restarts/revalidates Stage 1 would instead clear the
Stage 3 session and cascade checkpoint staleness, destroying the exact
reviewed candidate.

The repository already owns the lifecycle predicate
(:func:`tournament_scheduler.application.candidate_refinement.reviewed_unpromoted_candidate`);
this module is the thin CLI transport that refuses the run by default and
names both safe alternatives:

- ``stage3 refine`` for ordinary post-audit improvement, or
- ``run --new-full-run`` for a deliberate, explicitly opted-in new full run.
"""

from __future__ import annotations

from typing import Any

from ._shared import _console


def guard_new_full_run(args: Any) -> bool:
    """Return ``True`` when the caller must abort a Stage 1 restarting run.

    Only a genuinely new full run (``--resume-from 1`` and no decision to
    answer) is guarded, and only while a finalized, exported, unpromoted
    candidate exists. ``--new-full-run`` is the explicit opt-in.
    """
    if getattr(args, "new_full_run", False):
        return False
    try:
        from ...application.candidate_refinement import reviewed_unpromoted_candidate

        reviewed = reviewed_unpromoted_candidate(getattr(args, "work_dir", ".pipeline"))
    except Exception:
        return False
    if not reviewed:
        return False

    fingerprint = str(reviewed.get("candidate_fingerprint") or "")
    export_id = str(reviewed.get("export_id") or "")
    _console.print(
        "[red]✗[/red] Nektet: en ferdigstilt, ikke-promotert kandidat er allerede "
        "eksportert og gjennomgått "
        f"(eksport {export_id or '?'}, kandidat {fingerprint[:12] or '?'})."
    )
    _console.print(
        "  En ny full kjøring ville startet Stage 1 på nytt og ugyldiggjort den "
        "gjennomgåtte kandidaten."
    )
    _console.print(
        "  Forbedre den gjennomgåtte kandidaten: "
        "scripts/rvv-miniputt stage3 refine --finding <id> [--option-id <id>]"
    )
    _console.print(
        "  Start bevisst en ny full kjøring (erstatter den gjennomgåtte kandidaten): "
        "scripts/rvv-miniputt run --new-full-run"
    )
    return True


def guard_sealed_season_run(cfg: Any, start: Any, end: Any) -> bool:
    """Refuse a full pipeline run whose window matches a published_sealed season.

    A published season is an operational schedule, not a planning problem. A
    full run would rebuild Stage 1-4 facts (and a competing candidate) around an
    operational baseline; the supported path is explicit canonical maintenance
    (``season move`` / ``swap-participants`` / ``batch`` / repair search) plus
    ``season export``. This is a transport for the canonical lifecycle
    predicate; the authoritative refusal for actually replacing canonical state
    lives at the ``season promote`` / ``season apply`` boundary.
    """
    try:
        from ...canonical_baseline import resolve_canonical_state
        from ...published_baseline import is_published_sealed

        resolved = resolve_canonical_state(cfg, start, end)
    except Exception:
        return False
    if not resolved:
        return False
    decisions = resolved.get("decisions")
    if not is_published_sealed(decisions):
        return False
    season = resolved.get("season")
    _console.print(
        f"[red]✗[/red] Nektet: {season} er publisert og seilet (published_sealed). "
        "En publisert sesong er en operativ plan, ikke et planleggingsproblem."
    )
    _console.print(
        "  Vedlikehold den med eksplisitte kanoniske operasjoner "
        "(season move / swap-participants / batch / repair + season export)."
    )
    _console.print(
        f"  Se status: scripts/rvv-miniputt season lifecycle --season {season}"
    )
    return True
