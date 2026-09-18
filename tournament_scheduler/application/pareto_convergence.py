"""Bounded Pareto-convergence control plane for unpromoted refinement.

A hard-valid Stage 3/Stage 4 candidate often reaches a semantic audit with a
``REVIEW_REQUIRED`` verdict while the repository still exposes actionable
repair/search directions (unplaced obligations, hosting deficits,
participation deviations, local neighborhoods, ...). Historically that verdict
was treated as a terminal run state, so the human became the outer
optimization loop by repeatedly asking the harness to "try again". This module
owns the missing outer loop *mechanics*:

* a bounded, dominance-pruned archive of independently verified candidates;
* a convergence state machine (epochs, explored directions, plateau counter,
  remaining findings, search coverage);
* a truthful terminal reason that never conflates "the configured bounded
  search found nothing" with "no solution exists", and never claims global
  Pareto optimality for a non-exhaustive search space.

It deliberately contains no hockey legality, no mutation and no metric
computation: the deterministic providers/verifiers own candidate generation
and measurement, and the caller/agent owns the contextual choice of which
non-dominated direction to explore next. This module only decides how the
observed candidates and coverage update a bounded frontier and when automatic
exploration may stop.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

from ..pareto import dominates, representative_indices, vectors_equal

# Bounded frontier size and convergence safeguards. These are the "configured
# budgets" a terminal reason refers to; hitting one is bounded convergence, not
# proof of global optimality.
DEFAULT_FRONTIER_LIMIT = 6
DEFAULT_MAX_EPOCHS = 6
DEFAULT_MAX_NO_IMPROVEMENT_EPOCHS = 2

# Terminal reasons. ``TERMINAL_NONE`` means the controller may still explore.
TERMINAL_NONE = ""
TERMINAL_PASS = "pass"
TERMINAL_OPERATOR_REQUIRED = "operator_required"
TERMINAL_BOUNDED_SEARCH_EXHAUSTED = "bounded_search_exhausted"
TERMINAL_BOUNDED_BUDGET = "bounded_budget_exhausted"
TERMINAL_PARETO_STABLE = "pareto_stable"

# Canonical supported direction per finding category/code. The mapping is a
# *label* for the controller's explored-direction bookkeeping, not a routing
# decision: option enumeration still goes through the finding's provider.
DIRECTION_BY_CATEGORY: Mapping[str, str] = {
    "hard_violation": "hard_constraints",
    "hosting": "hosting",
    "participation": "participants",
    "manual_placement": "placement",
    "movable_capacity": "movable_capacity",
    "roster_shape": "roster_shape",
}
DIRECTION_BY_CODE: Mapping[str, str] = {
    "unplaced_tournament_placement": "unplaced_placement",
}
GENERIC_DIRECTION = "generic"

# Finding categories that always require human authority/input rather than
# another automatic search. A finding may also set ``requires_operator``
# explicitly (the repository-owned signal a provider uses for a
# policy/waiver/registration question).
OPERATOR_REQUIRED_CATEGORIES = frozenset({"policy", "registration", "publication"})


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _comparable(a: Mapping[str, float], b: Mapping[str, float]) -> bool:
    """True when two objective vectors can be compared dimension-by-dimension.

    Vectors from different measurement surfaces must not be silently treated
    as one non-dominated set; an incomparable vector is retained rather than
    dropped by an arithmetic accident.
    """
    return bool(a) and bool(b) and set(a) == set(b)


@dataclass
class ArchiveEntry:
    """One independently verified candidate retained on the bounded frontier."""

    candidate_ref: str
    candidate_fingerprint: str
    objective_vector: dict[str, float] = field(default_factory=dict)
    candidate_revision: int | None = None
    direction: str = ""
    export_fingerprint: str = ""
    source: dict[str, Any] = field(default_factory=dict)
    evidence: dict[str, Any] = field(default_factory=dict)
    # Named consequence metrics (manual/unresolved placement counts,
    # participation and hosting deltas, change cost, quality comparison) so a
    # retained trade-off is inspectable without decoding the objective vector.
    metrics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_ref": self.candidate_ref,
            "candidate_fingerprint": self.candidate_fingerprint,
            "objective_vector": {
                str(key): float(value) for key, value in self.objective_vector.items()
            },
            "candidate_revision": self.candidate_revision,
            "direction": self.direction,
            "export_fingerprint": self.export_fingerprint,
            "source": dict(self.source),
            "evidence": dict(self.evidence),
            "metrics": dict(self.metrics),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ArchiveEntry":
        raw_vector = data.get("objective_vector") or {}
        vector = (
            {str(key): _as_float(value) for key, value in raw_vector.items()}
            if isinstance(raw_vector, Mapping)
            else {}
        )
        return cls(
            candidate_ref=str(data.get("candidate_ref") or ""),
            candidate_fingerprint=str(data.get("candidate_fingerprint") or ""),
            objective_vector=vector,
            candidate_revision=(
                int(data["candidate_revision"])
                if data.get("candidate_revision") is not None
                else None
            ),
            direction=str(data.get("direction") or ""),
            export_fingerprint=str(data.get("export_fingerprint") or ""),
            source=dict(data.get("source") or {}),
            evidence=dict(data.get("evidence") or {}),
            metrics=dict(data.get("metrics") or {}),
        )


class ParetoArchive:
    """Bounded set of hard-valid, pairwise non-dominated candidates.

    ``consider`` implements the archive update policy in one place: an exact
    duplicate is ignored, a dominated candidate is rejected without mutating
    the frontier, an accepted candidate removes the entries it dominates, and
    the frontier is deterministically down-selected to a bounded size. A
    dominated later attempt therefore can never make an earlier
    non-dominated candidate unavailable.
    """

    def __init__(self, *, max_size: int = DEFAULT_FRONTIER_LIMIT, entries: Iterable[ArchiveEntry] = ()) -> None:
        self.max_size = max(1, int(max_size))
        self.entries: list[ArchiveEntry] = list(entries)

    @classmethod
    def from_list(
        cls, entries: Iterable[Mapping[str, Any]], *, max_size: int = DEFAULT_FRONTIER_LIMIT
    ) -> "ParetoArchive":
        return cls(
            max_size=max_size,
            entries=[ArchiveEntry.from_dict(entry) for entry in entries if entry],
        )

    def to_list(self) -> list[dict[str, Any]]:
        return [entry.to_dict() for entry in self.entries]

    def get(self, candidate_ref: str) -> ArchiveEntry | None:
        ref = str(candidate_ref or "")
        return next((e for e in self.entries if e.candidate_ref == ref), None)

    def fingerprints(self) -> set[str]:
        return {e.candidate_fingerprint for e in self.entries if e.candidate_fingerprint}

    def refs(self) -> list[str]:
        return [entry.candidate_ref for entry in self.entries]

    def is_duplicate(self, entry: ArchiveEntry) -> bool:
        return bool(entry.candidate_fingerprint) and entry.candidate_fingerprint in self.fingerprints()

    def dominated_by(self, entry: ArchiveEntry) -> list[str]:
        """Refs of retained candidates that dominate *entry* (non-mutating)."""
        return [
            existing.candidate_ref
            for existing in self.entries
            if _comparable(existing.objective_vector, entry.objective_vector)
            and dominates(existing.objective_vector, entry.objective_vector)
        ]

    def consider(self, entry: ArchiveEntry) -> dict[str, Any]:
        """Fold one candidate into the frontier and report what changed."""
        if not entry.candidate_ref:
            return {"accepted": False, "reason": "missing_candidate_ref"}
        if self.is_duplicate(entry):
            return {
                "accepted": False,
                "duplicate": True,
                "dominated_by": [],
                "dominated_refs": [],
                "pruned_refs": [],
            }
        dominated_by = self.dominated_by(entry)
        if dominated_by:
            return {
                "accepted": False,
                "duplicate": False,
                "dominated_by": dominated_by,
                "dominated_refs": [],
                "pruned_refs": [],
            }

        dominated_refs: list[str] = []
        retained: list[ArchiveEntry] = []
        for existing in self.entries:
            if _comparable(existing.objective_vector, entry.objective_vector) and dominates(
                entry.objective_vector, existing.objective_vector
            ):
                dominated_refs.append(existing.candidate_ref)
            else:
                retained.append(existing)
        retained.append(entry)
        pruned_refs: list[str] = []
        if len(retained) > self.max_size:
            retained, pruned_refs = self._bounded(retained)
        self.entries = retained
        return {
            "accepted": True,
            "duplicate": False,
            "dominated_by": [],
            "dominated_refs": dominated_refs,
            "pruned_refs": pruned_refs,
        }

    def _bounded(self, entries: list[ArchiveEntry]) -> tuple[list[ArchiveEntry], list[str]]:
        """Bounded down-select that always keeps the newest candidate.

        The newest entry is the candidate the controller just generated and (in
        the common case) is about to explore from; dropping it silently would
        make the frontier report a candidate the epoch did not actually reach.
        """
        newest_index = len(entries) - 1
        vectors = [entry.objective_vector for entry in entries]
        try:
            chosen = representative_indices(vectors, self.max_size)
        except (KeyError, ValueError):
            chosen = list(range(min(self.max_size, len(entries))))
        if newest_index not in chosen:
            chosen = [index for index in chosen if index != chosen[-1]][: self.max_size - 1]
            chosen = sorted([*chosen, newest_index])
        retained = [entries[index] for index in chosen]
        kept_refs = {entry.candidate_ref for entry in retained}
        pruned_refs = [
            entry.candidate_ref for entry in entries if entry.candidate_ref not in kept_refs
        ]
        return retained, pruned_refs


@dataclass
class FindingDirection:
    """Controller view of one material finding and its supported direction."""

    finding_id: str
    direction: str
    category: str = ""
    code: str = ""
    operator_required: bool = False
    accepted: bool = False
    search_incomplete: bool = False
    bounded_exhausted: bool = False

    @property
    def actionable(self) -> bool:
        if self.accepted or self.operator_required:
            return False
        # A direction whose supported search reported bounded exhaustion has no
        # further automatic work *for that search*; it stays non-actionable
        # unless its coverage is explicitly still incomplete.
        return not (self.bounded_exhausted and not self.search_incomplete)

    def to_dict(self) -> dict[str, Any]:
        return {
            "finding_id": self.finding_id,
            "direction": self.direction,
            "category": self.category,
            "code": self.code,
            "operator_required": self.operator_required,
            "accepted": self.accepted,
            "search_incomplete": self.search_incomplete,
            "bounded_exhausted": self.bounded_exhausted,
            "actionable": self.actionable,
        }


def classify_finding(finding: Mapping[str, Any]) -> FindingDirection:
    """Classify one finding into a supported direction and authority scope."""
    code = str(finding.get("code") or "")
    category = str(finding.get("category") or "")
    coverage = finding.get("search_coverage") or {}
    coverage_status = str(coverage.get("status") or "") if isinstance(coverage, Mapping) else ""
    search_incomplete = coverage_status == "search_incomplete"
    bounded_exhausted = coverage_status == "bounded_search_exhausted"
    operator_required = bool(
        finding.get("requires_operator")
        or finding.get("operator_required")
        or category in OPERATOR_REQUIRED_CATEGORIES
    )
    direction = DIRECTION_BY_CODE.get(code) or DIRECTION_BY_CATEGORY.get(category) or GENERIC_DIRECTION
    return FindingDirection(
        finding_id=str(finding.get("finding_id") or ""),
        direction=direction,
        category=category,
        code=code,
        operator_required=operator_required,
        accepted=bool(finding.get("accepted")),
        search_incomplete=search_incomplete,
        bounded_exhausted=bounded_exhausted,
    )


def classify_findings(findings: Iterable[Mapping[str, Any]]) -> list[FindingDirection]:
    return [classify_finding(finding) for finding in findings]


@dataclass
class ConvergenceState:
    """Durable outer-loop state for one refinement session.

    Stored on the Stage 3 session (not a parallel side file) so it survives
    resume exactly like the candidate revision/fingerprint it describes.
    """

    epoch: int = 0
    current_baseline_ref: str = ""
    explored_directions: list[str] = field(default_factory=list)
    explored_findings: list[str] = field(default_factory=list)
    remaining_findings: list[dict[str, Any]] = field(default_factory=list)
    search_incomplete_directions: list[str] = field(default_factory=list)
    # Resolved per-finding search coverage (finding_id -> coverage dict) for the
    # current candidate revision. Lets the controller recognise a finding whose
    # configured bounded search was actually run and found nothing, instead of
    # treating every cheap ``search_incomplete`` view as untried forever. The
    # map is cleared whenever the candidate changes (a new baseline deserves a
    # fresh search).
    search_coverage: dict[str, dict[str, Any]] = field(default_factory=dict)
    frontier_refs: list[str] = field(default_factory=list)
    no_improvement_epochs: int = 0
    last_direction: str = ""
    terminal_reason: str = ""
    terminal_detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "epoch": self.epoch,
            "current_baseline_ref": self.current_baseline_ref,
            "explored_directions": list(self.explored_directions),
            "explored_findings": list(self.explored_findings),
            "remaining_findings": [dict(finding) for finding in self.remaining_findings],
            "search_incomplete_directions": list(self.search_incomplete_directions),
            "search_coverage": {str(k): dict(v) for k, v in self.search_coverage.items()},
            "frontier_refs": list(self.frontier_refs),
            "no_improvement_epochs": self.no_improvement_epochs,
            "last_direction": self.last_direction,
            "terminal_reason": self.terminal_reason,
            "terminal_detail": self.terminal_detail,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | None) -> "ConvergenceState":
        if not data:
            return cls()
        return cls(
            epoch=int(data.get("epoch") or 0),
            current_baseline_ref=str(data.get("current_baseline_ref") or ""),
            explored_directions=[str(item) for item in (data.get("explored_directions") or [])],
            explored_findings=[str(item) for item in (data.get("explored_findings") or [])],
            remaining_findings=[
                dict(item) for item in (data.get("remaining_findings") or []) if item
            ],
            search_incomplete_directions=[
                str(item) for item in (data.get("search_incomplete_directions") or [])
            ],
            search_coverage={
                str(key): dict(value)
                for key, value in (data.get("search_coverage") or {}).items()
                if isinstance(value, Mapping)
            },
            frontier_refs=[str(item) for item in (data.get("frontier_refs") or [])],
            no_improvement_epochs=int(data.get("no_improvement_epochs") or 0),
            last_direction=str(data.get("last_direction") or ""),
            terminal_reason=str(data.get("terminal_reason") or ""),
            terminal_detail=str(data.get("terminal_detail") or ""),
        )

    def is_terminal(self) -> bool:
        return bool(self.terminal_reason)


@dataclass
class EpochOutcome:
    epoch: int
    direction: str
    accepted_refs: list[str]
    rejected_refs: list[str]
    improved: bool
    terminal_reason: str
    terminal_detail: str
    archive_size: int
    frontier_refs: list[str]


class ConvergenceController:
    """Owns frontier bookkeeping, exploration order and terminal reasons.

    It never generates or verifies a candidate: the caller passes in the
    categorized findings, the verified archive entries produced in one epoch,
    and the supported search coverage it observed. The controller then decides
    what is still actionable, updates the bounded frontier, and stops
    automatic exploration only on an explicit convergence criterion.
    """

    def __init__(
        self,
        state: ConvergenceState | None = None,
        archive: ParetoArchive | None = None,
        *,
        max_epochs: int = DEFAULT_MAX_EPOCHS,
        max_no_improvement_epochs: int = DEFAULT_MAX_NO_IMPROVEMENT_EPOCHS,
        frontier_limit: int = DEFAULT_FRONTIER_LIMIT,
    ) -> None:
        self.state = state or ConvergenceState()
        self.archive = archive or ParetoArchive(max_size=frontier_limit)
        self.max_epochs = max(1, int(max_epochs))
        self.max_no_improvement_epochs = max(1, int(max_no_improvement_epochs))

    # -- exploration order -------------------------------------------------

    def effective_findings(
        self, directions: Sequence[FindingDirection]
    ) -> list[FindingDirection]:
        """Apply recorded resolved coverage to freshly classified findings.

        A cheap finding view always reports ``search_incomplete`` because its
        bounded search has not run yet. Once this controller actually ran the
        configured bounded search for a finding on the current candidate and it
        produced no option, the recorded ``bounded_search_exhausted`` evidence
        must override that cheap view -- otherwise the loop would re-search an
        exhausted finding forever and never report truthful convergence.
        """
        effective: list[FindingDirection] = []
        for direction in directions:
            recorded = self.state.search_coverage.get(direction.finding_id) or {}
            if str(recorded.get("status") or "") == "bounded_search_exhausted":
                direction.bounded_exhausted = True
                direction.search_incomplete = False
            effective.append(direction)
        return effective

    def next_direction(
        self, directions: Sequence[FindingDirection], *, force_finding_id: str | None = None
    ) -> FindingDirection | None:
        """Choose the next supported direction to explore, or ``None``.

        Priority: an explicitly forced finding, then an actionable finding
        never explored before, then an actionable finding whose supported
        search is still incomplete (``search_incomplete`` must drive further
        exploration rather than false convergence). A terminal controller
        returns ``None``.
        """
        directions = self.effective_findings(directions)
        if force_finding_id:
            forced = next(
                (d for d in directions if d.finding_id == force_finding_id and d.actionable),
                None,
            )
            if forced is not None:
                return forced
        if self.state.is_terminal():
            return None
        actionable = [d for d in directions if d.actionable]
        explored_findings = set(self.state.explored_findings)
        for direction in actionable:
            if direction.finding_id not in explored_findings:
                return direction
        for direction in actionable:
            if direction.search_incomplete:
                return direction
        return None

    def record_epoch(
        self,
        *,
        direction: FindingDirection | None,
        generated: Sequence[ArchiveEntry],
        findings: Sequence[FindingDirection],
        search_incomplete_directions: Iterable[str] = (),
        resolved_coverage: Mapping[str, dict[str, Any]] | None = None,
        candidate_changed: bool = False,
        exploration_exhausted: bool = False,
        max_epochs: int | None = None,
    ) -> EpochOutcome:
        """Fold one epoch's verified candidates and fresh findings into state."""
        accepted_refs: list[str] = []
        rejected_refs: list[str] = []
        for entry in generated:
            outcome = self.archive.consider(entry)
            if outcome.get("accepted"):
                accepted_refs.append(entry.candidate_ref)
            else:
                rejected_refs.append(entry.candidate_ref)

        # A committed candidate is a new baseline: its recorded coverage and
        # explored-finding set no longer describe it, so both are reset before
        # this epoch's resolved coverage is folded in.
        if candidate_changed:
            self.state.search_coverage = {}
            self.state.explored_findings = []
        if resolved_coverage:
            self.state.search_coverage.update(
                {str(key): dict(value) for key, value in resolved_coverage.items()}
            )

        self.state.epoch += 1
        direction_label = direction.direction if direction is not None else ""
        self.state.last_direction = direction_label
        if direction_label and direction_label not in self.state.explored_directions:
            self.state.explored_directions.append(direction_label)
        if direction is not None and direction.finding_id and direction.finding_id not in self.state.explored_findings:
            self.state.explored_findings.append(direction.finding_id)
        effective = self.effective_findings(findings)
        self.state.remaining_findings = [d.to_dict() for d in effective]
        incomplete = {str(item) for item in search_incomplete_directions}
        incomplete |= {
            d.direction for d in effective if d.actionable and d.search_incomplete
        }
        self.state.search_incomplete_directions = sorted(incomplete)
        self.state.frontier_refs = self.archive.refs()
        improved = bool(accepted_refs)
        self.state.no_improvement_epochs = 0 if improved else self.state.no_improvement_epochs + 1
        self.state.current_baseline_ref = self.state.frontier_refs[-1] if improved else self.state.current_baseline_ref

        limit = max_epochs if max_epochs is not None else self.max_epochs
        self.state.terminal_reason, self.state.terminal_detail = self._terminal_for(
            effective, limit=limit, exploration_exhausted=exploration_exhausted
        )
        return EpochOutcome(
            epoch=self.state.epoch,
            direction=direction_label,
            accepted_refs=accepted_refs,
            rejected_refs=rejected_refs,
            improved=improved,
            terminal_reason=self.state.terminal_reason,
            terminal_detail=self.state.terminal_detail,
            archive_size=len(self.archive.entries),
            frontier_refs=list(self.state.frontier_refs),
        )

    # -- convergence criteria ---------------------------------------------

    def _terminal_for(
        self,
        findings: Sequence[FindingDirection],
        *,
        limit: int,
        exploration_exhausted: bool = False,
    ) -> tuple[str, str]:
        actionable = [d for d in findings if d.actionable]
        operator_required = [d for d in findings if d.operator_required and not d.accepted]
        remaining = [d for d in findings if not d.accepted]
        if not remaining:
            return TERMINAL_PASS, describe_terminal(TERMINAL_PASS)
        if not actionable and operator_required:
            return TERMINAL_OPERATOR_REQUIRED, describe_terminal(TERMINAL_OPERATOR_REQUIRED)
        if not actionable:
            # Findings remain, but none has automatic work: every remaining
            # supported direction reported bounded exhaustion.
            return TERMINAL_BOUNDED_SEARCH_EXHAUSTED, describe_terminal(
                TERMINAL_BOUNDED_SEARCH_EXHAUSTED
            )
        # Untried supported dimensions remain: report search_incomplete, never
        # a false convergence, while the configured budget lasts.
        if self.state.search_incomplete_directions and self.state.epoch < limit:
            return TERMINAL_NONE, ""
        if exploration_exhausted:
            # Every supported direction was tried on this candidate and none
            # produced a new useful non-dominated candidate: a bounded plateau.
            return TERMINAL_PARETO_STABLE, describe_terminal(TERMINAL_PARETO_STABLE)
        if actionable and all(d.bounded_exhausted for d in actionable):
            return TERMINAL_BOUNDED_SEARCH_EXHAUSTED, describe_terminal(
                TERMINAL_BOUNDED_SEARCH_EXHAUSTED
            )
        if self.state.epoch >= limit:
            return TERMINAL_BOUNDED_BUDGET, describe_terminal(TERMINAL_BOUNDED_BUDGET)
        if self.state.no_improvement_epochs >= self.max_no_improvement_epochs:
            return TERMINAL_PARETO_STABLE, describe_terminal(TERMINAL_PARETO_STABLE)
        return TERMINAL_NONE, ""


