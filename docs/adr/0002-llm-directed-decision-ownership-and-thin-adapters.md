# ADR 0002: LLM-directed soft decisions, deterministic guardrails, and thin harness adapters

- **Status:** Accepted
- **Date:** 2026-09-04
- **Decision owner:** RVV hockey project
- **Related ADR:** ADR 0001

## Context

RVV Miniputt combines deterministic scheduling and validation with agent-assisted operation. As the workflow gained multiple harnesses, calendar-recovery paths, and plan-refinement mechanisms, policy started to drift between Python, prompts, and harness-specific command files.

We need one durable ownership boundary so a new harness does not become another implementation of the pipeline.

## Decision

The RVV workflow uses an **LLM-directed decision loop over deterministic repository capabilities**.

```text
controlled inputs / registrations / calendars / browser evidence
                              ↓
                   deterministic application/core
                              ↓
                       DecisionContext
        facts / hard violations / warnings / metrics /
                 available validated actions
                              ↓
                 shared RVV policy/runbook
                              ↓
                      LLM / agent
                              ↓
                       DecisionAction
                              ↓
              deterministic validator/applier
                              ↓
             new context / export / escalation
```

The LLM is the controller of contextual soft decisions. It is not the verifier of hard correctness and does not replace deterministic search/optimization where combinatorics are required.

## Ownership rules

### Deterministic code owns facts

Repository/application code is authoritative for workbook parsing, registration identities, source/calendar evidence, arena/date/host facts, provenance, fingerprints, checkpoints, manifests, and generated artifacts.

### Deterministic code owns hard rules and safety gates

Examples include candidate schema validity, valid/registered teams, impossible duplicate participation or arena overlaps, explicit locks/exclusions, declared hard source requirements, privacy rules, and publication/rollback confirmation.

An agent cannot override a hard violation through prose.

### Deterministic code owns measurement

Participation, opponent diversity/repetition, turnaround, hosting distribution, temporal distribution, travel, source health, and rule violations should be reproducible measurements. Deciding how to trade off soft metrics is separate from measuring them.

### Deterministic code provides generic execution/search capabilities

The repository may provide CP-SAT or other optimizers, local search, candidate enumeration, Pareto filtering, safe move generation, browser extraction, recovery injection, and validated candidate/action application.

These mechanisms expose possibilities; they do not silently become the owner of contextual hockey preferences.

### Agent/LLM owns contextual soft judgment

The agent decides which warning to address first, which valid refinement/search action to request, whether a small soft regression is justified by a larger gain, which source-recovery path to try, and what recommendation to present to the operator when no hard rule decides the answer.

The shared policy/runbook for these judgments is `.agents/skills/rvv/SKILL.md`.

### Humans own explicit approval and policy authority

Human input remains required for credentials/MFA, public publication or rollback approval, explicit policy changes, and decisions the system deliberately escalates.

## Structured decision contract

Interactive and headless agent transports should converge on the same small versioned protocol:

- `DecisionContext`: decision-relevant facts, hard violations, warnings, metrics, candidate references, and validated available actions.
- `DecisionAction`: one selected action plus validated arguments and a concise audit rationale.
- `DecisionResult`: accepted/rejected action, deterministic result/reference, changed findings, and next actions.

Private chain-of-thought is never a persistence requirement. Only concise operational rationale needed for audit/handover is stored.

## Harness boundary

Pi, Claude Code, ChatGPT, Codex, OpenCode, GitHub Actions, and future interfaces are adapters/transports.

They may own command registration, UI/rendering, progress/cancellation, browser-controller integration, and environment-specific launch details.

They must not independently own Stage 1–4 semantics, source-validity rules, scheduling policy, planner acceptance thresholds, or publication safety policy.

A harness adapter should therefore be conceptually:

```text
read shared RVV runbook
invoke canonical repo capability
receive DecisionContext
choose one available action
submit DecisionAction
repeat / escalate / finish
```

## Stage 2 boundary

Keep deterministic: extraction results, source status, event-count/shape evidence, cache provenance, declared hard source gates, and final merge/validation.

Agent-directed: investigation/recovery ordering, browser/session recovery strategy, and what exception or recommendation to present.

Recovered source data must return through repository validation before it is considered usable.

## Stage 3 boundary

Keep deterministic: normalized planning problem, candidate contract, hard verification, quality measurements, and generic solver/search primitives.

Agent-directed: which soft quality dimension to prioritize, which valid optimization/refinement action to request, and which valid candidate/trade-off to prefer.

The solver performs combinatorics; it does not own organizer preference judgment. Deterministic code may still encode an explicitly approved business rule when RVV chooses to make that preference a rule rather than a judgment.

## Consequences

### Positive

- one shared policy instead of harness-specific forks;
- deterministic correctness remains testable;
- agent judgment can use current context without turning into hidden hard-coded weights;
- headless and interactive operation can share semantics;
- new harnesses require little project-specific orchestration code;
- operational decisions remain auditable without storing private reasoning.

### Costs

- agent decisions can differ across models, so deterministic validation and measurable outcomes are mandatory;
- capabilities and decision schemas must stay stable enough for multiple adapters;
- some browser/authentication behavior still requires environment-specific integration and human participation.

## Non-goals

- No LLM-only correctness verifier.
- No requirement for identical schedules across models/harnesses.
- No requirement for the LLM to manually construct a full season plan.
- No new microservice/database/queue solely to implement the decision loop.
- No persistence of sensitive session material or private model reasoning.

## Supersession / precedence

ADR 0001 remains authoritative for its detailed Stage 3/BookUp decisions. This ADR generalizes the decision-ownership rule repository-wide:

> deterministic facts, hard rules, measurements, and safe execution belong in code; contextual soft judgment belongs to the agent; harness adapters remain thin.
