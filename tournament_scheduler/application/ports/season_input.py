"""Season input contract: normalized registration, season and rule facts (#958).

One normalized result regardless of where the facts came from (the RVV
season workbook, a local CSV/JSON bundle, or manual entry). The shape
carries operator-supplied facts only. Availability and booking *evidence* stays
in the ice-time contract (:mod:`.ice_time`); this module adds no second trust
layer. Operator-supplied allocation windows and booking assertions are the same
``ice_time`` DTOs with a ``MANUAL``/``FILE`` :class:`SourceRef`.

Failures are typed: an adapter raises :class:`SeasonInputError` carrying every
:class:`InputDiagnostic` it found, and never returns a partial result.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol, runtime_checkable

from .common import PortCapabilities, PortError, PortErrorCode, SchemaCompatibility, SourceRef
from .ice_time import AllocationWindow, BookingAssertion

SEASON_INPUT_SCHEMA = SchemaCompatibility("season_input", 1, 1)
SEASON_INPUT_FEATURES = frozenset({"season_input", "teams", "age_groups"})


class DiagnosticCode(str, Enum):
    MISSING_INPUT = "missing_input"
    UNSUPPORTED_FORMAT = "unsupported_format"
    UNREADABLE = "unreadable"
    MISSING_FIELD = "missing_field"
    INVALID_VALUE = "invalid_value"
    DUPLICATE = "duplicate"
    UNKNOWN_REFERENCE = "unknown_reference"
    AMBIGUOUS = "ambiguous"


@dataclass(frozen=True)
class InputDiagnostic:
    """One located problem: ``location`` is ``<document>:<table>[#row].<field>``."""

    code: DiagnosticCode
    location: str
    message: str


class SeasonInputError(PortError):
    """Input could not be normalized. ``diagnostics`` lists every problem found."""

    def __init__(self, diagnostics: tuple[InputDiagnostic, ...]) -> None:
        if not diagnostics:
            raise ValueError("SeasonInputError needs at least one diagnostic")
        absent = all(
            d.code in (DiagnosticCode.MISSING_INPUT, DiagnosticCode.UNREADABLE) for d in diagnostics
        )
        super().__init__(
            PortErrorCode.NOT_FOUND if absent else PortErrorCode.SCHEMA_INCOMPATIBLE,
            "; ".join(f"[{d.code.value}] {d.location}: {d.message}" for d in diagnostics),
        )
        self.diagnostics = diagnostics


@dataclass(frozen=True)
class TeamInput:
    club: str
    label: str
    age_group: str
    target_tournament_count: int | None = None


@dataclass(frozen=True)
class SourceDeclaration:
    """A declared availability source. ``url`` is optional: manual-only is valid."""

    name: str
    source_type: str | None = None
    url: str | None = None


@dataclass(frozen=True)
class NormalizedSeasonInput:
    """Registration, season window, age-group parameters and controlled rules.

    ``age_group_params`` maps ``parameter -> {age_group: value}`` (for example
    ``parallel_games``); ``rules`` holds the remaining controlled settings
    (hosting limits, fairness thresholds, participant pools, date preferences).
    ``age_groups`` is the declared list, or - when the input declares none - the
    explicit derivation *the age groups used by the teams* (``age_groups_declared``
    is then False). ``operator_allocations`` / ``operator_assertions`` are operator-supplied
    facts, never source observations; empty means *nothing supplied*, not free.
    """

    start_date: str
    end_date: str
    age_groups: tuple[str, ...]
    teams: tuple[TeamInput, ...]
    origin: SourceRef
    age_group_params: dict[str, dict[str, Any]] = field(default_factory=dict)
    rules: dict[str, Any] = field(default_factory=dict)
    sources: tuple[SourceDeclaration, ...] = ()
    operator_allocations: tuple[AllocationWindow, ...] = ()
    operator_assertions: tuple[BookingAssertion, ...] = ()
    age_groups_declared: bool = True
    schema_version: int = 1

    def __post_init__(self) -> None:
        SEASON_INPUT_SCHEMA.require(self.schema_version)

    def problem_fingerprint(self) -> str:
        """Identity of the registration, season-window and rule facts only.

        It deliberately excludes origin and the operator-supplied allocations and
        assertions, so it is *not* the full input identity: use
        :meth:`evidence_fingerprint` when operator facts matter.
        """
        payload = {
            "start_date": self.start_date,
            "end_date": self.end_date,
            "age_groups": list(self.age_groups),
            "teams": [vars(t) for t in self.teams],
            "age_group_params": self.age_group_params,
            "rules": self.rules,
            "sources": [vars(s) for s in self.sources],
        }
        blob = json.dumps(payload, sort_keys=True, default=str, ensure_ascii=False)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    def evidence_fingerprint(self) -> str:
        """Deterministic identity of the problem facts plus the operator-supplied facts.

        Covers each allocation window and booking assertion with its source identity
        (id, kind, actor, document fingerprint, revision) but not capture time.
        """

        def ref(r: SourceRef) -> list[Any]:
            return [r.source_id, r.kind.value, r.actor, r.document, r.fingerprint, r.revision]

        payload = {
            "problem": self.problem_fingerprint(),
            "allocations": sorted(
                [ref(w.source), w.venue_id, w.starts_at.isoformat(), w.ends_at.isoformat(), w.kind.value]
                for w in self.operator_allocations
            ),
            "assertions": sorted(
                [ref(a.source), a.tournament_id, a.venue_id, a.starts_at.isoformat(), a.ends_at.isoformat(), a.trust.value, a.event_id or ""]
                for a in self.operator_assertions
            ),
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


@runtime_checkable
class SeasonInputPort(Protocol):
    """Read one season's normalized input from workbook, files or manual entry."""

    def capabilities(self) -> PortCapabilities: ...

    def read(self) -> NormalizedSeasonInput: ...


def season_input_capabilities(adapter_id: str) -> PortCapabilities:
    return PortCapabilities(
        adapter_id=adapter_id,
        features=SEASON_INPUT_FEATURES,
        schemas=(SEASON_INPUT_SCHEMA,),
        requires_network=False,
    )
