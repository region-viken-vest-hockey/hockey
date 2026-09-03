# RVV Miniputt

RVV Miniputt is Region Viken Vest's operational system for team registrations, calendar collection, miniputt season planning, review, publication, and handover. The scheduling core is deterministic Python. Agent harnesses such as Pi, Claude, Codex, ChatGPT, and OpenCode are thin interfaces over the same repository commands and shared RVV runbook.

## Canonical architecture

The ownership boundary is deliberate:

- `.agents/skills/rvv/` is the shared operating policy/runbook.
- `scripts/rvv-miniputt` and `tournament_scheduler/` are the canonical executable implementation.
- `.pi/`, `.claude/`, `.chatgpt/`, `.codex/`, and `.opencode/` may add harness-specific UI or browser capabilities, but must not redefine pipeline rules.
- `input.xlsx` is the controlled season-planning input.
- SharePoint is the reviewed registration store; Forms is an intake channel.
- GitHub Pages serves generated output; WordPress links or embeds it; Spond is used for operational communication/distribution.

Read `.agents/skills/rvv/SKILL.md` and `.agents/skills/rvv/RUNBOOK.md` before changing scraping, source readiness, planning, recovery, fairness, or checkpoint behavior.

## Normal operator workflow

```bash
make help
make check
make operator-run
make status
make logs
```

The goal-oriented equivalent is:

```bash
scripts/rvv-miniputt operator run
```

The four checkpointed stages remain configuration, calendar collection, planning, and export. The CLI owns resume behavior and validity; harness files must not recreate Stage 1–4 themselves.

For a complete rebuild:

```bash
make operator-run-force
```

For lower-level debugging, `make run` delegates to `scripts/rvv-miniputt run`.

## Calendar sources and BookUp

Inspect source health before approving a plan:

```bash
make sources-status
make calendars
make calendars-refresh
```

Stage 2 distinguishes unresolved sources from sources that actually block planning. Python writes `blocked`, `blocking_sources`, `temporarily_unresolved_sources`, and `planning_ready`. Tønsberg and Sandefjord Penguins are currently temporary non-blocking BookUp exceptions when their authenticated calendars cannot be reached from Lima; they remain unresolved recovery targets. Other unexpected unresolved sources remain blocking unless a human explicitly chooses `--allow-missing-sources`.

BookUp authentication should normally be established in a visible browser on the macOS host. Playwright storage state is saved under `.pipeline/auth/` and reused by headless runs in Lima. Do not make Claude/Pi/Codex independently perform credential/MFA flows as their default behavior.

The encrypted dotenvx wrappers remain available for explicit host-side credential use:

```bash
make run-dotenvx
make calendars-refresh-dotenvx
```

To refresh BookUp authentication with a visible browser on the host, follow `.agents/skills/rvv/RUNBOOK.md`. `.env.keys`, `.pipeline/auth/`, credentials, and session state must remain private and uncommitted.

A sparse calendar can still be untrustworthy even when technically reachable. Review source-health/event-count warnings before final approval.

## Recovery

A browser-capable harness may recover a source, but Python still decides whether Stage 2 is valid. After browser recovery, inject/merge the shared data and rerun Stage 2 rather than jumping directly to planning.

```bash
scripts/rvv-miniputt recovery-targets
cat recovered-events.json | scripts/rvv-miniputt recovery-inject --source "<name>"
scripts/rvv-miniputt scrape-merge
scripts/rvv-miniputt run --resume-from 2
```

`--allow-missing-sources` is a broad human override. Harnesses must never add it automatically.

## Registrations and activities

Validate reviewed SharePoint exports before changing the planning workbook:

```bash
scripts/rvv-miniputt registrations validate registrations.csv --input input.xlsx
scripts/rvv-miniputt registrations export registrations.csv --input input.xlsx --output input.updated.xlsx --dry-run
scripts/rvv-miniputt registrations export registrations.csv --input input.xlsx --output input.updated.xlsx
```

