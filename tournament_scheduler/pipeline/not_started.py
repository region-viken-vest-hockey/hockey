"""Shared helpers for the not-started/empty-input pipeline state."""

from __future__ import annotations

from html import escape

from tournament_scheduler.html.templates import NOT_STARTED

NOT_STARTED_MESSAGE = "Ikke begynt: sesongplanleggingen er ikke startet ennå."


def render_not_started_html(message: str = NOT_STARTED_MESSAGE) -> str:
    """Return the public placeholder page shown while planning is unavailable."""
    return NOT_STARTED.replace("{{MESSAGE}}", escape(message))
