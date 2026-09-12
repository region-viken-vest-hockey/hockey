# System architecture

This document describes the current high-level RVV Miniputt system. It owns the end-to-end boundaries and sources of truth; detailed workbook fields belong in `rvv-miniputt-input-formats.md` and detailed pipeline operation belongs in `rvv-miniputt-pipeline.md`.

## System shape

RVV Miniputt is primarily a repository-operated Python system, not a continuously hosted application.

It has three related workflows:

1. **Season planning** — registrations/configuration + external calendar evidence → verified season plan + review/export bundle.
2. **Påmeldte lag** — reviewed registration export → public registered-team snapshot.
3. **Aktivitetskalender** — regional activity workbook → public activity-calendar snapshot.

All three can feed the same sanitized GitHub Pages publication snapshot.

## Season-planning flow

```text
Microsoft Forms
      ↓
Power Automate validation
      ↓
reviewed private SharePoint registrations
      ↓
controlled Lag import ─────────────┐
                                   ↓
controlled settings ─────────> root input.xlsx
                                   ↓
                         Stage 1: validate/config
                                   ↓
external hall/club calendars -> Stage 2: evidence collection
                                   ↓
                         Stage 3: plan/search/solve
                                   ↓
                         deterministic verification
                                   ↓
                         Stage 4: review/export bundle
                                   ↓
                         explicit publication approval
                                   ↓
                             GitHub Pages
                                   ↓
                         WordPress links/embeds
```

## Sources of truth

- **SharePoint List** is the reviewed source for registration-workflow data.
- **Root `input.xlsx`** is the canonical controlled input to season planning. Registration import replaces only `Lag`; planning/administrative sheets remain controlled in the workbook.
- **`Årshjul for aktiviteter.xlsx`** is the activity-calendar source workbook.
- **External calendar sources** are authoritative for their own availability evidence, subject to source-health/provenance checks.
- **Repository code and tests** define deterministic parsing, hard constraints, verification, metrics, persistence, export and publication safety.
- **`.agents/skills/rvv/SKILL.md`** is the shared agent runbook for contextual/soft decisions.
- **GitHub issues** are the implementation backlog. ADRs preserve durable rationale.

Generated HTML, CSV, Excel, iCal, caches, checkpoints and Pages bundles are derived data, not new sources of truth.

## Runtime state and storage

The normal runtime is local/agent/CI execution from the repository checkout:

```text
controlled files in repo
        ↓
Python CLI / Make / harness adapter
        ↓
.pipeline/        local checkpoints, cache, manifest, logs, decisions
export/<time>/    review/export bundle
        ↓
public-bundle preparation + privacy gate
        ↓
gh-pages branch   published static snapshots
```

No database, queue, long-running web service or object store is required for normal operation.

## Decision ownership

### Deterministic repository code owns

- workbook/config parsing and normalization;
- team, club, source, arena and calendar facts;
- hard scheduling constraints and candidate validation;
- reproducible metrics and scorecards;
- solver/search mechanics;
- checkpoints, manifests, fingerprints and provenance;
- action validation/application;
- export, privacy and publication safety gates.

### Agent/LLM owns contextual soft judgment

- which warning/quality dimension to address first;
- which exposed recovery/search/refinement action to request;
- soft trade-offs when no hard rule decides the result;
- recovery strategy for suspicious/blocked sources;
- recommendations and focused escalation.

The agent acts through validated repository capabilities/decision contracts. It cannot override a hard violation through prose.

### Human operator owns

- credentials and MFA;
- explicit policy changes/exceptions requiring authority;
- final public publication/rollback approval;
- questions deliberately escalated by the system.

ADR 0002 is the durable decision for this boundary.

## Adapter boundary

Pi, Claude, ChatGPT, Codex, GitHub Actions and future interfaces are adapters over repository capabilities. They may provide command registration, UI/rendering, browser control, progress/cancellation or environment-specific launch details, but they must not maintain independent Stage 1–4 semantics.

The conceptual adapter loop is:

```text
read shared RVV runbook
invoke canonical repository capability
receive DecisionContext/result
choose one available validated action
submit action
repeat, escalate or finish
```

Pi retains RVV-specific browser/UI integration for recovery cases. Generic personal agent frameworks/tooling are intentionally outside this repository.

## Microsoft 365 boundary

Use Microsoft 365 for intake and lightweight integration: Forms submission, registration-code validation, reviewed SharePoint storage, notifications and controlled exports. Keep planning, verification and publication logic in tested repository code rather than duplicating it in Power Automate.

## Publication boundary

GitHub Pages is a static publication target, not the planning system of record. Publication:

1. starts from an already generated/reviewed export snapshot;
2. creates a separate allowlisted public bundle;
3. checks/redacts/blocklists sensitive/internal content;
4. requires explicit public-write approval;
5. updates the Pages branch and verifies the result.

Spond exports and per-club review packets remain private/review artifacts unless a deliberate separate distribution step is performed. WordPress is the editorial/navigation layer and should link/embed generated Pages output instead of copying schedules by hand.

## Generated data

Generated checkpoints, exports, reports, visualizations and evidence are not maintained documentation. Keep them under runtime/export/test/CI locations; promote only durable conclusions into current docs or ADRs.
