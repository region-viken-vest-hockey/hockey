"""Task-oriented operator shell over the packaged application services (epic #364, sub-issue #621).

The shell owns no scheduling, verification, persistence or publication policy. It
only gathers readiness from the owning services and wraps the gated flows the
terminal already uses:

* ``readiness``: read-only view of software version, repository, sign-in and Claude
  readiness, with the operator-language reasons canonical writes are blocked;
* ``flows``: repository refresh from ``main`` as preview, then confirmed apply;
* ``render``: operator-language text for the view (JSON is produced by the CLI).

Canonical commits, season maintenance and publication are not run from the shell.
"""

from .flows import PUBLICATION_HANDOFF, RefreshOutcome, RefreshPreview, apply_refresh, preview_refresh
from .readiness import ReadinessView, gather_readiness
from .render import (
    render_publication_handoff,
    render_readiness,
    render_refresh_outcome,
    render_refresh_preview,
)

__all__ = [
    "PUBLICATION_HANDOFF", "RefreshOutcome", "RefreshPreview", "ReadinessView", "apply_refresh",
    "gather_readiness", "preview_refresh", "render_publication_handoff", "render_readiness",
    "render_refresh_outcome", "render_refresh_preview",
]
