---
name: to-tickets
description: Convert design decisions, grilling outcomes, and review findings into GitHub issues with traceable links. Use when a decision creates work, a finding needs tracking, or a prototype decision needs implementation.
---

# To-Tickets: Decision → Issue Traceability

Every decision that creates work becomes a GitHub issue with a **traceable link** back to the decision artifact. No work exists without an issue. No issue exists without a decision trace.

## When to Use

- Grilling settles a design decision that needs implementation
- Code review finds High/Critical findings that need fixes
- Prototype decision is "Adopt" — needs real implementation
- Operator requests a capability not in current backlog
- ADR is accepted — needs implementation tasks

## Ticket Anatomy

Every ticket created by this skill has:

```markdown
---
title: "[Area] <Imperative summary>"
labels: [area:<module>, type:<feature|fix|refactor>, source:<grilling|review|prototype|adr>]
---

## Source Decision
**Decision Artifact:** <Link to HANDOVER.md, ADR, prototype decision, or review comment>
**Decision Point:** <Specific question/decision that created this ticket>
**Confidence:** <High/Medium/Low from decision artifact>

## Acceptance Criteria
- [ ] <Testable criterion 1>
- [ ] <Testable criterion 2>
- [ ] Verification: <Exact command to verify>

## Seam & Contract
**Seam:** <Which confirmed seam this touches>
**Contract Change:** <None / New seam / Contract modification — link design map>

## Dependencies
- Blocks: #<issue-number>
- Blocked by: #<issue-number>
- Related: #<issue-number>

## Implementation Notes
<Only what the decision artifact specifies. No design freedom here — that was decided.>
```

## Ticket Creation Procedure

### 1. From Grilling
When grilling frontier is empty and operator confirms:

```bash
# For each settled decision that needs work:
gh issue create \
  --title "[Approval] Implement participant lock flag on approval record" \
  --body "$(cat <<'EOF'
## Source Decision
**Decision Artifact:** HANDOVER.md (grilling round 3)
**Decision Point:** Q3 — "Should approval support participant lock separate from placement lock?"
**Confidence:** High (operator confirmed)

## Acceptance Criteria
- [ ] ApprovalRecord type adds `participantsLocked: boolean`
- [ ] CLI `season approve --participants-lock` records flag
- [ ] Idempotent: repeat call with same flags returns same record
- [ ] Verification: `scripts/rvv-miniputt season approve --season 2024-2025 --tournament-id t-abc --participants-lock --json`

## Seam & Contract
**Seam:** ApprovalService.approve
**Contract Change:** Modification — adds optional `participantsLock` to options, adds `participantsLocked` to ApprovalRecord

## Implementation Notes
- Follow TDD at ApprovalService seam
- Mock ReconciliationEngine, BookingRepository
- No changes to placement lock logic
EOF
)"
```

### 2. From Code Review
For each Critical/High finding:

```bash
gh issue create \
  --title "[Reconciliation] Fix unmatched calendar finding classification" \
  --body "$(cat <<'EOF'
## Source Decision
**Decision Artifact:** PR #234 review comment (code-review skill)
**Decision Point:** Finding [CORRECTNESS Critical] — reconciliation misclassifies duration_mismatch as matched
**Confidence:** High (test failure reproduces)

## Acceptance Criteria
- [ ] Duration mismatch produces `DURATION_MISMATCH` finding (not `MATCHED`)
- [ ] Existing `MATCHED` tests still pass
- [ ] Verification: `mise run test:reconciliation`

## Seam & Contract
**Seam:** ReconciliationEngine.reconcile
**Contract Change:** None — bug fix within contract

## Implementation Notes
- Fix in ReconciliationEngine owner module
- Add regression test at seam
EOF
)"
```

### 3. From Prototype
When prototype decision is "Adopt":

```bash
gh issue create \
  --title "[Calendar] Integrate zod schemas as single source of truth" \
  --body "$(cat <<'EOF'
## Source Decision
**Decision Artifact:** prototype/zod-schema-decision.md
**Decision Point:** "Can zod schemas serve as both validation and TypeScript types?"
**Confidence:** Medium (prototype showed partial success, needs refinement)

## Acceptance Criteria
- [ ] Zod schemas in `src/rvv/schemas/` validate all canonical types
- [ ] TypeScript types inferred via `z.infer<typeof schema>`
- [ ] No manual type duplication
- [ ] Validation < 5ms per tournament
- [ ] Verification: `mise run test:schemas`

## Seam & Contract
**Seam:** New — `CanonicalSchemas` module
**Contract Change:** New seam exposing `validateTournament`, `validateBooking`, etc.

## Implementation Notes
- TDD at new seam
- Deprecate manual types in `src/rvv/types.ts`
- Update all seam interfaces to use inferred types
EOF
)"
```

## Traceability Rules

### Every Ticket Links to Source
- `source:grilling` → links to HANDOVER.md or grilling session log
- `source:review` → links to PR review comment (GitHub URL)
- `source:prototype` → links to prototype decision artifact
- `source:adr` → links to ADR file in `docs/adr/`

### Every Decision Creates Tickets or Records "No Work Needed"
At the end of grilling/prototype/review:
```markdown
## Ticket Creation Log
| Decision | Ticket Created | Link |
|----------|----------------|------|
| Participant lock flag | Yes | #456 |
| Waiver workflow | No — already in #123 | #123 |
| Calendar source refresh | Deferred to next season | N/A |
```

### No Orphan Tickets
Every ticket must have a `Source Decision` section. If the source is lost, the ticket is invalid and should be closed.

## Integration with RVV Workflow

### RVV Canonical Mutation Tickets
For approval/move/confirm/review-baseline:
- Ticket type: `type:maintenance` (published season) or `type:planning` (initial planning)
- Area label matches capability: `area:approval`, `area:reconciliation`, `area:booking`, `area:publication`
- Must reference canonical revision in acceptance criteria

### Orca Flow Integration
When `orca-flow` picks up an issue:
1. Reads `Source Decision` to understand context
2. Runs entry gate verification from decision artifact
3. Implements via TDD at confirmed seam
4. Closes issue with verification evidence

## Anti-Patterns

### The "Vague Ticket"
```markdown
# BAD: "Improve approval workflow"
```
No seam, no contract, no verification, no source decision.

### The "Design Freedom Ticket"
```markdown
# BAD: "Refactor approval however you see fit"
```
Design was decided in grilling/prototype/ADR. Ticket executes the decision.

### The "Lost Traceability"
```markdown
# BAD: Ticket created but no link to why
```
Every ticket must answer "why does this exist?" with a link.

### The "Ticket as Discussion"
```markdown
# BAD: Ticket becomes a design debate
```
Design debates happen in grilling/prototype/ADR. Tickets are for execution.