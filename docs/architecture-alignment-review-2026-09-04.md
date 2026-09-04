# Architecture alignment review — LLM-directed RVV workflow

- **Date:** 2026-09-04
- **Scope:** RVV miniputt orchestration, Stage 2 recovery, Stage 3 planning, LLM judgment, and harness adapters
- **Related:** ADR 0001, ADR 0002, issues #44 and #257

## Executive conclusion

The repo is moving toward the right architecture, but implementation ownership is still mixed.

The useful foundation is already present:

- `scripts/rvv-miniputt` is a thin canonical launcher.
- Issue #44 established a typed application/use-case direction and thin-adapter principle.
- Stage 2 produces deterministic source evidence and recovery state.
- Issue #257 added normalized Stage 3 problem/candidate contracts, deterministic verification/scoring, and generic optimization experiments.
- `tournament_scheduler.llm_judge` already provides a backend-neutral LLM transport abstraction.
- `.agents/skills/rvv/` is intended to be the shared RVV policy/runbook.

The main misalignment is that **soft policy is still encoded in several deterministic Python modules and duplicated in harness-specific runbooks**. In multiple places the LLM is currently asked to approve or summarize a decision after Python has already selected priorities, thresholds, repairs, or tradeoffs. The target is the reverse: deterministic code should expose facts, constraints, metrics, available actions, and generic search mechanisms; the LLM/agent should choose the soft action/tradeoff within those guardrails.

This review therefore recommends an incremental ownership migration, not another rewrite.

## Target ownership model

Every meaningful decision should fit one of these categories.

| Category | Owner | Examples |
|---|---|---|
| Facts / I/O | Deterministic repo code | workbook parsing, registrations, calendar events, club/arena facts, checkpoint persistence |
| Hard rules / safety gates | Deterministic repo code | registered teams only, no impossible overlaps, schema validity, explicit operator restrictions, privacy/publication confirmation |
| Measurement | Deterministic repo code | opponent diversity, turnaround gaps, hosting deviation, travel, source-health evidence |
| Generic search / execution | Deterministic repo code | enumerate candidates, local search, CP-SAT/MILP primitives, browser attach/extract, apply validated action |
| Soft judgment / tradeoffs | LLM/agent | what weakness matters next, which search to try, whether a marginal tradeoff is acceptable, which age group to focus on |
| Irreversible/operator decisions | Human | MFA/credentials, explicit source exceptions where required, publication/rollback approval |
| Harness UX | Harness adapter | progress, cancellation, browser-controller bridge, rendering/tool registration |

The important principle is that **generic optimization code may search a space but should not silently become the permanent owner of hockey scheduling judgment**.

## Current-state findings

### 1. Application/CLI foundation — aligned

`docs/application-architecture.md` and completed issue #44 established the right structural direction: adapters should be thin and application use cases should own orchestration. `scripts/rvv-miniputt` is already only a launcher into the Python CLI.

Keep this foundation. Do not introduce a new service or parallel orchestration framework.

However, #44 predates the newer decision boundary. Its wording allows scheduling/policy to live in the domain layer generally. ADR 0002 now refines that: **hard rules and deterministic mechanics live in code; soft scheduling policy belongs to the LLM/agent layer.**

### 2. Stage 2 source evidence — mostly aligned

`tournament_scheduler/pipeline/stage2_scraping.py` correctly owns deterministic evidence:

- per-source event results
- blocked/error state
- cache provenance
- low/suspicious event-shape warnings
- strict blocked-source behavior
- explicit `allow_missing_sources` override behavior

The event-count expectation is explicitly a warning heuristic rather than a hard validation rule, which is a good separation.

Desired boundary:

- Python continues to calculate source-health facts and enforce genuine hard gates.
- The LLM may decide which suspicious/empty source to investigate first, whether to retry, which recovery mechanism to invoke, and what to recommend to the operator.
- The LLM must not silently declare bad/missing evidence valid.
- After BookUp/browser recovery, recovered evidence returns to Python and the canonical Stage 2 gate decides whether the source is sufficient.

The portable BookUp session requirement from ADR 0001 remains required: the same repo-owned session-handoff contract must support direct macOS-host execution and a caller inside Lima/`limactl`, without adapter-specific recovery authority.

