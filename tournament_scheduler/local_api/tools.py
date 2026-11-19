"""Backend-only AI tool boundary: a closed, typed registry of approved capabilities.

The (future, #965/#966) provider and router reach the application only through
``ToolRegistry``. The registry is *closed*: it is constructed from the two services it
may read and binds a fixed table of their read-only methods. Callers cannot register
callables, so no shell, Git, file, network, apply, approve, publish or authority-changing
capability can be added through this boundary. Apply is a separate human-confirmed HTTP
endpoint that this registry cannot reach.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Mapping, Protocol

from .contracts import OperatorIntent
from .ice_time_view import IceTimeEvidence
from .usecases import SeasonUseCases


class ToolRefused(Exception):
    pass


class Capability(str, Enum):
    SEASON_SUMMARY = "season_summary"
    TOURNAMENT_INSPECTION = "tournament_inspection"
    ICE_TIME_SOURCES = "ice_time_sources"
    MOVE_PREVIEW = "move_preview"


KINDS = {Capability.SEASON_SUMMARY: "inspect", Capability.TOURNAMENT_INSPECTION: "inspect",
         Capability.ICE_TIME_SOURCES: "search", Capability.MOVE_PREVIEW: "preview"}


class ToolRegistry:
    def __init__(self, uses: SeasonUseCases, ice: IceTimeEvidence) -> None:
        if not isinstance(uses, SeasonUseCases) or not isinstance(ice, IceTimeEvidence):
            raise ToolRefused("the registry is bound to the approved application services only")
        self._table: dict[Capability, Callable[..., Any]] = {
            Capability.SEASON_SUMMARY: uses.summary,
            Capability.TOURNAMENT_INSPECTION: uses.tournament,
            Capability.ICE_TIME_SOURCES: ice.list,
            Capability.MOVE_PREVIEW: uses.preview_move,
        }

    def names(self) -> list[str]:
        return sorted(c.value for c in self._table)

    def kind(self, name: str) -> str:
        return KINDS[self._capability(name)]

    def _capability(self, name: str) -> Capability:
        try:
            return Capability(name)
        except ValueError:
            raise ToolRefused(f"unregistered tool {name!r}") from None

    def invoke(self, name: str, **kwargs: Any) -> Any:
        return self._table[self._capability(name)](**kwargs)


@dataclass(frozen=True)
class RouterDecision:
    """What an intent router returns: a structured intent, a clarification, or a refusal."""

    intent: OperatorIntent | None = None
    clarification: str | None = None
    unsupported: str | None = None
    findings: tuple[Mapping[str, Any], ...] = ()


class IntentRouterPort(Protocol):
    """Seam for #966/#965: turns a text goal into a ``RouterDecision`` using only ``tools``."""

    def route(self, intent: OperatorIntent, tools: ToolRegistry, emit: Callable[[str, Mapping[str, Any]], None]
              ) -> RouterDecision: ...
