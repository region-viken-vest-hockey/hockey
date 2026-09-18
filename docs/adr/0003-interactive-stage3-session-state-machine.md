# ADR 0003: Interactive Stage 3 is one persisted session state machine

- **Status:** Accepted
- **Date:** 2026-09-15
- **Decision owner:** RVV hockey project
- **Related ADR:** ADR 0001, ADR 0002

## Context

Stage 3 started as a mostly batch-oriented pipeline stage but now behaves like a resumable interactive workflow: build a candidate, pause for a decision, persist, resume in a new process, mutate/repair, optionally search, compare/adopt, verify, and continue to Stage 4.

That workflow was implemented mainly by branching and fall-through logic in the CLI adapter plus several side files (`stage3_interactive_state.json`, `shared_host_decision_state.json`, `arena_conflict_decision_state.json`, the ordinary Stage 3 checkpoint and the CP-SAT candidate cache). As a result there were multiple overlapping representations of "where Stage 3 is" and "which candidate is current". Every new capability had to decide which of those to read, preserve, rewrite, invalidate or clear, and repeated production defects came from exactly the expected failure modes: resume rebuilding instead of continuing, a local repair persisted in one place but not another, a decision context referring to candidate A while the checkpoint held candidate B, a run-scoped pre-plan decision forgotten by a later interaction, local repair triggering global replanning, and Stage 4 verifying something different from what the harness reviewed.

## Decision

Interactive Stage 3 has one authoritative, typed, versioned application object: `Stage3Session` (`application/stage3_session.py`), persisted through one repository API `Stage3SessionStore` (`application/stage3_session_store.py`) and mutated only by explicit transitions applied by `Stage3Controller` (`application/stage3_controller.py`).

The session owns:

- run scoping (`run_id`);
- candidate identity (`candidate_revision`, `candidate_fingerprint`, `candidate_source`, selected candidate);
- run-scoped decisions (for example shared/joint-club hosting choices) versus candidate-scoped decisions (arena/local repairs);
- the currently pending decision and its exact scope;
- concise transition provenance (`revision N -> N+1`);
- search/attempt metadata;
- the finalized revision/fingerprint Stage 4 must consume.

Transitions use a stable vocabulary: `create_baseline`, `assign_shared_host`, `resolve_placement_conflict`, `apply_repair`, `run_search`, `select_candidate`, `keep_baseline`, `request_operator`, `finalize_stage3`. Domain repair option ids vary underneath `apply_repair`; they do not require new lifecycle machinery.

A transition validates the submitted action against the session's exact pending scope and the expected candidate revision/fingerprint **before** any accepted-action provenance is recorded, invokes a deterministic domain/application capability, advances the revision only when the candidate actually changed, and returns the next decision context or terminal result. A stale action targeting an old revision/fingerprint is rejected, not replayed.

A candidate a search/planner just produced is bound as the session's current candidate (`Stage3SessionStore.bind_candidate`) before any candidate-scoped context for it is emitted; binding retains the replaced candidate as the session's baseline (`keep_baseline`). This keeps a pending context and its semantic guards aligned on one exact candidate revision instead of comparing a new attempt against a previous attempt's baseline, and it makes revision changes explicit (`N -> N+1`). No-action `--resume-from 3` renders the session's single `pending_decision` and pauses; it does not enumerate capabilities or fall through to re-run Stage 3.

A candidate-scoped `request_operator` is an explicit awaiting-operator transition: it records the question/rationale and pauses on the exact current candidate revision/fingerprint. It never restores the baseline, rewrites the checkpoint or finalizes Stage 3, so escalating a finding cannot silently change which candidate would be exported. The operator answer continues from the same revision, and a hard-valid current attempt stays adoptable through `select_candidate` (`apply_candidate`) even when the pending context is a local/manual repair context where `keep_baseline` would restore the previous attempt.

The CLI/harness adapter parses `DecisionAction`, renders `DecisionContext`/diagnostics, maps typed results to exit codes, and chooses the stage entry point. It does not encode whether an arena answer reruns the planner, how a repair mutates session state, or which side file to clear. The deterministic domain operations a transition invokes live in one adapter (`cli/pipeline_orchestrator/stage3_capabilities.InteractiveStage3Capabilities`: shared-host recording, arena-conflict application and next-collision enumeration, local repair application, candidate selection, baseline retention), so `run_command_interactive.py` is transport/orchestration only.

`rvv-miniputt stage3 session` exposes one compact machine-readable view of the session for debugging resume problems.

## Consequences

- A single `run_id` has one authoritative session; a genuinely new run starts fresh and cannot inherit a superseded run's interactive state.
- Local repair operates on exactly one revision and cannot implicitly trigger a full Stage 3 replan; global search is an explicit transition.
- Run-scoped decisions remain valid across later candidate revisions where their facts remain valid; candidate-scoped decisions are invalidated by a deliberate candidate transition rather than silently replayed.
- Finalization records the exact revision/fingerprint Stage 4 must consume, and the store can assert that the exported candidate matches it.
- A candidate-scoped pending decision always refers to the exact candidate revision the capability will mutate; a pre-existing defect in the current attempt is never attributed to a local answer, while a mutation that really worsens the canonical semantic guard is still rejected.
- Inspecting or resuming a run without a decision action is idempotent: it re-renders the persisted pending context for any capability and never rebuilds the candidate.
- During migration, `Stage3SessionStore` migrates the existing side files into a session on first load and (while a decision is pending) keeps writing them as non-authoritative compatibility mirrors, so existing work directories and external observers keep working. The store now owns shared-host and arena sub-decision state too, in separate run-scoped/candidate-scoped buckets; the CLI's `interactive_state_io` helpers delegate to it instead of parsing the files, and finalization removes every mirror. New capabilities must not add a feature-specific `*_state.json` authority; architecture tests block a new authoritative side-state file and block any module outside the store/facade from naming the legacy files.
- The lifecycle boundary contains no hockey legality and no repair/search selection; those remain deterministic domain/application capabilities invoked by the session (ADR 0002 is unchanged).

## Non-goals

- rewriting `SeasonPlanner` or replacing the scheduling algorithms;
- moving hard rules or verifier authority into prompts/LLMs;
- inventing a generic workflow framework for the whole application;
- changing hockey policy while restructuring lifecycle state.
