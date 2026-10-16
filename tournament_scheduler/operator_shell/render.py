"""Operator-language text for the shell. Presentation only: every decision is made by the services."""

from __future__ import annotations

from .flows import PUBLICATION_HANDOFF, RefreshOutcome, RefreshPreview
from .readiness import ReadinessView

_UPDATE_TEXT = {
    "current": "up to date",
    "update-available": "update available (optional)",
    "update-required": "update required before writing",
    "unsupported": "version no longer supported; reinstall from a full package",
    "unknown": "unknown (no update check has run on this computer)",
    "unreadable-cache": "unreadable update cache; writes are blocked until an update check runs",
}
_SIGN_IN_TEXT = {
    "signed-in": "signed in",
    "stored-unverified": "a sign-in is stored but not verified",
    "signed-out": "not signed in",
    "unavailable": "stored sign-in cannot be read",
}


def render_readiness(view: ReadinessView) -> str:
    version = view.version
    repo = view.repository
    lines = [
        f"{version.name} {version.installed} ({version.distribution})",
        f"  Software: {_UPDATE_TEXT.get(version.update_status, version.update_status)}",
        f"  Repository: {repo.state.value} - {repo.reason}",
        f"  GitHub sign-in: {_SIGN_IN_TEXT.get(view.sign_in.state, view.sign_in.state)}",
        f"  Claude assistant: {view.claude.message}",
        "  Claude is optional: the command line works without it.",
        "",
    ]
    if view.canonical_writes_allowed:
        lines.append("Canonical season writes: allowed.")
    else:
        lines.append("Canonical season writes: BLOCKED.")
        lines.extend(f"  - {blocker}" for blocker in view.blockers)
    return "\n".join(lines)


def render_refresh_preview(preview: RefreshPreview) -> str:
    if preview.can_refresh:
        return (
            f"{preview.message}\n"
            f"To apply, run: rvv-miniputt shell refresh --confirm {preview.confirm_token}"
        )
    return preview.message


def render_refresh_outcome(outcome: RefreshOutcome) -> str:
    return outcome.message


def render_publication_handoff() -> str:
    return PUBLICATION_HANDOFF
