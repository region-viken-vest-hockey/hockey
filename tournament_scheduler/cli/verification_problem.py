"""
Canonical verification problem resolution for RVV Miniputt CLI commands.

Shared by season approval/move/guest-slot transports so every mutation is
verified against the same planning problem contract.
"""

from __future__ import annotations


def _canonical_verification_problem(
    work_dir: str, season: str | None = None, root: str | None = None
) -> dict | None:
    """Best-effort full ``planning_problem`` for canonical approval/move gates.

    Reconstructs the same problem contract Stage 3 was given (including any
    canonical baseline locks and active operator waivers) so approving or
    moving a canonical tournament is checked against the real hard
    invariants, not only self-consistency.  When *season*/*root* are given,
    an unrelated ``--work-dir`` pipeline (a different season's config) is
    ignored rather than applied to this season.  A promoted canonical season
    owns the authoritative problem: once its schedule exists, any failure to
    load it or project its live overlays raises :class:`SeasonStateError`
    rather than silently degrading to self-consistency verification.  Only a
    genuinely absent canonical schedule (or one without a promoted problem)
    falls back to the pipeline reconstruction and may return ``None``.
    """
    from datetime import date as _date

    if season:
        from ..season_state import SeasonStateError, load_decisions, load_schedule, schedule_path

        season_root = root or "season"
        # Only a genuinely absent canonical schedule may fall through to the
        # pipeline reconstruction below. Once the schedule exists, a failure
        # to load it or project its live overlays must fail closed: verifying
        # against an incomplete or absent contract is worse than refusing the
        # mutation.
        if schedule_path(season, root=season_root).exists():
            schedule = load_schedule(season, root=season_root)
            context = schedule.get("verification_context") if isinstance(schedule, dict) else None
            problem = context.get("problem") if isinstance(context, dict) else None
            if isinstance(problem, dict) and problem:
                # The stored problem is frozen at the season's original
                # promotion; project the *current* canonical decisions into it
                # through the one shared overlay facade -- holiday exceptions,
                # banned dates, calendar-booking associations, ice-time
                # overrides and durable participation withdrawals -- mirroring
                # ``season findings``/``season export``, so approve/move/etc.
                # never refuse a mutation over a since-superseded fact or an
                # already-committed withdrawal, and never re-derive an
                # incomplete subset of the canonical overlays.
                from ..season_maintenance import project_canonical_overlays

                decisions = load_decisions(season, root=season_root)
                try:
                    return project_canonical_overlays(
                        problem,
                        decisions=decisions,
                        plan=schedule.get("plan") or {},
                    )
                except SeasonStateError:
                    raise
                except Exception as exc:
                    raise SeasonStateError(
                        f"Canonical season {season} verification overlay projection "
                        f"failed: {type(exc).__name__}: {exc}"
                    ) from exc

    from ..pipeline.stage1_config import load_effective_config
    from ..pipeline.stage4_export_verification import _build_export_verification_problem
    from ..pipeline.state import PipelineState

    state = PipelineState(work_dir)
    try:
        effective_config = load_effective_config(state)
    except Exception:
        effective_config = {}
    if not effective_config:
        return None
    if season:
        start_raw = effective_config.get("start_date")
        end_raw = effective_config.get("end_date")
        if not start_raw or not end_raw:
            return None
        try:
            from ..canonical_baseline import resolve_canonical_season

            resolved = resolve_canonical_season(
                effective_config,
                _date.fromisoformat(str(start_raw)),
                _date.fromisoformat(str(end_raw)),
                root=root,
            )
        except Exception:
            return None
        if resolved != season:
            return None
    return _build_export_verification_problem(effective_config, state)


