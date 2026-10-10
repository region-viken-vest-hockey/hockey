"""``rvv-miniputt season search`` must reach its handler, not the missing-subcommand fallback."""

from __future__ import annotations

from tournament_scheduler.cli import repair_command
from tournament_scheduler.cli.args import build_parser
from tournament_scheduler.cli.season_command import _cmd_season


def test_season_search_is_dispatched_to_repair_handler(monkeypatch) -> None:
    calls: list[str] = []

    def fake_repair_handler(args) -> int:
        calls.append(args.season_command)
        return 0

    monkeypatch.setattr(repair_command, "_cmd_season_repair_options", fake_repair_handler)
    args = build_parser().parse_args(["season", "search", "--season", "2026-2027", "--finding", "f1"])

    assert _cmd_season(args) == 0
    assert calls == ["search"]
