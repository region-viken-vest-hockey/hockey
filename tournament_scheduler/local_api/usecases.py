"""Typed read / preview / apply over ``CanonicalSeasonService`` — the only mutation owner.

No scheduling or booking policy lives here: previews are the service's own
``dry_run`` results, applies are the service's own revision-checked commit. This
module only binds them to a revision, shapes a change table, and maps domain
failures to typed API errors. Nothing here runs at start-up or on browse.
"""

from __future__ import annotations

from typing import Any, Mapping

from tournament_scheduler.application.canonical_season_service import CanonicalSeasonService
from tournament_scheduler.infrastructure.canonical_season_store import CanonicalRevisionConflictError

from .contracts import ApiError, ErrorCode, MOVE_DIMENSIONS, OperatorIntent


def _season_error(exc: Exception) -> ApiError:
    if isinstance(exc, CanonicalRevisionConflictError):
        return ApiError(409, ErrorCode.STALE_REVISION, "canonical state changed since the preview")
    name = type(exc).__name__
    if name == "WriteBlockedError":
        return ApiError(403, ErrorCode.READ_ONLY, "canonical writes are blocked for this installation")
    if isinstance(exc, FileNotFoundError):
        return ApiError(404, ErrorCode.NOT_FOUND, "unknown season")
    return ApiError(422, ErrorCode.REVIEW_REQUIRED, str(exc) or name)


class SeasonUseCases:
    def __init__(self, service: CanonicalSeasonService) -> None:
        self.service = service

    def revision(self, season: str) -> str:
        try:
            return str(self.service.load(season).decisions.get("canonical_state_revision") or "")
        except Exception as exc:
            raise _season_error(exc) from exc

    def summary(self, season: str) -> dict[str, Any]:
        try:
            snap = self.service.load(season)
            lifecycle = self.service.season_lifecycle_report(season)
        except Exception as exc:
            raise _season_error(exc) from exc
        tournaments = (snap.schedule.get("plan") or {}).get("tournaments", [])
        return {
            "season": season,
            "revision": snap.decisions.get("canonical_state_revision"),
            "fingerprint": snap.schedule.get("fingerprint"),
            "lifecycle_state": lifecycle.get("state"),
            "sealed": bool(lifecycle.get("sealed")),
            "tournament_count": len(tournaments),
            "tournaments": [
                {"id": t.get("id"), "date": t.get("date"), "arena": t.get("arena"),
                 "age_group": t.get("age_group"), "host_club": t.get("host_club"),
                 "start_time": t.get("start_time"), "cancelled": bool(t.get("cancelled"))}
                for t in tournaments
            ],
        }

    def capabilities(self, season: str, operator: str, provider_configured: bool) -> dict[str, Any]:
        summary = self.summary(season)
        writable = not summary["sealed"]
        return {
            "operator": operator,
            "season": season,
            "revision": summary["revision"],
            "writable": writable,
            "season_scope": "sealed" if summary["sealed"] else "current",
            "mode": "writable" if writable else "read_only",
            "provider_configured": provider_configured,
            "operations": ["move_tournament"] if writable else [],
        }

    def tournament(self, season: str, tournament_id: str) -> dict[str, Any]:
        try:
            return self.service.tournament_inspection(season=season, tournament_id=tournament_id)
        except Exception as exc:
            raise _season_error(exc) from exc

    @staticmethod
    def _move_kwargs(intent: OperatorIntent) -> dict[str, Any]:
        assert intent.dimension in MOVE_DIMENSIONS
        return {"tournament_id": intent.tournament_id, intent.dimension: intent.value}

    def preview_move(self, intent: OperatorIntent) -> dict[str, Any]:
        """Dry-run through the service; never writes. Fails closed on any unexpected structure.

        The revision is *not* read separately: the proposal binds to the
        ``before_canonical_revision`` the dry-run itself reports, and a mismatch with the
        revision the operator reviewed is ``stale``, never a valid preview.
        """
        requested = intent.expected_revision
        try:
            result = self.service.move_tournament(
                season=intent.season, dry_run=True, request_id=intent.request_id,
                actor="operate-preview", expected_revision=requested, **self._move_kwargs(intent))
        except Exception as exc:
            raise _season_error(exc) from exc
        return _shape_preview(result, requested)

    def apply_move(self, intent: OperatorIntent, *, expected_revision: str, actor: str) -> dict[str, Any]:
        """Human-confirmed canonical write: compare-and-swap on the previewed revision."""
        try:
            self.service.move_tournament(
                season=intent.season, dry_run=False, request_id=intent.request_id, actor=actor,
                expected_revision=expected_revision, **self._move_kwargs(intent))
        except Exception as exc:
            raise _season_error(exc) from exc
        return {"operation": "move_tournament", "revision_before": expected_revision,
                "revision_after": self.revision(intent.season)}


def _unexpected(what: str) -> ApiError:
    return ApiError(422, ErrorCode.REVIEW_REQUIRED, f"canonical preview has an unexpected structure: {what}")


def _shape_preview(result: Any, requested: str | None) -> dict[str, Any]:
    if not isinstance(result, Mapping) or result.get("dry_run") is not True:
        raise _unexpected("not a dry-run result")
    detail = result.get("move_preview")
    if not isinstance(detail, Mapping):
        raise _unexpected("missing move_preview")
    verification = detail.get("verification_result")
    if not isinstance(verification, Mapping):
        raise _unexpected("missing verification_result")
    canonical_verdict = result.get("verdict")
    if isinstance(canonical_verdict, Mapping) and canonical_verdict.get("status") == "blocked":
        # Canonical blocked envelope: hard verification failed. Reported, never appliable.
        blockers = [str(b) for b in canonical_verdict.get("blockers") or []] or ["blocked"]
        return {"operation": "move_tournament", "revision": requested or "", "tournament_id": detail.get("tournament_id"),
                "change_table": [], "after_fingerprint": None, "blockers": blockers,
                "verdict": {"valid": False, "operational_acceptable": False,
                            "change_protection_acceptable": False, "request_constraints_acceptable": False}}
    revision = detail.get("before_canonical_revision")
    old, new = detail.get("old_placement"), detail.get("new_placement")
    if not (isinstance(revision, str) and revision and isinstance(old, Mapping) and isinstance(new, Mapping)):
        raise _unexpected("missing revision or placements")
    if requested and revision != requested:
        raise ApiError(409, ErrorCode.STALE_REVISION, "expected revision is not the current revision",
                       current_revision=revision)
    operational = detail.get("operational_acceptability")
    flags = (verification.get("ok"), (operational or {}).get("ok") if isinstance(operational, Mapping) else None,
             detail.get("change_protection_acceptable"), detail.get("request_constraint_acceptable"))
    if any(not isinstance(f, bool) for f in flags):
        raise _unexpected("missing verdict fields")
    return {
        "operation": "move_tournament", "revision": revision, "tournament_id": detail.get("tournament_id"),
        "change_table": [{"field": f, "from": old.get(f), "to": new.get(f)} for f in MOVE_DIMENSIONS
                         if old.get(f) != new.get(f)],
        "after_fingerprint": detail.get("after_fingerprint"), "blockers": [],
        "verdict": {"valid": flags[0], "operational_acceptable": flags[1],
                    "change_protection_acceptable": flags[2], "request_constraints_acceptable": flags[3]},
    }


def verdict_is_clean(verdict: Mapping[str, Any]) -> bool:
    return all(bool(v) for v in verdict.values())