### 3. `SeasonPlanner` still owns too much soft policy — misaligned

`season_planner.py` remains the baseline/fallback, which is intentional during migration. But it contains policy that should not be copied into Stage 3 v2.

Examples:

- weak metric scores are fed back as `penalty_hints`;
- hosting/game-spread limits may be relaxed automatically;
- diversity/pairwise/month thresholds may be relaxed automatically;
- greedy and optimized date schedules are compared by a built-in scoring judgment;
- many planner decisions are driven by implicit preferences rather than explicit hard constraints.

The most concerning behavior is automatic threshold relaxation based on weak scores. That is a contextual tradeoff decision and should eventually be made by the LLM/agent, not hidden inside the planner.

Action: preserve current behavior only as legacy fallback until v2 proves parity; do not reproduce these decisions in new optimizer code.

### 4. Participant selection contains explicit magic-weight policy — misaligned

`tournament_scheduler/participant_selection.py` contains many hardcoded tradeoff coefficients, including strong club-diversity penalties and numeric weights for deficit, invite count, repeated matchups, grouping history, same-day scheduling, and overlapping age groups.

Some underlying facts are valid and reusable:

- team participation counts
- club representation
- previous opponent counts
- target deficits
- age-group overlap facts

But the numeric preference combination is soft policy. Stage 3 v2 should expose these measurements/candidate options and let the LLM select priorities/bounds/search strategy instead of replacing one fixed score with another fixed score.

### 5. Host assignment mixes facts with soft preferences — partially misaligned

`tournament_scheduler/host_assignment.py` contains both legitimate deterministic mechanics and subjective ranking:

Keep deterministic:

- which clubs have teams in an age group
- arena availability
- tournament duration
- explicit host obligations/overrides
- collision detection
- proportional target calculation when that rule is explicitly configured/accepted

Move or expose as soft policy:

- how strongly to prefer hosting recency versus holiday burden versus target completion
- whether a consecutive hosting streak is acceptable
- travel-derived preferred start-time rules such as 60/120 km cutoffs unless explicitly promoted to configured business rules

A generic host/date search should return valid alternatives plus metrics; the LLM should choose among non-hard tradeoffs.

### 6. Fairness scoring mixes measurement and policy — needs separation

`tournament_scheduler/fairness_scoring.py` is valuable because it computes reproducible metrics. Keep that.

But `DEFAULT_FAIRNESS_THRESHOLDS` and `build_fairness_gate()` also encode pass/warn/fail policy for several subjective qualities, including travel, diversity, month balance, and weekend load.

Target split:

- `metrics`: deterministic measured values and factual breakdowns.
- `hard_constraints`: explicit invariants that can fail a candidate.
- `policy/preferences`: shared runbook/config interpreted by the LLM.

A threshold may remain deterministic only when the project explicitly declares it a business rule. Arena collision is an obvious hard failure. “Pairwise diversity below X means mixed/rough” is not inherently a hard rule.

### 7. Opinionated judgment in Python is directly misaligned

`tournament_scheduler/html/renderers/judgment.py` hardcodes subjective thresholds for `rough`, `mixed`, and `strong`, then generates opinionated prose and recommended next actions.

Examples include fixed pairwise/diversity/month-balance cutoffs and statements such as which part of the plan is weakest or what should be adjusted next.

This is exactly the kind of interpretation an LLM is good at once the deterministic scorecard exists.

Target:

- keep rendering code deterministic;
- render stored LLM judgment/audit summary or a neutral deterministic metric summary;
- remove Python as the authority for subjective tone and next-action selection.

### 8. Plan critic currently decides repair policy — misaligned

`tournament_scheduler/cli/plan_critic.py` is intentionally LLM-free, but it currently ranks subjective issues and proposes concrete repairs such as moving a collision by +7 days or relocating a hosting clump.

Split it into:

- deterministic findings: collision details, host-month concentration, game-spread facts, candidate free dates, affected teams/arenas;
- generic candidate actions: safe possible moves/search operators;
- LLM decision: which issue to address and which action/search request to choose.

Do not automatically turn “collision exists” into “move this tournament exactly +7 days” unless +7 is itself an explicit rule.

