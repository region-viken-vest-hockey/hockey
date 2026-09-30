---
name: rvv
description: Harness-neutral router for RVV Miniputt work. Load for any RVV planning, canonical-season maintenance, source/calendar, export, audit or publication task, then lazy-load the matching capability guidance.
---

# RVV Miniputt shared router

Shared RVV entry point for Claude, Codex, ChatGPT, Pi and future harnesses. Read `AGENTS.md` first. Harness adapters are transport/UI only; repository code owns facts, validation, persistence, evidence, export and publication safeguards. Never create a harness-local scheduler, verifier, decision controller, audit engine, scraper policy or canonical mutation implementation.

## Lifecycle overview

### Initial season creation

Use planning guidance and `scripts/rvv-miniputt run --interactive` for a deliberately opened new-season run; repository code owns stages, decisions and Stage 4 handoff.

### Promoted-season maintenance

Use production guidance and `scripts/rvv-miniputt season` for a promoted/published/sealed season; repository code owns canonical mutation, approvals, booking evidence, reconciliation and export freshness. Do not run Stage 3 merely to make maintenance pass.

## Route before loading detail

Load only the capability guidance needed for the task:

| Task | Load |
|---|---|
| Existing promoted/published season: booking, calendars, participants, constraints, moves, cancellation, audit/export | [`production/SKILL.md`](production/SKILL.md) |
| Initial/new-season planning or explicitly reopened planning lifecycle | [`planning/SKILL.md`](planning/SKILL.md) |
| Scraping, source freshness, calendar acquisition/recovery | [`sources/SKILL.md`](sources/SKILL.md) |
| Publish, republish, rollback, publication audit/scope | [`publication/SKILL.md`](publication/SKILL.md) |

Combine capability files only when the task crosses boundaries. Lifecycle routing is state-derived: promoted/published/sealed work routes to production; initial/reopened planning routes to planning. Exact command execution belongs in [`.agents/commands/rvv-miniputt/`](../../commands/rvv-miniputt/); load only the specific procedure.

## Stage gating policy

Shared soft-policy excerpts for inter-stage judgment. Repository checks own hard validity; the agent/judge only decides proceed/retry/recover/abort.

### Stage 1

Proceed when controlled input parsed into coherent season facts with no hard input errors. Abort/request correction when required dates, age groups, clubs, arenas, sources or registrations are missing/ambiguous enough to poison later stages.

### Stage 2

Proceed when scraping/source recovery produced usable evidence for meaningful planning. Blocked calendars, zero-event sources or health warnings are not automatically fatal, but must be visible; retry/recover material gaps and abort/request operator input when availability conclusions are unsafe.

### Stage 3

Proceed only with a hard-verified candidate or explicit repository decision context. Do not use prose to waive hard violations, approval locks, hosting responsibility or unresolved placement facts.

## Semantic safety-net audit

Semantic audit is second-pass judgment over repository-produced evidence, not a verifier. Use `operator audit-context`, `operator audit-evidence`, `operator audit-submit` and `operator audit-run`. Checklist:

1. Antall cuper pr lag?
2. Antall hjemmeturneringer pr lag?
3. Lengde på turneringer?
4. Er det faktisk ledig tid på is?
5. Deltar vertsklubben i samme turnering?
6. Deltar hvert lag maksimalt én gang per dag?
7. Er det normalt maks 2 lag fra samme klubb, med 3 kun som synlig unntak?
8. Er eksportformatene konsistente?
9. Ser harnesset andre materielle problemer eller manglende regler vi ikke allerede har tenkt på?

## Universal RVV invariants

These apply across all capabilities:

- Current code, tests and controlled inputs define executable truth.
- Start new/lost-context operational sessions with read-only [handover](../../commands/rvv-miniputt/handover.md); it grants no mutation/planning/publication authority.
- Operator-facing harness surface is `operate`; shared procedures and `scripts/rvv-miniputt` are repository-owned transport.
- Never hand-edit canonical state, decisions, checkpoints, exports or audit artifacts.
- Hard validity and durable identity are repository-owned; prompts/approvals/locks/waivers/bookings/audits cannot override another authority.
- Preserve provenance and revision/fingerprint identity; reject stale evidence/actions.
- Fix defects at the canonical owner, not in callers/renderers/adapters/prose.
- Generated output is derived data; fix sources/code/canonical state and regenerate.
- Planning/export does not imply publication; publication/rollback require explicit workflow and authority.

## Progressive context loading

Inspect public entry point/capability contract, canonical owner and focused tests first. Follow only relevant dependencies. For booking/reconciliation read [ADR 0005](../../../docs/adr/0005-reconciliation-is-not-planning.md). For architecture changes read engineering principles, system architecture and docs index.

## Instruction ownership

- `AGENTS.md`: repository-wide, always-on harness-neutral behavior and routing.
- this file: RVV-common invariants and capability routing.
- `rvv/<capability>/SKILL.md`: capability-specific invariants.
- `.agents/commands/rvv-miniputt/*.md`: exact shared procedures.
- `docs/` and ADRs: explanation, architecture and durable rationale.
- code/tests: executable contracts.
- GitHub issues: unfinished implementation work.

If detailed procedure prose appears here and already has an authoritative command/doc owner, remove the duplication and link to that owner instead.
