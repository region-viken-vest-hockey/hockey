"""Public publication history page for the GitHub Pages site (issue #798).

Pure rendering: :mod:`pages_publish` reads the publication log that it already
keeps on ``gh-pages`` (one commit per publish or rollback) and passes the
entries here. Nothing in this module touches git, the filesystem or the
network.

Only public-safe fields are rendered: publication time, run id and kind.
Source export paths, fingerprints and commit shas are deliberately left out.
"""

from __future__ import annotations

import html
import re
import subprocess
from collections.abc import Callable, Iterable, Mapping
from datetime import datetime, timezone
from pathlib import Path

HISTORY_DIRECTORY = "history"

PUBLISH_COMMIT_RE = re.compile(r"^Publish run (\S+)$")
ROLLBACK_COMMIT_RE = re.compile(r"^Rollback latest to run (\S+)$")

_KIND_LABELS = {"publish": "Publisert", "rollback": "Tilbakerullet til"}

# ``git(args, cwd=...)`` -> CompletedProcess, supplied by pages_publish.
GitRunner = Callable[..., subprocess.CompletedProcess]


class PublicationHistoryError(RuntimeError):
    """The existing publication log could not be read, so history must not be rewritten."""


def publication_log(git: GitRunner, cwd: str) -> list[dict[str, str]]:
    """Newest-first ``{date, kind, run_id}`` entries from HEAD's commit log in *cwd*.

    Returns an empty list only when HEAD has no commits yet (the first publish
    on an orphan branch). If HEAD exists but its log cannot be read, raises
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
        publish_match = PUBLISH_COMMIT_RE.match(subject)
        rollback_match = ROLLBACK_COMMIT_RE.match(subject)
        if publish_match:
            entries.append({"date": date, "kind": "publish", "run_id": publish_match.group(1)})
        elif rollback_match:
            entries.append({"date": date, "kind": "rollback", "run_id": rollback_match.group(1)})
    return entries


def write_history_page(branch_root: Path, *, run_id: str, kind: str, git: GitRunner) -> str:
    """Write ``/history/index.html`` in the worktree, listing *run_id* as current.

    Returns the publication time (ISO-8601 UTC). The caller must commit with that
    value as the author date (``git commit --date``), because :func:`publication_log`
    reads the author date (``%ad``). That keeps the rendered time identical to the
    committed log's time. Callers must only invoke this when the publication
    actually produces a commit, so every history entry matches one commit.
    """
    when = datetime.now(timezone.utc).isoformat(timespec="seconds")
    entries = compose_history(publication_log(git, str(branch_root)), run_id=run_id, kind=kind, date=when)
    history_dir = branch_root / HISTORY_DIRECTORY
    history_dir.mkdir(parents=True, exist_ok=True)
    (history_dir / "index.html").write_text(render_history_page(entries), encoding="utf-8")
    return when


def compose_history(
    prior: Iterable[Mapping[str, str]],
    *,
    run_id: str,
    kind: str,
    date: str,
) -> list[dict[str, str]]:
    """Return newest-first entries with the new publication prepended.

    *prior* is the existing publication log, newest first. This always adds an
    entry: whether a publication is a no-op is decided by its content change
    before the history is written, never by comparing run ids.
    """
    entries = [
        {"date": str(entry.get("date", "")), "kind": str(entry.get("kind", "")), "run_id": str(entry.get("run_id", ""))}
        for entry in prior
    ]
    return [{"date": date, "kind": kind, "run_id": run_id}, *entries]


def render_history_page(entries: Iterable[Mapping[str, str]]) -> str:
    """Render the public history page. The first entry is the current publication.

    Each row carries ``data-run-id`` and ``data-current`` so that verification
    can check the current marker without parsing the layout.
    """
    rows: list[str] = []
    for index, entry in enumerate(entries):
        run_id = html.escape(entry.get("run_id", ""))
        current = "true" if index == 0 else "false"
        label = _KIND_LABELS.get(entry.get("kind", ""), html.escape(entry.get("kind", "")))
        status = "Gjeldende" if index == 0 else "Tidligere"
        rows.append(
            f'<tr data-run-id="{run_id}" data-current="{current}">'
            f"<td>{html.escape(entry.get('date', ''))}</td>"
            f"<td>{label} <code>{run_id}</code></td>"
            f"<td>{status}</td>"
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
        '<p><a href="../latest/">Gå til gjeldende sesongplan</a></p>\n'
        "</body>\n"
        "</html>\n"
    )