Only approved/current registrations should be imported. Private contact/comment fields must never be copied into public output.

Standalone public views can be generated without rebuilding the season plan:

```bash
make registered-teams CSV=downloads/Miniputt-26-27.csv
make aktivitetskalender
```

Publishing those views remains an explicit public mutation:

```bash
make registered-teams-publish CSV=downloads/Miniputt-26-27.csv CONFIRM_PUBLIC=1
make aktivitetskalender-publish CONFIRM_PUBLIC=1
```

## Human decisions and publication

List and resolve durable operator questions with:

```bash
make questions
make questions-all
make answer ID=<id> ANSWER='<answer>'
make promote ID=<id> SCOPE=workspace
```

Preview before publishing:

```bash
make publish-preview
make publish CONFIRM_PUBLIC=1
make verify-publish
```

Direct equivalents are also available:

```bash
scripts/rvv-miniputt operator publish --dry-run
scripts/rvv-miniputt operator publish --confirm-public
```

Generation is not publication. Public mutation and rollback retain explicit confirmation gates.

```bash
make publish-history
make rollback RUN_ID=<id> CONFIRM_PUBLIC=1
```

## Verification and dependencies

`pyproject.toml` is the canonical direct Python dependency declaration. `requirements.lock` is the deterministic, hash-checked install set used by CI and operator setup.

```bash
make dependency-lock
make check
```

CI installs the lock with `--require-hashes` and installs the project with `--no-deps`. Refresh the lock intentionally with `scripts/refresh-python-lock.sh` when dependency declarations change. Playwright browser binaries are installed separately from the Python package lock.

## Release helper

The repository still has a guarded source/tag release helper. It validates the Python project version and repository state; it no longer builds or validates a separate packaged UI.

```bash
make release-dry-run TAG=vX.Y.Z
make release TAG=vX.Y.Z
scripts/release --dry-run vX.Y.Z
```

## Make command overview

Common operator targets:

```text
make help
make check
make dependency-lock
make operator-run
make operator-run-force
make run
make run-dotenvx
make status
make logs
make calendars
make calendars-refresh
make calendars-refresh-dotenvx
make sources-status
make questions
make questions-all
make answer ID=<id> ANSWER='<answer>'
make promote ID=<id> SCOPE=workspace
make publish-preview
make publish CONFIRM_PUBLIC=1
make verify-publish
make publish-history
make rollback RUN_ID=<id> CONFIRM_PUBLIC=1
make release-dry-run TAG=vX.Y.Z
make release TAG=vX.Y.Z
```

Specialist internal/debug commands are intentionally not exposed as Make targets when a simpler operator command exists.

## Repository map

| Path | Purpose |
|---|---|
| `input.xlsx` | Controlled planning workbook |
| `.agents/skills/rvv/` | Shared RVV policy and runbook |
| `tournament_scheduler/` | Canonical validation, scraping, planning, export, recovery, and operator logic |
| `scripts/rvv-miniputt` | Harness-neutral CLI launcher |
| `scripts/check` | Canonical local/CI verification entry point |
| `Makefile` | Human-discoverable command menu |
| `.github/workflows/` | CI and browser-operated validation/review/publish/rollback workflows |
| `.pipeline/` | Generated local checkpoints, logs, decisions, caches, and private auth state |
| `export/` | Generated review/export output |
| `docs/` | Architecture, input, CI, security, and handover documentation |

## Handover principles

Critical GitHub, Microsoft 365, WordPress, Spond, and calendar-source assets should be club-controlled with a backup owner or documented recovery path. Generated files should never be patched manually as the permanent fix; change the authoritative input or implementation and regenerate.

See:

- `docs/rvv-miniputt-pipeline.md`
- `docs/rvv-miniputt-input-formats.md`
- `docs/ownership-and-handover.md`
- `docs/ci.md`
- `docs/application-architecture.md`
- `docs/ai-operator-product-direction.md`
