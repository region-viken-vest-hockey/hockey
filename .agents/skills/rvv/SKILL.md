---
name: rvv
description: Harness-neutral router for RVV Miniputt work. Load for any RVV planning, canonical-season maintenance, source/calendar, export, audit or publication task, then lazy-load the matching capability guidance.
---

# RVV Miniputt shared router

This is the shared RVV entry point for Claude, Codex, ChatGPT, Pi and future harnesses. Read `AGENTS.md` first. Repository semantics live in shared code/docs/instructions; harness adapters provide transport/UI only and must not redefine policy.

Use repository code for facts, hard constraints, validation, persistence, evidence, export and publication safeguards. Use the active agent for contextual judgment only among supported repository actions. Never create a harness-local scheduler, verifier, decision controller, semantic-audit engine, scraper policy or canonical mutation implementation.

## Route before loading detail

Load only the capability guidance needed for the task:

| Task | Load |
|---|---|
| Existing promoted/published season: booking, calendars, participants, constraints, moves, cancellation, audit/export | [`production/SKILL.md`](production/SKILL.md) |
| Initial/new-season planning or explicitly reopened planning lifecycle | [`planning/SKILL.md`](planning/SKILL.md) |
| Scraping, source freshness, calendar acquisition/recovery | [`sources/SKILL.md`](sources/SKILL.md) |
| Publish, republish, rollback, publication audit/scope | [`publication/SKILL.md`](publication/SKILL.md) |

Combine capability files only when the task actually crosses those boundaries. For example, reconciling a booking against a refreshed host calendar loads production + sources; republishing a corrected sealed season loads production + publication.

**Lifecycle routing is state-derived:** establish the authoritative season lifecycle first. A promoted/published/sealed season routes ordinary operational work to `production/SKILL.md`; an initial/new-season planning lifecycle routes to `planning/SKILL.md`. Do not encode a season/year's current lifecycle in permanent instructions, and do not load Stage 3/SeasonPlanner guidance merely because a maintenance operation is difficult.

Exact command execution belongs in [`.agents/commands/rvv-miniputt/`](../../commands/rvv-miniputt/). Load the specific procedure only when executing that workflow; do not preload the entire command directory.

## Universal RVV invariants

These apply across all capabilities:

- Current code, tests and controlled inputs define executable truth. This router and capability skills define shared agent policy; active docs/ADRs explain contracts and rationale.
- Start a new/lost-context operational session with the read-only [handover procedure](../../commands/rvv-miniputt/handover.md). Handover evidence grants no mutation/planning/publication authority.
- The operator-facing harness surface is `operate`; shared procedures and `scripts/rvv-miniputt` are repository-owned transport. Do not add harness-local orchestration or another root scheduler CLI.
- Never hand-edit canonical season state, decisions, pipeline checkpoints, generated exports or audit artifacts to obtain a desired result.
- Hard validity and durable identity are repository-owned. A prompt, approval, lock, waiver, booking assertion or semantic audit cannot silently override a different authority.
- Preserve provenance and revision/fingerprint identity. Reject stale candidate/action/evidence rather than combining state from different revisions.
- Answer routine season/operational investigation through repository-owned read-only projections and the documented procedures (`season inspect ...`, `season constraints`, `season booking-status`, `season findings`, ...) — not by parsing canonical `schedule.json`/`decisions.json`/export artifacts with `python -c`, `jq`, shell pipelines or temporary scripts. Raw artifact parsing couples the harness to internal schema and duplicates domain semantics outside tests. If a needed query is not exposed, surface the tooling gap and add the projection at its canonical owner with tests; do not silently script around it.
- Fix defects at the canonical owner. Do not compensate in a caller, renderer, adapter or harness instruction.
- Generated output is derived data. Correct authoritative input/code/canonical state and regenerate.
- Planning/export does not imply publication. Publication and rollback require their explicit supported workflow and operator authority.
- Human escalation is for genuine policy/authority/information boundaries, not as a substitute for a safe repository action.

## Progressive context loading

For implementation work, inspect the public entry point/capability contract, canonical owner and focused tests first. Follow only directly relevant dependencies. Load architecture docs or ADRs when the task crosses an architectural boundary; do not ingest unrelated planner, source, publication or maintenance internals by default.

For canonical booking/reconciliation semantics read [ADR 0005](../../../docs/adr/0005-reconciliation-is-not-planning.md). For architectural changes read [engineering principles](../../../docs/engineering-principles.md), [system architecture](../../../docs/system-architecture.md), and the active docs index in [docs/README.md](../../../docs/README.md).

## Instruction ownership

- `AGENTS.md`: repository-wide, always-on harness-neutral behavior and routing.
- this file: RVV-common invariants and capability routing.
- `rvv/<capability>/SKILL.md`: capability-specific invariants.
- `.agents/commands/rvv-miniputt/*.md`: exact shared procedures.
- `docs/` and ADRs: explanation, architecture and durable rationale.
- code/tests: executable contracts.
- GitHub issues: unfinished implementation work.

If detailed procedure prose appears here and already has an authoritative command/doc owner, remove the duplication and link to that owner instead.
