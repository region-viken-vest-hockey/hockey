# ADR 0001: Stage 3 v2 uses LLM-directed planning, generic optimization, deterministic verification, and portable BookUp session handoff

- **Status:** Accepted — migration in progress
- **Date:** 2026-09-04
- **Decision owner:** RVV hockey project
- **Tracking issue:** #257
- **Related implementation:** `tournament_scheduler/planning_contract.py`, `tournament_scheduler/stage3_optimizer.py`, `tournament_scheduler/stage3_ab.py`, `scripts/rvv-miniputt`, `.agents/skills/rvv/`

## Context

The RVV miniputt pipeline currently has two areas where implementation details have grown too tightly coupled to individual agents or to bespoke code:

1. **Stage 3 planning** contains substantial scheduling intelligence in `SeasonPlanner` and related participant/date/host heuristics.
2. **BookUp recovery/authentication** is browser- and environment-sensitive. The pipeline must work both when invoked directly on the macOS host and when the agent/runtime is inside a Lima VM (`limactl`). BookUp may require Vipps/SMS MFA, so repeatedly starting an independent browser login in each execution environment is undesirable and sometimes impractical.

The architecture should let Pi, Claude Code, Codex, OpenCode, ChatGPT, or another harness operate the same canonical repo workflow without each harness owning its own scheduling policy or BookUp recovery semantics.

A central motivation for the Stage 3 migration is to **move judgment and soft planning decisions out of bespoke hardcoded Python heuristics and into the LLM/agent planning loop**, while keeping facts, hard constraints, verification, scoring, persistence, and publication deterministic and repo-owned.

This work is primarily tracked in GitHub issue #257. BookUp/session portability is an adjacent execution requirement that must be preserved while Stage 3 is simplified.

## Decision summary

The target Stage 3 architecture is:

```text
input.xlsx + registrations + calendars
                 |
                 v
       normalized planning problem
                 |
                 v
      shared RVV planning policy/runbook
                 |
                 v
          LLM / agent controller
        /          |           \
       /           |            \
choose priorities  |        explain/review
and tradeoffs      |
       \           v
        +--> generic optimizer/search tool
                    |
                    v
               candidate.json
                    |
                    v
          deterministic verifier
             + quality scorer
                    |
              bad / scorecard
                    |
                    +------> LLM/agent iterates
                    |
                 accepted
                    |
                    v
             existing Stage 4 exports
```

The generic optimizer is **not** intended to become a second large hardcoded policy engine. It provides generic search/optimization mechanisms. The LLM/agent decides how to use those mechanisms within deterministic guardrails.

Browser authentication/recovery is a separate boundary:

```text
already-authenticated BookUp browser/session
                 |
         session handoff/attach
          /                \
 macOS host run         Lima VM run
          \                /
           canonical Stage 2 scraper
                    |
             scrape/cache result
                    |
              resume pipeline
```

The repo owns the contracts, hard constraints, verifier, scoring, optimizer primitives, BookUp session-handoff semantics, scraping result acceptance, and export/publication behavior. The LLM/agent owns soft-policy interpretation and planning tradeoff decisions. Harness adapters remain thin.

## Core decision boundary: what belongs in code vs the LLM

This boundary is intentional and is the main architectural goal of the Stage 3 work.

### Deterministic code owns facts and guardrails

Python/repo code should own things that must be reproducible, testable, and authoritative:

- parsing and normalizing `input.xlsx`, registrations, and calendar data
- source validity and BookUp/calendar evidence
- team/date/arena/host facts
- hard scheduling constraints
- explicit operator overrides
- candidate schema and persistence
- deterministic verification
- deterministic quality metrics/audit
- generic optimization/search primitives
- exports, publication, rollback, and privacy controls

An LLM must never be the sole authority for whether a schedule is valid.

### The LLM/agent owns soft planning judgment

The LLM/agent should increasingly own decisions that are contextual, preference-driven, or involve tradeoffs, for example:

