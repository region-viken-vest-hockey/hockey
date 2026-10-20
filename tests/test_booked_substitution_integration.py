"""End-to-end booked participant substitution: preview, dry-run, apply and booked projection."""

from __future__ import annotations

from pathlib import Path

from tests.test_batch_scenarios import DONOR, RECIPIENT, SEASON, _revision, _rotation_operations
from tests.test_canonical_batch_maintenance import _promote
from tournament_scheduler.application.canonical_season.batch import batch_maintenance
from tournament_scheduler.application.canonical_season.consequence_preview import (
    STATUS_SAFE_TO_APPLY,
    preview_canonical_operation,
)
from tournament_scheduler.application.canonical_season.replacement_search_evidence import booked_projection
from tournament_scheduler.application.canonical_season.store_facade import (
    booking_status_report,
    set_manual_booking_assertion,
)
from tournament_scheduler.application.canonical_season_service import CanonicalSeasonService


def _confirm_donor_booking(root: Path) -> None:
    # Only the donor's confirmed slot is booked evidence. The recipient stays unconfirmed.
    set_manual_booking_assertion(
        season=SEASON,
        root=root,
        tournament_id=DONOR,
        booking_status="booked",
        source_scope="tournament",
        reference="email:confirmed-slot",
        stated_date="2026-09-12",
        stated_start="10:00",
        stated_end="12:00",
        actor="tester",
    )


def _season_tree_bytes(root: Path) -> dict[str, bytes]:
    """Every persisted file under the canonical root, so a dry-run side effect cannot hide."""
    return {str(path.relative_to(root)): path.read_bytes() for path in sorted(root.rglob("*")) if path.is_file()}


def _rosters(root: Path) -> dict[str, tuple[str, str, list[str]]]:
    plan = CanonicalSeasonService(root=root).load(SEASON).schedule["plan"]
    return {
        tournament["id"]: (
            tournament["date"],
            tournament["host_club"],
            sorted(team["label"] for team in tournament["teams"]),
        )
        for tournament in plan["tournaments"]
    }


def _booked_team_counts(root: Path) -> dict[str, int]:
    """Booked team labels, projected from the stored plan and booking report."""
    snapshot = CanonicalSeasonService(root=root).load(SEASON)
    report = booking_status_report(season=SEASON, root=root)
    projected, _meta = booked_projection(snapshot.schedule["plan"], report["tournaments"], "U10")
    counts: dict[str, int] = {}
    for tournament in projected["tournaments"]:
        for team in tournament["teams"]:
            counts[team["label"]] = counts.get(team["label"], 0) + 1
    return counts


def test_booked_substitution_preview_dry_run_apply_and_projection_agree(tmp_path: Path) -> None:
    _work_dir, root = _promote(tmp_path)
    _confirm_donor_booking(root)
    service = CanonicalSeasonService(root=root)
    reviewed = _revision(root)
    assert _booked_team_counts(root) == {"K1": 1, "X1": 1, "Y1": 1, "Z1": 1}

    preview = preview_canonical_operation(
        service,
        operation="batch",
        season=SEASON,
        operations=_rotation_operations(),
        scope=[DONOR, RECIPIENT],
        request_id="rotate-y1-w1",
        actor="tester",
        expected_revision=reviewed,
    )

    # Preview: the substitution is applicable and reports booked consequences per team.
    # The unconfirmed recipient keeps the fairness provisional.
    assert preview["verdict"]["status"] == STATUS_SAFE_TO_APPLY
    consequences = preview["consequences"]
    assert consequences["basis"] == "booked_reality"
    assert consequences["provisional"] is True
    rows = {row["team"]: row for row in consequences["affected_teams"]}
    assert rows["Y1"]["before"]["booked"] == 1 and rows["Y1"]["after"]["booked"] == 0
    assert rows["W1"]["before"]["booked"] == 0 and rows["W1"]["after"]["booked"] == 1
    for team in ("K1", "X1", "Z1"):
        assert rows[team]["delta"]["booked"] == 0, team

    # Dry-run: same candidate, and no persisted file under the canonical root changes.
    tree_before = _season_tree_bytes(root)
    rosters_before = _rosters(root)
    dry_run = batch_maintenance(
        service,
        season=SEASON,
        operations=_rotation_operations(),
        scope=[DONOR, RECIPIENT],
        request_id="rotate-y1-w1",
        actor="tester",
        expected_revision=preview["expected_revision"],
        expected_token=preview["reviewed_token"],
        dry_run=True,
    )
    assert dry_run["dry_run"] is True
    assert dry_run["committed"] is False
    assert dry_run["verdict"]["status"] == STATUS_SAFE_TO_APPLY
    assert dry_run["reviewed_token"] == preview["reviewed_token"]
    assert _season_tree_bytes(root) == tree_before
    assert _rosters(root) == rosters_before

    # Apply: commits exactly the reviewed candidate.
    applied = batch_maintenance(
        service,
        season=SEASON,
        operations=_rotation_operations(),
        scope=[DONOR, RECIPIENT],
        request_id="rotate-y1-w1",
        actor="tester",
        expected_revision=preview["expected_revision"],
        expected_token=preview["reviewed_token"],
    )
    assert applied["committed"] is True
    assert applied["revision"] == preview["candidate_fingerprint"]

    # Every rostered team moved exactly as the preview said: the donor loses Y1 and gains W1,
    # the recipient swaps W1 for Y1, and no date or host changes.
    rosters_after = _rosters(root)
    assert rosters_after[DONOR] == (rosters_before[DONOR][0], rosters_before[DONOR][1], ["K1", "W1", "X1", "Z1"])
    assert rosters_after[RECIPIENT] == (
        rosters_before[RECIPIENT][0],
        rosters_before[RECIPIENT][1],
        ["K1", "V1", "X1", "Y1"],
    )
    assert {tid for tid, roster in rosters_after.items() if roster != rosters_before[tid]} == {DONOR, RECIPIENT}

    # The post-apply booked projection agrees with the preview's predicted booked count for every team.
    predicted_booked = {team: row["after"]["booked"] for team, row in rows.items() if row["after"]["booked"]}
    assert _booked_team_counts(root) == predicted_booked
    states = {row["tournament_id"]: row["operational_state"] for row in booking_status_report(season=SEASON, root=root)["tournaments"]}
    assert states[DONOR] == "booked"
    assert states[RECIPIENT] != "booked"
