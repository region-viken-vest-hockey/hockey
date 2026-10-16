"""Machine-readable capability and compatibility report for ``rvv-miniputt capabilities --json``.

This module only assembles facts owned elsewhere. Each value has exactly one owner:

* application version: ``distribution.version_metadata()`` (``tournament_scheduler.__version__``);
* agent protocol version and supported range: ``agent_protocol``;
* canonical schema versions: ``infrastructure.canonical_season_layout``, the same
  constants the canonical store validates on read;
* supported Claude runtime range: ``infrastructure.claude_runtime.contract``;
* command list: passed in by the CLI, which discovers it from the parser.

Nothing here decides scheduling, verification or publication behaviour.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from . import distribution
from .agent_protocol import (
    AGENT_PROTOCOL_MAX_SUPPORTED,
    AGENT_PROTOCOL_MIN_SUPPORTED,
    AGENT_PROTOCOL_VERSION,
)
from .infrastructure.canonical_season_layout import (
    DECISIONS_SCHEMA_VERSION,
    PARTITIONED_LAYOUT_VERSION,
    SEASON_STATE_SCHEMA_VERSION,
)
from .infrastructure.claude_runtime.contract import SUPPORTED_CONTRACT


def build_capabilities(commands: Sequence[str]) -> dict[str, Any]:
    """Return the capability report as a JSON-serialisable dictionary."""
    metadata = distribution.version_metadata()
    return {
        "name": metadata["name"],
        "application_version": metadata["version"],
        "distribution": metadata["distribution"],
        "agent_protocol_version": AGENT_PROTOCOL_VERSION,
        "agent_protocol_supported": {
            "min": AGENT_PROTOCOL_MIN_SUPPORTED,
            "max": AGENT_PROTOCOL_MAX_SUPPORTED,
        },
        "canonical_schema_version": {
            "schedule": SEASON_STATE_SCHEMA_VERSION,
            "decisions": DECISIONS_SCHEMA_VERSION,
            "physical_layout": PARTITIONED_LAYOUT_VERSION,
        },
        "claude_runtime_supported": {
            "min_version": str(SUPPORTED_CONTRACT.min_claude_version),
            "max_version_exclusive": str(SUPPORTED_CONTRACT.max_claude_version_exclusive),
        },
        "commands": sorted(commands),
    }