- which quality objective deserves attention next
- whether opponent diversity is worth a small spacing cost when the operator has not made that a hard rule
- which age groups need different priorities
- whether to keep a baseline assignment when improvement is marginal
- how to interpret organizer guidance expressed in natural language
- which solver/search strategy or restart to try next
- how to react to a verifier/scorer report
- when a candidate is worth presenting to the operator for review

These decisions should be driven from the shared RVV policy/runbook and current problem evidence, not copied into separate harness-specific prompts.

### Do not migrate soft policy from one hardcoded planner into another

Removing `SeasonPlanner` heuristics only to recreate the same decisions as:

- a growing list of Python `if` statements,
- permanent magic penalty weights,
- per-age-group hardcoded tuning tables,
- or a second bespoke optimization policy layer

would miss the point of this ADR.

Generic code may expose objective parameters, hard bounds, candidate generators, Pareto sets, or search operators. **The choice of soft priorities and tradeoffs should normally be made by the LLM/agent at runtime**, unless a preference has been explicitly promoted to a deterministic business rule by the operator/project.

### Promotion from soft preference to hard rule must be explicit

If repeated real-world use establishes that something is no longer a preference but a true invariant — for example “never schedule the same team twice on one date” — it should be deliberately added to deterministic verification/configuration with tests.

Do not silently turn an LLM preference into a permanent hardcoded rule merely because one benchmark favored it.

## Stage 3 evidence

### U10 controlled experiment

Using the real published U10 schedule while holding the tournament skeleton fixed, a generic optimization/repair experiment produced a strict improvement over the current participant assignment:

- appearances/team: 6 -> 6
- unique team-pair matchups: 244 -> 249
- pairwise novelty: 65.1% -> 66.4%
- pairs meeting 3+ times: 17 -> 5
- max repeat: 4 -> 3
- same-club pairings: 23 -> 23
- max same-club teams/tournament: 3 -> 2
- minimum turnaround: 1 -> 7 days
- gaps under 7 days: 2 -> 0
- gaps under 14 days: 14 -> 14

A more diversity-focused candidate reached 264 unique pairings / 70.4% novelty, but at the cost of more 7–13 day turnarounds. This demonstrated that the current plan is not uniquely forced by the constraints and that the planning problem has a real Pareto frontier.

### Full-season Stage 3 v2 experiment

The first repo implementation used simulated annealing over team swaps while keeping dates, arenas, hosts, capacities, and participation counts fixed.

The production A/B benchmark (152 tournaments, 9 age groups) showed material improvements in opponent diversity:

- unique opponent pairs: 1301 -> 1343
- pairwise novelty: 52.2% -> 53.9%
- pairs meeting 3+ times: 255 -> 217
- max same-club teams/tournament: 5 -> 3

But the default/global weighted objective regressed other important metrics:

- same-club pairings: 112 -> 145
- gaps under 7 days: 24 -> 28
- gaps under 14 days: 110 -> 167

A weight sweep showed that one global weighted sum mostly moves along the Pareto frontier. Reducing spacing/same-club regressions eventually gives back the diversity improvements. Therefore **continuing to tune one global weight vector is not the chosen design direction**. It also reinforces the architectural goal: the project should expose measurable tradeoffs to the agent rather than attempting to encode one universally correct judgment in permanent Python weights.

Benchmark snapshots:

- `docs/issue-257/ab-2026-09-03/`
- `docs/issue-257/ab-2026-09-03-v2/`

## Stage 3 decisions

### 1. Keep the repo as the deterministic authority

The hockey repo continues to own:

- controlled inputs and registration sync
- calendar/BookUp acquisition and source health
- normalized Stage 3 planning problem
- candidate-plan contract
- deterministic verification
- deterministic quality scoring/audit
- generic optimization/search mechanisms
- Stage 4 exports and publication

Hard rules, facts, quality measurements, and promotion safety gates must not live only in LLM prompts or harness-specific adapters.

