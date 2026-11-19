"""Loopback-only access control: Host/Origin checks, bootstrap token, session, CSRF.

Threat model: a hostile web page in the operator's browser (or DNS rebinding)
trying to reach the loopback listener. Defence: exact Host and Origin allowlists,
no CORS, an HttpOnly SameSite=Strict session cookie obtained by exchanging a
one-time bootstrap token (delivered in a URL *fragment*, never sent to a server),
and a per-session CSRF token required on every state-changing request.
"""

from __future__ import annotations

import hmac
import ipaddress
import secrets
import threading
from dataclasses import dataclass
from typing import Mapping

from .contracts import ApiError, ErrorCode

COOKIE = "operate_session"
CSRF_HEADER = "x-csrf-token"
SAFE_METHODS = frozenset({"GET", "HEAD"})
MAX_SESSIONS = 8


def validate_bind_host(host: str, *, allow_remote: bool = False) -> str:
    """Refuse a non-loopback bind unless remote binding is explicitly requested."""
    if host == "localhost":
        return "127.0.0.1"
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        raise ValueError(f"bind host must be a loopback IP address, got {host!r}") from None
    if not addr.is_loopback and not allow_remote:
        raise ValueError(f"refusing to bind non-loopback address {host}; remote binding is off by default")
    return str(addr)


@dataclass(frozen=True)
class AuthContext:
    session_id: str
    operator: str
    csrf_token: str


class SecurityPolicy:
    def __init__(self, *, operator: str, port: int | None = None) -> None:
        self.allowed_hosts: frozenset[str] = frozenset()
        self.allowed_origins: frozenset[str] = frozenset()
        self.operator = operator
        if port is not None:
            self.set_port(port)
        self._lock = threading.Lock()
        self._bootstrap: str | None = secrets.token_urlsafe(32)
        self._sessions: dict[str, AuthContext] = {}

    def set_port(self, port: int) -> None:
        """Called once the dynamic port is bound; until then every Host is refused."""
        self.allowed_hosts = frozenset({f"127.0.0.1:{port}", f"localhost:{port}", f"[::1]:{port}"})
        self.allowed_origins = frozenset(f"http://{h}" for h in self.allowed_hosts)

    def bootstrap_token(self) -> str:
        assert self._bootstrap is not None
        return self._bootstrap

    def exchange_bootstrap(self, presented: str) -> AuthContext:
        """One-time exchange: the bootstrap token dies on first valid use."""
        with self._lock:
            token = self._bootstrap
            if token is None or not hmac.compare_digest(token, presented or ""):
                raise ApiError(401, ErrorCode.UNAUTHENTICATED, "bootstrap token invalid or already used")
            self._bootstrap = None
            if len(self._sessions) >= MAX_SESSIONS:
                raise ApiError(403, ErrorCode.UNAUTHENTICATED, "too many sessions")
            ctx = AuthContext(secrets.token_urlsafe(32), self.operator, secrets.token_urlsafe(32))
            self._sessions[ctx.session_id] = ctx
            return ctx

    def check_host(self, headers: Mapping[str, str]) -> None:
        if headers.get("host", "").lower() not in self.allowed_hosts:
            raise ApiError(403, ErrorCode.FORBIDDEN_HOST, "Host header is not an allowed loopback authority")

    def check_origin(self, method: str, headers: Mapping[str, str]) -> None:
        origin = headers.get("origin")
        if origin is None:
            if method not in SAFE_METHODS:
                raise ApiError(403, ErrorCode.FORBIDDEN_ORIGIN, "state-changing requests need an Origin header")
            return
        if origin not in self.allowed_origins:
            raise ApiError(403, ErrorCode.FORBIDDEN_ORIGIN, "Origin is not allowed")

    def authenticate(self, method: str, headers: Mapping[str, str]) -> AuthContext:
        ctx = self._sessions.get(_cookie(headers.get("cookie", ""), COOKIE) or "")
        if ctx is None:
            raise ApiError(401, ErrorCode.UNAUTHENTICATED, "no valid local session")
        if method not in SAFE_METHODS:
            presented = headers.get(CSRF_HEADER, "")
            if not hmac.compare_digest(ctx.csrf_token, presented):
                raise ApiError(403, ErrorCode.CSRF_REJECTED, "missing or invalid CSRF token")
        return ctx

    def session_cookie(self, ctx: AuthContext) -> str:
        return f"{COOKIE}={ctx.session_id}; HttpOnly; SameSite=Strict; Path=/api/"


def _cookie(header: str, name: str) -> str | None:
    for part in header.split(";"):
        key, _, value = part.strip().partition("=")
        if key == name:
            return value
    return None


SECRET_KEYS = ("token", "cookie", "authorization", "api_key", "apikey", "secret", "password", "csrf")


def redact(mapping: Mapping[str, object]) -> dict[str, object]:
    """Copy of ``mapping`` safe to log: any secret-looking key is masked."""
    return {k: ("***" if any(s in k.lower() for s in SECRET_KEYS) else v) for k, v in mapping.items()}
