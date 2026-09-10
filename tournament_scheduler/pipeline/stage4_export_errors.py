"""Stage 4 export error type.

Split out from ``stage4_export.py`` so sibling helper modules (e.g. the
timestamp resolver) can raise it without importing back from
``stage4_export.py`` and creating a cycle.
"""

from __future__ import annotations


class Stage4Error(RuntimeError):
    """Raised when Stage 4 export fails."""
