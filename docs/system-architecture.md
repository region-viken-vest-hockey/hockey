# System architecture

This document describes the current high-level architecture and responsibility boundaries. Keep it current when canonical inputs, workflow ownership, or public-output behavior changes.

## Core flow

```text
Microsoft Forms
      ↓
Power Automate
      ↓
Reviewed private SharePoint registrations
      ↓
controlled import of Lag rows
      ↓
root input.xlsx  ←  controlled season settings / age groups / sources
      ↓
RVV repository pipeline (Stage 1 → 4)
      ↓
reviewable exports + deterministic verification
      ↓
explicit publication approval
      ↓
GitHub Pages
      ↓
WordPress links/embeds + Spond operational use
```

## Sources of truth

- **SharePoint List** is the reviewed source of truth for registration workflow data.
- **Root `input.xlsx`** is the canonical controlled input to season planning. The normal registration import replaces only `Lag`; administrative sheets remain controlled in the workbook.
- **External calendar sources** are authoritative for their own availability evidence, subject to source-health/provenance checks.
- **Repository code and tests** define deterministic parsing, hard constraints, measurement, persistence, export, and publication safety.
- **`.agents/skills/rvv/SKILL.md`** is the canonical shared agent runbook for contextual/soft decisions.
- **GitHub issues** are the current implementation backlog. ADRs preserve architectural decisions; dated reviews and old roadmaps are historical context.

There is no active plan to move the canonical planner workbook to an `inputs/` directory. If that changes, update this document, `README.md`, the CLI defaults, and input-format documentation together.

## Decision ownership

### Deterministic code owns

- workbook/config parsing and normalization
- team, club, source, arena, and calendar facts
- hard scheduling constraints and validation
- reproducible metrics and scorecards
- checkpoints, manifests, fingerprints, and provenance
- candidate/action validation and application
- export/privacy/publication safety gates

### Agent/LLM owns contextual soft judgment

- which warning or quality dimension to prioritize
- which valid search/refinement action to request next
- trade-offs between soft metrics when no hard rule decides the outcome
- recovery strategy for suspicious or blocked sources
- what to recommend or escalate to the operator

The agent acts through validated `DecisionContext` / `DecisionAction` capabilities. It does not bypass hard constraints or replace the solver with prose.

### Human operator owns

- credentials and MFA
- explicit policy changes and exceptions that require human authority
- publication and rollback approval
- decisions the system explicitly escalates

## Adapter boundary

Pi, Claude, ChatGPT, Codex, OpenCode, GitHub Actions, and future interfaces are adapters over the repository capabilities. They may provide UI, browser integration, progress reporting, argument parsing, or rendering, but they must not maintain independent Stage 1–4 policy.

The desired adapter loop is:

```text
read shared RVV runbook
       ↓
invoke repository capability
       ↓
receive DecisionContext
       ↓
choose one available validated action
       ↓
submit DecisionAction
       ↓
repeat / escalate / finish
```

## Power Automate and Microsoft 365

Use Microsoft 365 for intake and lightweight integration: Forms submission, registration-code validation, reviewed SharePoint storage, notifications, and controlled exports. Keep scheduling, plan generation, verification, and publication logic in tested repository code rather than duplicating it in Power Automate.

## GitHub Actions

GitHub Actions runs validation/tests and provides browser-accessible review/publication workflows. Actions must call the same repository capabilities as local operation; workflow YAML is not an alternate policy engine.

## WordPress and Spond

WordPress is the public editorial/navigation layer. Prefer links or embeds to generated GitHub Pages output instead of maintaining a second copy of generated schedules.

Spond is an operational communication/event-distribution system. Only distribute an approved plan, and treat later corrections as source changes followed by regeneration/review rather than permanent manual patches to generated files.

## Generated data

Generated checkpoints, exports, reports, architecture visualizations, and run evidence are not active documentation. Keep reproducible runtime artifacts under their runtime/export locations; do not add one-off generated evidence under `docs/` unless it is intentionally promoted into a maintained document.