### 9. Pipeline orchestration already has an LLM hook, but it is too weak

`tournament_scheduler/cli/pipeline_orchestrator.py` already calls the backend-neutral `LLMJudge` between stages. This is a useful foundation and should be extended rather than replaced.

Today, however:

- the judge generally returns only `PROCEED`/`ABORT`;
- Python computes subjective tone separately;
- Python computes a composite plan-attempt rank;
- the refinement loop uses deterministic critic policy and auto-applies `can_auto_fix` moves.

Target: replace the narrow judge response with a **structured decision contract**. The LLM should be able to request a recovery, retry, optimization/search action, baseline retention, operator question, or acceptance. Python validates and applies the action.

### 10. Headless LLM prompts duplicate soft policy — misaligned

`tournament_scheduler/llm_judge/prompts.py` currently embeds criteria such as:

- at least one source should exist;
- proceed if “most” sources scraped;
- abort if fewer than roughly half have data;
- a plan should have at least a handful of tournaments.

The prompt builder should format the current structured decision context and include the canonical shared RVV policy. It should not become another hidden source of hockey policy.

`LLMJudge` remains a useful transport abstraction. Harness-active and headless operation should differ only in how the LLM is reached, not in decision semantics.

### 11. Harness detection currently enables policy divergence — misaligned

`tournament_scheduler/llm_judge/harness.py` disables the headless judge when a known harness is active because the harness is assumed to make its own in-session judgment.

That is reasonable for transport, but currently each harness carries different workflow instructions. The fix is not to force all harnesses through a specific backend; it is to make all of them consume the **same decision context/action contract and shared policy**.

### 12. Harness adapters are not consistently thin — major misalignment

Current examples:

- `.codex/commands/rvv-miniputt/run.md` is relatively close to the target: invoke `scripts/rvv-miniputt` and do not reimplement stage modules.
- `.claude/commands/rvv-miniputt/run.md` is roughly 10 KB and independently defines Stage 1 review, dotenvx/BookUp behavior, Stage 2 recovery, proceed/abort rules, Stage 3 verdict, and refinement.
- `.chatgpt/commands/rvv-miniputt/run.md` independently defines a similar stage-by-stage recovery workflow.
- `.opencode/commands/rvv-miniputt/run.md` directly invokes individual stage modules and carries its own checkpoint rules.
- `.pi/lib/pipeline-runner.ts` is roughly 29 KB and independently executes all four stages. It runs deterministic Stage 2 non-strict, reads blocked state itself, then invokes the Pi ScraperAgent and owns substantial recovery/orchestration behavior.

This is exactly the duplication the earlier architecture discussion wanted to eliminate.

Target:

- adapters call canonical application/CLI capabilities;
- adapters may show progress/cancellation/UI;
- browser-enabled adapters may provide browser control/session attachment;
- adapters do not independently decide Stage 2 validity or Stage 3 policy;
- `.agents/skills/rvv/` is the shared policy/runbook.

Pi's ScraperAgent/browser integration can remain where technically necessary, but extracted/recovered data must flow back through a repo-owned recovery interface and Python validity gate.

## Recommended target flow

```text
controlled inputs + registrations + calendar/browser evidence
                         |
                         v
              deterministic application/core
                         |
                         v
                  DecisionContext
        facts / hard violations / warnings
       metrics / candidate refs / available actions
                         |
                         v
       shared .agents/skills/rvv policy/runbook
                         |
                         v
                 LLM / agent controller
                         |
                    DecisionAction
                         |
                         v
          deterministic action validator/applier
            /            |              \
           /             |               \
  generic search     source recovery    operator request
       tools              tools          / human gate
           \             |               /
            +------------+--------------+
                         |
                         v
                  new DecisionContext
                         |
                    iterate/accept
                         |
                         v
               export / publication gate
```

For headless execution, an `LLMJudge` backend can act as the controller transport. For Pi, Claude, Codex, OpenCode, or ChatGPT, the active harness can be the transport/UI. All use the same context/action schema and shared policy.

## Proposed structured decision protocol

Do not build a large agent framework. A small versioned JSON contract is sufficient.

### `DecisionContext`

