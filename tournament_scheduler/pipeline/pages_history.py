"""Public publication history page for the GitHub Pages site (issue #798).

:mod:`pages_publish` writes the page into the ``gh-pages`` worktree before each
publish or rollback commit. The page is derived from the publication log that
the branch already keeps (one commit per publish or rollback), so no separate
store exists.

Only public-safe fields are rendered: publication time, run id, kind and the
bundle fingerprint. Source export paths and commit shas are deliberately left out.
"""

from __future__ import annotations

import html
import json
import re
import subprocess
from collections.abc import Callable, Iterable, Mapping
from datetime import datetime, timedelta, timezone
from pathlib import Path

HISTORY_DIRECTORY = "history"

# Subjects carry the publication scope ("season" or "activities"). Subjects written before scopes
# existed have no scope group; their scope is "" and they are shown as unclassified, never guessed.
PUBLISH_COMMIT_RE = re.compile(r"^Publish (?:(season|activities) )?run (\S+)$")
ROLLBACK_COMMIT_RE = re.compile(r"^Rollback latest to (?:(season|activities) )?run (\S+)$")

_KIND_LABELS = {"publish": "Publisert", "rollback": "Tilbakerullet til"}

# ``git(args, cwd=...)`` -> CompletedProcess, supplied by pages_publish.
GitRunner = Callable[..., subprocess.CompletedProcess]


class PublicationHistoryError(RuntimeError):
    """The existing publication log could not be read, so history must not be rewritten."""


def _canonical_time(value: str) -> str:
    """ISO-8601 UTC with an explicit ``+00:00`` offset, at second resolution."""
    return datetime.fromisoformat(value).astimezone(timezone.utc).isoformat(timespec="seconds")


def commit_subject(kind: str, run_id: str, scope: str) -> str:
    """The commit subject that records *kind* of publication for *run_id* in *scope*."""
    label = f"{scope} " if scope else ""
    if kind == "publish":
        return f"Publish {label}run {run_id}"
    return f"Rollback latest to {label}run {run_id}"


def publication_log(git: GitRunner, cwd: str) -> list[dict[str, str]]:
    """Newest-first ``{date, kind, run_id, scope, fingerprint}`` entries from HEAD's log in *cwd*.

    The commit log does not carry the bundle fingerprint, so ``fingerprint`` is
    left empty here; :func:`write_history_page` fills it for the current entry.
    Returns an empty list only when HEAD has no commits yet (the first publish on
    an orphan branch). If HEAD exists but its log cannot be read, raises
    :class:`PublicationHistoryError` rather than returning a partial history.
    """
    if git(["rev-parse", "--verify", "--quiet", "HEAD"], cwd=cwd).returncode != 0:
        return []
    proc = git(["log", "HEAD", "--date=iso-strict", "--format=%ad%x1f%s"], cwd=cwd)
    if proc.returncode != 0:
        raise PublicationHistoryError(f"kunne ikke lese publiseringsloggen: {proc.stderr.strip()}")
    entries: list[dict[str, str]] = []
    for line in proc.stdout.splitlines():
        parts = line.split("\x1f")
        if len(parts) != 2:
            continue
        date, subject = parts
        # git prints UTC as "Z" on some hosts and "+00:00" on others. Normalise so the
        # publication time is the same string everywhere it is compared.
        date = _canonical_time(date)
        publish_match = PUBLISH_COMMIT_RE.match(subject)
        rollback_match = ROLLBACK_COMMIT_RE.match(subject)
        if publish_match:
            entries.append(
                {"date": date, "kind": "publish", "run_id": publish_match.group(2),
                 "scope": publish_match.group(1) or "", "fingerprint": ""}
            )
        elif rollback_match:
            entries.append(
                {"date": date, "kind": "rollback", "run_id": rollback_match.group(2),
                 "scope": rollback_match.group(1) or "", "fingerprint": ""}
            )
    return entries


def publish_scopes(git: GitRunner, cwd: str, run_id: str) -> set[str]:
    """The scopes that *run_id* was published under, from the publication log. ``""`` means unclassified."""
    return {entry["scope"] for entry in publication_log(git, cwd) if entry["kind"] == "publish" and entry["run_id"] == run_id}