This does **not** mean all planning policy belongs in Python. Soft preferences and tradeoff decisions belong in the LLM/agent layer unless deliberately promoted to deterministic rules.

### 2. Move soft planning decisions from bespoke code into the LLM/agent loop

The migration target is not merely “replace `SeasonPlanner` with a better optimizer.” The target is to remove unnecessary hardcoded scheduling judgment.

The LLM/agent should receive:

- the normalized planning problem
- shared RVV policy/runbook
- available generic optimization actions/options
- deterministic verifier results
- deterministic scorecards/Pareto comparisons

It should then decide what to optimize, which tradeoffs to accept within allowed bounds, and whether to iterate or retain the baseline.

The agent must be able to make these decisions similarly from Pi, Claude Code, Codex, OpenCode, ChatGPT, or another harness because the policy and tooling are shared and harness-neutral.

### 3. Keep `SeasonPlanner` as baseline/fallback during migration

Do not delete or heavily rewrite the current planner until Stage 3 v2 has demonstrated that it can replace the relevant responsibilities without regression.

This is a strangler migration, not a rewrite-in-place.

### 4. Use deterministic non-regression bounds where we know we must not get worse

Important qualities that the project has explicitly chosen to protect should not be freely traded away merely because a weight vector permits it.

For the current participant-assignment migration experiment, the baseline is used as a temporary non-regression envelope. A candidate for an age group must preserve or improve at least:

- participation counts/balance
- same-club pairing count
- max same-club concentration per tournament
- gaps under 7 days
- gaps under 14 days
- hosting fairness where participant reassignment can affect it
- hard-constraint status: no new hard violations

Within that safe region, the LLM/agent can direct optimization of opponent quality, for example:

- minimize repeated opponents
- minimize pairs meeting 3+ times
- minimize max pair repetition
- maximize unique inter-club opponents / pairwise novelty

These baseline bounds are a migration/evaluation safety mechanism, not a declaration that every protected metric must become a permanent business rule. After evidence and operator review, some may remain deterministic constraints while others may return to agent-controlled soft tradeoffs.

If no strictly better feasible assignment is found for an age group, retain the baseline assignment for that age group. Stage 3 v2 does **not** need to improve every age group; unchanged is acceptable, regression is not during this migration phase.

### 5. Separate participant-assignment success from full production readiness

The current v2 optimizer keeps dates/arenas/hosts fixed. It cannot repair every pre-existing skeleton-level problem.

After fixing the verifier's false participation-target interpretation, the production baseline still exposes three pre-existing hard violations:

- 2x `arena_interval_conflict`
- 1x `duplicate_participation_same_date`

Evaluation therefore needs two distinct concepts:

- **dominates_baseline:** the candidate introduces no new hard violations and has no material quality regression versus the baseline for the scope it optimizes.
- **production_ready:** the complete candidate has zero hard violations and passes the full promotion gate.

Participant-only Stage 3 v2 can prove `dominates_baseline` before later skeleton optimization is capable of proving `production_ready`.

### 6. Do not spend the next cycle on harness comparison

Pi vs Claude Code vs Codex vs OpenCode is not the next algorithm-quality experiment. They would currently drive the same repo optimizer/verifier.

Harness independence should be tested after the Stage 3 v2 planning loop itself meets the quality bar. The eventual test should verify that different harnesses can make acceptable planning decisions from the same shared policy, problem contract, optimizer tools, and deterministic feedback — not that they produce byte-identical schedules.

## BookUp session-handoff decisions

### 7. Treat BookUp authentication as a reusable session, not a harness-local login step

BookUp can require credentials plus Vipps/SMS MFA. The operator may already have completed login in a browser. The pipeline must be able to **take over that authenticated session** instead of requiring a fresh login in the environment where the agent is currently running.

This applies in both supported execution modes:

1. **Standalone host execution** — `scripts/rvv-miniputt` / Python / agent runs directly on the macOS host.
2. **Lima execution** — Pi, Claude, Codex, OpenCode, or another runtime is running inside a VM started/managed with `limactl`, while the usable authenticated browser session may exist on the macOS host.

