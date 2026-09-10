# Claude instructions

Read and follow the shared, tool-neutral project guidance first:

- [`docs/engineering-principles.md`](docs/engineering-principles.md)
- [`docs/system-architecture.md`](docs/system-architecture.md)
- [`AGENTS.md`](AGENTS.md)

## RVV Miniputt skill

When working with scraping, calendar generation, season planning, or pipeline debugging, use the RVV skill in `.agents/skills/rvv/SKILL.md`.

## RVV Miniputt commands in Claude

Claude does not load Pi extensions directly. Use the Claude project commands under `.claude/commands/rvv-miniputt/`:

- `/rvv-miniputt:run`
- `/rvv-miniputt:publish` — run the full pipeline and publish to GitHub Pages in one step, auto-confirmed
- `/rvv-miniputt:status`
- `/rvv-miniputt:logs`
- `/rvv-miniputt:calendars`
- `/rvv-miniputt:guide`

These commands must execute the repository-local launcher, not the Pi slash command. Never run `/rvv-miniputt ...` through a shell.

Harness-neutral entrypoints are:

- `scripts/rvv-miniputt ...`
- `python3 -m tournament_scheduler.cli.rvv_cli ...`

When planning or changing scheduling logic, review whether the rules report and related documentation must be updated to match the new behavior.

## No issue numbers in user-facing text

Never reference a GitHub issue number (e.g. "issue #302") inside strings that end up in reports, exported files, rendered HTML, rule/status descriptions, or other output consumers see (`rules_report.py`, `rules_model.py`, exported HTML/Excel, CLI-printed messages, etc.). These go stale as soon as the issue is closed or renumbered, and the people reading that output have no access to the issue tracker anyway. Explain the *reason* in plain language instead. Issue numbers belong in commit messages, code comments, and PR/issue text aimed at developers — not in anything downstream of an export.
