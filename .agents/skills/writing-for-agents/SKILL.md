---
name: writing-for-agents
description: Authoring skills and docs that agents can execute reliably. Use when writing or reviewing any skill, command procedure, ADR, or instruction file. Enforces executable contracts, minimal indirection, and lazy-loading boundaries.
---

# Writing for Agents: Executable Documentation

Agents don't read like humans. They execute. This skill defines how to write skills, procedures, and docs that agents follow correctly on the first pass without re-reading or clarification loops.

## Core Principles

### 1. Executable Contracts, Not Prose
Every instruction must be a testable contract with:
- **Preconditions**: What must be true before starting
- **Steps**: Exact operations in order (bash, read, edit, tool calls)
- **Verification**: How to confirm each step succeeded
- **Postconditions**: What is true after completion

```markdown
# BAD: Prose instruction
"Check the schedule and make sure tournaments don't overlap."

# GOOD: Executable contract
## Preconditions
- `season/2024-2025/schedule.json` exists
- `jq` available

## Steps
1. `jq '.tournaments | group_by(.date + .arena) | map(select(length > 1))' season/2024-2025/schedule.json`

## Verification
- Output is empty array `[]` → no overlaps
- Non-empty → list of conflicting tournaments

## Postconditions
- Operator informed of any conflicts with tournament IDs
```

### 2. Lazy-Load by Default
Skills and procedures **must not** require reading other files to execute. If context from another file is needed:
- Embed the essential content inline, OR
- Provide an explicit "load X skill first" step with the exact tool call

```markdown
# BAD: "Read the RVV skill first"
# GOOD:
## Step 0: Load RVV capability
`skill read rvv-confirm-tournament`
```

### 3. One Reason to Change
A skill/procedure has one reason to change: its capability. If a change requires touching multiple skills, the ownership is wrong. Factor the shared concern into a dedicated skill or procedure.

### 4. No Hidden State
Every tool call must have its arguments fully specified in the skill. No "use the same parameters as before" or "continue from where you left off" — agents don't have persistent memory between invocations.

### 5. Verification Gates
Every multi-step procedure has explicit gates:
- **Entry gate**: Preconditions checked
- **Progress gates**: Verification after each major step
- **Exit gate**: Postconditions confirmed

If a gate fails, the procedure stops and reports the failure. No silent continuation.

## Skill Anatomy

Every skill file follows this structure:

```markdown
---
name: <kebab-case-name>
description: <When to use this skill. One sentence trigger.>
---

# <Title>

## When to Use
<Explicit triggers: user says X, task involves Y, condition Z>

## Prerequisites
<Files that must exist, tools that must be available, state that must hold>

## Procedure
### Step 1: <Name>
<Exact tool calls with arguments>
<Verification>

### Step 2: <Name>
...
```

## Procedure Files (`.agents/commands/`)

Procedures are **exact command sequences** — no agent judgment required. They are the "how", skills are the "when/why".

```markdown
# Procedure: Confirm Tournament

## Entry Gate
- [ ] `season/2024-2025/schedule.json` exists
- [ ] Tournament ID provided or resolvable

## Steps
1. **Inspect approval state**
   ```bash
   scripts/rvv-miniputt season approvals --season 2024-2025 --json
   ```
   → Verify output contains tournament, check `placementLocked`, `participantsLocked`, `fingerprint`

2. **Verify hard validity**
   ```bash
   scripts/rvv-miniputt season verify --season 2024-2025 --tournament-id <id> --json
   ```
   → Verify `valid: true`, no `hardViolations`

3. **Record approval**
   ```bash
   scripts/rvv-miniputt season approve --season 2024-2025 --tournament-id <id> --placement-lock
   ```
   → Verify new approval record in `decisions.json`, revision incremented

## Exit Gate
- [ ] Approval recorded with correct locks
- [ ] Canonical revision incremented
- [ ] No unresolved hard violations
```

## Anti-Patterns

### The "Context Soup"
```markdown
# BAD: Assumes agent has context from earlier
"Then run the usual verification steps..."
```
Every step must be self-contained.

### The "Delegation Without Contract"
```markdown
# BAD: Delegates without specifying what the delegate returns
"Use a researcher to check the calendar sources."
```
```markdown
# GOOD: Explicit delegate contract
acp_delegate({
  agent: "researcher",
  task: "Check calendar source validity for club HC-123. Return: source URL, last fetch time, event count, any errors."
})
```

### The "Implicit Dependency"
```markdown
# BAD: "This uses the standard RVV types"
# GOOD: Explicit import
"Import types from `src/rvv/types.ts`: `CanonicalTournament`, `ApprovalRecord`, `BookingAssertion`"
```

## Review Checklist for Skill/Procedure Authors

Before committing a skill or procedure:

- [ ] **Trigger is specific** — "when user says X" not "when relevant"
- [ ] **Prerequisites are checkable** — file exists, tool available, state verifiable
- [ ] **Every step has exact tool calls** — copy-paste executable
- [ ] **Every step has verification** — how to know it worked
- [ ] **No references to "earlier" or "previous" steps** — each step standalone
- [ ] **Lazy-load boundaries explicit** — "load skill X" with exact call
- [ ] **Owner is clear** — one module/skill owns each contract
- [ ] **Glossary terms used exactly** — check `GLOSSARY.md`
- [ ] **No markdown linting errors** — run `mise run check` or equivalent

## Integration with Codebase Design

The design map (from `codebase-design` skill) **is** the skill's contract section. When writing a skill that creates or modifies a seam, include the design map as the skill's specification.

## Integration with TDD

Skills that produce code **must** specify the test seam and expected test pattern (from `tdd` skill). The skill's procedure ends with "run tests at seam X" not "write tests".