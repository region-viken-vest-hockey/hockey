---
name: tdd
description: Test-driven development with seam discipline. Use when building features or fixing bugs test-first, mentions "red-green-refactor", or wants integration tests. Enforces testing at confirmed seams only, vertical slices, and mocking at system boundaries only.
---

# Test-Driven Development (TDD) with Seam Discipline

TDD is the red → green loop. This skill makes that loop produce tests worth keeping: what a good test is, **where tests go (seams)**, the anti-patterns, and the rules of the loop. Every section applies on every cycle — consult before and during, not after.

When exploring the codebase, read `GLOSSARY.md` so test names and interface vocabulary match the project's domain language, and respect ADRs in `docs/adr/` in the area you're touching.

## What a Good Test Is

Tests verify behavior through **public interfaces at confirmed seams**, not implementation details. Code can change entirely; tests shouldn't. A good test reads like a specification:

```typescript
// GOOD: Tests observable behavior at a seam
test("canonical tournament can be approved with placement lock", async () => {
  const season = createSeason({ tournaments: [tournament] });
  const result = await approveTournament(season, tournament.id, { placementLock: true });
  expect(result.approval.placementLocked).toBe(true);
  expect(result.approval.participantsLocked).toBe(false);
});
```

Characteristics:
- Tests behavior callers/callers care about
- Uses **public API at a confirmed seam** only
- Survives internal refactors
- Describes **WHAT**, not HOW
- One logical assertion per test

## Seams: Where Tests Go

A **seam** (Michael Feathers) is a place where you can alter behaviour without editing in that place — the *location* at which a module's interface lives. **Tests live at seams, never against internals.**

**Test only at pre-agreed seams.** Before writing any test, write down the seams under test and confirm them. No test is written at an unconfirmed seam. You can't test everything, so agreeing seams up front is how testing effort lands on critical paths and complex logic instead of every edge case.

Ask: "What's the public interface, and which seams should we test?" Give each proposed seam a one-line note on what it catches and what it misses.

### RVV Miniputt Canonical Seams

These are the primary seams in this codebase. Confirm before testing elsewhere:

| Seam | Interface | What It Catches | What It Misses |
|------|-----------|-----------------|----------------|
| `SeasonPlanner` | `planSeason(input: PlanningInput): Schedule` | End-to-end scheduling logic, constraint satisfaction | Internal stage algorithms, heuristic choices |
| `CalendarSource` | `fetchCalendar(clubId: ClubId): CalendarEvent[]` | Source integration, parsing, normalization | Scraper internals, HTTP retry logic |
| `BookingRepository` | `assertBooking(assertion: BookingAssertion): void` | Booking authority, conflict detection | Storage implementation, serialization |
| `ReconciliationEngine` | `reconcile(season: CanonicalSeason): Finding[]` | Finding classification, evidence preservation | Calendar fetching, booking assertion creation |
| `ApprovalService` | `approve(tournamentId, options): ApprovalRecord` | Fingerprint verification, lock semantics, idempotency | Git persistence, notification side effects |
| `PublicationPipeline` | `publish(season: CanonicalSeason): PublicationResult` | Export generation, GitHub Pages deploy, rollback | HTML rendering, CDN propagation |

When the shape of an interface is in question (how deep the module is, where the seam belongs, what the interface should expose), call the Skill tool with "codebase-design" for the vocabulary.

## Anti-Patterns

### Implementation-Coupled
Mocks internal collaborators, tests private methods, or verifies through a side channel (querying the database instead of using the interface). The tell: the test breaks when you refactor but behavior hasn't changed.

```typescript
// BAD: Mocks internal collaborator
test("approveTournament calls verifyPlacement", async () => {
  const mockVerifier = jest.fn();
  await approveTournament(season, id, { verifier: mockVerifier });
  expect(mockVerifier).toHaveBeenCalled();
});
```

### Tautological
The assertion recomputes the expected value the way the code does, so it passes by construction and can never disagree with the code. Expected values must come from an independent source of truth: a known-good literal, a worked example, the spec.

