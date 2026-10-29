"""Render ``public_history.html`` -- public tournament changes between publications (#869 item 3).

Read-only projection of the publication records kept in canonical state. The
derivation lives in :mod:`tournament_scheduler.publication.public_history`;
this module only loads the records for a season and renders them. A tournament
that did not change between publications gets no entry and no marker, and the
page is written only when at least one tournament changed.
"""

from __future__ import annotations

import html as _html
import logging
from pathlib import Path
from typing import Any, Mapping

from ..html.templates import STYLES_CSS
from ..publication.public_history import PUBLIC_CHANGE_FIELDS, public_change_history

PUBLIC_HISTORY_FILENAME = "public_history.html"

logger = logging.getLogger(__name__)

_FIELD_LABELS = {
    "date": "Dato",
    "start_time": "Starttid",
    "end_time": "Sluttid",
    "duration_minutes": "Varighet (min)",
    "arena": "Arena",
    "host_club": "Arrangør",
    "cancelled": "Avlyst",
    "cancellation_reason": "Årsak til avlysning",
    "participants": "Lag",
}

__all__ = ["PUBLIC_HISTORY_FILENAME", "public_change_history_for_season", "render_html", "write_html"]


def _esc(value: Any) -> str:
    return _html.escape(str(value if value is not None else ""), quote=True)


def _value(value: Any) -> str:
    if isinstance(value, bool):
        return "ja" if value else "nei"
    if isinstance(value, list):
        return ", ".join(str(item) for item in value) or "—"
    return str(value) if value not in (None, "") else "—"


def _entry_html(entry: Mapping[str, Any]) -> str:
    head = f'<span class="meta">Publisering {_esc(entry.get("publication_id"))} ({_esc(entry.get("published_at"))})</span>'
    if entry.get("event") in ("added", "removed"):
        wording = "Lagt til i publiseringen" if entry.get("event") == "added" else "Fjernet fra publiseringen"
        return f"<li>{head} {wording}</li>"
    changes = "".join(
        f"<li><strong>{_esc(_FIELD_LABELS.get(str(change.get('field')), change.get('field')))}:</strong> "
        f"{_esc(_value(change.get('before')))} &rarr; {_esc(_value(change.get('after')))}</li>"
        for change in entry.get("changes") or []
        if isinstance(change, Mapping) and change.get("field") in PUBLIC_CHANGE_FIELDS
    )
    return f"<li>{head}<ul>{changes}</ul></li>"


def render_html(history: Mapping[str, Any], *, season: str = "", season_plan_href: str = "season_plan.html") -> str:
    """Render the public history page from a :func:`public_change_history` result."""

    tournaments = history.get("tournaments") if isinstance(history.get("tournaments"), Mapping) else {}
    labels = history.get("labels") if isinstance(history.get("labels"), Mapping) else {}
    sections = "".join(
        f'<section class="history-item" id="t-{_esc(tournament_id)}">'
        f"<h3>{_esc(labels.get(tournament_id) or tournament_id)} "
        '<span class="report-status-pill report-status-pill--warn">ENDRET</span></h3>'
        f"<ul>{''.join(_entry_html(entry) for entry in entries)}</ul></section>"
        for tournament_id, entries in tournaments.items()
        if entries
    )
    subtitle = f"{season} — publiseringshistorikk" if season else "Publiseringshistorikk"
    nav = f'<a href="{_esc(season_plan_href)}">Sesongplan</a>' if season_plan_href else ""
    return (
        '<!DOCTYPE html>\n<html lang="no">\n<head>\n<meta charset="UTF-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1.0">\n'
        f"<title>Publiseringshistorikk — RVV Hockey</title>\n<style>\n{STYLES_CSS}\n</style>\n</head>\n<body>\n"
        f'<div class="navbar"><span class="brand">RVV Miniputt</span>{nav}</div>\n'
        f'<main class="report-hero report-hero--warn"><h1>{_esc(subtitle)}</h1>'
        "<p>Turneringer som er endret mellom publiseringer. Siden er en read-only projeksjon av "
        "publiseringshistorikken og endres ikke herfra.</p></main>\n"
        f"<main>{sections or '<p>Ingen turneringer er endret mellom publiseringer.</p>'}</main>\n"
        "</body>\n</html>\n"
    )


def write_html(history: Mapping[str, Any] | None, export_dir: str | Path, **hrefs: str) -> str:
    """Write ``public_history.html`` into *export_dir* when a tournament changed; return its path or "".

    ``None`` means canonical state could not be read. The existing page is then left
    as it was, because an unreadable history is not the same as an empty one.
    """

    target = Path(export_dir) / PUBLIC_HISTORY_FILENAME
    if history is None:
        return ""
    if not any((history.get("tournaments") or {}).values()):
        if target.exists():
            target.unlink()
        return ""
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_html(history, **hrefs), encoding="utf-8")
    return str(target)


def public_change_history_for_season(*, season: str, root: str | Path) -> dict[str, Any] | None:
    """Load the season's publication records and derive their public change history.

    Returns ``None`` when canonical state cannot be read. The history is a derived
    page, so that skips it with a warning instead of failing the export. The other
    canonical projections already record their own failures in the export errors.
    """

    from ..application.canonical_season.store_facade import load_decisions
    from ..infrastructure.canonical_season_store import SeasonStateError
    from ..publication.baseline import publication_history

    try:
        decisions = load_decisions(season, root=root)
    except SeasonStateError as exc:
        logger.warning("Publiseringshistorikk hoppet over: %s", exc)
        return None
    return public_change_history(publication_history(decisions))
