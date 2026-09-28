"""Regression coverage for clearing the canonical "fresh export required" latch.

A calendar refresh (or config reconciliation) marks the promoted season's
``export_state.requires_fresh_export`` so publication refuses an artifact that
predates the evidence change. Before this regression the marker was a one-way
latch: re-exporting never cleared it, so a later publish was refused forever
with no actual freshness problem.
"""

from __future__ import annotations

from pathlib import Path

from tournament_scheduler.application.canonical_season_service import CanonicalSeasonService
from tournament_scheduler.cli.rvv_cli import main
from tournament_scheduler.pipeline.export_parity.gate import publish_parity_gate
from tournament_scheduler.pipeline.state import PipelineState, StageName
from tournament_scheduler.season_state import (
    add_banned_date,
    canonical_state_revision,
    load_decisions,
    load_schedule,
    mark_export_fresh,
    refresh_calendars,
)
from tests.test_export_parity import _canonical_export
from tests.test_refresh_calendar_evidence import _patch_refresh_inputs, _promote

SEASON = "2026-2027"


def _stale_after_refresh(tmp_path: Path, monkeypatch) -> str:
    """Promote a season, refresh calendars, and return the new revision."""

    root = _promote(tmp_path)
    _patch_refresh_inputs(monkeypatch, busy=False)
    refresh_calendars(season=SEASON, root=root, input_path="input.xlsx", actor="tester")
    decisions = load_decisions(SEASON, root=root)
    assert decisions["export_state"]["requires_fresh_export"] is True
    return canonical_state_revision(load_schedule(SEASON, root=root), decisions)


def test_season_export_clears_fresh_export_latch_and_unblocks_publish(
    tmp_path: Path, monkeypatch
) -> None:
    """The failing path: refresh -> export must clear the latch and pass the gate."""

    stale_revision = _stale_after_refresh(tmp_path, monkeypatch)
    root = tmp_path / "season"
    export_dir = tmp_path / "canonical-export"

    rc = main(
        [
            "season",
            "export",
            "--season",
            SEASON,
            "--work-dir",
            str(tmp_path / ".pipeline"),
            "--root",
            str(root),
            "--export-dir",
            str(export_dir),
            "--flat",
        ]
    )
    assert rc == 0

    decisions = load_decisions(SEASON, root=root)
    exported_revision = str(decisions["export_state"]["fresh_canonical_revision"])
    assert exported_revision == stale_revision
    assert decisions["export_state"]["requires_fresh_export"] is False
    assert decisions["export_state"]["status"] == "fresh"
    # Export freshness must not imply that the independent audit requirement or
    # any approval has been satisfied.
    assert decisions["export_state"]["requires_fresh_audit"] is True
    schedule = load_schedule(SEASON, root=root)
    promoted_from = schedule["promoted_from"]
    assert "export_stale" not in promoted_from
    assert promoted_from["export_fresh_revision"] == stale_revision

    # The publish preflight no longer cites requires_fresh_export.
    assert publish_parity_gate(export_dir=export_dir, repo_dir=tmp_path) is None


def test_gate_blocks_matching_artifacts_only_because_of_latch(tmp_path: Path) -> None:
    """Matching artifacts plus the latch must block, then clear after mark_export_fresh."""

    export_dir, _, _, _, revision = _canonical_export(tmp_path)

    # Arm the latch through the canonical service (export_state is not part of
    # the semantic revision, so the artifacts still match).
    service = CanonicalSeasonService(root=tmp_path / "season")
    snapshot = service.load(SEASON)
    decisions = dict(snapshot.decisions)
    decisions["export_state"] = {
        "status": "stale",
        "requires_fresh_export": True,
        "requires_fresh_audit": True,
    }
    service._commit(snapshot.with_decisions(decisions))

    blocked = publish_parity_gate(export_dir=export_dir, repo_dir=tmp_path)
    assert blocked is not None
    assert any("requires_fresh_export" in str(evidence) for evidence in blocked.evidence)

    mark_export_fresh(season=SEASON, root=tmp_path / "season", expected_revision=revision)

    assert publish_parity_gate(export_dir=export_dir, repo_dir=tmp_path) is None


def test_mark_export_fresh_is_revision_bound(tmp_path: Path, monkeypatch) -> None:
    """A mutation after the export snapshot must keep the newer freshness latch."""

    stale_revision = _stale_after_refresh(tmp_path, monkeypatch)
    root = tmp_path / "season"

    # A decision-only mutation advances canonical state after the export ran.
    add_banned_date(
        season=SEASON,
        date="2027-01-15",
        request_id="rev-bound-test",
        root=root,
        note="state advanced after export",
    )
    current_revision = canonical_state_revision(
        load_schedule(SEASON, root=root), load_decisions(SEASON, root=root)
    )
    assert current_revision != stale_revision

    result = mark_export_fresh(
        season=SEASON,
        root=root,
        expected_revision=stale_revision,
        export_dir=tmp_path / "canonical-export",
        note="stale export",
    )

    assert result["cleared"] is False
    assert result["reason"] == "canonical_state_advanced_after_export"
    assert load_decisions(SEASON, root=root)["export_state"]["requires_fresh_export"] is True


def test_mark_export_fresh_cas_preserves_latch_on_interleaving_write(
    tmp_path: Path, monkeypatch
) -> None:
    """A writer between the freshness read and its commit must not be overwritten."""

    stale_revision = _stale_after_refresh(tmp_path, monkeypatch)
    root = tmp_path / "season"
    service = CanonicalSeasonService(root=root)
    stale_snapshot = service.load(SEASON)

    # Simulate a concurrent refresh/reconciliation committing after our read.
    add_banned_date(
        season=SEASON,
        date="2027-01-15",
        request_id="interleaved-write",
        root=root,
        note="concurrent state change",
    )
    current_revision = canonical_state_revision(
        load_schedule(SEASON, root=root), load_decisions(SEASON, root=root)
    )
    assert current_revision != stale_revision

    # Force the freshness operation to still hold its stale read, so the only
    # thing preventing a lost update is the store's atomic expected-revision CAS.
    monkeypatch.setattr(service, "load", lambda season: stale_snapshot)
    result = service.mark_export_fresh(season=SEASON, expected_revision=stale_revision)

    assert result["cleared"] is False
    assert result["reason"] == "canonical_state_changed_during_commit"
    decisions = load_decisions(SEASON, root=root)
    assert decisions["export_state"]["requires_fresh_export"] is True
    # The newer canonical revision is still on disk; the stale snapshot did not win.
    assert decisions["canonical_state_revision"] == current_revision


def test_season_export_surfaces_stale_export_when_latch_not_cleared(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    """A not-cleared export must warn and flag itself as not publishable."""

    root = _promote(tmp_path)
    export_dir = tmp_path / "canonical-export"

    import tournament_scheduler.season_state as season_state

    def stale_freshness(*_args, **_kwargs):
        return {
            "season": SEASON,
            "cleared": False,
            "reason": "canonical_state_changed_during_commit",
        }

    monkeypatch.setattr(season_state, "mark_export_fresh", stale_freshness)
    rc = main(
        [
            "season",
            "export",
            "--season",
            SEASON,
            "--work-dir",
            str(tmp_path / ".pipeline"),
            "--root",
            str(root),
            "--export-dir",
            str(export_dir),
            "--flat",
        ]
    )
    assert rc == 0
    output = capsys.readouterr().out
    assert "NOT publishable" in output

    stage = PipelineState(tmp_path / ".pipeline").read_stage(StageName.EXPORT)
    assert stage["stale_export"] is True
    assert stage["export_freshness"]["cleared"] is False
