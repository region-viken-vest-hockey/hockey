---
name: rvv
description: Canonical shared runbook for RVV Miniputt season planning, calendar-source recovery, plan review, export, and publication. Use for work on the hockey repo's planning pipeline.
---

# RVV Miniputt shared runbook

This is the canonical agent-facing operating policy for the RVV Miniputt repository.

Use repository code for facts, hard constraints, validation, search/solver mechanics, persistence, export and publication safeguards. Use agent judgment only for contextual soft decisions among actions the repository exposes.

Read `AGENTS.md` first for repository-wide precedence and hygiene rules.

## Command boundary

### Pi

Pi provides the RVV-specific `/rvv-miniputt ...` command/tool integration. Use the Pi command directly there; it is not a shell binary.

### Other environments

Use the repository-local entrypoints:

```bash
scripts/rvv-miniputt ...
python3 -m tournament_scheduler.cli.rvv_cli ...
```

Human-friendly operation is exposed through `make help` and the Makefile.

Harness adapters may add UI/browser/progress integration but must not redefine shared pipeline policy.

## Normal operation

For a human/operator goal-oriented run:

```bash
make operator-run
make status
make logs
```

For checkpoint-reviewed agent operation:

```bash
scripts/rvv-miniputt run --interactive --input input.xlsx
```

Do not call individual `stageN_*` modules when doing so bypasses the normal checkpoint/decision/verification path.

## Inputs

The four-stage season planner uses:

- root `input.xlsx` as the controlled planner workbook;
- reviewed registration exports only through the controlled import path when `Lag` needs rebuilding;
- external calendar/source evidence collected in Stage 2;
- local browser/session access only when a configured source requires interactive recovery.

`Årshjul for aktiviteter.xlsx` and the public registered-team CSV workflow are related repository workflows but are not Stage 1–4 planner policy inputs.

See `docs/rvv-miniputt-input-formats.md` for the workbook contract.

## Four-stage pipeline

### Stage 1 — configuration

Repository code validates and normalizes the controlled workbook.

Agent policy:

- do not invent missing teams, clubs, age groups or settings;
- treat invalid/reversed season windows and invalid identities as input problems to fix, not as soft preferences;
- when Stage 1 returns an explicit decision context, choose only from its available actions.

### Stage 2 — source/calendar evidence

Repository code owns extraction results, cache/provenance, source status and validation.

Agent policy:

- inspect blocked, empty and suspiciously sparse sources before trusting the plan;
- prefer a bounded recovery/retry action when the context exposes one;
- browser-assisted recovery is investigation/extraction only—the recovered result must return through repository validation/merge before it is trusted;
- do not declare a source healthy merely because a request technically succeeded.

Useful commands:

```bash
make sources-status
make calendars
scripts/rvv-miniputt scrape --club <name>
scripts/rvv-miniputt scrape-llm --club <name>
scripts/rvv-miniputt recovery-targets
```

### Stage 3 — planning

Repository code owns:

- normalized planning problem;
- hard constraints;
- candidate schema;
- solver/search primitives;
- deterministic candidate verification;
- reproducible quality metrics.

Agent policy:

- never accept a candidate with hard verification failures;
- use available optimize/refine/apply/keep/request actions rather than hand-editing the complete season plan in prose;
- compare candidates using the returned metrics/findings, not intuition alone;
- prefer Pareto/multi-objective evidence when several valid trade-offs exist instead of pretending one global score is absolute policy;
- do not turn a one-run preference into a new hard rule. If RVV wants a preference to become mandatory, implement/test it explicitly in deterministic code/configuration.

Typical soft dimensions include participation balance, hosting distribution, temporal spacing, opponent diversity/repetition, travel and source uncertainty. The repository measures them; the agent decides contextual priority only when no hard rule decides the outcome.

### Stage 4 — export/review

Stage 4 re-verifies the selected candidate before serialization.

Agent policy:

- hard verification failure blocks export/publication;
- review manual arena/hosting/calendar follow-up separately from plan-quality warnings;
- generated output is derived data: correct input/config/code and regenerate rather than permanently patching HTML/CSV/Excel/iCal;
- use the Stage 4 `output_files` map to know what the run actually produced.

Common outputs include the season-plan HTML/report, optional manual follow-up view, calendar/input views, Excel/CSV/iCal downloads, Spond workbooks and per-club review packets.

## Structured decision protocol

`run --interactive` returns a `DecisionContext` with facts, hard violations, warnings, metrics (when relevant), available actions and action argument/template information.

For each pause:

1. read the current context;
2. if a hard violation exists, do not bypass it;
3. choose exactly one returned available action;
4. use only the action's declared argument shape;
5. submit a concise operational rationale;
6. run the next canonical command and reassess the new context.

Do not persist or request hidden/private reasoning. The durable record only needs the action, relevant facts/outcome and concise rationale.

## Human escalation

Escalate when the repository explicitly requires human authority or information, for example:

- a real policy exception/change;
- an interactive access step that cannot be completed by the active environment;
- an impossible hard-constraint situation requiring organizer action;
- public publication or rollback approval.

Do not escalate merely because a safe repository action can be retried/refined automatically.

Human decision queue:

```bash
make questions
make answer ID=<id> ANSWER='<answer>'
make operator-run
```

## Publication

Planning/export does not imply publication.

Use:

```bash
make publish-preview
make publish CONFIRM_PUBLIC=1
make verify-publish
```

Publication creates a separate allowlisted public bundle. Review packets and Spond exports are private/review artifacts by default and should not be assumed public.

Rollback is also explicit:

```bash
make publish-history
make rollback RUN_ID=<id> CONFIRM_PUBLIC=1
```

## Related public workflows

The repository also manages:

```bash
make aktivitetskalender
make registered-teams CSV=<reviewed-registration-export.csv>
```

Their publishing variants use the same explicit Pages publication machinery but are not Stage 1–4 planner stages.

## Documentation ownership

Use these rather than creating new overlapping notes:

- `README.md` — what the system does, inputs/outputs, normal operation;
- `docs/system-architecture.md` — current end-to-end boundaries;
- `docs/rvv-miniputt-pipeline.md` — Stage 1–4 workflow;
- `docs/rvv-miniputt-input-formats.md` — workbook/input contract;
- `docs/adr/` — durable architectural rationale;
- GitHub issues — unfinished implementation work.
