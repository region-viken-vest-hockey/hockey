"""Transport-independent request dispatch: security gate first, then a typed route table.

``LocalApp.handle`` is the whole API. It never touches sockets, so contract tests
drive it directly while ``server.py`` merely adapts HTTP onto it.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Callable, Mapping
from urllib.parse import parse_qs, urlsplit

from .contracts import (
    API_VERSION, ApiError, ErrorCode, OperatorIntent, SEASON_ID, DURABLE_ID, failure, ok,
)
from .ice_time_view import IceTimeEvidence
from .jobs import JobManager
from .security import AuthContext, SecurityPolicy, redact
from .sessions import OperateService
from .usecases import SeasonUseCases

MAX_BODY = 1024 * 1024
PREFIX = "/api/v1"
FORBIDDEN_QUERY = ("token", "secret", "key", "password", "csrf")


@dataclass(frozen=True)
class Response:
    status: int
    payload: Any
    headers: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class Ctx:
    auth: AuthContext
    query: Mapping[str, str]
    body: Mapping[str, Any]
    params: tuple[str, ...]


class LocalApp:
    def __init__(self, *, security: SecurityPolicy, uses: SeasonUseCases, operate: OperateService,
                 ice: IceTimeEvidence, jobs: JobManager, season: str, log: Callable[[dict], None] = lambda r: None
                 ) -> None:
        self.security, self.uses, self.operate, self.ice, self.jobs = security, uses, operate, ice, jobs
        self.season, self.log = season, log
        s = r"([A-Za-z0-9_.:-]{1,96})"
        self._routes: list[tuple[str, re.Pattern[str], Callable[[Ctx], Any]]] = [
            (m, re.compile(f"^{PREFIX}{p}$"), h) for m, p, h in [
                ("GET", "/capabilities", self._capabilities),
                ("GET", "/readiness", lambda c: self.operate.readiness.check().to_dict()),
                ("GET", rf"/seasons/{s}", self._season_summary),
                ("GET", rf"/seasons/{s}/tournaments/{s}", self._tournament),
                ("GET", "/ice-time/sources", lambda c: {"sources": self.ice.list(),
                                                       "evidence_revision": self.ice.evidence_revision()}),
                ("GET", rf"/ice-time/sources/{s}", lambda c: self.ice.get(c.params[0])),
                ("PUT", "/ice-time/sources", lambda c: self.ice.put_manual(c.body, c.auth.operator)),
                ("POST", "/operate/sessions", self._create_session),
                ("GET", rf"/operate/sessions/{s}/events", self._events),
                ("POST", rf"/operate/sessions/{s}/intents", self._intent),
                ("POST", rf"/operate/sessions/{s}/proposals/{s}/apply", self._apply),
                ("GET", rf"/jobs/{s}", lambda c: self.jobs.get(c.params[0]).view()),
                ("POST", rf"/jobs/{s}/cancel", lambda c: self.operate.cancel_job(c.params[0])),
            ]
        ]

    # -- entry point ----------------------------------------------------------
    def handle(self, method: str, target: str, headers: Mapping[str, str], raw_body: bytes = b"") -> Response:
        headers = {k.lower(): v for k, v in headers.items()}
        parts = urlsplit(target)
        status = 500
        try:
            self.security.check_host(headers)
            query = {k: v[-1] for k, v in parse_qs(parts.query).items()}
            if any(any(f in k.lower() for f in FORBIDDEN_QUERY) for k in query):
                raise ApiError(400, ErrorCode.BAD_REQUEST, "secrets must not be sent in URLs")
            if not parts.path.startswith(PREFIX):
                if method != "GET" or parts.path != "/":
                    raise ApiError(404, ErrorCode.NOT_FOUND, "not found")
                from .ui import page
                return Response(200, page(), (("Content-Type", "text/html; charset=utf-8"),))
            self.security.check_origin(method, headers)
            body = self._body(method, headers, raw_body)
            if method == "POST" and parts.path == f"{PREFIX}/session/bootstrap":
                return self._bootstrap(body)
            auth = self.security.authenticate(method, headers)
            for m, rx, handler in self._routes:
                match = rx.match(parts.path)
                if match and m == method:
                    result = handler(Ctx(auth, query, body, match.groups()))
                    status = 200
                    return Response(200, ok(result))
            known = any(rx.match(parts.path) for _, rx, _ in self._routes)
            raise ApiError(405 if known else 404, ErrorCode.METHOD_NOT_ALLOWED if known else ErrorCode.NOT_FOUND,
                           "method not allowed" if known else "not found")
        except ApiError as exc:
            status = exc.status
            return Response(exc.status, failure(exc))
        except Exception:
            status = 500
            return Response(500, failure(ApiError(500, ErrorCode.INTERNAL, "internal error")))
        finally:
            self.log(redact({"method": method, "path": parts.path, "status": status}))

    def _body(self, method: str, headers: Mapping[str, str], raw: bytes) -> Mapping[str, Any]:
        if method in ("GET", "HEAD") or not raw:
            return {}
        if len(raw) > MAX_BODY:
            raise ApiError(413, ErrorCode.PAYLOAD_TOO_LARGE, "request body too large")
        if not headers.get("content-type", "").startswith("application/json"):
            raise ApiError(400, ErrorCode.BAD_REQUEST, "content type must be application/json")
        try:
            data = json.loads(raw)
        except ValueError:
            raise ApiError(400, ErrorCode.BAD_REQUEST, "body is not valid JSON") from None
        if not isinstance(data, dict):
            raise ApiError(400, ErrorCode.BAD_REQUEST, "body must be a JSON object")
        return data

    def _bootstrap(self, body: Mapping[str, Any]) -> Response:
        ctx = self.security.exchange_bootstrap(str(body.get("token", "")))
        return Response(200, ok({"csrf_token": ctx.csrf_token, "operator": ctx.operator, "season": self.season,
                                 "api_version": API_VERSION}),
                        (("Set-Cookie", self.security.session_cookie(ctx)),))

    # -- handlers ---------------------------------------------------------------
    def _season(self, value: str) -> str:
        if not SEASON_ID.match(value) or not DURABLE_ID.match(value):
            raise ApiError(400, ErrorCode.BAD_REQUEST, "invalid season id")
        return value

    def _capabilities(self, c: Ctx) -> dict[str, Any]:
        return self.uses.capabilities(self.season, c.auth.operator, self.operate.router is not None)

    def _season_summary(self, c: Ctx) -> dict[str, Any]:
        return self.uses.summary(self._season(c.params[0]))

    def _tournament(self, c: Ctx) -> dict[str, Any]:
        return self.uses.tournament(self._season(c.params[0]), c.params[1])

    def _create_session(self, c: Ctx) -> dict[str, Any]:
        return self.operate.create(self._season(str(c.body.get("season", self.season))), c.auth.operator)

    def _events(self, c: Ctx) -> dict[str, Any]:
        after = c.query.get("after", "0")
        return self.operate.events(c.params[0], int(after) if after.isdigit() else 0)

    def _intent(self, c: Ctx) -> dict[str, Any]:
        return self.operate.submit(c.params[0], OperatorIntent.parse(c.body))

    def _apply(self, c: Ctx) -> dict[str, Any]:
        return self.operate.apply_proposal(c.params[0], c.params[1], str(c.body.get("confirm", "")),
                                           c.auth.operator)
