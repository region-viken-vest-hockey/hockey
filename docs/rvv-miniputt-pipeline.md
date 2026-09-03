# RVV Miniputt pipeline

The executable RVV Miniputt workflow is owned by `scripts/rvv-miniputt` and the Python package under `tournament_scheduler/`. Harness-specific commands are adapters, not alternative pipeline implementations.

For canonical operational policy—including Stage 2 source readiness, recovery, BookUp authentication, and harness boundaries—read:

- `.agents/skills/rvv/SKILL.md`
- `.agents/skills/rvv/RUNBOOK.md`

## Normal operator flow

```bash
make operator-run
make status
make logs
```

or directly:

```bash
scripts/rvv-miniputt operator run
scripts/rvv-miniputt status
scripts/rvv-miniputt logs list
```

The four stages remain:

1. validate configuration
2. collect/cache calendar data and decide source readiness
3. generate/evaluate the season plan
4. export review/publication artifacts

Checkpoints live under `.pipeline/stage*.json`. The CLI owns resume behavior and stage validity.

## Stage 2 and BookUp

Stage 2 distinguishes unresolved sources from sources that must block planning. Python writes `blocked`, `blocking_sources`, `temporarily_unresolved_sources`, and `planning_ready`; adapters must use those results rather than recalculating them.

Tønsberg and Sandefjord Penguins are currently temporary non-blocking BookUp exceptions when authenticated calendars cannot be reached. They remain unresolved recovery targets. Other unexpected calendar failures remain blocking unless a human explicitly chooses `--allow-missing-sources`.

BookUp authentication should normally be established/refreshed in a visible browser on the macOS host and stored as Playwright state under `.pipeline/auth/`. Headless runs in Lima reuse that state. See the shared runbook for the exact host command and security notes.

## Browser-capability boundary

Deterministic scraping belongs to Python. A browser-capable harness such as Pi may recover a browser-only source, but recovered events must be written back to the shared cache and Python Stage 2 rerun before planning continues.

For terminal-only recovery, inspect targets and inject externally recovered event JSON through the repository bridge:

```bash
scripts/rvv-miniputt recovery-targets
cat recovered-events.json | python3 -m tournament_scheduler.cli.rvv_cli recovery-inject --source "<source>"
scripts/rvv-miniputt scrape-merge
scripts/rvv-miniputt run --resume-from 2
```

`recovery-inject` reads a JSON array from stdin. `recovery-inject`/`scrape-merge` do not create a separate readiness policy; the normalized checkpoint uses the same Python source classification as Stage 2, and the final Stage 2 rerun owns the continue/stop decision.

## Calendar inspection

```bash
make sources-status
make calendars
make calendars-refresh
```

On the macOS host, BookUp auth refresh can be run through the encrypted dotenvx environment:

```bash
make calendars-refresh-dotenvx ARGS='--manual-bookup-login'
```

A source can be technically reachable yet suspiciously sparse. Review source-health/event-count warnings before approving a schedule.

## Planning and publication

Planning/fairness rules are Python behavior. Do not copy thresholds or retry policy into Claude/Pi/Codex command files.

Useful operator commands:

```bash
make questions
make answer ID=<id> ANSWER='<answer>'
make publish-preview
make publish CONFIRM_PUBLIC=1
make verify-publish
make publish-history
make rollback RUN_ID=<id> CONFIRM_PUBLIC=1
```

Generation is not publication. Public mutation remains behind explicit confirmation safeguards.

## Verification

```bash
make check
make dependency-lock
```

See `docs/ci.md` for the canonical check phases and locked dependency workflow.
