"""Canonical write gate: refusal happens before any local canonical state is touched.

The application gate is one decorator (``canonical_write``) on every public
canonical write use case. These tests pin the enumerated gated set, refuse
every gated method through a sentinel store, and prove with a byte-level
snapshot of the canonical data directory that a stale client changes nothing.
"""

from __future__ import annotations

import ast
import inspect
import json
from pathlib import Path

import pytest

from tests.distribution_signing_support import trust
from tests.repository_sync_support import write_cache
from tournament_scheduler import distribution
from tournament_scheduler.application.canonical_season.write_gate import (
    GATED_WRITE_METHODS,
    production_write_gate,
    write_gate_for,
)
from tournament_scheduler.application.canonical_season_service import CanonicalSeasonService
from tournament_scheduler.distribution_update import WriteBlockedError
from tournament_scheduler.infrastructure.canonical_season_store import CanonicalSeasonStore

SERVICE_SOURCE = Path("tournament_scheduler/application/canonical_season_service.py")
SEASON = "2026-2027"

GATED_WRITES = frozenset(
    {
        "compact_history", "season_baseline_create", "season_baseline_advance", "refresh_calendars",
        "reconcile_config", "mark_export_fresh", "add_request_constraint", "release_request_constraints",
        "allow_holiday_date", "disallow_holiday_dates", "add_banned_date", "release_banned_dates", "promote",
        "move_tournament", "swap_participants", "replace_participant", "remove_participant",
        "release_participation_withdrawals", "apply_retire_team", "batch_maintenance", "rename_teams",
        "apply_candidate", "seal_published_season", "reopen_planning", "normalize_placements",
        "normalize_arena_identities", "reserve_guest_slot", "fill_guest_slot", "release_guest_slot",
        "release_change_protections", "reconcile_calendar_bookings", "confirm_calendar_booking",
        "release_calendar_booking", "set_manual_booking_assertion", "clear_manual_booking_assertion",
        "set_club_booking_source", "set_ice_time_minutes", "clear_ice_time_minutes",
        "record_placement_infeasibility_proofs", "release_placement_infeasibility_proofs",
        "approve_tournament", "unapprove_tournament", "record_participation_acceptance",
        "revoke_participation_acceptance",
    }
)

# Public methods that read or only report. ``replacement_candidates`` runs dry-run
# previews internally through a module function, never through the gated facade.
READ_ONLY = frozenset(
    {
        "load", "season_baseline_show", "history_inventory", "request_constraint_report", "change_request_ledger",
        "tournament_inspection", "constraint_inspection", "replacement_candidates",
        "holiday_date_exception_report", "banned_date_report", "withdrawal_report", "preview_retire_team",
        "season_lifecycle_report", "verify_sealed_reconciliation", "publication_evidence_report",
        "guest_slot_report", "guest_slot_candidates", "change_protection_report", "calendar_booking_candidates",
        "calendar_booking_assessment", "calendar_booking_findings", "booking_status_report",
        "club_booking_sources", "ice_time_override_report", "placement_infeasibility_report",
        "load_participation_acceptances", "approval_report",
    }
)


class _UntouchableStore:
    """Fails the test if any use case reaches the canonical store."""

    def __getattr__(self, name: str):
        raise AssertionError(f"gated refusal must precede store access (touched store.{name})")


class _RefusingGate:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self) -> None:
        self.calls += 1
        raise WriteBlockedError("Canonical changes are blocked: test refusal. Nothing was changed.")


def _snapshot(root: Path) -> dict[str, bytes]:
    if not root.exists():
        return {}
    return {path.relative_to(root).as_posix(): path.read_bytes() for path in sorted(root.rglob("*")) if path.is_file()}


def _seed_season(root: Path) -> None:
    """Minimal promoted-season state: empty plan, pending decisions."""
    season_dir = root / SEASON
    season_dir.mkdir(parents=True)
    schedule = {"schema_version": 1, "season": SEASON, "revision": "rev-1", "fingerprint": "rev-1",
                "plan_schema_version": 1, "plan": {"schema_version": 1, "tournaments": []},
                "verification_context": {"problem": {}}}
    decisions = {"schema_version": 1, "season": SEASON, "schedule_fingerprint": "rev-1", "decisions": {}}
    (season_dir / "schedule.json").write_text(json.dumps(schedule, sort_keys=True), encoding="utf-8")
    (season_dir / "decisions.json").write_text(json.dumps(decisions, sort_keys=True), encoding="utf-8")


