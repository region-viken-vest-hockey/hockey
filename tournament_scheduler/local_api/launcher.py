"""Start the local operate application: readiness, single instance, loopback bind, browser.

Starting or browsing never mutates season data or publishes anything; the only
writes are the private ``.operate`` state files (lock, 0600 bootstrap token).
"""

from __future__ import annotations

import argparse
import getpass
import logging
import signal
import threading
import webbrowser
from pathlib import Path

from tournament_scheduler.application.canonical_season_service import CanonicalSeasonService

from .ice_time_view import IceTimeEvidence
from .instance import InstanceRunningError, WorkspaceInstance
from .jobs import JobManager
from .readiness import LocalWorkspaceReadiness
from .routes import LocalApp
from .security import SecurityPolicy
from .server import make_server
from .sessions import OperateService
from .tools import IntentRouterPort, ToolRegistry
from .usecases import SeasonUseCases

LOG = logging.getLogger("operate.local_api")


def build_app(workspace: Path, season: str, *, service: CanonicalSeasonService | None = None,
              router: IntentRouterPort | None = None, operator: str | None = None, wait_s: float = 5.0,
              require_ice_time: bool = False):
    workspace = Path(workspace)
    service = service or CanonicalSeasonService(root=workspace / "season")
    uses = SeasonUseCases(service)
    ice = IceTimeEvidence(workspace / "ice_time", required=require_ice_time)
    jobs = JobManager()
    tools = ToolRegistry(uses, ice)
    readiness = LocalWorkspaceReadiness(service, season, workspace / "season", evidence_status=ice.status)
    operate = OperateService(uses, jobs, readiness, tools, router, ice.evidence_revision, wait_s,
                             commit_gate=ice.gate)
    security = SecurityPolicy(operator=operator or getpass.getuser())
    app = LocalApp(security=security, uses=uses, operate=operate, ice=ice, jobs=jobs, season=season,
                   log=lambda r: LOG.info("%s", r))
    return app


def run(workspace: Path, season: str, *, host: str = "127.0.0.1", open_browser: bool = True,
        allow_remote: bool = False) -> int:
    instance = WorkspaceInstance(workspace)
    try:
        instance.acquire()
    except InstanceRunningError as exc:
        print(str(exc))
        return 2
    app = build_app(workspace, season)
    server = make_server(app, host, 0, allow_remote=allow_remote)
    port = server.server_port
    app.security.set_port(port)
    instance.publish(port, app.security.bootstrap_token())
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    print(f"operate listening on http://127.0.0.1:{port}/ (bootstrap token in {instance.bootstrap_file})", flush=True)
    if open_browser:
        webbrowser.open(f"http://127.0.0.1:{port}/#{app.security.bootstrap_token()}")
    try:
        stop.wait()
    finally:
        server.shutdown()
        server.server_close()
        app.jobs.shutdown()
        instance.release()
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="operate-local", description="Local operate application (loopback only)")
    p.add_argument("--workspace", type=Path, default=Path("."))
    p.add_argument("--season", required=True)
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--no-browser", action="store_true")
    a = p.parse_args(argv)
    return run(a.workspace, a.season, host=a.host, open_browser=not a.no_browser)
