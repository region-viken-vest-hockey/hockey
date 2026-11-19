"""``operate`` readiness adapters: Git-free local workspace and legacy RVV Git preflight.

Both return a backend-neutral ``ReadinessReport`` and fail closed: any fact that
cannot be established yields ``REVIEW_REQUIRED`` with one stable reason code, never
a guess and never a requirement for ``origin/main`` on a local workspace.
"""

from __future__ import annotations

import fcntl
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Callable, Protocol

from tournament_scheduler import distribution
from tournament_scheduler.application.canonical_season_service import CanonicalSeasonService
from tournament_scheduler.application.ports.readiness import (
    ReadinessReason as R,
    ReadinessReport,
    ReadinessStatus,
)

LOCAL_BACKEND = "local"
WORKFLOW_CONTRACT = 1  # bump when the operate typed contract changes incompatibly


class ReadinessCheck(Protocol):
    def check(self) -> ReadinessReport: ...


def _refuse(backend: str, reason: R, detail: str) -> ReadinessReport:
    return ReadinessReport(backend, ReadinessStatus.REVIEW_REQUIRED, reason, detail)


def application_workflow_version() -> str:
    return f"app-{distribution.version_metadata()['version']}+api{WORKFLOW_CONTRACT}"


def _writer_lock_free(root: Path, season: str) -> bool:
    lock = root / f".{season}.lock"
    if not lock.exists():
        return True
    fd = os.open(lock, os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False
    finally:
        os.close(fd)


class LocalWorkspaceReadiness:
    """Checks contract, canonical schema/revision, writer lock, evidence and publication state."""

    def __init__(self, service: CanonicalSeasonService, season: str, root: Path,
                 expected_version: str | None = None,
                 workflow_version: Callable[[], str] = application_workflow_version) -> None:
        self._service, self._season, self._root = service, season, Path(root)
        self._version = workflow_version
        # The contract this session was started under; a different running version fails closed.
        self._expected = expected_version if expected_version is not None else workflow_version()

    def check(self) -> ReadinessReport:
        b = LOCAL_BACKEND
        try:
            version = self._version()
        except Exception:
            return _refuse(b, R.WORKFLOW_VERSION_MISMATCH, "application workflow version could not be determined")
        if version != self._expected:
            return _refuse(b, R.WORKFLOW_VERSION_MISMATCH,
                           f"running workflow {version} differs from the expected contract {self._expected}")
        if not self._service.store.has_state(self._season):
            return _refuse(b, R.STORE_UNAVAILABLE, "no canonical season state in this workspace")
        if not _writer_lock_free(self._root, self._season):
            return _refuse(b, R.WRITER_LOCKED, "another writer holds the season lock")
        try:
            snap = self._service.load(self._season)
            revision = snap.decisions.get("canonical_state_revision")
            lifecycle = self._service.season_lifecycle_report(self._season)
        except Exception as exc:  # schema/IO/corruption: uncertainty fails closed
            return _refuse(b, R.SCHEMA_INCOMPATIBLE, f"canonical state could not be verified ({type(exc).__name__})")
        if not revision:
            return _refuse(b, R.REVISION_UNVERIFIED, "canonical state has no recorded revision")
        recon = lifecycle.get("reconciliation")
        if lifecycle.get("sealed") and not (recon and recon.get("ok")):
            return _refuse(b, R.PUBLICATION_STATE_UNVERIFIED, "sealed season does not reconcile with its baseline")
        if (snap.schedule.get("calendar_evidence_stale") or snap.decisions.get("evidence_stale")) is True:
            return _refuse(b, R.EVIDENCE_STALE, "calendar evidence is marked stale")
        return ReadinessReport(b, ReadinessStatus.READY, R.READY_CURRENT,
                               "local workspace contract and canonical state verified",
                               workflow_version=version, canonical_revision=str(revision))


class LegacyGitReadiness:
    """Runs the repo's ``operate-preflight`` and maps its verdict; fails closed on any doubt."""

    def __init__(self, repo_root: Path, runner: Callable[[list[str], Path], subprocess.CompletedProcess] | None = None):
        self._root = Path(repo_root)
        self._run = runner or (lambda argv, cwd: subprocess.run(
            argv, cwd=cwd, capture_output=True, text=True, timeout=120, check=False))

    def check(self) -> ReadinessReport:
        b = "git"
        try:
            done = self._run([sys.executable, str(self._root / "scripts" / "operate-preflight.py"), "--json"], self._root)
            data = json.loads(done.stdout)
        except (OSError, ValueError, subprocess.TimeoutExpired):
            return _refuse(b, R.INSPECTION_FAILED, "operate preflight did not produce a verdict")
        sha = data.get("workflow_version")
        if done.returncode == 0 and data.get("status") == "READY" and sha:
            return ReadinessReport(b, ReadinessStatus.READY, R.READY_CURRENT,
                                   str(data.get("reason", "")), workflow_version=str(sha))
        try:
            reason = R(data.get("reason_code"))
        except ValueError:
            reason = R.INSPECTION_FAILED
        if reason is R.READY_CURRENT:
            reason = R.INSPECTION_FAILED
        return _refuse(b, reason, str(data.get("reason", "preflight did not report ready")))