def describe_terminal(reason: str) -> str:
    """Truthful operator-facing wording for one terminal reason.

    No branch claims global optimality: every non-PASS outcome is explicitly
    relative to the explored repair/search neighborhoods and configured
    budgets.
    """
    if reason == TERMINAL_PASS:
        return "No remaining material finding for the current candidate."
    if reason == TERMINAL_OPERATOR_REQUIRED:
        return (
            "Automatic refinement stopped: at least one remaining finding requires "
            "operator policy/waiver/input rather than another automatic search."
        )
    if reason == TERMINAL_BOUNDED_SEARCH_EXHAUSTED:
        return (
            "Pareto-stable with respect to the explored repair/search neighborhoods and "
            "configured budgets: every remaining supported direction reported bounded "
            "exhaustion. Zero options from a bounded search is not proof of infeasibility."
        )
    if reason == TERMINAL_BOUNDED_BUDGET:
        return (
            "Stopped at the configured bounded epoch budget; this is bounded convergence, "
            "not proof of global Pareto optimality."
        )
    if reason == TERMINAL_PARETO_STABLE:
        return (
            "Pareto-stable with respect to the explored repair/search neighborhoods and "
            "configured budgets: a complete controller epoch produced no new useful "
            "non-dominated candidate."
        )
    return ""


