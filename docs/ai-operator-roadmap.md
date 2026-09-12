# AI operator implementation roadmap — historical

> **Status: historical implementation record.** This file is retained to explain how the current AI-operator architecture evolved. It is **not** the live backlog, not a runbook, and not an instruction source for agents. Use GitHub issues for current work, `docs/README.md` for maintained documentation, accepted ADRs for durable decisions, and `.agents/skills/rvv/SKILL.md` for shared agent policy.

The original roadmap drove the transition from a command-oriented pipeline toward a goal-oriented operator model with structured capability results, durable run state, bounded actions, human escalation, publication safety, and cross-harness adapters.

## Durable outcomes from the roadmap

The parts that remain architecturally relevant are now represented by maintained code/docs rather than by this roadmap:

- structured run/capability state → [`run-manifest-schema.md`](run-manifest-schema.md)
- goal-oriented operator entry point → repository CLI / `make operator-run`
- source-health/recovery capabilities → Stage 2 application/pipeline code and [`rvv-miniputt-pipeline.md`](rvv-miniputt-pipeline.md)
- deterministic candidate verification/measurement → planning contract, verifier, and Stage 3 implementation
- durable human questions/decisions → operator state/application layer
- publication sanitization/approval/rollback → publication application/pipeline code and deployment docs
- thin multi-harness adapters → ADR 0002, `AGENTS.md`, and `.agents/skills/rvv/SKILL.md`

## Why the detailed checklist was removed

The previous version contained a long issue-by-issue implementation checklist with many items marked “implemented” and some stale “not yet implemented” entries. Keeping that list beside current operational docs made it easy for agents to mistake historical work sequencing for present architecture or backlog.

Git history preserves the original detailed roadmap when archaeology is needed. Current incomplete work belongs in GitHub issues, where state, dependencies, discussion, and priority can be kept up to date.
