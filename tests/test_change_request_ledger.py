from __future__ import annotations

import json
from pathlib import Path

from tournament_scheduler.application.canonical_season.changes import (
    render_change_log_markdown,
    write_change_log_markdown,
)
from tournament_scheduler.canonical_state import canonical_state_revision, schedule_fingerprint
from tournament_scheduler.cli.rvv_cli import _cmd_season
from tournament_scheduler.html import SEASON_CHANGES_FILENAME
from tournament_scheduler.pipeline.season_changes_view import (
    render_html,
    request_label,
    status_label,
    write_html,
)
from tournament_scheduler.season_state import change_request_ledger


SEASON = "2026-2027"


def _team(club: str, label: str, age_group: str = "U10") -> dict:
    return {"club": club, "label": label, "age_group": age_group}


def _tournament(tid: str, date: str, teams: list[dict] | None = None) -> dict:
    roster = teams or [_team("Frisk Asker", "FA1"), _team("Kongsberg", "K1"), _team("Skien", "S1"), _team("Jutul", "J1")]
    return {
        "id": tid,
        "date": date,
        "arena": "Arena A",
        "host_club": roster[0]["club"],
        "age_group": "U10",
        "start_time": "10:00",
        "teams": roster,
        "games": [],
    }


def _write_state(root: Path, *, decisions: dict) -> None:
    plan = {
        "schema_version": 1,
        "start_date": "2026-09-01",
        "end_date": "2027-04-30",
        "tournaments": [
            _tournament("rvv-001", "2026-11-07"),
            _tournament("rvv-002", "2026-12-05", [_team("Ringerike", "R1", "JU8"), _team("Jutul", "J1", "JU8"), _team("Skien", "S1", "JU8"), _team("Holmen", "H1", "JU8")]),
        ],
    }
    fingerprint = schedule_fingerprint(plan)
    schedule = {
        "schema_version": 1,
        "season": SEASON,
        "revision": fingerprint,
        "fingerprint": fingerprint,
        "plan": plan,
        "verification_context": {
            "problem": {
                "start_date": plan["start_date"],
                "end_date": plan["end_date"],
                "teams": [_team("Frisk Asker", "FA1"), _team("Kongsberg", "K1"), _team("Skien", "S1"), _team("Jutul", "J1"), _team("Ringerike", "R1", "JU8"), _team("Holmen", "H1", "JU8")],
            }
        },
    }
    decisions = {"schema_version": 1, "season": SEASON, "schedule_fingerprint": fingerprint, "decisions": {}, **decisions}
    decisions["canonical_state_revision"] = canonical_state_revision(schedule, decisions)
    season_dir = root / SEASON
    season_dir.mkdir(parents=True)
    (season_dir / "schedule.json").write_text(json.dumps(schedule, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    (season_dir / "decisions.json").write_text(json.dumps(decisions, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def test_change_request_ledger_groups_constraints_moves_and_withdrawals(tmp_path: Path) -> None:
    before_revision = "before-rev"
    _write_state(
        tmp_path,
        decisions={
            "request_constraints": [
                {
                    "id": "request:frisk-weekend",
                    "type": "team_unavailable",
                    "request_id": "club-feedback:frisk:u10:nov-weekend",
                    "teams": [_team("Frisk Asker", "FA1")],
                    "date_from": "2026-11-07",
                    "date_to": "2026-11-08",
                    "status": "active",
                    "created_at": "2026-09-01T10:00:00+00:00",
                    "created_by": "Frisk Asker",
                    "note": "U10 unavailable 7-8 Nov",
                }
            ],
            "change_protections": [
                {
                    "id": "change:rvv-001-date",
                    "kind": "placement_field",
                    "source_event": "move",
                    "source_revision": before_revision,
                    "request_id": "club-feedback:frisk:u10:nov-weekend",
                    "tournament_id": "rvv-001",
                    "field": "date",
                    "value": "2026-11-14",
                    "status": "active",
                    "created_at": "2026-09-02T09:00:00+00:00",
                    "created_by": "operator",
                }
            ],
            "participation_withdrawals": [
                {
                    "id": "withdrawal:ringerike-ju8",
                    "kind": "participation_withdrawal",
                    "request_id": "club-feedback:ringerike:ju8-withdrawal",
                    "status": "active",
                    "scope": "age_group",
                    "team": _team("Ringerike", "R1", "JU8"),
                    "tournament_ids": ["rvv-002"],
                    "effective_from": "2026-12-01",
                    "created_at": "2026-09-03T12:00:00+00:00",
                    "created_by": "Ringerike",
                    "note": "Ringerike JU8 withdrawn",
                }
            ],
            "history": [
                {
                    "event": "move",
                    "timestamp": "2026-09-02T09:01:00+00:00",
                    "actor": "operator",
                    "tournament_id": "rvv-001",
                    "details": {
                        "old_placement": {"date": "2026-11-07", "arena": "Arena A", "host_club": "Frisk Asker", "start_time": "10:00"},
                        "new_placement": {"date": "2026-11-14", "arena": "Arena A", "host_club": "Frisk Asker", "start_time": "10:00"},
                        "before_canonical_revision": before_revision,
                    },
                },
                {
                    "event": "participant_removal",
                    "timestamp": "2026-09-03T12:05:00+00:00",
                    "actor": "operator",
                    "tournament_id": "rvv-002",
                    "details": {
                        "request_id": "club-feedback:ringerike:ju8-withdrawal",
                        "removed_team": _team("Ringerike", "R1", "JU8"),
                        "tournament_ids": ["rvv-002"],
                    },
                },
            ],
        },
    )

    ledger = change_request_ledger(SEASON, root=tmp_path)

    assert ledger["schema_version"] == 1
    frisk = next(item for item in ledger["requests"] if item["request_id"] == "club-feedback:frisk:u10:nov-weekend")
    assert frisk["status"] == "partially_resolved"
    assert frisk["affected_tournaments"] == ["rvv-001"]
    assert frisk["mutations"][0]["request_id_inferred"] is True
    assert frisk["mutations"][0]["details"]["before"]["date"] == "2026-11-07"

    withdrawal = next(item for item in ledger["requests"] if item["request_id"] == "club-feedback:ringerike:ju8-withdrawal")
    assert withdrawal["status"] == "needs_action"
    assert withdrawal["withdrawals"][0]["team"]["club"] == "Ringerike"
    assert "Ringerike:R1:JU8" in withdrawal["affected_teams"]


def test_uncorrelated_legacy_history_uses_separate_unknown_buckets(tmp_path: Path) -> None:
    _write_state(
        tmp_path,
        decisions={
            "history": [
                {
                    "event": "move",
                    "timestamp": "2026-09-01T08:00:00+00:00",
                    "actor": "operator",
                    "tournament_id": "rvv-001",
                    "details": {
                        "old_placement": {"date": "2026-11-07"},
                        "new_placement": {"date": "2026-11-14"},
                    },
                },
                {
                    "event": "move",
                    "timestamp": "2026-09-02T08:00:00+00:00",
                    "actor": "operator",
                    "tournament_id": "rvv-002",
                    "details": {
                        "old_placement": {"date": "2026-12-05"},
                        "new_placement": {"date": "2026-12-12"},
                    },
                },
            ],
        },
    )

    ledger = change_request_ledger(SEASON, root=tmp_path)
    unknown = [item for item in ledger["requests"] if item["request_id"] is None]

    assert len(unknown) == 2
    assert {item["bucket_id"] for item in unknown} == {
        "unknown:move:rvv-001:2026-09-01T08:00:00+00:00:0",
        "unknown:move:rvv-002:2026-09-02T08:00:00+00:00:1",
    }
    assert all(len(item["mutations"]) == 1 for item in unknown)


def test_change_log_markdown_is_deterministic_and_cli_can_write(tmp_path: Path, capsys) -> None:
    _write_state(
        tmp_path,
        decisions={
            "request_constraints": [],
            "history": [
                {
                    "event": "batch_maintenance",
                    "timestamp": "2026-09-04T08:00:00+00:00",
                    "actor": "operator",
                    "details": {
                        "request_id": "operator:batch:one",
                        "scope": ["rvv-001"],
                        "operations": [{"op": "move", "tournament_id": "rvv-001", "date": "2026-11-14"}],
                    },
                }
            ],
        },
    )
    ledger = change_request_ledger(SEASON, root=tmp_path)

    markdown = render_change_log_markdown(ledger)
    assert markdown == render_change_log_markdown(ledger)
    assert "Generated from canonical `schedule.json` and `decisions.json`; do not edit" in markdown
    assert "`operator:batch:one`" in markdown

    path = write_change_log_markdown(ledger, root=tmp_path)
    assert path == tmp_path / SEASON / "change-log.md"
    assert path.read_text(encoding="utf-8") == markdown

    class Args:
        season_command = "changes"
        season = SEASON
        root = str(tmp_path)
        json = True
        markdown = False
        write = True

    assert _cmd_season(Args()) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["markdown_path"].endswith("change-log.md")
    assert (tmp_path / SEASON / "change-log.md").exists()


def test_html_projection_uses_the_same_ledger_statuses_as_markdown(tmp_path: Path) -> None:
    """The HTML page must not reclassify requests relative to the Markdown view."""

    before_revision = "before-rev"
    _write_state(
        tmp_path,
        decisions={
            "request_constraints": [
                {
                    "id": "request:frisk-weekend",
                    "type": "team_unavailable",
                    "request_id": "club-feedback:frisk:u10:nov-weekend",
                    "teams": [_team("Frisk Asker", "FA1")],
                    "date_from": "2026-11-07",
                    "date_to": "2026-11-08",
                    "status": "active",
                    "created_at": "2026-09-01T10:00:00+00:00",
                    "created_by": "Frisk Asker",
                    "note": "U10 unavailable 7-8 Nov",
                },
                {
                    "id": "request:released",
                    "type": "team_unavailable",
                    "request_id": "operator:released:old",
                    "teams": [_team("Skien", "S1")],
                    "status": "released",
                    "created_at": "2026-08-01T10:00:00+00:00",
                    "created_by": "operator",
                    "note": "no longer relevant",
                },
            ],
            "participation_withdrawals": [
                {
                    "id": "withdrawal:ringerike-ju8",
                    "kind": "participation_withdrawal",
                    "request_id": "club-feedback:ringerike:ju8-withdrawal",
                    "status": "active",
                    "scope": "age_group",
                    "team": _team("Ringerike", "R1", "JU8"),
                    "tournament_ids": ["rvv-002"],
                    "created_at": "2026-09-03T12:00:00+00:00",
                    "created_by": "Ringerike",
                    "note": "Ringerike JU8 withdrawn",
                },
                {
                    "id": "withdrawal:superseded",
                    "kind": "participation_withdrawal",
                    "request_id": "operator:superseded:old",
                    "status": "active",
                    "scope": "age_group",
                    "team": _team("Ghost", "G1", "JU8"),
                    "tournament_ids": ["rvv-001"],
                    "created_at": "2026-07-01T12:00:00+00:00",
                    "created_by": "operator",
                    "note": "superseded by later request",
                },
            ],
            "change_protections": [
                {
                    "id": "change:rvv-001-date",
                    "kind": "placement_field",
                    "source_event": "move",
                    "source_revision": before_revision,
                    "request_id": "club-feedback:frisk:u10:nov-weekend",
                    "tournament_id": "rvv-001",
                    "field": "date",
                    "value": "2026-11-14",
                    "status": "active",
                    "created_at": "2026-09-02T09:00:00+00:00",
                    "created_by": "operator",
                },
            ],
            "history": [
                {
                    "event": "move",
                    "timestamp": "2026-09-02T09:01:00+00:00",
                    "actor": "operator",
                    "tournament_id": "rvv-001",
                    "details": {
                        "old_placement": {
                            "date": "2026-11-07",
                            "arena": "Arena A",
                            "host_club": "Frisk Asker",
                            "start_time": "10:00",
                        },
                        "new_placement": {
                            "date": "2026-11-14",
                            "arena": "Arena A",
                            "host_club": "Frisk Asker",
                            "start_time": "10:00",
                        },
                        "before_canonical_revision": before_revision,
                    },
                }
            ],
        },
    )
    ledger = change_request_ledger(SEASON, root=tmp_path)
    markdown = render_change_log_markdown(ledger)
    html = render_html(ledger)

    statuses = {item["status"] for item in ledger["requests"]}
    assert {"partially_resolved", "needs_action", "released", "superseded"} <= statuses
    for item in ledger["requests"]:
        label = request_label(item)
        assert label in markdown
        assert label in html
        assert status_label(item["status"]) in html

    # Mutation-backed before/after placement stays meaningful on the page.
    assert "2026-11-07 · Arena A · 10:00" in html
    assert "2026-11-14 · Arena A · 10:00" in html
    # Unresolved/partially resolved requests are visually distinct from history.
    assert "rules-status--attention" in html
    assert "rules-status--warn" in html


def test_html_projection_escapes_hostile_provenance(tmp_path: Path) -> None:
    _write_state(
        tmp_path,
        decisions={
            "request_constraints": [
                {
                    "id": "request:hostile",
                    "type": "team_unavailable",
                    "request_id": "operator:hostile",
                    "teams": [_team("<b>Evil</b>", "<script>x</script>")],
                    "date_from": "2026-11-07",
                    "date_to": "2026-11-08",
                    "status": "active",
                    "satisfied": False,
                    "created_at": "2026-09-01T10:00:00+00:00",
                    "created_by": "<img src=x onerror=alert(1)>",
                    "note": "<script>alert(2)</script>",
                }
            ],
        },
    )
    ledger = change_request_ledger(SEASON, root=tmp_path)
    html = render_html(ledger)

    assert "<script>alert(2)</script>" not in html
    assert "<img src=x onerror=alert(1)>" not in html
    assert "&lt;script&gt;alert(2)&lt;/script&gt;" in html
    assert "&lt;img src=x onerror=alert(1)&gt;" in html


def test_write_html_writes_shared_canonical_filename(tmp_path: Path) -> None:
    _write_state(tmp_path, decisions={"request_constraints": []})
    ledger = change_request_ledger(SEASON, root=tmp_path)
    path = write_html(ledger, tmp_path)
    assert Path(path).name == SEASON_CHANGES_FILENAME
    assert Path(path).read_text(encoding="utf-8") == render_html(ledger)