def archive_entry_from_option(
    option: Mapping[str, Any],
    *,
    direction: str,
    candidate_ref: str,
    candidate_fingerprint: str = "",
    candidate_revision: int | None = None,
    export_fingerprint: str = "",
    evidence: Mapping[str, Any] | None = None,
) -> ArchiveEntry:
    """Build an archive entry from a measured, verified repair/search option.

    ``option["objectives"]`` is produced by the shared maintenance measurement
    boundary (``season_maintenance._annotate_pareto``), so the entry vector is
    the same one the repair report marks non-dominated. The caller supplies the
    stable ref/fingerprint because candidate identity is a lifecycle contract
    owned by the Stage 3 session.
    """
    raw_vector = option.get("objectives") or {}
    vector = (
        {str(key): _as_float(value) for key, value in raw_vector.items()}
        if isinstance(raw_vector, Mapping)
        else {}
    )
    metrics = {
        "hard_verification_ok": vector.get("hard_violations", 0.0) == 0.0,
        "hard_violations": vector.get("hard_violations"),
        "manual_placement_count": vector.get("manual_placements"),
        "unresolved_placement_count": vector.get("unresolved_placement_obligations"),
        "participation_deviation_count": vector.get("participation_deviations"),
        "avoidable_participation_count": vector.get("avoidable_participation_deviations"),
        "hosting_imbalance_count": vector.get("hosting_balance_imbalances"),
        "unresolved_hosting_obligation_count": vector.get("unresolved_hosting_obligations"),
        "host_confirmation_dependency_count": vector.get("host_confirmation_dependencies"),
        "change_cost_changed_tournament_count": vector.get("changed_tournament_count"),
        "quality_vs_current": option.get("quality_vs_current"),
        "travel": option.get("travel"),
    }
    return ArchiveEntry(
        candidate_ref=str(candidate_ref),
        candidate_fingerprint=str(candidate_fingerprint),
        objective_vector=vector,
        candidate_revision=candidate_revision,
        direction=str(direction),
        export_fingerprint=str(export_fingerprint),
        source={
            "option_id": str(option.get("option_id") or ""),
            "finding_id": str(option.get("finding_id") or ""),
            "family": str(option.get("family") or ""),
        },
        evidence=dict(evidence or {}),
        metrics=metrics,
    )


def objective_vectors_equal(
    a: Mapping[str, float], b: Mapping[str, float], tol: float = 1e-9
) -> bool:
    """Same tolerance-aware equality used by the shared Pareto arithmetic."""
    return _comparable(a, b) and vectors_equal(a, b, tol)
