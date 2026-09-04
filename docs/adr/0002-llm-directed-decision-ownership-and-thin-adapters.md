# ADR 0002: LLM-directed soft decisions, deterministic guardrails, and thin harness adapters

- **Status:** Accepted — migration in progress
- **Date:** 2026-09-04
- **Decision owner:** RVV hockey project
- **Related review:** `docs/architecture-alignment-review-2026-09-04.md`
- **Related ADR:** ADR 0001
- **Related issues:** #44, #257

## Context

The RVV repo has accumulated planning and recovery behavior across several layers:

- deterministic Python pipeline/domain code;
- a typed application/use-case layer introduced through issue #44;
- `tournament_scheduler.llm_judge` for headless LLM judgment;
- `.agents/skills/rvv/` as shared policy/runbook;
- Pi, Claude Code, Codex, OpenCode, and ChatGPT adapters;
- bespoke Stage 3 scheduling heuristics;
- browser/BookUp recovery logic.

This evolved incrementally and now mixes three different kinds of concerns:

1. facts and hard correctness rules that must be reproducible;
2. generic mechanisms for searching, scraping, applying, exporting, and persisting;
3. contextual judgment and tradeoffs that should not be permanently encoded as magic weights or duplicated harness instructions.

ADR 0001 established this boundary for Stage 3 and portable BookUp session handoff. This ADR generalizes it to the entire RVV workflow.

Issue #44 remains the structural foundation: use application capabilities and thin adapters. This ADR refines ownership inside that structure. Where older architecture documentation says “policy” belongs in the domain generically, interpret that as **deterministic business rules only**. Soft policy/judgment belongs to the LLM/agent unless the project explicitly promotes it to a deterministic rule.

## Decision

The RVV workflow will use an **LLM-directed decision loop over deterministic repo capabilities**.

```text
controlled inputs / registrations / calendars / browser evidence
                              |
                              v
                   deterministic application/core
                              |
                              v
                       DecisionContext
          facts / hard violations / warnings / metrics
              candidates / available validated actions
                              |
                              v
                 shared RVV policy/runbook
                              |
                              v
                      LLM / agent controller
                              |
                       DecisionAction
                              |
                              v
              deterministic validator/applier
               /            |              \
              /             |               \
       generic search   source recovery    human gate
          / solver        / browser         / approval
              \             |               /
               +------------+--------------+
                              |
                              v
                       DecisionResult
                              |
                     new DecisionContext
                              |
                        iterate/accept
                              |
                              v
                   export / publication
```

The LLM is a **controller of soft decisions**, not a verifier of hard correctness and not a replacement for deterministic combinatorial/search tools.

## Ownership rules

### 1. Deterministic code owns facts

Repo/application code is authoritative for:

- workbook/config parsing and normalization;
- registrations and canonical team identities;
- calendar/source extraction results;
- arena/date/host facts;
- BookUp/source evidence and cache provenance;
- fingerprints, checkpoints, manifests, logs, and persistence;
- export/publication inputs and outputs.

The LLM may interpret these facts but must not redefine them.

### 2. Deterministic code owns hard rules and irreversible safety gates

Examples:

- candidate schema validity;
- only valid/registered teams;
- impossible duplicate participation/arena overlap rules;
- explicit operator restrictions/locks/exclusions;
- source-validity requirements that are declared hard;
- privacy rules;
- publication/rollback confirmation and fingerprint checks.

An LLM may not bypass a hard gate through prose or an unvalidated action.

### 3. Deterministic code owns measurement

Metrics should be reproducible and planner-independent where possible, including:

- participation balance;
- opponent diversity/repetition;
- turnaround spacing;
- hosting distribution;
- month/date distribution;
- travel;
- source-health measurements;
- rule/constraint violations.

Measurement is different from judgment. A metric value may be deterministic while deciding whether that value is acceptable is soft policy unless explicitly configured as a hard rule.

### 4. Deterministic code may provide generic search/execution primitives

The repo may implement:

- local search;
- CP-SAT/MILP or another general optimizer;
- candidate enumeration;
- Pareto filtering;
- safe move generation;
- browser attach/navigation/extraction;
- applying a validated candidate/action.

These mechanisms must not silently become the owner of contextual hockey policy.

### 5. The LLM/agent owns soft judgment and tradeoffs

Examples:

- which warning/quality dimension to address first;
- which age group deserves a different priority;
- which generic search objective/bounds/restart to request;
- whether a small soft regression is justified by a larger benefit when not prohibited by a hard rule;
- whether to retain the baseline because improvement is marginal;
- which recovery path to try for a suspicious source;
- how to interpret organizer guidance expressed in natural language;
- what to present to the operator and what next action to recommend.

