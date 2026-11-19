"""Backend-only AI tool boundary: a strict allowlist of typed application tools.

The (future, #965/#966) provider and router reach the application only through
``ToolRegistry``. Registered capabilities are inspect/search/preview/plan; there is
no shell, Git, file, network, apply, approve, publish or authority-changing tool,
and registration of such a capability is refused at construction time. Apply is a
separate human-confirmed HTTP endpoint that this registry cannot reach.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping, Protocol

from .contracts import OperatorIntent

READ_ONLY_KINDS = frozenset({"inspect", "search", "preview", "plan"})
FORBIDDEN_NAME_PARTS = ("apply", "approve", "publish", "seal", "reopen", "shell", "git", "file", "network",
                        "write", "delete", "promote", "authority")


class ToolRefused(Exception):
    pass


@dataclass(frozen=True)
class Tool:
    name: str
    kind: str
    run: Callable[..., Any]


class ToolRegistry:
    def __init__(self, tools: list[Tool]) -> None:
        self._tools: dict[str, Tool] = {}
        for tool in tools:
            if tool.kind not in READ_ONLY_KINDS or any(p in tool.name.lower() for p in FORBIDDEN_NAME_PARTS):
                raise ToolRefused(f"tool {tool.name!r} ({tool.kind}) is outside the allowed capability family")
            self._tools[tool.name] = tool

    def names(self) -> list[str]:
        return sorted(self._tools)

    def invoke(self, name: str, **kwargs: Any) -> Any:
        tool = self._tools.get(name)
        if tool is None:
            raise ToolRefused(f"unregistered tool {name!r}")
        return tool.run(**kwargs)


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
