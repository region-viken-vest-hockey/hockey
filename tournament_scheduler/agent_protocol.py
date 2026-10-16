"""Repository-owned agent protocol version and the range this package supports.

This is the single declaration of the agent protocol. ``rvv-miniputt capabilities
--json`` reports it, and the Claude runtime adapter reads it before any dispatch.
Changing the range is a deliberate, reviewed change: bump the version when the
argv shape, the ``/rvv-miniputt:operate`` transport or the structured output
changes incompatibly, and widen the supported range only after the operate
procedure has been exercised against it.

This module has no imports on purpose so that both the CLI capability owner and
the infrastructure adapter can depend on it without a cycle.
"""

from __future__ import annotations

AGENT_PROTOCOL_VERSION = 1
AGENT_PROTOCOL_MIN_SUPPORTED = 1
AGENT_PROTOCOL_MAX_SUPPORTED = 1


def is_supported_protocol(version: int) -> bool:
    """True when ``version`` lies inside the supported protocol range (inclusive)."""
    return AGENT_PROTOCOL_MIN_SUPPORTED <= version <= AGENT_PROTOCOL_MAX_SUPPORTED
