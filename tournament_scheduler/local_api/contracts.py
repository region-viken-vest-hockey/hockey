"""Versioned, typed request/result vocabulary of the local ``operate`` API.

Pure data: no I/O, no HTTP. Every response is an envelope carrying ``api_version``
and either ``result`` or a structured ``error``; clients never parse prose.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping

API_VERSION = 1
SEASON_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,39}$")
DURABLE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,95}$")
REQUEST_ID = re.compile(r"^[A-Za-z0-9_-]{8,64}$")


class Outcome(str, Enum):
    PROPOSAL_PENDING = "proposal_pending"
    PREVIEWED = "previewed"
    REVIEW_REQUIRED = "review_required"
    BLOCKED = "blocked"
    STALE = "stale"
    APPLIED = "applied"


class Phase(str, Enum):
    RECEIVED = "received"
    ROUTING = "routing"
    PREVIEWING = "previewing"
    AWAITING_APPROVAL = "awaiting_approval"
    APPLYING = "applying"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ErrorCode(str, Enum):
    BAD_REQUEST = "bad_request"
    UNAUTHENTICATED = "unauthenticated"
    FORBIDDEN_ORIGIN = "forbidden_origin"
    FORBIDDEN_HOST = "forbidden_host"
    CSRF_REJECTED = "csrf_rejected"
    FORBIDDEN = "forbidden"
    NOT_FOUND = "not_found"
    METHOD_NOT_ALLOWED = "method_not_allowed"
    PAYLOAD_TOO_LARGE = "payload_too_large"
    STALE_REVISION = "stale_revision"
    REPLAYED = "replayed"
    RECOVERY_REQUIRED = "recovery_required"
    REQUEST_ID_REUSED = "request_id_reused"
    REQUEST_ID_EXPIRED = "request_id_expired"
    SESSION_REQUEST_LIMIT = "session_request_limit"
    READ_ONLY = "read_only"
    REVIEW_REQUIRED = "review_required"
    UNSUPPORTED = "unsupported"
    INTERNAL = "internal"


class ApiError(Exception):
    """A structured failure; ``status`` is the HTTP status the transport maps it to."""

    def __init__(self, status: int, code: ErrorCode, message: str, **details: Any) -> None:
        super().__init__(message)
        self.status, self.code, self.message, self.details = status, code, message, details

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code.value, "message": self.message, "details": self.details}


def ok(result: Any) -> dict[str, Any]:
    return {"api_version": API_VERSION, "result": result}


def failure(error: ApiError) -> dict[str, Any]:
    return {"api_version": API_VERSION, "error": error.to_dict()}


class Gesture(str, Enum):
    TEXT = "text"
    CALENDAR_DRAG = "calendar_drag"
    CALENDAR_EDIT = "calendar_edit"
    ROSTER_CONTROL = "roster_control"


MOVE_DIMENSIONS = ("date", "start_time", "arena", "host_club")


@dataclass(frozen=True)
class OperatorIntent:
    """One validated operator intent. Structured gestures never degrade to prose."""

    request_id: str
    gesture: Gesture
    season: str
    expected_revision: str | None = None
    text: str | None = None
    tournament_id: str | None = None
    team_id: str | None = None
    dimension: str | None = None
    value: str | None = None
    timezone: str | None = None

    @classmethod
    def parse(cls, raw: Mapping[str, Any]) -> "OperatorIntent":
        def text_field(name: str, limit: int = 4000) -> str | None:
            value = raw.get(name)
            if value is None:
                return None
            if not isinstance(value, str) or len(value) > limit:
                raise ApiError(400, ErrorCode.BAD_REQUEST, f"{name} must be a string up to {limit} chars")
            return value

        def pattern(name: str, rx: re.Pattern[str], required: bool = True) -> str | None:
            value = text_field(name, 100)
            if value is None:
                if required:
                    raise ApiError(400, ErrorCode.BAD_REQUEST, f"{name} is required")
                return None
            if not rx.match(value):
                raise ApiError(400, ErrorCode.BAD_REQUEST, f"{name} is not a valid durable id")
            return value

        try:
            gesture = Gesture(raw.get("gesture", "text"))
        except ValueError:
            raise ApiError(400, ErrorCode.BAD_REQUEST, "unknown source gesture") from None
        request_id = pattern("request_id", REQUEST_ID)
        season = pattern("season", SEASON_ID)
        assert request_id and season
        intent = cls(
            request_id=request_id, gesture=gesture, season=season,
            expected_revision=text_field("expected_revision", 128), text=text_field("text"),
            tournament_id=pattern("tournament_id", DURABLE_ID, required=False),
            team_id=pattern("team_id", DURABLE_ID, required=False),
            dimension=text_field("dimension", 32), value=text_field("value", 64),
            timezone=text_field("timezone", 64),
        )
        intent._validate_shape()
        return intent

    def _validate_shape(self) -> None:
        if self.gesture is Gesture.TEXT:
            if not (self.text or "").strip():
                raise ApiError(400, ErrorCode.BAD_REQUEST, "a text intent needs non-empty text")
            return
        if self.dimension not in MOVE_DIMENSIONS or not self.value or not self.tournament_id:
            raise ApiError(400, ErrorCode.BAD_REQUEST,
                           "a structured gesture needs tournament_id, dimension and value")
        if not self.expected_revision:
            raise ApiError(400, ErrorCode.BAD_REQUEST, "a structured gesture needs expected_revision")
        if self.dimension == "date" and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", self.value):
            raise ApiError(400, ErrorCode.BAD_REQUEST, "date must be YYYY-MM-DD")
        if self.dimension == "start_time":
            if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", self.value):
                raise ApiError(400, ErrorCode.BAD_REQUEST, "start_time must be HH:MM")
        if self.dimension in ("date", "start_time"):
            from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
            try:
                ZoneInfo(self.timezone or "")
            except (ZoneInfoNotFoundError, ValueError):
                raise ApiError(400, ErrorCode.BAD_REQUEST, "an IANA timezone is required") from None

    def body_digest_fields(self) -> tuple:
        return (self.gesture.value, self.season, self.expected_revision, self.text, self.tournament_id,
                self.team_id, self.dimension, self.value, self.timezone)