The same canonical Stage 2 recovery semantics must work in both modes.

### 8. The session-handoff mechanism must be harness-neutral

Do not solve this separately in `.pi`, `.claude`, `.codex`, or `.opencode`.

The repo should expose one canonical BookUp recovery/session interface that adapters can invoke. A harness may provide UI/progress notifications, but it must not define its own rules for when a BookUp source is considered recovered or valid.

The handoff implementation may use an attachable browser endpoint, reusable browser profile/storage state, or another secure mechanism, but it must satisfy the behavior below rather than tying the contract to one specific browser technology prematurely.

### 9. Required BookUp takeover behavior

The canonical workflow must be able to:

- detect or be given an already-authenticated BookUp session
- attach/reuse it without re-entering credentials when possible
- work when the caller is on the host
- work when the caller is inside Lima while the authenticated browser/session is on the host
- preserve MFA-completed state for the duration needed to scrape BookUp sources
- return control to the deterministic Python pipeline after browser recovery/extraction
- let Python decide whether Stage 2 source evidence is sufficient to proceed
- avoid leaking BookUp credentials, session cookies, tokens, or browser-profile secrets to logs, committed files, command-line history, or benchmark artifacts
- fail with a clear recovery instruction when session takeover is unavailable

### 10. Lima must bridge to the host session; it must not create a second independent authority

When invoked inside Lima, the preferred model is that the VM reaches a host-owned authenticated browser/session through an explicit handoff/bridge or receives a deliberately exported ephemeral session state.

The VM should not silently create an unrelated BookUp login state and then let harness-specific code independently decide whether it is valid.

Exact transport is an implementation detail to be selected after testing what is robust on macOS + Lima. The architectural requirement is **one session-handoff contract and one Stage 2 validation authority**.

## Current implementation state

Already implemented on `main` for Stage 3:

- normalized `planning_problem` contract
- stable candidate-plan contract
- `rvv-miniputt plan problem`
- `rvv-miniputt plan verify`
- `rvv-miniputt plan score`
- generic participant-reassignment optimizer behind explicit `plan optimize`
- old-vs-new `plan ab` benchmark
- full-season benchmark artifacts
- participation-target verifier bug fixed
- CLI weight overrides for experiments

Existing BookUp behavior already includes credentialed scraping and `--manual-bookup-login` for visible-browser MFA recovery. The new requirement is to generalize that into **portable session takeover**, especially across the host/Lima boundary, rather than duplicating login/recovery per harness.

Do not redo the shipped Stage 3 contracts/tools from scratch. Extend them toward the LLM-directed planning boundary described above.

## Known Stage 3 implementation issues before trusting the next benchmark

### A. Return the actual best annealing state

At the time of this ADR, `stage3_optimizer.optimize_candidate()` tracks `best_score` but does not snapshot/restore the corresponding best slot assignment. It can therefore return the final accepted annealing state while reporting `objective_after` from an earlier, better state.

Required fix:

- snapshot the assignment whenever a new best objective is reached
- rebuild/return that best assignment
- ensure `objective_after` describes the actual returned candidate
- add a regression test

### B. Promotion must include per-age-group regressions

`stage3_ab.build_ab_report()` computes per-age-group comparisons, but the current `promotable` calculation only considers the overall comparison.

Required fix:

- a regression in one age group must not be hidden by gains in another
- expose `dominates_baseline` separately from `production_ready`
- add regression tests for both semantics

## Next implementation tasks

Do these in order.

### Task 1 — fix benchmark correctness

1. Fix best-state restoration in `stage3_optimizer.py`.
2. Fix per-age-group promotion/non-regression gating in `stage3_ab.py`.
3. Add/adjust tests proving both behaviors.
4. Make A/B output distinguish baseline domination from zero-hard-violation production readiness.

Do not do another large global weight-tuning sweep before this is complete.

