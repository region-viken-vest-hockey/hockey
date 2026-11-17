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
from tournament_scheduler.infrastructure.canonical_season_store import CanonicalSeasonStore

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


def test_legacy_store_satisfies_the_store_port_unchanged() -> None:
    store = CanonicalSeasonStore("season")
    assert isinstance(store, CanonicalStorePort)


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
