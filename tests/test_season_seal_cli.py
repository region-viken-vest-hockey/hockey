"""``rvv-miniputt season seal-published`` must report a refused seal, not raise."""

from __future__ import annotations

from tournament_scheduler.application.canonical_season import store_facade
from tournament_scheduler.cli import season_seal_command
from tournament_scheduler.cli.args import build_parser
from tournament_scheduler.publication import export_lifecycle
from tournament_scheduler.publication.baseline import PublishedBaselineError


class _RecordingConsole:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def print(self, *parts, **_kwargs) -> None:
        self.lines.append(" ".join(str(part) for part in parts))


def test_seal_refused_at_stale_revision_is_reported_as_exit_code(monkeypatch) -> None:
    console = _RecordingConsole()
    monkeypatch.setattr(season_seal_command, "_console", console)
    monkeypatch.setattr(
        export_lifecycle,
        "find_published_exports_for_season",
        lambda season, season_root: [
            {
                "export_id": "pub-1",
                "canonical_revision": "rev-published",
                "schedule_projection": {},
                "published_at": "2026-10-01T10:00:00Z",
            }
        ],
    )
    from tournament_scheduler.infrastructure import canonical_revision_history

    monkeypatch.setattr(
        canonical_revision_history,
        "load_canonical_schedule_at_revision",
        lambda season, revision, season_root: None,
    )

    def refuse_stale_seal(**kwargs):
        raise PublishedBaselineError(
            "Refusing to seal: the publication was verified at canonical revision "
            "'rev-published', but current canonical revision is 'rev-later'"
        )

    monkeypatch.setattr(store_facade, "seal_published_season", refuse_stale_seal)
    args = build_parser().parse_args(
        ["season", "seal-published", "--season", "2026-2027", "--root", "season"]
    )

    exit_code = season_seal_command._cmd_season_seal_published(args)

    assert exit_code == 1
    assert any("Refusing to seal" in line and "rev-later" in line for line in console.lines)
