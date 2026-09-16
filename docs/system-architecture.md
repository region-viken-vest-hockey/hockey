# System architecture

This document describes the current high-level RVV Miniputt system. Detailed workbook fields belong in `rvv-miniputt-input-formats.md`; detailed operation belongs in `rvv-miniputt-pipeline.md`.

## System shape

RVV Miniputt is a repository-operated Python system, not a continuously hosted application.

It has three related workflows:

1. **Season planning and canonical-season maintenance** — registrations/configuration + calendar evidence → verified schedule → promoted operational state → review/export bundle.
2. **Påmeldte lag** — reviewed registration export → public registered-team snapshot.
3. **Aktivitetskalender** — regional activity workbook → public activity-calendar snapshot.

All three may feed the same sanitized GitHub Pages publication snapshot.

## Sources of truth

- **SharePoint List** is the reviewed source for registration-workflow data.
- **Root `input.xlsx`** is the canonical controlled input to initial season planning.
- **`Årshjul for aktiviteter.xlsx`** is the activity-calendar source workbook.
- **External calendar sources** are authoritative for their own availability evidence, subject to source-health/provenance checks.
- **Repository code and tests** define deterministic parsing, hard constraints, verification, metrics, persistence, export and publication safety.
- **`season/<season>/schedule.json` and `season/<season>/decisions.json`** are the Git-backed canonical current season state after deliberate promotion. The schedule file owns schedule facts; the decisions file owns approval/lock workflow state.
- **`.agents/skills/rvv/SKILL.md`** and `.agents/commands/rvv-miniputt/` are the shared harness-neutral operating policy/procedures.
- **GitHub issues** are the implementation backlog; ADRs preserve durable rationale.

Generated HTML, CSV, Excel, iCal, caches, checkpoints and Pages bundles are derived data. After promotion, GitHub Pages remains the official published view, while the canonical season state in Git is the machine-readable operational truth.

## Runtime state and storage

```text
controlled inputs
      ↓
shared harness instructions
      ↓
scripts/rvv-miniputt / Python CLI
      ↓
.pipeline/        transient checkpoints/cache/logs
season/<season>/  canonical schedule + decisions
export/<time>/    generated review/export bundle
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
- canonical schedule/decision persistence;
- action validation/application;
- export, privacy and publication safety gates.

### Active agent owns contextual soft judgment

- which warning/quality dimension to address first;
- which exposed recovery/search/refinement action to request;
- soft trade-offs when no hard rule decides the result;
- focused recommendations and escalation;
- semantic safety-net review using bounded repository evidence.

The agent acts through validated repository capabilities/decision contracts. It cannot override a hard violation through prose.

### Human operator owns

- credentials/MFA;
- explicit policy changes/exceptions requiring authority;
- acceptance/promotion of the operational baseline;
- public publication/rollback approval;
- questions deliberately escalated by the system.

## Harness boundary

Claude, ChatGPT, Codex, Pi and future interactive agents all consume the same shared repository instructions and command procedures:

```text
AGENTS.md
  ↓
.agents/skills/rvv/SKILL.md
  ↓
.agents/commands/rvv-miniputt/<command>.md
  ↓
scripts/rvv-miniputt ...
  ↓
repository DecisionContext/result
  ↓
active harness chooses one declared action
```

There is no RVV-specific Pi scheduler/audit/scraper implementation. Harness-local code may exist only when a transport/UI capability truly cannot be expressed through the shared repository command surface, and it must remain thin.

Browser-assisted source recovery is not tied to a particular harness. A browser-capable session may perform navigation/extraction and then hand recovered data back through repository `recovery-inject` / `scrape-merge`; repository validation determines whether it becomes trusted evidence.

Generic personal agent frameworks/tooling stay outside this repository.

## Canonical-season boundary

Once a verified schedule is promoted, normal planning becomes baseline-aware:

- durable tournament IDs survive ordinary moves/rehosting/participant edits;
- approved placement/participant locks are hard-preserve constraints;
- unapproved schedule changes receive weighted change-cost pressure to minimize churn;
- `season move` handles targeted changes;
- `season replan` + `season diff` + `season apply` handles bounded refinement;
- `season approve` / `season unapprove` owns the approval lifecycle;
- changed approval fingerprints become `stale_approval` and require explicit reapproval;
- `season export` projects the exact current canonical revision before audit/publication.

## Microsoft 365 boundary

Use Microsoft 365 for intake and lightweight integration: Forms submission, registration-code validation, reviewed SharePoint storage, notifications and controlled exports. Keep planning, verification and publication logic in tested repository code rather than duplicating it in Power Automate.

## Publication boundary

GitHub Pages is a static publication target, not the planning system of record. Publication:

1. starts from an already generated/reviewed export snapshot representing the intended canonical revision;
2. creates a separate allowlisted public bundle;
3. checks/redacts/blocklists sensitive/internal content;
4. requires explicit public-write approval;
5. updates Pages and verifies the result.

Spond exports and per-club review packets remain private/review artifacts unless deliberately distributed separately. WordPress is the editorial/navigation layer and should link/embed generated Pages output rather than copy schedules by hand.

## Generated data

Generated checkpoints, exports, reports, visualizations and evidence are not maintained documentation. Keep them under runtime/export/test/CI locations; promote only durable conclusions into current docs or ADRs.
