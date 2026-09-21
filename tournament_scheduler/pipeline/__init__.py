"""Pipeline state management subpackage.

Provides JSON checkpoint files per stage so the agentic pipeline can be
resumed from any completed stage without re-running earlier ones, plus
the ``TournamentUpdater`` for targeted post-generation modifications.

Keep this package initializer lightweight: many workflow smoke tests import
small pipeline helpers in environments that deliberately install only the
runtime/test dependency set.  ``TournamentUpdater`` pulls in the planner stack,
including optional solver-backed game generation, so expose it lazily instead
of making every ``tournament_scheduler.pipeline.*`` import require that stack.
"""

from typing import TYPE_CHECKING

from .state import PipelineState, StageName, StageStatus

if TYPE_CHECKING:  # pragma: no cover - import-time typing only
    from .tournament_updater import TournamentUpdater, UpdateResult

__all__ = ["PipelineState", "StageStatus", "StageName", "TournamentUpdater", "UpdateResult"]


def __getattr__(name: str):
    if name in {"TournamentUpdater", "UpdateResult"}:
        from .tournament_updater import TournamentUpdater, UpdateResult

        return {"TournamentUpdater": TournamentUpdater, "UpdateResult": UpdateResult}[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
