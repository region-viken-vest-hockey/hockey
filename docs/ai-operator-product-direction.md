# AI operator product direction

RVV Miniputt is an agent-operated planning system built on deterministic repository capabilities.

The repository owns controlled inputs, calendar facts, hard scheduling rules, validation, reproducible measurements, solver/search mechanics, checkpoints, exports and publication controls.

The agent chooses among repository-provided actions for contextual soft decisions: which warning to address first, which valid search/refinement action to try, and which acceptable trade-off to recommend.

The human operator remains responsible for explicit policy exceptions, access steps that require a person, and final publication or rollback approval.

The agent cannot override a hard violation through prose. Candidate correctness is decided by deterministic repository verification.

## Current references

- [`system-architecture.md`](system-architecture.md) — end-to-end architecture and sources of truth.
- [`rvv-miniputt-pipeline.md`](rvv-miniputt-pipeline.md) — Stage 1–4 operation.
- [`adr/0002-llm-directed-decision-ownership-and-thin-adapters.md`](adr/0002-llm-directed-decision-ownership-and-thin-adapters.md) — durable ownership decision.
- `../.agents/skills/rvv/SKILL.md` — shared agent operating policy.
- GitHub issues — current unfinished work.

Operational ownership and transfer requirements are documented in [`ownership-and-handover.md`](ownership-and-handover.md).
