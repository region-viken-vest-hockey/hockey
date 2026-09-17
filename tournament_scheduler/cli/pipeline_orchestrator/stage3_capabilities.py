"""Deterministic domain capabilities behind the interactive Stage 3 transitions.

The explicit :class:`~tournament_scheduler.application.stage3_controller.Stage3Controller`
owns *lifecycle*: which transition is legal from the current session state,
whether a submitted action targets the session's exact candidate
revision/fingerprint, how a successful transition advances the revision and
what is persisted. This adapter supplies the deterministic hockey/domain
operations those transitions invoke and reports what happened as a
:class:`~tournament_scheduler.application.stage3_controller.Stage3CapabilityResult`.

Keeping these bodies here (instead of in ``run_command_interactive.py``) is what
lets the CLI stay a transport: it parses the action, picks the persisted session,
hands it to the controller and renders the returned context/exit code. It no
longer encodes whether an arena answer replans the season, how a local repair
mutates the checkpoint, or which side file to clear.

This module owns no lifecycle semantics: it never advances a revision, never
records provenance and never clears a side file. It only performs one domain
operation and, when the operation leaves a follow-up sub-decision pending,
returns that context for the controller to bind to the session.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable

from ...application.decisions import DecisionAction
from ...application.stage3_controller import Stage3CapabilityResult
from ...application.stage3_session import Stage3Session
from ...hosting_responsibility import (
    RESPONSIBILITY_TRANSFER_CODE,
    unexplained_responsibility_transfers,
)


class InteractiveStage3Capabilities:
    """Domain operations for one interactive Stage 3 run directory."""

    def __init__(
        self,
        state: "Any",
        args: "Any",
        log_fn: Callable[[str], None],
        *,
        run_id: str = "",
        problem_fn: Callable[..., Any] | None = None,
    ) -> None:
        self.state = state
        self.args = args
        self.log_fn = log_fn
        self.run_id = run_id
        self._problem_fn = problem_fn
        self._resolved: tuple[dict[str, Any], dict[str, Any], Any, Any] | None = None
        self._last_responsibility_findings: list[dict[str, Any]] = []

    # -- dispatch ---------------------------------------------------------

    def apply(self, transition: str, session: Stage3Session, action: DecisionAction) -> Stage3CapabilityResult:
        handler = getattr(self, f"_apply_{transition}", None)
        if handler is None:
            return Stage3CapabilityResult(ok=False, reason=f"unsupported_transition:{transition}")
        return handler(session, action)

    # -- responsibility-preserving candidate guard -----------------------

    def _responsibility_guard(
        self, session: Stage3Session, candidate: Any, problem: dict[str, Any] | None = None
    ) -> str:
        """Reject a candidate change that transfers hosting responsibility.

        This is the common Stage 3 boundary hook: whichever deterministic
        capability produced *candidate*, the repository-owned semantic owner
        (``hosting_responsibility``) decides whether the change moved hosting
        burden onto a club the fairness model did not assign it to. The
        lifecycle controller stays unaware of hockey semantics; it only
        accepts or rejects the capability's typed result.
        """
        self._last_responsibility_findings = []
        if not candidate or session.candidate is None:
            return ""
        try:
            from ...planning_contract import extract_candidate

            before = extract_candidate(session.candidate)
            after = extract_candidate(candidate)
        except (ValueError, KeyError):
            return ""
        problem = problem if problem is not None else self._problem()
        if not problem:
            # Without the registration/fairness facts there is no canonical
            # target to compare against; leave the candidate to the ordinary
            # hard verifier rather than inventing a responsibility rule.
            return ""
        findings = unexplained_responsibility_transfers(before, after, problem)
        if not findings:
            return ""
        self._last_responsibility_findings = findings
        return RESPONSIBILITY_TRANSFER_CODE

    def _responsibility_rejection(self, reason: str) -> Stage3CapabilityResult:
        return Stage3CapabilityResult(
            ok=False,
            reason=reason,
            data={"responsibility_transfers": list(self._last_responsibility_findings)},
        )

    # -- resolved planning context ---------------------------------------

    def resolved_problem(self) -> tuple[dict[str, Any], dict[str, Any], Any, Any]:
        """Load the effective config/scraping/date window for this work dir.

        A local repair or arena answer is a post-plan operation, so it loads
        the same effective config every other Stage 3 call site uses rather
        than depending on a just-run Stage 1/2 result.
        """
        if self._resolved is not None:
            return self._resolved
        from ...pipeline.stage1_config import load_effective_config
        from ...pipeline.state import StageName

        cfg: dict[str, Any] = {}
        try:
            cfg = load_effective_config(self.state, input_path=getattr(self.args, "input", None)) or {}
        except Exception as exc:
            self.log_fn(f"stage3: kunne ikke laste effektiv konfigurasjon: {exc}")
        if not cfg.get("start_date") or not cfg.get("end_date"):
            checkpoint_cfg = self.state.read_stage(StageName.CONFIG) or {}
            cfg = {**checkpoint_cfg, **cfg}
        scraping = self.state.read_stage(StageName.SCRAPING) or {}
        start = datetime.strptime(cfg["start_date"], "%Y-%m-%d")
        end = datetime.strptime(cfg["end_date"], "%Y-%m-%d")
        self._resolved = (cfg, scraping, start, end)
        return self._resolved

    def _problem(self) -> "dict[str, Any]":
        try:
            cfg, scraping, start, end = self.resolved_problem()
        except Exception as exc:
            self.log_fn(f"stage3: kunne ikke beregne planleggingsproblem for reparasjon: {exc}")
            return {}
        return self._mid_planning_decision_problem(cfg, scraping, start, end) or {}

    def _mid_planning_decision_problem(self, cfg: Any, scraping: Any, start: Any, end: Any) -> Any:
        if self._problem_fn is not None:
            return self._problem_fn(cfg, scraping, start, end, self.state.work_dir)
        from .verification import _mid_planning_decision_problem

        return _mid_planning_decision_problem(cfg, scraping, start, end, self.state.work_dir)

    # -- sub-decisions ----------------------------------------------------

    def _apply_assign_shared_host(self, session: Stage3Session, action: DecisionAction) -> Stage3CapabilityResult:
        from ...shared_host_decision import shared_host_decision_record

        marker = session.pending_marker()
        decisions = list(session.shared_host_decisions)
        unresolved = list(session.shared_host_unresolved)
        decisions.append(
            shared_host_decision_record(
                str(marker.get("registration", "")),
                str(marker.get("age_group", "")),
                str(action.arguments.get("chosen_club", "")),
                str(action.rationale or ""),
                decided_by="harness",
                decided_at=datetime.now(timezone.utc).isoformat(),
            )
        )
        return Stage3CapabilityResult(ok=True, shared_host_decisions=decisions, unresolved=unresolved)

    def _apply_resolve_placement_conflict(
        self, session: Stage3Session, action: DecisionAction
    ) -> Stage3CapabilityResult:
        from ...pipeline.state import StageName, StageStatus
        from ...stage3_decision import invalidate_stale_candidate_checkpoint_keys
        from .arena_conflict_decisions import (
            _apply_arena_conflict_decision,
            _arena_conflict_context,
            _candidate_dict,
            _pending_arena_collisions,
        )

        checkpoint = dict(self.state.read_stage(StageName.PLANNING) or {})
        candidate = _candidate_dict(checkpoint)
        if candidate is None:
            return Stage3CapabilityResult(ok=False, reason="no_stage3_candidate")

        # The pending arena context is bound to the exact candidate it was
        # computed from. Reject a stale answer before mutating the checkpoint.
        from .arena_conflict_decisions import _candidate_fingerprint

        expected_fingerprint = str(
            (((session.pending_decision or {}).get("context") or {}).get("facts") or {}).get("candidate_fingerprint")
            or ""
        )
        if expected_fingerprint and _candidate_fingerprint(candidate) != expected_fingerprint:
            return Stage3CapabilityResult(ok=False, reason="stale_candidate_fingerprint")

        marker = session.pending_marker()
        decisions = list(session.arena_decisions)
        unresolved = list(session.arena_unresolved)
        candidate_changed = False

        if action.action_id == "resolve_arena_conflict":
            from ...arena_conflict_decision import arena_conflict_decision_record

            facts = dict(((session.pending_decision or {}).get("context") or {}).get("facts") or {})
            side_ids = {str(side.get("tournament_id", "")) for side in facts.get("sides", [])}
            keep = str(action.arguments.get("keep_tournament_id", ""))
            manual_id = next(iter(side_ids - {keep}), "")
            decisions.append(
                arena_conflict_decision_record(
                    facts,
                    keep,
                    manual_id,
                    str(action.rationale or ""),
                    decided_by="harness",
                    decided_at=datetime.now(timezone.utc).isoformat(),
                )
            )
            _apply_arena_conflict_decision(candidate, keep, manual_id, str(action.rationale or ""))
            candidate_changed = True
        else:
            unresolved.append({"key": marker.get("key")})

        try:
            invalidate_stale_candidate_checkpoint_keys(checkpoint)
        except Exception:
            pass
        if "plan" not in checkpoint:
            checkpoint = {"plan": candidate}
        guard_reason = self._responsibility_guard(session, checkpoint)
        if guard_reason:
            return self._responsibility_rejection(guard_reason)
        self.state.write_stage(StageName.PLANNING, checkpoint, status=StageStatus.DONE)

        framework = str(self.state.work_dir)
        problem = self._problem() or {}
        ice_time = problem.get("ice_time_minutes") or problem.get("round_length_minutes") or {}
        pending = _pending_arena_collisions(candidate, decisions, unresolved, ice_time)
        common = dict(
            arena_decisions=decisions,
            unresolved=unresolved,
            candidate=checkpoint,
            candidate_source="arena_conflict_resolved",
            candidate_changed=candidate_changed,
        )
        if pending:
            context, next_marker = _arena_conflict_context(self.run_id, pending[0])
            return Stage3CapabilityResult(
                ok=True,
                **common,
                next_context=context.to_dict(),
                next_capability="arena_conflict_resolution",
                next_marker=next_marker,
                data={"work_dir": framework},
            )
        return Stage3CapabilityResult(ok=True, **common)

    def _apply_request_operator(self, session: Stage3Session, action: DecisionAction) -> Stage3CapabilityResult:
        capability = str((session.pending_decision or {}).get("capability") or "")
        marker = session.pending_marker()
        if capability == "shared_host_assignment":
            unresolved = list(session.shared_host_unresolved) + [dict(marker)]
            return Stage3CapabilityResult(ok=True, unresolved=unresolved)
        if capability == "arena_conflict_resolution":
            unresolved = list(session.arena_unresolved) + [{"key": marker.get("key")}]
            return Stage3CapabilityResult(ok=True, unresolved=unresolved)
        # Candidate-scoped operator request: keep the persisted best baseline
        # and let the session finalize it.
        return self._apply_keep_baseline(session, action)

    # -- candidate transitions -------------------------------------------

    def _apply_apply_repair(self, session: Stage3Session, action: DecisionAction) -> Stage3CapabilityResult:
        from ...local_repair_options import apply_local_repair_option
        from ...planning_contract import extract_candidate
        from ...pipeline.state import StageName, StageStatus
        from ...stage3_decision import invalidate_stale_candidate_checkpoint_keys
        from ...application.stage3_session_store import fingerprint_plan

        best_plan = session.candidate
        if best_plan is None:
            return Stage3CapabilityResult(ok=False, reason="no_stage3_candidate")
        problem = self._problem()
        outcome = apply_local_repair_option(
            extract_candidate(best_plan),
            problem,
            option_id=str(action.arguments.get("option_id") or ""),
            expected_fingerprint=str(action.arguments.get("candidate_fingerprint") or ""),
            run_id=self.run_id,
        )
        if not outcome.get("ok"):
            return Stage3CapabilityResult(ok=False, reason=str(outcome.get("reason") or "repair_rejected"))
        guard_reason = self._responsibility_guard(session, outcome.get("candidate"), problem)
        if guard_reason:
            return self._responsibility_rejection(guard_reason)

        checkpoint = dict(self.state.read_stage(StageName.PLANNING) or {})
        invalidate_stale_candidate_checkpoint_keys(checkpoint)
        checkpoint["plan"] = outcome["candidate"]
        family = outcome.get("family")
        result_payload = {key: value for key, value in outcome.items() if key != "candidate"}
        default_source = "host_team_missing_repair_applied"
        result_key = "host_team_missing_repair_result"
        if family == "underfilled_roster":
            default_source = "underfilled_roster_repair_applied"
            result_key = "underfilled_roster_repair_result"
        elif family == "host_placement":
            default_source = "host_placement_repair_applied"
            result_key = "host_placement_repair_result"
        elif family == "hosting_balance":
            default_source = "hosting_balance_repair_applied"
            result_key = "hosting_balance_repair_result"
        elif family == "participation_deviation":
            default_source = "participation_deviation_repair_applied"
            result_key = "participation_deviation_repair_result"
        elif family == "search_neighborhood":
            default_source = "search_neighborhood_repair_applied"
            result_key = "search_neighborhood_repair_result"
        checkpoint["source"] = default_source
        checkpoint[result_key] = result_payload
        self.state.write_stage(StageName.PLANNING, checkpoint, status=StageStatus.DONE)
        return Stage3CapabilityResult(
            ok=True,
            candidate=checkpoint,
            candidate_fingerprint=fingerprint_plan(checkpoint),
            candidate_source=default_source,
            candidate_changed=True,
            final=True,
            data={"family": family},
        )

    def _apply_select_candidate(self, session: Stage3Session, action: DecisionAction) -> Stage3CapabilityResult:
        from ...application.stage3_session import candidate_content_fingerprint
        from ...application.stage3_session_store import extract_candidate_body, fingerprint_plan
        from ...pipeline.state import StageName, StageStatus
        from ...stage3_decision import invalidate_stale_candidate_checkpoint_keys

        candidate_ref = str(action.arguments.get("candidate_ref") or "")
        pending_candidates = list((session.pending_decision or {}).get("candidates") or [])
        checkpoint = dict(self.state.read_stage(StageName.PLANNING) or {})
        chosen_body: dict[str, Any] | None = None
        source = ""

        if candidate_ref.startswith("stage3_cp_sat:"):
            from .stage3_cpsat_cache import resolve_candidate_ref

            entry = resolve_candidate_ref(self.state, self.run_id, candidate_ref)
            if entry is not None:
                chosen_body = extract_candidate_body(entry["candidate"])
                source = "stage3_cp_sat_shadow_applied"
        elif pending_candidates:
            match = next(
                (entry for entry in pending_candidates if entry.get("candidate_ref") == candidate_ref),
                None,
            )
            if match is not None:
                chosen_body = extract_candidate_body(match["candidate"])

        # Only rewrite the checkpoint when the chosen candidate is not already
        # the persisted one: on the single-optimizer path the checkpoint
        # already holds the pending attempt, while the Pareto/CP-SAT paths
        # leave the baseline on disk and must be rewritten explicitly.
        rewrite = chosen_body is not None and fingerprint_plan(checkpoint) != candidate_content_fingerprint(
            chosen_body
        )
        if rewrite:
            invalidate_stale_candidate_checkpoint_keys(checkpoint)
            checkpoint["plan"] = chosen_body
            if source:
                checkpoint["source"] = source
        else:
            checkpoint = dict(self.state.read_stage(StageName.PLANNING) or checkpoint)

        guard_reason = self._responsibility_guard(session, checkpoint)
        if guard_reason:
            return self._responsibility_rejection(guard_reason)
        if rewrite:
            self.state.write_stage(StageName.PLANNING, checkpoint, status=StageStatus.DONE)

        fingerprint = fingerprint_plan(checkpoint)
        return Stage3CapabilityResult(
            ok=True,
            candidate=checkpoint,
            candidate_fingerprint=fingerprint,
            candidate_source=source,
            candidate_changed=bool(fingerprint) and fingerprint != session.candidate_fingerprint,
            final=True,
        )

    def _apply_keep_baseline(self, session: Stage3Session, action: DecisionAction) -> Stage3CapabilityResult:
        from ...application.stage3_session_store import fingerprint_plan
        from ...pipeline.state import StageName, StageStatus

        # ``keep_baseline`` restores the adopted/best candidate the current
        # attempt was compared against, not the current attempt itself.
        restored = session.baseline_candidate
        if restored is not None:
            best_plan = dict(restored)
            self.state.write_stage(StageName.PLANNING, best_plan, status=StageStatus.DONE)
            fingerprint = fingerprint_plan(best_plan)
            return Stage3CapabilityResult(
                ok=True,
                candidate=best_plan,
                candidate_fingerprint=fingerprint,
                candidate_source="baseline",
                candidate_changed=bool(fingerprint) and fingerprint != session.candidate_fingerprint,
                final=True,
            )

        best_plan = session.candidate
        if best_plan is None:
            best_plan = dict(self.state.read_stage(StageName.PLANNING) or {})
        else:
            best_plan = dict(best_plan)
            self.state.write_stage(StageName.PLANNING, best_plan, status=StageStatus.DONE)
        return Stage3CapabilityResult(
            ok=True,
            candidate=best_plan,
            candidate_fingerprint=fingerprint_plan(best_plan),
            candidate_source=str(session.candidate_source or ""),
            candidate_changed=False,
            final=True,
        )
