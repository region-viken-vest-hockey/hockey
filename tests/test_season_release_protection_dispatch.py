"""``rvv-miniputt season release-protection`` must forward its parsed arguments to the canonical facade."""

from __future__ import annotations

from tournament_scheduler.application.canonical_season import store_facade
from tournament_scheduler.cli.args import build_parser
from tournament_scheduler.cli.season_command import _cmd_season


def test_release_protection_is_dispatched_with_protection_and_request_arguments(monkeypatch, capsys) -> None:
    calls: list[dict] = []

    def fake_release(**kwargs) -> dict:
        calls.append(kwargs)
        return {"released_protection_ids": ["change:abc"], "active_count": 0}

    monkeypatch.setattr(store_facade, "release_change_protections", fake_release)
    args = build_parser().parse_args(
        [
            "season",
            "release-protection",
            "--season",
            "2026-2027",
            "--protection-id",
            "change:abc",
            "--actor",
            "tester",
            "--note",
            "superseded by club-feedback:new",
            "--json",
        ]
    )

    assert _cmd_season(args) == 0
    assert calls == [
        {
            "season": "2026-2027",
            "root": "season",
            "protection_ids": ["change:abc"],
            "request_id": None,
            "actor": "tester",
            "note": "superseded by club-feedback:new",
        }
    ]
    assert '"released_protection_ids": [' in capsys.readouterr().out
