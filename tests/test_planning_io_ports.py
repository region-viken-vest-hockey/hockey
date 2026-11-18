"""Contract tests for the versioned planning I/O ports (ADR 0009)."""

from __future__ import annotations

import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from tournament_scheduler.application.ports.common import (
    PORT_CONTRACT_VERSION,
    PortCapabilities,
    PortError,
    PortErrorCode,
    SchemaCompatibility,
    SourceKind,
    SourceRef,
)
from tournament_scheduler.application.ports.delivery import (
    DestinationKind,
    ExportArtifact,
    ExportBundle,
    PublishIntent,
)
from tournament_scheduler.application.ports.ice_time import (
    AllocationWindow,
    AssertionTrust,
    BookingAssertion,
    Completeness,
    SourceCoverage,
    SourceScope,
    WindowKind,
)
from tournament_scheduler.application.ports.readiness import (
    ReadinessReason,
    ReadinessReport,
    ReadinessStatus,
)
from tournament_scheduler.application.ports.store import CanonicalStorePort
import tournament_scheduler.infrastructure.canonical_season_store as store_module
from tournament_scheduler.infrastructure.canonical_season_store import (
    CanonicalRevisionConflictError,
    CanonicalSeasonSnapshot,
    CanonicalSeasonStore,
    SeasonStateError,
)
from tournament_scheduler.season.state import canonical_state_revision

T0 = datetime(2027, 1, 9, 8, tzinfo=timezone.utc)
T1 = T0 + timedelta(hours=3)


def manual() -> SourceRef:
    return SourceRef("manual-1", SourceKind.MANUAL, T0, "Europe/Oslo", actor="operator")


def test_source_ref_enforces_accountability_and_timezone() -> None:
    with pytest.raises(ValueError):
        SourceRef("m", SourceKind.MANUAL, T0, "Europe/Oslo")
    with pytest.raises(ValueError):
        SourceRef("f", SourceKind.FILE, T0, "Europe/Oslo", document="a.xlsx")
    with pytest.raises(ValueError):
        SourceRef("l", SourceKind.LIVE, T0, "Not/AZone", fingerprint="x")
    with pytest.raises(ValueError):
        SourceRef("l", SourceKind.LIVE, datetime(2027, 1, 1), "Europe/Oslo", fingerprint="x")
    assert manual().kind is SourceKind.MANUAL  # manual-only mode needs no URL or network


def test_free_ice_and_empty_observation_are_not_booking_authority() -> None:
    window = AllocationWindow(manual(), "arena-1", T0, T1, WindowKind.AVAILABLE)
    scope = SourceScope("arena-1", T0, T1)
    empty = SourceCoverage(manual(), scope, Completeness.COMPLETE, T0, fresh_until=T1)
    for fact in (window, empty):
        assert not isinstance(fact, BookingAssertion)
        assert not hasattr(fact, "trust") and not hasattr(fact, "tournament_id")
    assert empty.is_fresh(T0) and not empty.is_fresh(T1 + timedelta(seconds=1))


def test_booking_assertion_invariants() -> None:
    with pytest.raises(ValueError):
        BookingAssertion(manual(), "", "arena-1", T0, T1, AssertionTrust.CONFIRMED)
    with pytest.raises(ValueError):
        BookingAssertion(manual(), "t-1", "arena-1", T1, T0, AssertionTrust.PROVISIONAL)
    ok = BookingAssertion(manual(), "t-1", "arena-1", T0, T1, AssertionTrust.CONFIRMED)
    assert ok.source.actor == "operator"


def test_schema_and_feature_negotiation_fail_closed() -> None:
    SchemaCompatibility("schedule", 1, 3).require(3)
    with pytest.raises(PortError) as exc:
        SchemaCompatibility("schedule", 1, 3).require(4)
    assert exc.value.code is PortErrorCode.SCHEMA_INCOMPATIBLE
    caps = PortCapabilities("x", features=frozenset({"atomic_write"}))
    caps.require("atomic_write")
    with pytest.raises(PortError) as exc:
        caps.require("revision_cas")
    assert exc.value.code is PortErrorCode.UNSUPPORTED_FEATURE
    with pytest.raises(PortError):
        PortCapabilities("old", contract_version=PORT_CONTRACT_VERSION + 1).require()


def test_publication_requires_explicit_authorization_and_exact_bundle() -> None:
    bundle = ExportBundle("rev-1", 1, (ExportArtifact("plan.xlsx", "x/y", "a" * 64, 10),))
    with pytest.raises(PortError) as exc:
        PublishIntent(DestinationKind.PAGES, "latest", bundle.fingerprint(), "rev-1", "", "")
    assert exc.value.code is PortErrorCode.AUTHORIZATION_REQUIRED
    intent = PublishIntent(DestinationKind.PAGES, "latest", bundle.fingerprint(), "rev-1", "op", "req-9")
    assert intent.matches(bundle)
    other = ExportBundle("rev-2", 1, bundle.artifacts)
    assert not intent.matches(other)