def season_run_id(branch_root: Path) -> str | None:
    """The run id of the published season (``latest/_season_meta.json``), or ``None`` when absent."""
    try:
        pointer = json.loads((branch_root / "latest" / "_season_meta.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(pointer, dict) or not pointer.get("run_id"):
        return None
    return str(pointer["run_id"])


def _latest_fingerprint(branch_root: Path) -> str:
    """Bundle fingerprint of the content currently under ``latest/``, or ``""``."""
    try:
        meta = json.loads((branch_root / "latest" / "_meta.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    return str(meta.get("bundle_fingerprint", "")) if isinstance(meta, dict) else ""


def _publication_time(prior: list[dict[str, str]]) -> str:
    """Current UTC time, strictly later than the newest publication on the branch.

    Publication times are the event identity that verification matches, so two
    publications must never share one. The clock has one-second resolution, so a
    publish that follows another within the same second is bumped to the next
    second instead of relying on the clock to have moved.
    """
    when = datetime.now(timezone.utc).replace(microsecond=0)
    if prior:
        newest = datetime.fromisoformat(prior[0]["date"])
        if when <= newest:
            when = newest + timedelta(seconds=1)
    return when.isoformat(timespec="seconds")


def write_history_page(
    branch_root: Path, *, run_id: str, kind: str, git: GitRunner, record: bool, scope: str = ""
) -> tuple[str, str]:
    """Write ``/history/index.html`` in the worktree.

    ``record=True``: the publication changed the branch, so it is prepended as a
    new event. ``record=False``: nothing changed, and the page only backfills
    history from the existing log (for example a branch published before this
    page existed). Either way the current entry carries the fingerprint of the
    content under ``latest/``, so verification can tell a cached older page apart.

    Returns ``(commit_time, published_at)``. ``commit_time`` (ISO-8601 UTC) is the
    author date the caller must commit with (``git commit --date``), because
    :func:`publication_log` reads the author date. ``published_at`` is the current
    entry's date: the identity of this publication event, which verification
    matches against the history page.
    """
    prior = publication_log(git, str(branch_root))
    when = _publication_time(prior)
    fingerprint = _latest_fingerprint(branch_root)
    if record:
        entries = compose_history(prior, run_id=run_id, kind=kind, date=when, fingerprint=fingerprint, scope=scope)
        published_at = when
    else:
        entries = [dict(entry) for entry in prior]
        if entries:
            entries[0]["fingerprint"] = fingerprint
        published_at = entries[0]["date"] if entries else when
    history_dir = branch_root / HISTORY_DIRECTORY
    history_dir.mkdir(parents=True, exist_ok=True)
    (history_dir / "index.html").write_text(
        render_history_page(entries, season_run_id=season_run_id(branch_root)), encoding="utf-8"
    )
    return when, published_at


def compose_history(
    prior: Iterable[Mapping[str, str]],
    *,
    run_id: str,
    kind: str,
    date: str,
    fingerprint: str = "",
    scope: str = "",
) -> list[dict[str, str]]:
    """Return newest-first entries with the new publication prepended.

    *prior* is the existing publication log, newest first. This always adds an
    entry: whether a publication is a no-op is decided by its content change
    before the history is written, never by comparing run ids.
    """
    entries = [
        {
            "date": str(entry.get("date", "")),
            "kind": str(entry.get("kind", "")),
            "run_id": str(entry.get("run_id", "")),
            "scope": str(entry.get("scope", "")),
            "fingerprint": str(entry.get("fingerprint", "")),
        }
        for entry in prior
    ]
    return [
        {"date": date, "kind": kind, "run_id": run_id, "scope": scope, "fingerprint": fingerprint},
        *entries,
    ]


def render_history_page(entries: Iterable[Mapping[str, str]], season_run_id: str | None = None) -> str:
    """Render the public history page. Two rows carry a current marker.

    The first entry is the current deployment (``data-current``). The newest season
    publication whose run id is *season_run_id* is the published season (``data-season-current``).
    Entries with no scope are unclassified: they are labelled, and no season is assigned to them.
    """
    entries = list(entries)
    season_index = next(
        (
            index
            for index, entry in enumerate(entries)
            if entry.get("scope") == "season" and season_run_id and entry.get("run_id") == season_run_id
        ),
        None,
    )
    rows: list[str] = []
    unclassified = False
    for index, entry in enumerate(entries):
        run_id = html.escape(entry.get("run_id", ""))
        fingerprint = html.escape(entry.get("fingerprint", ""))
        published_at = html.escape(entry.get("date", ""))
        scope = entry.get("scope", "")
        current = "true" if index == 0 else "false"
        season_marker = ' data-season-current="true"' if index == season_index else ""
        label = _KIND_LABELS.get(entry.get("kind", ""), html.escape(entry.get("kind", "")))
        statuses = []
        if index == 0:
            statuses.append("Gjeldende")
        if index == season_index:
            statuses.append("Gjeldende sesong")
        if not scope:
            statuses.append("Uklassifisert")
            unclassified = True
        if not statuses:
            statuses.append("Tidligere")
        rows.append(
            f'<tr data-run-id="{run_id}" data-fingerprint="{fingerprint}" '
            f'data-published-at="{published_at}" data-current="{current}" '
            f'data-scope="{html.escape(scope)}"{season_marker}>'
            f"<td>{html.escape(entry.get('date', ''))}</td>"
            f"<td>{label} <code>{run_id}</code></td>"
            f"<td>{', '.join(statuses)}</td>"
            f'<td><a href="../runs/{run_id}/">Vis snapshot</a></td>'
            "</tr>"
        )
    if rows:
        body = (
            '<table><thead><tr><th>Tidspunkt</th><th>Handling</th><th>Status</th><th>Snapshot</th></tr></thead>'
            f"<tbody>{''.join(rows)}</tbody></table>"
        )
    else:
        body = "<p>Ingen publiseringer er registrert ennå.</p>"
    return (
        "<!doctype html>\n"
        '<html lang="no">\n'
        "<head>\n"
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        "<title>Publiseringshistorikk — RVV Miniputt</title>\n"
        "<style>\n"
        "body{font-family:system-ui,sans-serif;margin:16px;color:#1b1b1b}\n"
        "table{border-collapse:collapse;width:100%}\n"
        "th,td{text-align:left;padding:6px 8px;border-bottom:1px solid #ddd}\n"
        "code{font-size:0.9em}\n"
        "</style>\n"
        "</head>\n"
        "<body>\n"
        "<h1>Publiseringshistorikk</h1>\n"
        '<p>Hver publisering er et uforanderlig øyeblikksbilde. Den gjeldende versjonen er merket.</p>\n'
        f"{body}\n"
        + (
            "<p>Uklassifiserte publiseringer ble gjort før publiseringsscope ble registrert. "
            "Deres sesongstatus er ikke fastslått.</p>\n"
            if unclassified
            else ""
        )
        + '<p><a href="../latest/">Gå til gjeldende sesongplan</a></p>\n'
        "</body>\n"
        "</html>\n"
    )
