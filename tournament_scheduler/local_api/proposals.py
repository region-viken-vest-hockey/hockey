"""Revision-bound proposals and the single-use human approval gate.

A proposal is created only by a preview. It records the canonical revision and the
evidence revision it was computed against and a digest of the exact change; apply
must present that digest, succeeds at most once, and fails ``stale`` if either
revision moved. Nothing in this module writes canonical state.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import threading
import uuid
from dataclasses import dataclass, field
from typing import Any

from .contracts import ApiError, ErrorCode, OperatorIntent

MAX_PROPOSALS = 64


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
    status: str = "pending"  # pending | applied | superseded | stale
    created_by: str = ""
    session_id: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def view(self) -> dict[str, Any]:
        return {"proposal_id": self.proposal_id, "status": self.status, "revision": self.revision,
                "evidence_revision": self.evidence_revision, "digest": self.digest, "preview": self.preview}


class ProposalBook:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._items: dict[str, Proposal] = {}

    def add(self, intent: OperatorIntent, revision: str, evidence: str, preview: dict[str, Any],
            actor: str, session_id: str) -> Proposal:
        with self._lock:
            for old in self._items.values():  # a newer preview for the same target supersedes older ones
                if (old.status == "pending" and old.intent.tournament_id == intent.tournament_id):
                    old.status = "superseded"
            digest = proposal_digest(intent, revision, evidence, preview.get("after_fingerprint"))
            item = Proposal(uuid.uuid4().hex, intent, revision, evidence, digest, preview, created_by=actor,
                        session_id=session_id)
            self._items[item.proposal_id] = item
            while len(self._items) > MAX_PROPOSALS:
                self._items.pop(next(iter(self._items)))
            return item

    def get(self, proposal_id: str) -> Proposal:
        item = self._items.get(proposal_id)
        if item is None:
            raise ApiError(404, ErrorCode.NOT_FOUND, "unknown proposal")
        return item

    def claim(self, proposal_id: str, confirm: str, *, session_id: str, actor: str) -> Proposal:
        """Atomically consume a pending proposal for apply.

        Ownership (creating session and operator), pending state and the exact reviewed
        digest are all verified before the status changes; nothing else is touched here.
        """
        with self._lock:
            item = self._items.get(proposal_id)
            if item is None or item.session_id != session_id:
                raise ApiError(404, ErrorCode.NOT_FOUND, "unknown proposal")  # no cross-session probing
            if not hmac.compare_digest(item.created_by, actor):
                raise ApiError(403, ErrorCode.FORBIDDEN, "proposal belongs to a different operator")
            if item.status == "applied":
                raise ApiError(409, ErrorCode.REPLAYED, "this proposal was already applied")
            if item.status != "pending":
                raise ApiError(409, ErrorCode.STALE_REVISION, f"proposal is {item.status}")
            if not hmac.compare_digest(confirm.encode(), item.digest.encode()):
                raise ApiError(400, ErrorCode.BAD_REQUEST, "confirmation does not match the reviewed change")
            item.status = "applying"
            return item