@pytest.fixture(autouse=True)
def _trusted_manifest_signer(monkeypatch):
    trust(monkeypatch)


def test_gated_set_is_the_enumerated_write_use_cases() -> None:
    assert GATED_WRITE_METHODS == GATED_WRITES
    for name in GATED_WRITES:
        assert hasattr(getattr(CanonicalSeasonService, name), "__wrapped__"), f"{name} is not gated"
    for name in READ_ONLY:
        assert not hasattr(getattr(CanonicalSeasonService, name), "__wrapped__"), f"{name} must stay ungated"

    tree = ast.parse(SERVICE_SOURCE.read_text(encoding="utf-8"))
    service = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "CanonicalSeasonService")
    public = {item.name for item in service.body if isinstance(item, ast.FunctionDef) and not item.name.startswith("_")}
    assert public == GATED_WRITES | READ_ONLY, "new public service methods must be classified as gated or read-only"


@pytest.mark.parametrize("name", sorted(GATED_WRITES))
def test_every_gated_write_is_refused_before_store_access(name: str) -> None:
    gate = _RefusingGate()
    service = CanonicalSeasonService(store=_UntouchableStore(), write_gate=gate)
    method = getattr(service, name)
    kwargs = {
        parameter.name: "x"
        for parameter in inspect.signature(method).parameters.values()
        if parameter.default is inspect.Parameter.empty
    }

    with pytest.raises(WriteBlockedError, match="Nothing was changed"):
        method(**kwargs)
    assert gate.calls == 1


def test_stale_client_is_refused_and_leaves_canonical_data_untouched(tmp_path: Path) -> None:
    root = tmp_path / "season"
    _seed_season(root)
    cache = tmp_path / "cache"
    write_cache(cache, minimum_write="2.0.0", latest="2.1.0")
    service = CanonicalSeasonService(
        CanonicalSeasonStore(root), write_gate=write_gate_for("1.9.0", cache)
    )
    before = _snapshot(root)

    with pytest.raises(WriteBlockedError) as refused:
        service.add_banned_date(season=SEASON, date="2026-12-24", request_id="stale-client")
    with pytest.raises(WriteBlockedError):
        service.allow_holiday_date(season=SEASON, date="2026-12-19", reason="stale client")

    message = str(refused.value)
    assert message.startswith("Canonical changes are blocked:")
    assert "below the minimum write version 2.0.0" in message
    assert "install 2.1.0 before writing" in message
    assert message.endswith("Nothing was changed.")
    assert _snapshot(root) == before


def test_corrupt_cached_manifest_refuses_writes_without_touching_data(tmp_path: Path) -> None:
    root = tmp_path / "season"
    _seed_season(root)
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "release-manifest.json").write_text("{not json", encoding="utf-8")
    service = CanonicalSeasonService(CanonicalSeasonStore(root), write_gate=write_gate_for("2.1.0", cache))
    before = _snapshot(root)

    with pytest.raises(WriteBlockedError, match="unusable"):
        service.add_banned_date(season=SEASON, date="2026-12-24", request_id="corrupt-cache")
    assert _snapshot(root) == before


def test_supported_client_proceeds_and_records_the_change(tmp_path: Path) -> None:
    root = tmp_path / "season"
    _seed_season(root)
    cache = tmp_path / "cache"
    write_cache(cache, minimum_write="2.0.0", latest="2.1.0")
    service = CanonicalSeasonService(CanonicalSeasonStore(root), write_gate=write_gate_for("2.1.0", cache))
    before = _snapshot(root)

    result = service.add_banned_date(season=SEASON, date="2026-12-24", request_id="supported-client")

    assert result["created"] is True
    assert _snapshot(root) != before
    assert service.load(SEASON).decisions["banned_dates"][0]["date"] == "2026-12-24"


def test_production_gate_reads_installed_version_and_default_data_root(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "season"
    _seed_season(root)
    data_root = tmp_path / "data"
    write_cache(data_root / "update-cache", minimum_write="2.0.0", latest="2.1.0")
    monkeypatch.setenv(distribution.DATA_ROOT_ENV_VAR, str(data_root))
    monkeypatch.setattr(distribution, "__version__", "1.9.0")
    before = _snapshot(root)

    service = CanonicalSeasonService(CanonicalSeasonStore(root))
    with pytest.raises(WriteBlockedError, match="below the minimum write version"):
        service.add_banned_date(season=SEASON, date="2026-12-24", request_id="production-gate")
    assert _snapshot(root) == before
    assert callable(production_write_gate())
