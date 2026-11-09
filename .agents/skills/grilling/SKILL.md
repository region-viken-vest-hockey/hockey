---
name: grilling
description: Relentless interview to sharpen a plan or design before canonical mutation. Maps decisions as a design tree, works the frontier in rounds, dispatches sub-agents for facts. Use before any RVV canonical mutation (approve, move, confirm, review-baseline) or when stress-testing a design.
---

# Grilling: Design Tree Interview

Grill the operator relentlessly about a plan, decision, or idea until you reach a shared understanding. Map this as a **design tree**: every decision branches into the decisions that hang off it.

Work the tree in **rounds**. The **frontier** is every decision whose prerequisites are already settled: the questions you can ask *now* without guessing at answers you haven't heard yet. Ask the whole frontier in one round: number each question and give your recommended answer. Then wait for the operator's answers before the next round.

## Round Format

```
❓ **Q1** - **<question title>**: <question body, might be multiple paragraphs, including multiple choices>

➡️ <your recommended answer>

---

❓ **Q2** - **<question title>**: <question body, might be multiple paragraphs, including multiple choices>

➡️ <your recommended answer>
```

Word each question so "yes" accepts your recommended answer.

Each round the operator's answers reshape the tree: settled decisions push the frontier outward and unblock questions that depended on them. Recompute the frontier and ask the next round. A question whose answer depends on another question still open in this round belongs to a *later* round, not this one.

## Finding Facts Is Your Job

When a frontier question needs a fact from the environment (filesystem, tools, calendar sources, canonical state), **dispatch a sub-agent with a fresh context window to find it**; don't ask the operator for anything you could look up yourself. Don't block on it: a running exploration is an unsettled prerequisite, so only the questions downstream of it wait for the sub-agent to report; ask the rest of the frontier now. The **decisions** are the operator's: put each to them and wait.

## Completion

The session is done when the frontier is empty: every branch of the design tree visited, nothing left silently assumed. **Do not act on the mutation until the operator confirms you have reached a shared understanding.**

## RVV Canonical Mutation Grilling Checklist

Before any canonical mutation (approval, move, confirm, review-baseline, booking assertion), run a grilling round covering these branches. Adapt based on the specific operation.

### Branch: Identity & Scope
- Which exact canonical tournament? (durable ID, or match criteria if ID unknown)
- Is the target season in the correct lifecycle state? (promoted/published/sealed — not initial planning)
- What is the current canonical revision?

### Branch: Preconditions
- Does the tournament already have a current, non-stale approval with the requested locks? (idempotency check)
- Are there any hard-validity violations that would block approval? (arena conflict, duration mismatch, overlap)
- Are all booking assertions for this tournament current and verified?
- Has reconciliation been run recently? Are there unresolved findings?

### Branch: Lock Semantics
- Placement lock only (default), or participant lock too? (operator must explicitly request participant lock)
- Does the operator understand placement lock prevents *future moves* but not reconciliation corrections?
- Should `--actor` be recorded for audit trail?

### Branch: Evidence & Verification
- What verification problem will the approval re-run against? (canonical, not waived)
- Are calendar sources for the host club valid and fresh?
- Is there a waiver request? (must be explicit, recorded in decisions.json with rationale)

### Branch: Side Effects & Persistence
- Will this change the canonical revision? (yes for new approval, no for idempotent)
- What exactly will be staged in Git? (only `season/<season>/decisions.json`, not pipeline/exports)
- Should a fresh export be generated after? (separate step, not automatic)
- Does this authorize publication? (no — publication is separate workflow)

### Branch: Rollback & Recovery
- If approval fails verification, what is the operator's fallback? (waive, move, cancel, investigate)
- If schedule facts change unexpectedly after approval, what is the signal to stop?

## Sub-Agent Dispatch Patterns

For fact-finding during grilling, dispatch sub-agents with these focused tasks:

```bash
# Check current approval state
scripts/rvv-miniputt season approvals --season 2024-2025 --json

# Verify hard validity for a tournament
scripts/rvv-miniputt season verify --season 2024-2025 --tournament-id t-abc123 --json

# Check reconciliation findings
scripts/rvv-miniputt season findings --season 2024-2025 --json

# Inspect canonical schedule
cat season/2024-2025/schedule.json | jq '.tournaments[] | select(.id=="t-abc123")'
```

Each sub-agent returns facts only; the grilling agent integrates them into the frontier.

## Integration with RVV Skills

**Before `rvv-confirm-tournament`:** Run grilling on the approval checklist above. Only proceed to step 2 (inspect approval state) after frontier is empty and operator confirms.

**Before `rvv-move-tournament`:** Grill on: target date/arena/host/time availability, approval lock status (cannot move if placement_locked unless operator explicitly overrides), participant impact, calendar reconciliation for new slot, booking assertion validity at new arena.

**Before `rvv-review-baseline`:** Grill on: which planning output is being promoted, whether Stage 3 is complete, whether any waivers are needed, whether the review baseline should be timestamped or overwrite previous.

**During `orca-flow` implementation:** When a design choice is ambiguous (seam placement, interface shape, mock boundary), pause and grill the frontier before coding.

## Leading Words

Use these compact concepts (recruits priors the model already holds):

- **frontier** — the questions answerable now
- **design tree** — decisions branching into dependent decisions
- **tracer bullet** — one vertical slice that responds to what the last cycle taught you
- **seam** — where behaviour can be altered without editing in that place
- **canonical-mutation** — the revision-bound contract for changing authoritative state