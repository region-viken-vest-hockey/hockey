"""Replay of batch cancellation/restoration history onto a published projection."""

from __future__ import annotations

from typing import Any, Mapping


class _BatchHistoryError(RuntimeError):
    """Raised when a batch cancel/restore history entry is malformed."""


def apply_batch_cancel_restore(
    projection: dict[str, dict[str, Any]],
    details: Mapping[str, Any],
    applied: list[dict[str, Any]],
    *,
    error: type[Exception] = _BatchHistoryError,
) -> None:
    """Apply a batch's cancellations, then its restorations, in recorded order."""

    for key, cancelled, event in (
        ("cancellations", True, "batch_cancel"),
        ("restorations", False, "batch_restore"),
    ):
        for record in details.get(key) or []:
            if not isinstance(record, Mapping):
                raise error(f"batch {key[:-1]} history entry is not an object")
            tournament_id = str(record.get("tournament_id") or "")
            entry = projection.get(tournament_id)
            if entry is None:
                continue
            entry["cancelled"] = cancelled
            entry["cancellation_reason"] = str(record.get("reason") or "") if cancelled else ""
            applied.append({"event": event, "tournament_id": tournament_id})