These decisions come from the shared RVV policy/runbook plus current evidence, not from separate harness prompts or permanent Python magic numbers.

### 6. Humans own explicit approval/exception decisions where required

The operator remains the authority for actions such as:

- credentials and MFA;
- publication/rollback approval;
- explicit exceptions that policy declares human-only;
- changing a soft preference into a hard business rule.

## Structured decision contract

Use a small, versioned, harness-neutral JSON protocol rather than a new agent framework.

### `DecisionContext`

A context should contain only decision-relevant, non-secret information:

- `schema_version`;
- run/capability/stage identifier;
- current operator objective/instruction;
- deterministic facts/evidence summary;
- hard violations;
- warnings/soft findings;
- deterministic scorecard;
- candidate/baseline references;
- available actions with validated parameter schemas;
- prior action/result summaries;
- human-approval requirements.

### `DecisionAction`

Keep the vocabulary capability-oriented and small. Expected actions include:

- `proceed`;
- `abort`;
- `retry_stage`;
- `recover_source`;
- `optimize_plan`;
- `apply_candidate`;
- `keep_baseline`;
- `request_operator`;
- `present_for_review`.

The action schema must be validated before execution. An unknown action or invalid parameters are rejected deterministically.

### `DecisionResult`

Persist an operational audit summary:

- accepted/rejected action;
- deterministic result/reference;
- concise rationale summary;
- changed hard violations/warnings/metrics;
- resulting available actions.

Do **not** persist private chain-of-thought. Only concise action rationale required for audit/handover belongs in repo logs/manifests.

## Shared policy rule

`.agents/skills/rvv/` is the canonical shared operational/planning policy source for agents.

Harness wrappers and headless LLM prompts should refer to/use this shared policy. They must not copy substantive stage policy into separate implementations.

If code requires a deterministic rule, that rule belongs in config/application/domain code with tests, not only in the skill.

## Harness rule

Pi, Claude Code, Codex, OpenCode, ChatGPT, and future harnesses are adapters/transports.

They may own:

- command/tool registration;
- UI/rendering;
- progress notifications;
- cancellation;
- browser-controller integration;
- environment-specific launch details.

They must not independently own:

- Stage 1-4 orchestration semantics;
- Stage 2 source validity;
- Stage 3 scheduling policy;
- planner acceptance thresholds;
- publication safety policy.

The desired wrapper is conceptually:

```text
read shared RVV policy
invoke canonical repo capability
receive DecisionContext
provide LLM decision / browser capability where needed
submit DecisionAction
repeat until repo capability completes or asks for human input
```

The existing Codex wrapper is closer to this desired shape than the current Claude/ChatGPT/OpenCode stage-by-stage wrappers. Pi should keep its useful UI/browser capabilities while delegating canonical orchestration/state decisions to the repo/application layer.

## Headless LLM rule

`tournament_scheduler.llm_judge` should be retained as an LLM transport for headless/cron/CLI operation, but its role changes from a narrow bespoke `PROCEED/ABORT` judge to the same structured decision contract used by interactive harnesses.

Harness-active versus headless execution must differ by **transport**, not by policy semantics.

Python prompt builders may serialize the shared policy/context and request a structured action. They should not embed independent soft thresholds such as “most sources” or arbitrary planning-quality cutoffs unless those rules are explicitly part of canonical policy/config.

## Stage 2 decision boundary

Keep deterministic:

- scrape/extraction results;
- source error/blocked state;
- event-count and shape evidence;
- cache provenance;
- declared hard source gates;
- final merge/validation.

LLM-directed:

- which blocked/empty/suspicious source to investigate first;
- retry/recovery strategy;
- whether to request browser/session takeover;
- what exception/recommendation to present to the operator.

A recovered source must return through Python before Stage 2 is considered valid.

### Portable BookUp session requirement

ADR 0001 remains authoritative for BookUp session portability:

- one operator-completed authenticated session should be reusable when possible;
- standalone macOS-host callers must be supported;
- callers inside Lima/`limactl` must be able to bridge/take over the host-authenticated session;
- harness-specific code must not define separate BookUp validity semantics;
- no cookies/tokens/browser profiles/storage-state secrets/MFA artifacts may be committed or logged unsafely.

## Stage 3 decision boundary

Issue #257 remains the Stage 3 migration/benchmark vertical slice.

Keep:

