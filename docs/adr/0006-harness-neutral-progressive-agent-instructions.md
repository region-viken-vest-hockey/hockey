# ADR 0006: Harness-neutral progressive agent instructions

- Status: Accepted
- Date: 2026-09-30

## Context

RVV is operated and implemented through multiple LLM harnesses. Repository policy had accumulated in large always-loaded instruction files and could be duplicated by harness adapters. That increases prompt cost, makes narrow work harder to navigate, and allows Claude/Codex/Pi/ChatGPT behavior to diverge or stale independently.

Operational facts also change. In particular, a specific season being planning, promoted, published or sealed is repository state, not a durable instruction fact.

## Decision

Maintain one repository-owned, harness-neutral instruction graph with progressive context loading.

1. `AGENTS.md` is the minimal always-loaded repository contract: ownership, source-of-truth order, architecture/safety invariants and routing.
2. `.agents/skills/rvv/SKILL.md` is the RVV router plus only invariants common to RVV work.
3. Capability skills under `.agents/skills/rvv/<capability>/SKILL.md` are lazy-loaded only when the task crosses that capability.
4. `.agents/commands/rvv-miniputt/*.md` own exact shared operational procedures.
5. Docs/ADRs own explanation and durable rationale; code/tests own deterministic semantics; GitHub issues own unfinished work.
6. Harness-specific adapters may contain bootstrap, transport or UI mechanics only. They must reference shared instructions rather than copy or redefine repository/RVV policy.
7. Routing based on mutable operational state must derive that state from authoritative repository commands/state. Instructions define the routing rule, not a hard-coded current season/lifecycle assumption.
8. Policy has one authoritative instruction owner. A more specific layer may add capability-specific invariants but must not copy a procedure or contradict an upstream invariant.
9. The instruction graph is machine-readable and architecture-tested. New capabilities/adapters update the manifest and tests rather than relying on prose discovery.

## Progressive-loading rule

Load the minimum authoritative context required to choose the next safe action:

```text
AGENTS.md
  -> RVV router when task is RVV
     -> relevant capability skill(s)
        -> exact procedure when executing it
           -> focused code/tests/docs as needed
```

Do not preload unrelated capabilities or the entire command/docs tree.

## Consequences

- Narrow agents receive less irrelevant context and have a smaller opportunity to wander into unrelated architecture.
- All harnesses share the same operational semantics.
- Adding detail requires choosing an explicit owner instead of growing an always-loaded runbook.
- Mutable season/lifecycle facts cannot silently stale in permanent instructions.
- Some tasks legitimately load multiple capability skills; the router should make those combinations explicit.
- CI may reject instruction growth, broken routes or harness policy duplication even when application code is unchanged.

## Enforcement

`.agents/instructions.yaml` declares the shared graph, size budgets and harness adapters. Architecture tests validate required files/routes, budgets, thin adapters and absence of known mutable lifecycle claims in always-on/router instructions.

Size budgets are guardrails, not optimization targets. If a file approaches its budget, first remove duplication or introduce a cohesive lazy-load boundary; do not compress important safety policy merely to satisfy a number.

## Supersession

A future instruction architecture may supersede this ADR explicitly. Until then, harness-specific convenience is not sufficient reason to fork repository policy.
