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

## Scheduling-rule implementation map

Scheduling rules must have one authoritative implementation and remain valid across initial generation, optimization, repair, promoted-season maintenance and export. Do not fix a persistent invariant only in whichever planner path first exposed the bug.

| Concern | Owning layer | Examples |
|---|---|---|
| Input/configured policy | controlled workbook/config parsing | participation targets, season window, source configuration |
| Domain facts and reusable rule math | small planner-independent deterministic modules | hosting targets, coverage, participant eligibility, availability facts |
| Hard/required verification | canonical verifier | host representation, target caps, collisions, required obligations |
| Soft deterministic measurements | scorecard/fairness measurement | hosting deviation, participation spread, temporal spacing |
| Feasible repair/action enumeration | canonical application/decision capability | `rehost_tournament`, participant swap, manual-placement fallback |
| State mutation and persistence | canonical application/season operation | atomically apply validated changes while respecting locks/approvals |
| Baseline generation/search | planner/optimizer implementations | propose candidates using the shared domain facts; never redefine the rule |
| Rules/report/export | rules model and renderers | display serialized/recomputed facts; never become the business-policy engine |
| Agent judgment | shared RVV decision protocol | choose among repository-exposed valid actions for soft trade-offs |
| Harness adapters | transport/UI only | parse/display/invoke; no independent scheduling policy |

The dependency direction for a scheduling rule is therefore:

```text
controlled policy / current evidence
        ↓
planner-independent domain facts + rule math
        ↓
verification + deterministic measurements
        ↓
validated feasible actions
        ↓
canonical mutation/persistence
        ↓
rules/report/export rendering
```

Generators and optimizers may consume these rules to construct candidates, but they do not own them. A rule that must remain true after later mutations must be independently measurable/verifiable outside the generator that produced the original candidate.

Likewise, renderers and generated HTML must consume authoritative serialized/recomputed state; they must not infer or repair scheduling policy themselves.

### Responsibility is separate from automatic placement

For hosting and similar obligations, distinguish the domain responsibility from whether a verified automatic slot currently exists. Calendar convenience must not silently transfer responsibility to another club.

Canonical behavior is:

```text
assign fair/intended hosting responsibility
        ↓
try verified automatic placement
        ↓
slot exists              no trustworthy/legal slot
    ↓                              ↓
automatic placement      keep responsibility with intended host
                         + mark MANUAL PLACEMENT REQUIRED
```

A manual-placement tournament still counts toward the intended club's assigned hosting burden. Another club must not absorb that tournament merely because it has easier or more abundant ice, unless an explicit validated operator/decision action intentionally changes the hosting responsibility.

### Change checklist for agents

When implementing or modifying a scheduling rule:

1. identify its authoritative deterministic owner before editing code;
2. implement reusable facts/math below planner/CLI/rendering layers;
3. make every candidate/mutation path consume or re-check the same rule;
4. expose legal repairs through validated repository actions instead of ad-hoc planner or harness logic;
5. preserve canonical IDs, provenance, approvals and locks when applying changes;
6. derive rules/report/export output from the final authoritative state rather than cached/stale intermediate evidence;
7. add regression coverage at the domain/verifier boundary and, when relevant, the mutation path that previously allowed the invariant to drift.

If a proposed fix only changes a baseline generator, one optimizer, one renderer, or one harness prompt for a rule that must survive later changes, the fix is incomplete.

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
