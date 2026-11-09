---
name: codebase-design
description: Design vocabulary for module interfaces. Use when shaping a new module, refactoring a boundary, or resolving "where does this logic belong". Produces a design map with explicit seams, ownership, and testability.
---

# Codebase Design: Vocabulary & Seam Mapping

This skill provides the vocabulary for **designing module interfaces** — not documenting them after the fact. Use when:
- Creating a new module or capability
- Refactoring a boundary between modules
- Resolving "where does this logic belong"
- Deciding what to test and where

The output is a **design map** with explicit seams, ownership, and testability. The map is the artifact the operator reviews, not code.

## Core Vocabulary

### Seam
> A place where you can alter behaviour without editing in that place — the *location* at which a module's interface lives. (Michael Feathers)

**Test at seams, never against internals.** A seam is a confirmed public interface with a contract. Not every function is a seam.

### Contract
The explicit guarantees a seam makes: inputs, outputs, errors, side effects, invariants. Documented in the interface definition (TypeScript interface, function signature, schema). **If it's not in the contract, it's not guaranteed.**

### Ownership
Every seam has **one canonical owner** — the module that defines the contract and implements it. Callers depend on the contract; they do not redefine it. When a defect is found, fix the owner, not a compensating caller.

### Testability
A seam is **testable** if:
- Its contract is fully expressible in its interface (no hidden state)
- It can be exercised without its implementation (mockable at the seam)
- Its behavior is deterministic given its inputs

### Dependency Direction
Dependencies point **toward the seam**, not away from it. The owner implements the seam; callers depend on the seam's contract.

```
[Caller] --> depends on --> [Seam Contract] <-- implemented by --> [Owner Module]
```

## Design Map Template

Before implementing or refactoring, produce a design map:

```markdown
# Design Map: <Module/Capability Name>

## Seams Exposed
| Seam Name | Interface (TypeScript/Schema) | Contract Summary | Owner | Testable? |
|-----------|-------------------------------|------------------|-------|-----------|
| `approveTournament` | `approveTournament(season, id, options): ApprovalResult` | Verifies fingerprint, records lock flags, idempotent | ApprovalService | Yes |

## Internal Boundaries (Non-Seams)
| Boundary | Why Not a Seam | Owner |
|----------|----------------|-------|
| `calculateFingerprint` | Implementation detail of ApprovalService; no caller needs it | ApprovalService (private) |

## Dependencies (Incoming)
| Caller | Seam Used | Purpose |
|--------|-----------|---------|
| CLI `season approve` | `approveTournament` | Operator approval workflow |

## Dependencies (Outgoing)
| Seam Depended On | Owner | Purpose |
|------------------|-------|---------|
| `ReconciliationEngine` | ReconciliationEngine | Verify no unresolved findings |
| `BookingRepository` | BookingRepository | Verify booking assertions current |

## Invariants (Hard Validity)
- Approval fingerprint must match current tournament placement
- Cannot approve if placement_locked=false and participant changes exist (waiver required)
- Idempotent: repeat call with same options returns same approval, no new revision

## Test Strategy
| Seam | Test Approach |
|------|---------------|
| `approveTournament` | Vertical slice at seam; mock ReconciliationEngine, BookingRepository |
```

## RVV Canonical Seams (Reference)

These are the **confirmed seams** in the RVV codebase. New modules must integrate at these seams.

| Seam | Interface | Owner | Contract Source |
|------|-----------|-------|-----------------|
| `SeasonPlanner.plan` | `plan(input: PlanningInput): Schedule` | SeasonPlanner | `planning/types.ts` |
| `CalendarSource.fetch` | `fetch(clubId): CalendarEvent[]` | CalendarSource | `sources/types.ts` |
| `BookingRepository.assert` | `assert(assertion): void` | BookingRepository | `booking/types.ts` |
| `ReconciliationEngine.reconcile` | `reconcile(season): Finding[]` | ReconciliationEngine | `reconciliation/types.ts` |
| `ApprovalService.approve` | `approve(tournamentId, options): ApprovalRecord` | ApprovalService | `approval/types.ts` |
| `PublicationPipeline.publish` | `publish(season): PublicationResult` | PublicationPipeline | `publication/types.ts` |

## Rules

1. **Seams are confirmed, not discovered.** Before writing code or tests, confirm the seam exists or should exist. Use the grilling skill to map the design tree.
2. **One owner per seam.** If two modules want to own the same seam, that's a design conflict — resolve it before coding.
3. **Contracts are immutable without operator consent.** Changing a contract is a breaking change; requires grilling.
4. **Internal boundaries are not tested.** They are implementation detail. Refactor freely.
5. **Test at the highest confirmed seam that catches the behavior.** Don't test lower than necessary.

## When to Call This Skill

- **Before** creating a new skill/command/capability: map the seams first
- **When** refactoring touches a boundary: verify seam contracts still hold
- **When** tests are flaky or coupled: check if they're testing at a non-seam
- **When** "where does this logic belong" has no clear answer: map ownership

## Integration with Grilling

The design map **is** the grilling frontier. Each seam, boundary, and invariant becomes a decision node. The operator confirms the map before implementation begins.