Should contain only decision-relevant, non-secret data such as:

- schema version
- run/stage/capability
- current objective/operator instruction
- facts/evidence summary
- hard violations
- warnings/soft findings
- deterministic scorecard
- current candidate/baseline references
- available validated actions and their parameters
- previous action/result summaries
- whether human approval is required

### `DecisionAction`

Examples:

- `proceed`
- `abort`
- `retry_stage`
- `recover_source`
- `optimize_plan`
- `apply_candidate`
- `keep_baseline`
- `request_operator`
- `present_for_review`

The exact action vocabulary should stay small and capability-oriented.

### `DecisionResult`

Record:

- accepted/rejected action
- deterministic result/reference
- concise rationale/audit summary
- resulting hard violations/warnings/scorecard changes
- next available actions

Do not store private chain-of-thought. Store only concise operational rationale useful for audit/handover.

## Migration order

### Phase 1 — decision contract and shared policy

1. Add versioned `DecisionContext`, `DecisionAction`, and `DecisionResult` models in the existing application layer.
2. Add deterministic action validation.
3. Make `.agents/skills/rvv/` the canonical soft-policy source.
4. Extend the existing LLM transport to consume/produce the structured contract.

### Phase 2 — Stage 2 alignment and BookUp portability

1. Expose blocked/empty/suspicious sources through `DecisionContext`.
2. Let the LLM choose recovery/retry/operator-request actions.
3. Keep final source validity in Python.
4. Implement the ADR 0001 BookUp authenticated-session handoff for both host and Lima callers.
5. Prove real host + Lima reuse after one operator MFA login; commit only sanitized diagnostics.

### Phase 3 — Stage 3 alignment

Continue #257, but treat its optimizer as an agent-callable search primitive.

1. Preserve normalized problem/candidate/verify/score work.
2. Stop adding permanent soft-policy weights/tables.
3. Separate metrics from subjective thresholds/severity.
4. Replace Python tone/critic/auto-move policy with factual findings + available generic actions.
5. Let the LLM direct optimization/repair based on the scorecard.
6. Keep `SeasonPlanner` baseline/fallback until the new loop passes full-season A/B.

### Phase 4 — thin adapters

1. Thin Claude/ChatGPT/OpenCode/Codex wrappers to shared runbook + canonical CLI/application actions.
2. Reduce Pi pipeline orchestration to invocation/progress/cancellation/browser bridge.
3. Keep Pi-specific ScraperAgent only as a browser capability implementation, not source-validity authority.
4. Add tests/lint that prevent adapters from calling stage modules or embedding duplicated policy where feasible.

### Phase 5 — prove parity and delete obsolete policy

1. Run same decision contexts through headless and at least Pi/Claude/Codex-or-OpenCode paths.
2. Require valid actions and comparable acceptable outcomes, not identical schedules.
3. Run full local production checkpoint evaluation.
4. Push sanitized evaluation artifacts for remote review.
5. Only then remove obsolete SeasonPlanner/critic/judgment/adapter policy.

## What not to do

- Do not make an LLM the sole verifier of schedule or source correctness.
- Do not ask the LLM to manually construct 152 tournaments entirely in context when a generic search tool can do combinatorics.
- Do not replace `SeasonPlanner` with another giant Python policy engine.
- Do not create separate policy implementations for Pi, Claude, ChatGPT, Codex, and OpenCode.
- Do not add a microservice, database, message queue, or new agent framework for this volunteer project.
- Do not persist BookUp cookies/tokens/browser profiles or private reasoning in git/log artifacts.
- Do not remove the legacy planner before benchmark evidence proves replacement.

## Review verdict

**Direction:** correct after ADR 0001 was clarified, but implementation is only partially aligned.

**Highest-value next architectural work:** introduce the shared structured LLM decision/action boundary and use it to remove duplicate soft-policy ownership. This should happen before investing heavily in making the standalone Python Stage 3 optimizer smarter.

**Stage 3 #257:** continue as a vertical slice under this architecture, not as an independent goal to build a fully autonomous Python planner.

**BookUp:** portable host/Lima authenticated-session takeover is part of the same principle: browser capability may be environment-specific, but recovery semantics and final validity stay canonical and repo-owned.
