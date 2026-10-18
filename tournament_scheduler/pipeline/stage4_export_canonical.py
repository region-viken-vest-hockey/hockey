"""Canonical-season decisions read by Stage 4 export.

Best-effort lookups over the plan's canonical season: the full decisions
document (for holiday/ban policy on the report plan) and the operator-confirmed
placement overlay used by normalization.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any


def canonical_decisions_document(effective_config: dict[str, Any], plan_dict: dict[str, Any]) -> dict[str, Any]:
    """The full canonical decisions document for the plan's season, if any.

    Best-effort -- a missing canonical season just means an empty document.
    """
    try:
        from ..season.baseline_overlay import resolve_canonical_state

        def _as_date(value: Any):
            if hasattr(value, "isoformat") and not isinstance(value, str):
                return value
            try:
                return datetime.strptime(str(value), "%Y-%m-%d").date()
            except (TypeError, ValueError):
                return None

        start = _as_date(plan_dict.get("start_date") or effective_config.get("start_date"))
        end = _as_date(plan_dict.get("end_date") or effective_config.get("end_date"))
        if start is None or end is None:
            return {}
        resolved = resolve_canonical_state(effective_config, start, end)
        if not resolved:
            return {}
        return dict(resolved.get("decisions", {}) or {})
    except Exception:
        return {}


def export_approvals(effective_config: dict[str, Any], plan_dict: dict[str, Any]) -> dict[str, Any]:
    """Operator-confirmed placements for the plan's canonical season, if any.

    An explicit approval/placement lock is confirmation: that placement is a
    real booking even when a scraped calendar disagrees, so normalization must
    never demote it. Best-effort -- a missing canonical season just means no
    approval overlay.
    """
    return dict(canonical_decisions_document(effective_config, plan_dict).get("decisions", {}) or {})
