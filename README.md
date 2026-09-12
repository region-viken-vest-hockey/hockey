# RVV Miniputt

RVV Miniputt is Region Viken Vest's repository for miniputt administration and season planning. It takes controlled team/configuration data plus calendar evidence, produces a verified tournament plan and review material, and can publish a sanitized public snapshot to GitHub Pages.

The repository also owns two related public-data workflows: **Påmeldte lag** (registered teams) and the regional **aktivitetskalender**. These share the same publication machinery but are not part of the four-stage season-planning pipeline.

## What the system does

The repository has four operational responsibilities:

1. **Team and configuration intake** — validate/rebuild the planner roster from reviewed registrations while keeping planning settings controlled in `input.xlsx`.
2. **Season planning** — collect arena/calendar evidence, build candidate tournament plans, verify hard rules, measure plan quality, and export reviewable artifacts.
3. **Public supporting views** — generate the registered-team overview and regional activity calendar from their own controlled inputs.
4. **Publication** — build a privacy-checked static bundle and publish an explicitly approved snapshot to GitHub Pages for linking/embedding from WordPress.

The Python code is authoritative for facts, hard constraints, verification, persistence, export, and publication safety. The agent/LLM may choose among validated recovery/search/refinement actions and soft trade-offs. Humans remain responsible for credentials/MFA, explicit policy exceptions, and public publication/rollback approval. See [`docs/adr/0002-llm-directed-decision-ownership-and-thin-adapters.md`](docs/adr/0002-llm-directed-decision-ownership-and-thin-adapters.md).

## Inputs

### Season planning

| Input | Role |
|---|---|
| `input.xlsx` | Canonical controlled planning workbook. Defines season dates, age-group settings, teams, calendar sources and date preferences. |
| Reviewed SharePoint registration export | Optional CSV/XLSX source used to rebuild only the `Lag` sheet in a controlled workbook copy. |
| External club/hall calendars | Availability evidence collected in Stage 2. Source health and provenance are recorded before planning trusts the data. |
| BookUp credentials/session | Local runtime credential/session input for gated sources. Never planner data and never public output. |

The workbook contract is documented in [`docs/rvv-miniputt-input-formats.md`](docs/rvv-miniputt-input-formats.md).

### Other public workflows

| Input | Role |
|---|---|
| `Årshjul for aktiviteter.xlsx` | Source for the regional activity calendar. |
| Reviewed SharePoint/Forms team export | Source for the public **Påmeldte lag** snapshot. This is separate from the controlled `input.xlsx` planner import. |

## How season planning works

```text
reviewed registrations ─┐
                        ├─> input.xlsx
controlled settings ────┘        │
                                 ▼
                        Stage 1 — validate/config
                                 │
external calendars ──────────────┤
                                 ▼
                        Stage 2 — collect evidence
                                 │
                                 ▼
                        Stage 3 — plan/optimize
                                 │
                        deterministic verifier
                                 │
                                 ▼
                        Stage 4 — export/review
                                 │
                                 ▼
                        explicit publication
                                 │
                                 ▼
                           GitHub Pages
```

### Stage 1 — configuration

Reads `input.xlsx`, validates team identities and age-group/configuration data, and records the normalized planning input in `.pipeline/`.

### Stage 2 — calendar/source evidence

Collects configured calendars, caches source evidence, records blocked/empty/suspicious sources, and validates recovered browser/session data before it becomes usable planning input.

### Stage 3 — planning

Builds a normalized planning problem and candidate season plan. Solvers/search code performs combinatorial work; deterministic verification decides whether a candidate is valid; reproducible metrics describe participation, hosting, spacing, opponent diversity, travel and other quality dimensions. Contextual soft trade-offs may be chosen by the agent through the structured decision interface.

### Stage 4 — export

Re-verifies the candidate at the export boundary and writes the review/export bundle. A plan that fails hard verification is not serialized as an approved export.

The maintained pipeline guide is [`docs/rvv-miniputt-pipeline.md`](docs/rvv-miniputt-pipeline.md).

## Outputs

A normal Stage 4 run writes a timestamped folder under `export/`. Depending on available data, it contains:

| Output | Purpose |
|---|---|
| `season_plan.html` | Primary human season-plan view. |
| `season_plan_report.html` | Rules, quality, fairness and diagnostic review. |
| `manual_schedule.html` | Only when manual arena/hosting/calendar follow-up remains. |
| `calendars.html` | Collected calendar/source overview when source data exists. |
| `input.html` | Public-safe overview of registered teams from the controlled workbook. |
| `season_plan.xlsx` | Full Excel plan. |
| `season_plan.csv` + `season_plan_overview.csv` | Flat games and tournament overview. |
| `season_plan.ics` | Calendar feed. |
| `season_plan_spond.xlsx` | Spond-oriented import workbook. |
| `season_plan_spond_games.xlsx` | Printable tournament/game attachment for Spond. |
| `review_packets/` | Per-club review material; private/review output, not published by default. |
| activity artifacts | Generated when the configured activity data is available. |

The Stage 4 checkpoint records the exact output paths. Generated files are derived data: fix the input/config/code and regenerate rather than maintaining permanent manual edits in generated output.

### What becomes public

Publication is a separate, explicit step. The Pages bundle uses an allowlist and privacy scan. Public season-plan HTML/ICS/workbook/CSV views and the approved `activities/` / `registered-teams/` trees may be included. Spond exports, review packets, validation metadata, unknown files and suspected secrets are excluded or block publication.

GitHub Pages is the generated/public layer. WordPress should link or embed it rather than maintain a second copy of the schedule.

## Working state

| Path | Meaning |
|---|---|
| `.pipeline/` | Local checkpoints, caches, manifests, logs and decision state. Generated; not documentation. |
| `export/` | Generated review/export bundles. |
| `gh-pages` branch | Published static snapshots (`latest/` plus run/history material managed by the publication code). |

## Normal operator workflow

### 1. Install and verify

```bash
make install
make check
```

### 2. Update registrations/workbook when needed

```bash
scripts/rvv-miniputt registrations validate registrations.csv --input input.xlsx
scripts/rvv-miniputt registrations export registrations.csv --input input.xlsx --output input.updated.xlsx --dry-run
scripts/rvv-miniputt registrations export registrations.csv --input input.xlsx --output input.updated.xlsx
```

Review the resulting workbook before replacing root `input.xlsx`.

### 3. Check sources and generate a plan

```bash
make sources-status
make operator-run
make status
make logs
```

`make operator-run` is the normal goal-oriented entry point. `scripts/rvv-miniputt run` remains the direct pipeline/debugging entry point.

If the operator loop asks a real human question:

```bash
make questions
make answer ID=<id> ANSWER='<answer>'
make operator-run
```

### 4. Review and publish

```bash
make publish-preview
make publish CONFIRM_PUBLIC=1
make verify-publish
```

Generation never implies publication. Public writes and rollback remain explicitly approved operations.

### 5. Related public workflows

```bash
make aktivitetskalender
make aktivitetskalender-publish CONFIRM_PUBLIC=1

make registered-teams CSV=downloads/Miniputt-26-27.csv
make registered-teams-publish CSV=downloads/Miniputt-26-27.csv CONFIRM_PUBLIC=1
```

These stage a complete Pages snapshot so updating one public view does not accidentally remove the others.

## Command surface

`Makefile` is the human menu. `scripts/rvv-miniputt` is the portable repository launcher. `python3 -m tournament_scheduler.cli.rvv_cli` is the Python fallback.

Run `make help` for the maintained list of operator commands.

## Repository map

| Path | Purpose |
|---|---|
| `input.xlsx` | Controlled season-planning workbook. |
| `Årshjul for aktiviteter.xlsx` | Activity-calendar source workbook. |
| `tournament_scheduler/` | Application/domain/infrastructure code for planning, verification, export and publication. |
| `scripts/rvv-miniputt` | Portable CLI launcher. |
| `Makefile` | Human operator menu; intentionally thin. |
| `.agents/skills/rvv/` | Canonical shared agent runbook. |
| `.claude/`, `.chatgpt/`, `.codex/` | Thin harness adapters only. |
| `.pi/` | RVV-specific Pi command/browser/UI integration. |
| `docs/` | Maintained current documentation, ADRs and external/reference material. |

## Documentation and handover

Start with [`docs/README.md`](docs/README.md). Current behavior belongs in current docs/code, durable rationale belongs in ADRs, and unfinished work belongs in GitHub issues. Old implementation plans and generated agent state are intentionally not kept as parallel sources of truth in `main`.

The system must be transferable. Critical Microsoft 365, GitHub, WordPress, Spond and calendar-source assets should have club-controlled ownership and backup access. See [`docs/ownership-and-handover.md`](docs/ownership-and-handover.md).
