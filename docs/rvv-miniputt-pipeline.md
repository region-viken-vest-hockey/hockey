# RVV Miniputt pipeline guide

## Overview

RVV Miniputt is a checkpointed four-stage planning pipeline backed by a goal-oriented operator/decision loop:

1. **Stage 1 — Config** validates root `input.xlsx` and normalizes the roster/configuration.
2. **Stage 2 — Scraping** collects calendar/source evidence and records source health/provenance.
3. **Stage 3 — Planning** builds and evaluates a season plan using deterministic constraints, metrics, and search/solver capabilities.
4. **Stage 4 — Export** verifies the selected plan and writes review/publication artifacts.

Normal operation should use the repository command surface rather than calling stage modules directly. The pipeline can resume from checkpoint state after an interruption or repaired source.

## Canonical input

Root `input.xlsx` is the only operator-maintained planning input. See [`rvv-miniputt-input-formats.md`](rvv-miniputt-input-formats.md) for the maintained workbook contract instead of duplicating the full schema here.

Important boundaries:

- `Lag` contains team identities (`club`, `label`, `age_group`).
- Age-group scheduling configuration belongs in `Aldersgrupper`.
- The canonical participation settings are the before/after-New-Year values per age group; do not add a workbook-global `deltakelser_per_lag` or normal per-team participation override.
- Reviewed SharePoint registrations may rebuild `Lag`, but must not replace the workbook's controlled administrative sheets.

Registration import:

```bash
scripts/rvv-miniputt registrations validate registrations.csv --input input.xlsx
scripts/rvv-miniputt registrations export registrations.csv --input input.xlsx --output input.updated.xlsx --dry-run
scripts/rvv-miniputt registrations export registrations.csv --input input.xlsx --output input.updated.xlsx
```

The non-dry-run export copies the controlled workbook, replaces only `Lag`, and writes an audit sidecar. Private contact/comment fields are not copied into the planner workbook or public output.

## Calendar/source collection

Stage 2 supports deterministic calendar/feed strategies and recovery paths for sources that cannot be collected reliably through the normal strategy. Source status, event evidence, cache provenance, and hard validation remain deterministic repository facts.

Use:

```bash
make sources-status
make calendars
make calendars-refresh
```

For a specific source:

```bash
scripts/rvv-miniputt scrape --club <name>
scripts/rvv-miniputt scrape-llm --club <name>
scripts/rvv-miniputt recovery-targets
```

Browser-assisted recovery depends on the active harness/environment having browser control. A plain terminal/CI session cannot pretend to drive a browser; recover event data externally when needed and return it through the repository's recovery injection/merge path so Stage 2 validates it before use.

### Credentials and MFA

Credentialed sources must use the documented local encrypted/session mechanisms; secrets, cookies, tokens, storage-state files, or MFA artifacts must not be committed or copied into command text/logs. If a source requires an operator-completed MFA/login step, use the supported manual-login/session handoff rather than weakening source validity.

### Sparse but technically successful sources

A source can be reachable yet still be untrustworthy if it returns suspiciously little data for the planning window. Stage 2/source-health output should surface that evidence. Treat source sufficiency as a decision based on current facts and the shared RVV runbook; do not equate scraper success with trustworthy planning coverage.

## Planning

Stage 3 separates deterministic correctness/measurement from contextual plan-quality judgment:

- code owns normalized inputs, hard constraints, candidate verification, reproducible metrics, and search/solver mechanics;
- the agent/controller may choose among exposed valid refinement/search actions and soft trade-offs;
- the operator decides explicit exceptions/policy changes when human authority is required.

The decision boundary is documented in ADR 0002 and `.agents/skills/rvv/SKILL.md`.

### Interactive checkpoint-reviewed agent flow

Harness adapters use the canonical interactive capability:

```bash
scripts/rvv-miniputt run --interactive --input input.xlsx
```

Each pause returns a structured `DecisionContext` with facts, hard violations, warnings, `available_actions`, and a `decision_action_template`. The controller must choose only a returned available action and submit it through the same command surface on the next invocation.

Harness-specific `.claude`, `.chatgpt`, `.codex`, `.opencode`, and Pi files are transports/UI integrations only. They must not redefine Stage 1–4 policy.

### Goal-oriented human/operator flow

For normal local operation:

```bash
make help
make operator-run
make status
make logs
```

Force a full rerun only when necessary:

```bash
make operator-run-force
```

Inspect unresolved questions when the operator loop needs explicit human input:

```bash
make questions
make answer ID=<id> ANSWER='<answer>'
make operator-run
```

## Output and review

Stage 4 produces the configured review/export bundle, including season-plan HTML/CSV/XLSX/iCal, reports, calendar/input views, activity output where configured, Spond-oriented data, and supporting manifests/evidence.

Generated files are derived data. Do not permanently patch generated HTML, CSV, Excel, iCal, Pages files, or Spond import files by hand. Correct the source/config/code and regenerate.

Before publication, review at least:

- hard verification status and manual-placement/conflict output;
- participation/hosting/temporal fairness findings;
- source-health uncertainty that can affect the plan;
- Rules/report content against the canonical workbook and verifier;
- privacy/public-bundle report.

## Publication

Generation and publication are separate operations. Public writes require explicit approval.

```bash
make publish-preview
make publish CONFIRM_PUBLIC=1
make verify-publish
```

Publication builds a sanitized public bundle and updates GitHub Pages through the protected repository path. WordPress should link/embed generated Pages output rather than maintaining a second generated schedule copy.

Use publication history/rollback commands for recovery; do not rewrite generated public files manually.

## Human-readable command surface

| Task | Command |
|---|---|
| Discover commands | `make help` |
| Verify repository | `make check` |
| Goal-oriented run | `make operator-run` |
| Pipeline status/logs | `make status`, `make logs` |
| Source health | `make sources-status` |
| Calendar refresh | `make calendars-refresh` |
| Pending decisions | `make questions` |
| Publication preview | `make publish-preview` |
| Publish approved bundle | `make publish CONFIRM_PUBLIC=1` |
| Verify publication | `make verify-publish` |

The underlying portable launcher is `scripts/rvv-miniputt`; the Python CLI fallback is `python3 -m tournament_scheduler.cli.rvv_cli`.

## Documentation ownership

- [`README.md`](../README.md): human/operator overview and handover entry point.
- [`docs/README.md`](README.md): documentation map and authority rules.
- [`rvv-miniputt-input-formats.md`](rvv-miniputt-input-formats.md): canonical workbook/interchange schema.
- [`system-architecture.md`](system-architecture.md): current system/source-of-truth boundaries.
- [`adr/`](adr/): durable decisions and rationale.
- `.agents/skills/rvv/SKILL.md`: shared agent operational policy/runbook.
- GitHub issues: live implementation backlog.

Do not add dated investigation reports or generated runtime evidence as a new competing runbook. Promote durable decisions into an ADR or maintained doc, and keep generated evidence in runtime/export/CI locations.