### Task 2 — implement baseline-bounded participant search as an agent-callable capability

Add a participant optimization mode that evaluates each age group independently against its baseline metrics.

Candidate acceptance during this migration experiment must be based on explicit non-regression bounds rather than only a weighted-sum score.

The implementation should expose generic search controls/results so an LLM/agent can direct the process instead of burying the next set of planning choices in Python.

Desired behavior:

```text
for each age group:
    baseline = existing assignment
    search for feasible alternatives

    deterministic code rejects candidates that violate hard/baseline bounds

    agent receives scorecard / alternatives
    agent chooses objective priority or next search action

    if a better acceptable candidate is selected:
        use it
    else:
        keep baseline age-group assignment

combine selected age-group assignments
verify + score full season
```

The search may use simulated annealing, local search, CP-SAT/MILP, multiple restarts, or another generic technique. Prefer explicit constraints/Pareto outputs for protected metrics over ever-larger permanent penalty weights.

Do **not** introduce a large per-age-group hardcoded weight table as the solution to the current Pareto problem. If temporary benchmark weights are needed as search parameters, keep them observable/configurable and let the agent choose or vary them.

### Task 3 — run the real local production-checkpoint experiment

A local run against the real `.pipeline` checkpoint is required because the relevant production checkpoint data is not committed in a form that remote review alone can reliably regenerate.

After Tasks 1–2, run the new participant optimization against the current real local checkpoint.

Use multiple deterministic seeds/restarts rather than judging one stochastic run. At minimum use seeds 1–5 unless the new solver is deterministic and seed-independent.

Report, overall and per age group:

- old/new hard-violation sets
- participation spread/counts
- unique opponent pairs
- pairwise novelty
- pairs meeting 3+ times
- max pair repeat
- inter-club diversity
- same-club pairing count
- max same-club teams per tournament
- min turnaround
- gaps under 7 days
- gaps under 14 days
- hosting fairness metrics available in the scorer/audit
- whether each age group improved, stayed unchanged, or regressed
- `dominates_baseline`
- `production_ready`
- which soft-objective/tradeoff choices were made by the agent versus enforced by deterministic code

Key decision question:

> Can Stage 3 v2, with deterministic guardrails and agent-directed soft planning decisions, produce a full-season participant assignment that Pareto-dominates the existing participant assignment, retaining the old assignment for age groups it cannot improve?

### Task 4 — push benchmark artifacts for external evaluation

The local result must be committed/pushed so another agent can evaluate it without access to the local `.pipeline` state.

Create a new immutable snapshot directory, for example:

```text
docs/issue-257/participant-bounded-YYYY-MM-DD/
```

Include at minimum:

- `README.md` — result, interpretation, exact commands, seed/restart settings, next recommendation
- `problem.json`
- `old_candidate.json`
- final selected `new_candidate.json`
- `ab_report.json`
- per-seed/per-restart summary if applicable
- any Pareto/selection summary needed to explain which age-group candidate was chosen
- a concise record of agent-selected soft priorities/tradeoffs so another agent can understand why the candidate was selected

Do not overwrite previous benchmark snapshots.

Push the snapshot to `main` and add a comment to #257 linking the directory/commit and stating whether the participant optimizer dominates the baseline.

That pushed snapshot is the handoff point for the next evaluator.

### Task 5 — design and prove portable BookUp session takeover

Do this as a focused Stage 2 execution-portability slice; do not bury it inside a harness adapter.

1. Inspect the existing `--manual-bookup-login`, Playwright/browser worker, recovery-target, recovery-inject, and scrape-merge paths.
2. Define a small repo-owned session-handoff contract that supports both:
   - host caller -> host authenticated BookUp browser/session
   - Lima caller -> host authenticated BookUp browser/session
