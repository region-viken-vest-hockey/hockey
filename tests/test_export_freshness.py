"""Regression coverage for clearing the canonical "fresh export required" latch.

A calendar refresh (or config reconciliation) marks the promoted season's
``export_state.requires_fresh_export`` so publication refuses an artifact that
predates the evidence change. Before this regression the marker was a one-way
latch: re-exporting never cleared it, so a later publish was refused forever
with no actual freshness problem.
"""

from __future__ import annotations

import json
from pathlib import Path

from tournament_scheduler.application.canonical_season_service import CanonicalSeasonService
from tournament_scheduler.cli.rvv_cli import main
from tournament_scheduler.infrastructure.canonical_season_migration import (
    migrate_season_to_partitioned,
)
from tournament_scheduler.pipeline.export_parity.gate import publish_parity_gate
from tournament_scheduler.pipeline.state import PipelineState, StageName
from tournament_scheduler.publication.export_lifecycle import write_draft_manifest
from tournament_scheduler.application.canonical_season.store_facade import (
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


def test_season_export_fails_closed_when_latch_not_cleared(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    """A not-cleared export must fail the command/stage contract, keep artifacts."""

    root = _promote(tmp_path)
    export_dir = tmp_path / "canonical-export"

    import tournament_scheduler.application.canonical_season.store_facade as season_state

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
    assert rc == 1
    output = capsys.readouterr().out
    assert "NOT publishable" in output

    state = PipelineState(tmp_path / ".pipeline")
    envelope = state.read_envelope(StageName.EXPORT)
    assert envelope["status"] == "failed"
    stage = envelope["data"]
    assert stage["stale_export"] is True
    assert stage["export_freshness"]["cleared"] is False
    assert any(str(error).startswith("stale_export:") for error in stage["errors"])
    # Artifacts are retained for diagnosis even though the stage failed.
    assert (export_dir / "season_plan.xlsx").exists()
    assert (export_dir / "season_plan.html").exists()


def _mark_export_fresh_events(decisions: dict) -> list[dict]:
    return [event for event in decisions.get("history") or [] if event.get("event") == "mark_export_fresh"]


def test_repeated_mark_export_fresh_of_unchanged_state_is_idempotent(
    tmp_path: Path, monkeypatch
) -> None:
    """A second clearing at the same revision must not write or append history."""

    revision = _stale_after_refresh(tmp_path, monkeypatch)
    root = tmp_path / "season"

    first = mark_export_fresh(
        season=SEASON,
        root=root,
        expected_revision=revision,
        export_dir=tmp_path / "canonical-export",
        note="first export",
    )
    assert first["cleared"] is True
    after_first = load_decisions(SEASON, root=root)
    assert len(_mark_export_fresh_events(after_first)) == 1

    second = mark_export_fresh(
        season=SEASON,
        root=root,
        expected_revision=revision,
        export_dir=tmp_path / "canonical-export-2",
        note="second export",
    )
    assert second["cleared"] is True
    assert second.get("reason") == "already_fresh"

    after_second = load_decisions(SEASON, root=root)
    # Byte-for-byte stable: no duplicate event, no timestamp/revision churn.
    assert after_second == after_first
    assert len(_mark_export_fresh_events(after_second)) == 1


def test_mark_export_fresh_already_fresh_path_does_not_commit(
    tmp_path: Path, monkeypatch
) -> None:
    """The semantic no-op must never invoke the physically rewriting store."""

    revision = _stale_after_refresh(tmp_path, monkeypatch)
    root = tmp_path / "season"
    service = CanonicalSeasonService(root=root)

    first = service.mark_export_fresh(season=SEASON, expected_revision=revision)
    assert first["cleared"] is True

    def unexpected_commit(*_args, **_kwargs):
        raise AssertionError("already-fresh export must not commit canonical state")

    monkeypatch.setattr(service, "_commit", unexpected_commit)
    result = service.mark_export_fresh(season=SEASON, expected_revision=revision)

    assert result["cleared"] is True
    assert result["reason"] == "already_fresh"
    assert result["canonical_state_revision"] == revision
    assert result["committed_canonical_state_revision"] == revision


def test_mark_export_fresh_noop_still_binds_the_current_revision(
    tmp_path: Path, monkeypatch
) -> None:
    """A write landing after an already-fresh read must not be reported fresh."""

    revision = _stale_after_refresh(tmp_path, monkeypatch)
    root = tmp_path / "season"
    service = CanonicalSeasonService(root=root)

    first = service.mark_export_fresh(season=SEASON, expected_revision=revision)
    assert first["cleared"] is True
    already_fresh_snapshot = service.load(SEASON)

    # A decision-only mutation advances canonical state but does not arm the
    # export-freshness latch, so the stale snapshot still looks already fresh.
    add_banned_date(
        season=SEASON,
        date="2027-01-15",
        request_id="noop-interleave",
        root=root,
        note="state advanced after export",
    )
    current_revision = canonical_state_revision(
        load_schedule(SEASON, root=root), load_decisions(SEASON, root=root)
    )
    assert current_revision != revision

    # Hold the already-fresh read past the concurrent write. The store's
    # read-only expected-revision boundary must detect it without rewriting.
    monkeypatch.setattr(service, "load", lambda season: already_fresh_snapshot)
    result = service.mark_export_fresh(season=SEASON, expected_revision=revision)

    assert result["cleared"] is False
    assert result["reason"] == "canonical_state_changed_during_commit"
    decisions = load_decisions(SEASON, root=root)
    assert decisions["canonical_state_revision"] == current_revision
    assert decisions["export_state"]["fresh_canonical_revision"] == revision


def test_season_export_is_stale_when_freshness_does_not_cover_artifacts(
    tmp_path: Path, monkeypatch
) -> None:
    """The caller must trust the returned revision, not a bare cleared flag."""

    root = _promote(tmp_path)
    export_dir = tmp_path / "canonical-export"

    import tournament_scheduler.application.canonical_season.store_facade as season_state

    def mismatched_freshness(*_args, **_kwargs):
        return {
            "season": SEASON,
            "cleared": True,
            "reason": "already_fresh",
            "canonical_state_revision": "some-other-revision",
        }

    monkeypatch.setattr(season_state, "mark_export_fresh", mismatched_freshness)
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
    assert rc == 1

    state = PipelineState(tmp_path / ".pipeline")
    envelope = state.read_envelope(StageName.EXPORT)
    assert envelope["status"] == "failed"
    assert envelope["data"]["stale_export"] is True


def test_repeated_season_export_does_not_churn_canonical_history(
    tmp_path: Path, monkeypatch
) -> None:
    """Re-exporting unchanged canonical content must leave canonical state stable."""

    _stale_after_refresh(tmp_path, monkeypatch)
    root = tmp_path / "season"
    args = [
        "season",
        "export",
        "--season",
        SEASON,
        "--work-dir",
        str(tmp_path / ".pipeline"),
        "--root",
        str(root),
        "--export-dir",
        str(tmp_path / "canonical-export"),
        "--flat",
    ]

    assert main(args) == 0
    after_first = load_decisions(SEASON, root=root)
    assert len(_mark_export_fresh_events(after_first)) == 1
    first_revision = after_first["canonical_state_revision"]
    assert after_first["export_state"]["fresh_canonical_revision"] == first_revision

    assert main(args) == 0
    after_second = load_decisions(SEASON, root=root)
    assert len(_mark_export_fresh_events(after_second)) == 1
    assert after_second["canonical_state_revision"] == first_revision
    assert after_second["updated_at"] == after_first["updated_at"]


def _canonical_tree_bytes(root: Path) -> dict[str, bytes]:
    season_root = root / SEASON
    return {
        str(path.relative_to(season_root)): path.read_bytes()
        for path in sorted(season_root.rglob("*"))
        if path.is_file()
    }


def test_presentation_only_export_keeps_already_fresh_partitioned_season_byte_exact(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    """Renderer-only regeneration must not rewrite partitioned canonical files."""

    _stale_after_refresh(tmp_path, monkeypatch)
    root = tmp_path / "season"
    common_args = [
        "season",
        "export",
        "--season",
        SEASON,
        "--work-dir",
        str(tmp_path / ".pipeline"),
        "--root",
        str(root),
        "--flat",
    ]

    # First perform the explicit stale -> fresh transition, then preserve that
    # same semantic state in the production partitioned layout.
    assert main([*common_args, "--export-dir", str(tmp_path / "initial-export")]) == 0
    capsys.readouterr()
    migrate_season_to_partitioned(SEASON, root=root)
    # Materialize the deterministic change ledger that accompanies the real
    # tracked partitioned season before taking the byte-level baseline.
    assert main([*common_args, "--export-dir", str(tmp_path / "partitioned-baseline")]) == 0
    capsys.readouterr()
    before = _canonical_tree_bytes(root)

    # The minimal promoted fixture predates canonical manifest metadata. Supply
    # the same revision-bound draft manifest that a production canonical export
    # carries so the presentation contract can prove artifact parity.
    import tournament_scheduler.cli.season_presentation_export as presentation_cli

    real_export = presentation_cli.run_canonical_season_export

    def export_with_manifest(args):
        result = real_export(args)
        write_draft_manifest(
            result["export_dir"],
            export_id="presentation-byte-stability",
            generated_at="2026-10-16T00:00:00+00:00",
            export_fingerprint=result["export_fingerprint"],
            source_run_id=None,
            canonical_season=SEASON,
            canonical_revision=result["canonical_revision"],
        )
        return result

    monkeypatch.setattr(presentation_cli, "run_canonical_season_export", export_with_manifest)

    rc = main(
        [
            *common_args,
            "--export-dir",
            str(tmp_path / "presentation-export"),
            "--presentation-only",
        ]
    )
    report = json.loads(capsys.readouterr().out)

    assert report["artifact_parity"]["status"] == "PASS", report["artifact_parity"].get("reasons")
    assert rc == 0, report
    assert report["status"] == "PASS"
    assert report["canonical_revision_unchanged"] is True
    assert report["canonical_schedule_or_decisions_changed"] is False
    assert report["artifact_parity"]["status"] == "PASS"
    assert report["publication"] == {"performed": False, "authorized": False}
    assert _canonical_tree_bytes(root) == before