def test_readiness_verdict_invariants_and_serialisation() -> None:
    ready = ReadinessReport("local", ReadinessStatus.READY, ReadinessReason.READY_CURRENT, "ok", workflow_version="v1")
    assert ready.to_dict()["reason_code"] == "ready_current"
    with pytest.raises(ValueError):
        ReadinessReport("local", ReadinessStatus.READY, ReadinessReason.READY_CURRENT, "ok")
    with pytest.raises(ValueError):
        ReadinessReport("local", ReadinessStatus.READY, ReadinessReason.DIVERGED, "x", workflow_version="v")
    with pytest.raises(ValueError):
        ReadinessReport("local", ReadinessStatus.REVIEW_REQUIRED, ReadinessReason.DIVERGED, "x", workflow_version="v")
    review = ReadinessReport("local", ReadinessStatus.REVIEW_REQUIRED, ReadinessReason.STORE_UNAVAILABLE, "no store")
    assert review.workflow_version is None
    with pytest.raises(ValueError):
        ReadinessReport("local", ReadinessStatus.READY, ReadinessReason.READY_CURRENT, "ok", workflow_version="v", canonical_revision="")
    # Readiness vouches for the workflow contract only; state is the lifecycle gate's job.
    assert ready.canonical_revision is None
    assert "does **not** vouch for canonical season state" in Path(
        "tournament_scheduler/application/ports/readiness.py"
    ).read_text(encoding="utf-8")


def _snapshot(marker: str) -> CanonicalSeasonSnapshot:
    return CanonicalSeasonSnapshot(
        season="2026-2027",
        schedule={"schema_version": 1, "plan": {"start_date": "2026-09-01", "end_date": "2027-04-30", "tournaments": []}},
        decisions={"schema_version": 1, "decisions": {"marker": marker}},
    )


def test_legacy_store_satisfies_the_store_port_unchanged(tmp_path: Path) -> None:
    store = CanonicalSeasonStore(tmp_path / "season")
    assert isinstance(store, CanonicalStorePort)
    assert not store.has_state("2026-2027")
    first = _snapshot("a")
    store.write(first, require_absent=True)
    assert store.has_state("2026-2027")
    loaded = store.load("2026-2027")
    assert loaded.schedule == first.schedule and loaded.decisions == first.decisions


def test_legacy_store_honours_port_write_semantics(tmp_path: Path, monkeypatch) -> None:
    store = CanonicalSeasonStore(tmp_path / "season")
    store.write(_snapshot("a"))
    base = store.load("2026-2027")
    revision = canonical_state_revision(base.schedule, base.decisions)

    # require_absent refuses to replace existing state and leaves it intact.
    with pytest.raises(SeasonStateError):
        store.write(_snapshot("b"), require_absent=True)
    assert store.load("2026-2027").decisions == base.decisions

    # A stale expected_revision is a compare-and-swap conflict; state is unchanged.
    with pytest.raises(CanonicalRevisionConflictError):
        store.write(_snapshot("b"), expected_revision="stale")
    assert store.load("2026-2027").decisions == base.decisions

    # A failed write leaves the previous complete state in place.
    def boom(*_a, **_k):
        raise OSError("injected")

    monkeypatch.setattr(store_module, "_fsync_tree", boom)
    with pytest.raises(OSError):
        store.write(_snapshot("c"), expected_revision=revision)
    monkeypatch.undo()
    assert store.load("2026-2027").decisions == base.decisions

    # Legacy exception boundary: the unchanged store does NOT yet raise PortError.
    # Translation is the phase-2 adapter's job (ADR 0009); consumers must not
    # assume the normalized error contract from the reference store today.
    assert not issubclass(CanonicalRevisionConflictError, PortError)
    assert not issubclass(SeasonStateError, PortError)

    # The matching revision commits atomically.
    store.write(_snapshot("d"), expected_revision=revision)
    assert store.load("2026-2027").decisions["decisions"]["marker"] == "d"


def test_readiness_contract_loads_without_the_package() -> None:
    """The bootstrap gate loads the file directly; it must stay self-contained."""
    path = Path("tournament_scheduler/application/ports/readiness.py")
    spec = importlib.util.spec_from_file_location("standalone_readiness", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    import sys

    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    assert module.ReadinessStatus.READY.value == "READY"
