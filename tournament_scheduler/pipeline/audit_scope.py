"""Publication-delta scope for semantic audit findings.

A scoped republish concerns only the tournaments the current export adds,
removes or changes relative to the last publication. This module turns the
typed publication scope into that set so audit evidence can separate
current-delta findings from pre-existing background debt. It classifies only;
it never hides a finding or changes publication eligibility.
"""

from __future__ import annotations

from typing import Any, Mapping

from tournament_scheduler.publication.scope import STATUS_BLOCKED, STATUS_ELIGIBLE, STATUS_HELD


def current_delta_tournament_ids(scope: Mapping[str, Any] | None) -> frozenset[str] | None:
    """Tournament IDs the current publication delta touches, or ``None``.

    The delta is the set of tournaments the current export adds, removes or
    changes relative to the last publication. Findings about tournaments
    outside this set are pre-existing background debt for a scoped
    republish. ``None`` means no delta basis exists (no published baseline,
    NOT_CHECKABLE, or an incomplete projection), so callers must treat every
    finding as actionable rather than guess a scope.
    """

    if not isinstance(scope, Mapping):
        return None
    delta = scope.get("delta")
    if not isinstance(delta, Mapping) or scope.get("status") not in (
        STATUS_ELIGIBLE,
        STATUS_HELD,
        STATUS_BLOCKED,
    ):
        return None
    ids: set[str] = set()
    for key in ("added_tournament_ids", "removed_tournament_ids", "changed_tournament_ids"):
        ids.update(str(item) for item in delta.get(key) or [] if item)
    return frozenset(ids)
