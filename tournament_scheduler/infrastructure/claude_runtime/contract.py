"""Compatibility contract between the Windows package, Claude Code CLI and repository procedures.

The contract is declared here, in one place. It is checked before any canonical
mutation is dispatched (see ``dispatch.py``). Changing the range is a
deliberate, reviewed change: widen it only after the operate procedure has been
exercised against that runtime version.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_VERSION_RE = re.compile(r"(\d+)\.(\d+)\.(\d+)")


@dataclass(frozen=True, order=True)
class Version:
    major: int
    minor: int
    patch: int

    @classmethod
    def parse(cls, text: str) -> Version | None:
        """Return the first ``X.Y.Z`` in ``text``, or ``None`` when there is none."""
        match = _VERSION_RE.search(text)
        if match is None:
            return None
        return cls(int(match.group(1)), int(match.group(2)), int(match.group(3)))

    def __str__(self) -> str:
        return f"{self.major}.{self.minor}.{self.patch}"


@dataclass(frozen=True)
class RuntimeContract:
    """Supported Claude runtime range and agent protocol version.

    ``min_claude_version`` is inclusive; ``max_claude_version_exclusive`` is not.
    ``protocol_version`` identifies the dispatch protocol (argv shape and the
    ``/rvv-miniputt:operate`` transport) that this package speaks.
    """

    min_claude_version: Version
    max_claude_version_exclusive: Version
    protocol_version: int

    def supports(self, version: Version) -> bool:
        return self.min_claude_version <= version < self.max_claude_version_exclusive

    def describe_range(self) -> str:
        return (
            f">= {self.min_claude_version} and < {self.max_claude_version_exclusive}"
        )


# Claude Code 2.1.x is the line verified against this repository (2.1.296 is
# installed on the build VM). Major version 3 is not assessed and is excluded.
SUPPORTED_CONTRACT = RuntimeContract(
    min_claude_version=Version(2, 1, 0),
    max_claude_version_exclusive=Version(3, 0, 0),
    protocol_version=1,
)
