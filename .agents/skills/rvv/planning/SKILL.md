---
name: rvv-planning
description: Shared harness-neutral policy for creating or deliberately replanning an RVV season through Stage 1-4. Do not load for ordinary maintenance of a published/sealed season.
---

# RVV initial planning

Read `AGENTS.md` and `../SKILL.md` first. This file is shared by every harness.

Use this only for initial/new-season planning or a deliberately operator-authorized reopened planning lifecycle. A published/sealed season is maintenance-only by default; load `../production/SKILL.md` instead.

Repository code owns facts, hard constraints, candidate generation/search, verification, persistence, export and deterministic gates. The active agent chooses among repository-exposed actions and contextual soft trade-offs; it must not implement a second scheduler, verifier, audit judge or decision controller.

## Routing

- End-to-end interactive planning: `../../../commands/rvv-miniputt/run.md`
- Season lifecycle/promotion: `../../../commands/rvv-miniputt/season.md`
- Source acquisition/recovery: load `../sources/SKILL.md`
- Stage 4 audit/publication: load `../publication/SKILL.md` when relevant
- Current architecture and rule ownership: `../../../../docs/system-architecture.md` and `../../../../docs/architecture/rule-catalog.md`

Use the returned `DecisionContext` as authority for available actions and argument schemas. A hard violation blocks proceed. Do not infer resume points, fabricate actions, use attempt count as proof of impossibility, or bypass checkpoint/session identity.

Planning candidates must be verified independently of the generator that proposed them. Preserve deterministic fingerprints/revisions and the reviewed Stage 4 handoff. Promotion is deliberate; publication is separate.

Planner/Stage 3 structural decomposition is next-season work. Keep behavior-preserving refactors separate from scheduling-policy changes.
