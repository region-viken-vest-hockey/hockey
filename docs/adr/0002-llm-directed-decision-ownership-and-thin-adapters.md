# ADR 0002: Deterministic guardrails and thin adapters

- **Status:** Accepted
- **Date:** 2026-09-04
- **Decision owner:** RVV hockey project
- **Related ADR:** ADR 0001

## Decision

RVV Miniputt uses an agent-directed decision loop over deterministic repository capabilities.

Repository code is authoritative for inputs, source facts, hard scheduling rules, candidate validation, reproducible metrics, solver/search mechanics, persistence, export and publication safeguards.

The agent is responsible for contextual soft choices: which warning to address first, which available recovery/search/refinement action to request, and which valid trade-off to recommend when no hard rule decides the answer.

Human input remains required for explicit policy changes or exceptions and for public publication/rollback approval.

## Decision protocol

Adapters should use the same structured contract:

- `DecisionContext`: facts, hard violations, warnings, metrics and available validated actions.
- `DecisionAction`: one selected action, validated arguments and concise rationale.
- `DecisionResult`: accepted/rejected action, deterministic result and next actions.

An agent cannot bypass a hard violation through prose.

## Adapter boundary

Pi, Claude, ChatGPT, Codex, GitHub Actions and future interfaces are adapters over the same repository capabilities.

They may provide command registration, rendering, progress/cancellation, browser control and environment-specific launch details. They must not independently define Stage 1–4 behavior, source-validity rules, scheduling policy or publication policy.

The conceptual loop is:

```text
read shared RVV runbook
invoke repository capability
receive DecisionContext
choose an available action
submit DecisionAction
repeat / escalate / finish
```

## Stage 2

Deterministic code owns extraction results, source status, event evidence, cache provenance and final validation. The agent may choose investigation/recovery ordering. Recovered data returns through repository validation before it is usable.

## Stage 3

Deterministic code owns the normalized planning problem, candidate contract, hard verification, quality measurements and generic solver/search primitives. The agent chooses contextual soft priorities and valid candidate trade-offs.

A preference becomes a deterministic rule only when RVV explicitly promotes it and tests it as such.

## Consequences

This boundary keeps correctness reproducible, avoids adapter-specific policy forks, lets solver technology evolve, and allows contextual plan-quality judgment without hiding it inside permanent magic weights.

The cost is that capability/decision contracts must remain stable and agent choices can vary, so deterministic verification and measurable outcomes are mandatory.