- normalized planning problem;
- candidate contract;
- deterministic verification;
- deterministic quality metrics;
- generic optimizer/search primitives;
- full-season A/B evidence;
- legacy `SeasonPlanner` fallback during migration.

Move away from:

- automatic threshold relaxation from `penalty_hints`;
- permanent magic objective weights as the main policy mechanism;
- Python selecting subjective “best” tradeoffs without exposing alternatives;
- Python opinionated `rough/mixed/strong` judgment as the acceptance authority;
- deterministic critic code choosing subjective repair strategy such as an automatic +7-day move;
- hidden per-age-group policy tables that recreate the old planner.

The LLM should direct generic search and repair from deterministic scorecards. The solver performs combinatorics; it does not choose the organizer's preferences.

## Specific current code to migrate

The architecture alignment review identified these soft-policy hotspots:

- `tournament_scheduler/season_planner.py` — penalty-hint-driven threshold relaxation and legacy preference logic;
- `tournament_scheduler/pipeline/stage3_planning.py` — weak-score feed-forward and deterministic candidate/tradeoff selection;
- `tournament_scheduler/participant_selection.py` — magic weighted participant policy;
- `tournament_scheduler/host_assignment.py` — mixed hard mechanics and soft host/start-time preference ranking;
- `tournament_scheduler/fairness_scoring.py` — deterministic metrics mixed with subjective thresholds/severity;
- `tournament_scheduler/html/renderers/judgment.py` — opinionated tone and next-action policy;
- `tournament_scheduler/cli/plan_critic.py` — issue prioritization and concrete repair policy;
- `tournament_scheduler/cli/pipeline_orchestrator.py` — deterministic auto-refinement loop;
- `tournament_scheduler/llm_judge/prompts.py` — separate headless soft criteria;
- `.pi/lib/pipeline-runner.ts` — duplicate Stage 1-4 orchestration and recovery semantics;
- `.claude/commands/rvv-miniputt/`, `.chatgpt/commands/rvv-miniputt/`, `.opencode/commands/rvv-miniputt/` — duplicated stage policy.

Do not delete all of these at once. Migrate one decision boundary at a time while keeping tests and legacy fallback.

## Migration sequence

1. Add structured decision context/action/result models to the existing application layer.
2. Add deterministic action validation/application and manifest audit records.
3. Make headless `LLMJudge` use the contract and shared policy.
4. Align Stage 2 recovery and portable BookUp session takeover.
5. Align Stage 3 #257 so optimizer/critic functionality becomes agent-callable facts/search/actions rather than a new policy engine.
6. Thin harness adapters to canonical repo capabilities.
7. Prove headless + multiple harnesses can consume the same context/action protocol.
8. Run real local production checkpoint and BookUp host/Lima tests where required; push sanitized evidence.
9. Remove obsolete soft-policy code only after parity/evidence.

## Compatibility and fallback

During migration:

- existing publication/export behavior must remain compatible;
- `SeasonPlanner` remains available as Stage 3 baseline/fallback;
- old wrapper paths may remain temporarily while new canonical flows are proven;
- explicit feature flags/commands are preferred for risky replacement steps;
- public mutation continues to require existing human confirmation/protected workflow gates.

## Consequences

### Positive

- less duplicated harness policy;
- fewer magic scheduling heuristics to maintain;
- LLMs can apply contextual judgment without becoming correctness authority;
- headless and interactive execution converge on the same semantics;
- generic optimizer code stays reusable and measurable;
- new harnesses require little integration work;
- BookUp recovery becomes environment-portable rather than Pi/host-specific;
- easier handover because decision ownership is explicit.

### Costs

- a migration period with both legacy hardcoded decisions and new LLM-directed actions;
- need for structured context/action schemas and audit records;
- LLM decisions may differ across models, so deterministic validation and measurable outcomes are mandatory;
- some real BookUp validation cannot be proven by unit tests alone;
- deleting old policy requires staged A/B evidence.

## Non-goals

- No LLM-only correctness verifier.
- No requirement for identical schedules across models/harnesses.
- No requirement for the LLM to construct the entire season manually in context.
- No new microservice, database, queue, or heavy agent framework.
- No immediate removal of `SeasonPlanner`.
- No persistence of sensitive BookUp session material or private model reasoning.

## Supersession / precedence

This ADR does not supersede ADR 0001. ADR 0001 remains the detailed Stage 3 v2 + BookUp portability decision.

This ADR **generalizes** the decision-ownership rule repo-wide and refines older architecture wording from issue #44 / `docs/application-architecture.md`:

> deterministic business rules belong in code; contextual soft policy belongs to the LLM/agent; harness adapters remain thin.