```typescript
// BAD: Expected value recomputed same way
test("calculatePlacementFingerprint hashes fields", () => {
  const fields = { date: "2024-01-15", arena: "Arena A", time: "18:00" };
  const expected = hash(fields); // Same function!
  expect(calculatePlacementFingerprint(fields)).toBe(expected);
});

// GOOD: Independent literal
test("calculatePlacementFingerprint produces stable hash", () => {
  expect(calculatePlacementFingerprint({ date: "2024-01-15", arena: "Arena A", time: "18:00" }))
    .toBe("a1b2c3d4"); // Known-good literal from spec
});
```

### Horizontal Slicing
Writing all tests first, then all implementation. Bulk tests verify *imagined* behavior: you test the *shape* of things rather than user-facing behavior, tests go insensitive to real changes, and you commit to test structure before understanding the implementation. Work in **vertical slices** instead: one test → one implementation → repeat, each test a **tracer bullet** that responds to what the last cycle taught you.

## Rules of the Loop

- **Red before green.** Write the failing test first, then only enough code to pass it. Don't anticipate future tests or add speculative features.
- **One slice at a time.** One seam, one test, one minimal implementation per cycle.
- **Refactoring is not part of the loop.** It belongs to the review stage (see `code-review` skill), not the red → green implementation cycle.

## Mocking Discipline

Mock at **system boundaries only**:

- External APIs (HTTP clients, third-party services)
- Databases (sometimes — prefer test DB or in-memory adapter)
- Time / randomness
- File system (sometimes)

**Don't mock:**
- Your own classes/modules
- Internal collaborators
- Anything you control

### Designing for Mockability

At system boundaries, design interfaces that are easy to mock:

**1. Use dependency injection**

```typescript
// Testable: dependency injected
async function approveTournament(season, tournamentId, options, clock = realClock) { ... }

// Hard to test: creates dependency internally
async function approveTournament(season, tournamentId, options) {
  const clock = new RealClock(); // Can't substitute in test
}
```

**2. Prefer SDK-style interfaces over generic fetchers**

```typescript
// GOOD: Each function independently mockable
const calendarApi = {
  fetchEvents: (clubId) => fetch(`/clubs/${clubId}/events`),
  fetchVenues: (clubId) => fetch(`/clubs/${clubId}/venues`),
};

// BAD: Mocking requires conditional logic inside mock
const calendarApi = {
  request: (endpoint, options) => fetch(endpoint, options),
};
```

The SDK approach means each mock returns one specific shape, no conditional logic in test setup, easier to see which endpoints a test exercises, and type safety per endpoint.

## RVV-Specific Test Patterns

### Testing Canonical Mutations
Use the **revision-bound contract** as the test seam:

```typescript
test("approval produces new revision with placement lock", async () => {
  const season = createCanonicalSeason({ revision: 5, tournaments: [t] });
  const result = await approveTournament(season, t.id, { placementLock: true });
  
  expect(result.season.revision).toBe(6);
  expect(result.approval.placementLocked).toBe(true);
  expect(result.approval.fingerprint).toBeDefined();
});
```

### Testing Reconciliation Findings
Test at the `ReconciliationEngine` seam with controlled inputs:

```typescript
test("unmatched calendar event produces finding", () => {
  const season = createSeason({ 
    placements: [{ id: "t-1", date: "2024-01-15" }],
    calendarEvents: [{ date: "2024-01-15", arena: "Arena A", title: "Extra Event" }]
  });
  
  const findings = reconcile(season);
  
  expect(findings).toContainEqual(
    expect.objectContaining({ code: "UNMATCHED_CALENDAR", severity: "warning" })
  );
});
```

### Testing Approval Idempotency
```typescript
test("approving already-approved tournament is idempotent", async () => {
  const season = createSeason({ approvals: [{ tournamentId: "t-1", placementLocked: true }] });
  
  const result = await approveTournament(season, "t-1", { placementLock: true });
  
  expect(result.approval).toEqual(existingApproval); // No change
  expect(result.season.revision).toBe(originalRevision); // No new revision
});
```