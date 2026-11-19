"""HTTP transport over ``LocalApp``: loopback bind, bounded reads, hardened headers, no CORS."""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .contracts import ApiError, ErrorCode, failure
from .routes import MAX_BODY, LocalApp
from .security import validate_bind_host

SECURITY_HEADERS = (("Cache-Control", "no-store"), ("X-Content-Type-Options", "nosniff"),
                    ("Referrer-Policy", "no-referrer"), ("Cross-Origin-Resource-Policy", "same-origin"),
                    ("X-Frame-Options", "DENY"))


def make_server(app: LocalApp, host: str = "127.0.0.1", port: int = 0, *, allow_remote: bool = False
                ) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "operate"

        def log_message(self, *args) -> None:  # request lines may carry URLs; app.log is the only log
            pass

        def _serve(self) -> None:
            length = self.headers.get("Content-Length")
            if length is not None and (not length.isdigit() or int(length) > MAX_BODY):
                resp = self._error(413, ErrorCode.PAYLOAD_TOO_LARGE, "request body too large")
                self.close_connection = True
            else:
                body = self.rfile.read(int(length)) if length else b""
                resp = app.handle(self.command, self.path, dict(self.headers.items()), body)
            data = resp.payload if isinstance(resp.payload, bytes) else json.dumps(resp.payload).encode()
            self.send_response(resp.status)
            ctype = dict(resp.headers).get("Content-Type", "application/json")
            self.send_header("Content-Type", ctype)
            for k, v in (*SECURITY_HEADERS, *(h for h in resp.headers if h[0] != "Content-Type")):
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(data)

        def _error(self, status, code, message):
            from .routes import Response
            return Response(status, failure(ApiError(status, code, message)))

        do_GET = do_POST = do_PUT = do_DELETE = do_PATCH = do_HEAD = do_OPTIONS = _serve

    bind = validate_bind_host(host, allow_remote=allow_remote)
    server = ThreadingHTTPServer((bind, port), Handler)
    server.daemon_threads = True
    return server
