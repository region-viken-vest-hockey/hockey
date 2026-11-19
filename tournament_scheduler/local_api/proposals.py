"""Revision-bound proposals: ONE lifecycle state machine behind one lock.

    pending -> applying -> applied | stale | recovery_required
    pending -> superseded | cancelled          applying -> pending (only after a typed no-write rejection,
                                               and not replaced meanwhile, else superseded)

Every transition is a method of :class:`ProposalBook` taking the book lock; callers
never assign ``status``. ``recovery_required`` is terminal: the canonical write may
have committed, so the proposal is never offered again and the operator must reconcile
against the durable revision. Nothing here writes canonical state.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import threading
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

from .contracts import ApiError, ErrorCode, OperatorIntent

MAX_PROPOSALS = 64
TERMINAL = frozenset({"applied", "stale", "recovery_required", "superseded"})
LIVE = frozenset({"pending", "applying"})


def proposal_digest(intent: OperatorIntent, revision: str, evidence: str, after: Any) -> str:
    payload = json.dumps([intent.body_digest_fields(), revision, evidence, after], sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()


@dataclass
class Proposal:
    proposal_id: str
    intent: OperatorIntent
    revision: str
    evidence_revision: str
    digest: str
    preview: dict[str, Any]
    status: str = "pending"
    created_by: str = ""
    session_id: str = ""
    job_id: str | None = None
    seq: int = 0
    extra: dict[str, Any] = field(default_factory=dict)

    def view(self) -> dict[str, Any]:
        return {"proposal_id": self.proposal_id, "status": self.status, "revision": self.revision,
                "evidence_revision": self.evidence_revision, "digest": self.digest, "preview": self.preview}


class ProposalBook:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._items: dict[str, Proposal] = {}
        self._seq = 0

    # -- reads ------------------------------------------------------------------
    def get(self, proposal_id: str) -> Proposal:
        with self._lock:
            item = self._items.get(proposal_id)
        if item is None:
            raise ApiError(404, ErrorCode.NOT_FOUND, "unknown proposal")
        return item

    def snapshot(self) -> list[Proposal]:
        with self._lock:
            return list(self._items.values())

    # -- transitions (all under the lock) -----------------------------------------
    def register(self, intent: OperatorIntent, revision: str, evidence: str, preview: dict[str, Any],
                 actor: str, session_id: str, *, job_id: str | None = None,
                 still_active: Callable[[], bool] = lambda: True) -> Proposal | None:
        # ``still_active`` is the job's atomic cancellation boundary (JobContext.try_commit).
        """Create a pending proposal, superseding older pending ones for the same target.

        Returns ``None`` (creating nothing) when the originating job is no longer active,
        so a cancelled operation can never leave an applicable proposal behind.
        """
        with self._lock:
            if not still_active():
                return None
            for old in self._items.values():
                if old.status == "pending" and old.intent.tournament_id == intent.tournament_id:
                    old.status = "superseded"
            self._seq += 1
            digest = proposal_digest(intent, revision, evidence, preview.get("after_fingerprint"))
            item = Proposal(uuid.uuid4().hex, intent, revision, evidence, digest, preview, created_by=actor,
                            session_id=session_id, job_id=job_id, seq=self._seq)
            self._items[item.proposal_id] = item
            while len(self._items) > MAX_PROPOSALS:  # evict only finished proposals
                gone = next((k for k, v in self._items.items() if v.status in TERMINAL), None)
                if gone is None:
                    break
                del self._items[gone]
            return item

    def claim(self, proposal_id: str, confirm: str, *, session_id: str, actor: str) -> Proposal:
        """pending -> applying, after ownership, state and the exact reviewed digest are verified."""
        with self._lock:
            item = self._items.get(proposal_id)
            if item is None or item.session_id != session_id:
                raise ApiError(404, ErrorCode.NOT_FOUND, "unknown proposal")  # no cross-session probing
            if not hmac.compare_digest(item.created_by, actor):
                raise ApiError(403, ErrorCode.FORBIDDEN, "proposal belongs to a different operator")
            if item.status == "applied":
                raise ApiError(409, ErrorCode.REPLAYED, "this proposal was already applied")
            if item.status == "recovery_required":
                raise ApiError(409, ErrorCode.RECOVERY_REQUIRED, "an earlier apply may have committed; reconcile first")
            if item.status != "pending":
                raise ApiError(409, ErrorCode.STALE_REVISION, f"proposal is {item.status}")
            if not hmac.compare_digest(confirm.encode(), item.digest.encode()):
                raise ApiError(400, ErrorCode.BAD_REQUEST, "confirmation does not match the reviewed change")
            item.status = "applying"
            return item

    def finish_applied(self, item: Proposal) -> None:
        self._move(item, "applying", "applied")

    def finish_stale(self, item: Proposal) -> None:
        self._move(item, "applying", "stale")

    def mark_recovery_required(self, item: Proposal) -> None:
        self._move(item, "applying", "recovery_required")

    def release_unwritten(self, item: Proposal) -> str:
        """applying -> pending, only when no write occurred AND nothing replaced it meanwhile.

        If a newer proposal for the same target exists the older one becomes ``superseded``;
        an older proposal never becomes pending again once replaced.
        """
        with self._lock:
            if item.status != "applying":
                return item.status
            replaced = any(q.seq > item.seq and q.intent.tournament_id == item.intent.tournament_id
                           and q.status != "cancelled" for q in self._items.values())
            item.status = "superseded" if replaced else "pending"
            return item.status

    def _move(self, item: Proposal, src: str, dst: str) -> None:
        with self._lock:
            if item.status != src:
                raise ApiError(409, ErrorCode.INTERNAL, f"illegal proposal transition {item.status} -> {dst}")
            item.status = dst