3. Choose and prototype the safest practical transport (for example, attachable browser endpoint vs deliberately exported ephemeral storage/session state). Do not commit auth material.
4. Add environment detection/configuration that is explicit and testable; do not make Lima-specific behavior a hidden Pi convention.
5. Prove a BookUp source can be scraped after an operator completes MFA once, then reused by:
   - a standalone host invocation
   - an invocation from inside Lima
6. After browser takeover, return to Python and require the normal Stage 2 source validation/gating before proceeding.
7. Document operator recovery steps and failure modes in the shared RVV skill/runbook.

If a real BookUp/MFA session is required to prove this, the implementation agent must leave a clearly named **local manual verification task** rather than claiming completion from unit tests alone.

If the proof produces useful non-secret diagnostics, commit/push a sanitized report under `docs/` so the next evaluator can confirm both execution modes without rediscovering the design. Never commit cookies, tokens, browser profiles, storage-state secrets, credentials, or MFA artifacts.

### Task 6 — prove the LLM-directed boundary before deleting planner heuristics

Once the bounded participant experiment succeeds, demonstrate that at least two capable harness/model combinations can consume the same shared policy/problem/scorecard and independently reach acceptable candidates without planner-specific policy embedded in their adapters.

The proof is not byte-identical output. The proof is:

- same deterministic contracts and guardrails
- same shared RVV policy/runbook
- no harness-specific scheduling rules
- agent-selected soft priorities are observable in the run/snapshot
- final candidates pass the same verifier and quality gates

Only after this should planner-specific heuristic code be removed aggressively.

## Decision gate after the next participant benchmark

### If participant v2 dominates the baseline

Proceed to generic tournament-skeleton optimization, beginning with dates and arena/time placement so v2 can address the remaining skeleton-level hard violations and replace more of `SeasonPlanner`.

As that scope expands, preserve the same boundary: deterministic code defines facts/hard constraints/search primitives; the LLM/agent directs soft tradeoffs and iterative planning.

Keep the existing planner as fallback until complete v2 output is production-ready and export-compatible.

### If participant v2 cannot dominate the baseline

Do not expand Stage 3 scope yet. Determine whether the blocker is:

- search algorithm quality
- the fixed tournament skeleton
- a genuinely unavoidable tradeoff
- a scoring/constraint-definition problem
- or insufficient agent guidance/tooling for making the tradeoff decisions

Use that evidence to choose the next experiment.

## Consequences

### Positive

- Scheduling correctness and quality remain measurable and harness-independent.
- Soft planning intelligence can evolve through better models/prompts/shared policy without rewriting scheduling algorithms for every preference change.
- We can simplify bespoke `SeasonPlanner` intelligence only after evidence proves a replacement.
- Agents can change without changing the deterministic correctness authority.
- The repo does not need to permanently encode every organizer preference as Python heuristics or magic weights.
- BookUp MFA can be completed once and reused across execution environments instead of being coupled to whichever harness launched the run.
- Lima becomes an execution environment, not a separate source of scheduling/scraping policy.
- Local experiments become reviewable through immutable pushed benchmark snapshots.

### Costs / tradeoffs

- During migration, old and new planning implementations coexist.
- Agent-directed planning is less byte-for-byte deterministic than one fixed heuristic planner; reproducibility therefore relies on persisted inputs, scorecards, chosen priorities, model/harness metadata where useful, and final candidate artifacts.
- Baseline-bounded optimization may preserve an imperfect age-group assignment when no safe improvement is found.
- Full production readiness requires later optimization of dates/arenas/hosts, not only participant swapping.
- Portable BookUp session takeover needs careful host/VM connectivity and secret-handling design.
- Some validation remains inherently local/manual when real BookUp MFA is involved.

## Non-decisions

This ADR deliberately does **not** choose:

- a permanent optimization library (CP-SAT, MILP, local search, etc.)
- one preferred LLM/harness or model
- one permanent set of soft-objective weights or per-age-group tuning values
- a specific BookUp browser transport such as CDP vs storage-state export
- removal of `SeasonPlanner` today

Those decisions should be made from benchmark/prototype evidence rather than guessed in advance.