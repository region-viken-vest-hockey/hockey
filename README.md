# RVV Miniputt

RVV Miniputt is Region Viken Vest's repository for miniputt administration and season planning. It takes controlled team/configuration data plus calendar evidence, produces a verified tournament plan and review material, maintains the promoted operational season, and can publish a sanitized public snapshot to GitHub Pages.

The repository also owns two related public-data workflows: **Påmeldte lag** and the regional **aktivitetskalender**. These share publication machinery but are not part of the four-stage season-planning pipeline.

## What the system does

1. **Team and configuration intake** — validate/rebuild the planner roster from reviewed registrations while keeping planning settings controlled in `input.xlsx`.
2. **Season planning** — collect calendar evidence, build candidates, verify hard rules, measure quality and export reviewable artifacts.
3. **Promoted-season maintenance** — preserve a stable Git-backed baseline while clubs approve ice, request moves and trigger bounded replanning.
4. **Public supporting views** — generate registered-team and activity-calendar views from their own controlled inputs.
5. **Publication** — build a privacy-checked static bundle and publish an explicitly approved snapshot to GitHub Pages.

Python code is authoritative for facts, hard constraints, verification, persistence, export and publication safety. The active agent may choose among validated actions and soft trade-offs. Humans remain responsible for credentials/MFA, explicit policy exceptions and public publication/rollback approval.

## Inputs

### Season planning

| Input | Role |
|---|---|
| `input.xlsx` | Canonical controlled planning workbook. |
| Reviewed registration export | Optional CSV/XLSX used to rebuild only `Lag` through the controlled import path. |
| External club/hall calendars | Availability evidence collected in Stage 2. |
| Browser/session credentials when genuinely required | Runtime recovery input only; never planner data/public output. |

See [`docs/rvv-miniputt-input-formats.md`](docs/rvv-miniputt-input-formats.md).

### Other public workflows

| Input | Role |
|---|---|
| `Årshjul for aktiviteter.xlsx` | Regional activity-calendar source. |
| Reviewed team export | Public **Påmeldte lag** source. |

## Season lifecycle

```text
reviewed registrations + controlled settings
                  │
                  ▼
              input.xlsx
                  │
                  ▼
Stage 1 config → Stage 2 evidence → Stage 3 plan/search → verify → Stage 4 export
                                                               │
                                                               ▼
                                                  explicit season promote
                                                               │
                                                               ▼
                                                canonical season state
                                                               │
                         approvals / moves / bounded replan / diff / apply
                                                               │
                                                               ▼
                                                    season export + audit
                                                               │
                                                               ▼
                                                     explicit publication
                                                               │
                                                               ▼
                                                        GitHub Pages
```

Before promotion, `.pipeline/` is transient run state. After promotion, `season/<season>/schedule.json` and `season/<season>/decisions.json` are the machine-readable operational truth. GitHub Pages is the official published view, not the planning database.

## Main outputs

A Stage 4/canonical export writes review artifacts under `export/`, including the season-plan HTML/report, optional manual follow-up view, Excel/CSV/iCal, Spond workbooks and per-club review packets. The exact `output_files` map in the Stage 4 checkpoint is authoritative for what a run produced.

Generated files are derived data. Correct source/config/code/canonical state and regenerate rather than permanently patching generated output.

Publication builds a separate allowlisted public bundle. Spond exports, review packets, validation metadata and unknown/private files are not public by default.

## Working state

| Path | Meaning |
|---|---|
| `.pipeline/` | Transient checkpoints, cache, manifest, logs and run decisions. |
| `season/<season>/` | Canonical promoted schedule plus approval/lock workflow state. |
| `export/` | Generated review/export bundles with lifecycle metadata. |
| `gh-pages` branch | Published static snapshots. |

## Normal operator workflow

The normal interface is an agent harness, but **all harnesses use the same repository-owned instructions and command procedures**:

```text
AGENTS.md
  ↓
.agents/skills/rvv/SKILL.md
  ↓
.agents/commands/rvv-miniputt/<command>.md
  ↓
scripts/rvv-miniputt ...
```

Claude, Codex, ChatGPT, Pi and future harnesses should follow that same path. Harness-specific command files may exist as thin aliases where useful, but must not implement their own scheduler loop, decision prompt, semantic-audit judge, source-validity policy or browser scraper.

For a normal season run, use the shared `run` procedure and reason directly over each repository `DecisionContext`. For promoted-season work, use the shared `season` procedure. For publication, use the shared `publish` procedure.

If browser recovery is ever needed, any browser-capable harness may perform the navigation/extraction, then return recovered evidence through `recovery-inject` and `scrape-merge`. No Pi-specific or other harness-specific scraper is required.

Interactive semantic audit is also performed by the active harness itself from `operator audit-context` plus selective `operator audit-evidence` queries; do not spawn a second model conversation inside a harness adapter.

Generation never implies publication. Publication and rollback remain explicit operator decisions.

## Command/transport surface

There is one Python command transport: `tournament_scheduler.cli.rvv_cli`.

- `scripts/rvv-miniputt` is the canonical repository launcher for agent operation and selects the repository environment.
- The installed `rvv-miniputt` console script targets the same module.
- `python3 -m tournament_scheduler.cli.rvv_cli ...` is the low-level developer/test fallback.

These are transport mechanisms, not independent products. Business rules, stage sequencing, defaults and verification must not be reimplemented in shell wrappers or harness adapters.

## Developer verification

```bash
make install
make check
```

The Makefile is a developer/CI convenience layer, not a separate source of scheduling behavior.

## Repository map

| Path | Purpose |
|---|---|
| `input.xlsx` | Controlled season-planning workbook. |
| `Årshjul for aktiviteter.xlsx` | Activity-calendar source workbook. |
| `tournament_scheduler/` | Planning, verification, persistence, export and publication implementation. |
| `tournament_scheduler/cli/rvv_cli.py` | Canonical Python command transport. |
| `scripts/rvv-miniputt` | Canonical repository launcher. |
| `.agents/skills/rvv/` | Canonical shared agent runbook. |
| `.agents/commands/rvv-miniputt/` | Shared harness-neutral command procedures. |
| `.claude/`, `.chatgpt/`, `.codex/` | Optional thin harness aliases only. |
| `docs/` | Maintained current documentation and ADRs. |

## Documentation and handover

Start with [`docs/README.md`](docs/README.md). Current behavior belongs in current docs/code, durable rationale belongs in ADRs, and unfinished work belongs in GitHub issues. Critical Microsoft 365, GitHub, WordPress, Spond and calendar-source assets should have club-controlled ownership and backup access; see [`docs/ownership-and-handover.md`](docs/ownership-and-handover.md).
