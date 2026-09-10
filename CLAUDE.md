# Claude instructions

Read and follow [`AGENTS.md`](AGENTS.md) first — it is the single source of truth for the shared, tool-neutral project guidance (engineering principles, system architecture, command surface). The rest of this file is Claude-specific additions only.

## RVV Miniputt skill

When working with scraping, calendar generation, season planning, or pipeline debugging, use the RVV skill in `.agents/skills/rvv/SKILL.md`.

## RVV Miniputt commands in Claude

Claude does not load Pi extensions directly. Use the Claude project commands under `.claude/commands/rvv-miniputt/`:

- `/rvv-miniputt:run`
- `/rvv-miniputt:publish` — publish the last Stage 4 export already on disk to GitHub Pages, auto-confirmed; never runs or reruns the pipeline (use `/rvv-miniputt:run` first for a fresh export)
- `/rvv-miniputt:status`
- `/rvv-miniputt:logs`
- `/rvv-miniputt:calendars`
- `/rvv-miniputt:guide`

These commands must execute the repository-local launcher, not the Pi slash command. Never run `/rvv-miniputt ...` through a shell.

Harness-neutral entrypoints are:

- `scripts/rvv-miniputt ...`
- `python3 -m tournament_scheduler.cli.rvv_cli ...`

When planning or changing scheduling logic, review whether the rules report and related documentation must be updated to match the new behavior.

## No issue numbers in user-facing text or code comments

Never reference a GitHub issue number (e.g. "issue #302") inside strings that end up in reports, exported files, rendered HTML, rule/status descriptions, or other output consumers see (`rules_report.py`, `rules_model.py`, exported HTML/Excel, CLI-printed messages, etc.), and don't put them in code comments either. These go stale as soon as the issue is closed or renumbered, and the reader — whether a report consumer or a future dev without tracker access — has no reference to look it up. Explain the *reason* in plain language instead. Issue numbers belong only in commit messages and PR/issue text.
