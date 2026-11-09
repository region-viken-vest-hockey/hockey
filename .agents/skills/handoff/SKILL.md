---
name: handoff
description: Structured context transfer between agents. Use when handing off work to another agent (or future self). Produces a verified handover document with exact state, decisions, and next steps.
---

# Handoff: Verified Context Transfer

A handoff transfers **executable context** — not a summary. The receiving agent must be able to continue without re-reading history or asking clarifying questions.

## When to Use

- Handing off to another agent (orca-cli, acp_delegate, human operator)
- Ending a session with work in progress
- Switching between capabilities (e.g., grilling → TDD implementation)
- Pausing a multi-step procedure

## Handover Document Structure

Every handoff produces a `HANDOVER.md` at the working directory root:

```markdown
# Handover: <Task Name>

## Status
<In Progress / Blocked / Ready for Review / Complete>

## Current State (Exact)
- **Working directory:** `/absolute/path`
- **Git branch:** `agent/issue-123`
- **Base commit:** `abc1234`
- **Canonical revision (if RVV):** `season/2024-2025 revision 7`

## Decisions Made (Design Tree Settled)
| Decision | Answer | Confidence | Glossary Term |
|----------|--------|------------|---------------|
| Seam for approval | ApprovalService.approve | Confirmed | canonical-mutation |
| Lock semantics | Placement lock only | Confirmed | placement-lock |

## Open Decisions (Frontier)
| Question | Depends On | Blocker |
|----------|------------|---------|
| Participant lock for tournament t-abc? | Operator input | Waiting on grilling round 3 |

## Completed Steps (Verified)
- [x] Step 1: Loaded RVV skill — verified via `skill read rvv`
- [x] Step 2: Inspected approval state — `scripts/rvv-miniputt season approvals --json` returned 3 approvals
- [x] Step 3: Verified hard validity — `scripts/rvv-miniputt season verify --json` returned `valid: true`

## Next Steps (Exact Commands)
1. **Run grilling round 3** (operator decisions needed):
   ```bash
   # Ask operator: participant lock for t-abc?
   # Then: scripts/rvv-miniputt season approve --season 2024-2025 --tournament-id t-abc --participants-lock
   ```

2. **If approved, generate export**:
   ```bash
   scripts/rvv-miniputt season export --season 2024-2025 --format json
   ```

## Files Modified (Since Base)
- `season/2024-2025/decisions.json` — added approval for t-abc (staged)
- `.agents/commands/rvv-miniputt/handover.md` — this file

## Tests Passing
- `mise run check` — passes
- `mise run test:approval` — passes

## Blockers / Risks
- Calendar source for HC-456 stale (last fetch 14 days ago) — may need refresh before reconciliation
- Operator unavailable until 14:00 for participant lock decision

## Environment
- Node: 22.12.0
- mise: 2025.1.0
- RVV CLI: 1.3.0
```

## Handoff Protocol

### Sender Responsibilities
1. **Stop at a clean boundary** — after a verified step, not mid-operation
2. **Run verification gates** — all checks passing before handoff
3. **Write HANDOVER.md** — use the exact template above
4. **Stage relevant files** — `git add` so diff is visible
5. **Communicate the handoff** — tell the receiver "Handover ready at HANDOVER.md"

### Receiver Responsibilities
1. **Read HANDOVER.md first** — before any other action
2. **Verify entry gates** — run the verification commands listed
3. **Confirm state matches** — git status, canonical revision, test results
4. **Acknowledge or question** — "State verified, continuing from Step 4" or "Discrepancy: X"

## RVV-Specific Handoff Requirements

For RVV canonical mutations, handoffs **must** include:
- Current canonical revision (from `season/<season>/schedule.json` or `.revision`)
- Approval state for affected tournaments (fingerprint, locks)
- Any pending reconciliation findings
- Whether publication is authorized/pending

## Integration with Orca CLI

When using `orca-cli` for worktree handoffs:
```bash
# Sender: create handover in worktree
orca worktree comment add --worktree agent/issue-123 --file HANDOVER.md

# Receiver: read handover from worktree
orca worktree comment read --worktree agent/issue-123 --file HANDOVER.md
```

## Integration with ACP Delegate

When delegating via `acp_delegate`:
```json
{
  "agent": "worker",
  "task": "Continue from HANDOVER.md at /path/to/worktree. Entry gates: verify canonical revision 7, approval state for t-abc. Next step: grilling round 3 for participant lock.",
  "cwd": "/path/to/worktree"
}
```

## Anti-Patterns

### The "Summary Handoff"
```markdown
# BAD: "I did the approval stuff, you do the export"
```
No executable context. Receiver must re-discover state.

### The "Stale Handoff"
```markdown
# BAD: HANDOVER.md written 3 steps ago, not updated
```
Handoff document must reflect **current** state at moment of transfer.

### The "Implicit Context"
```markdown
# BAD: "You know what to do"
```
Every next step must be an exact command with expected output.