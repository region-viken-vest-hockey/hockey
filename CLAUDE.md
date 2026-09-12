# Claude instructions

Read and follow [`AGENTS.md`](AGENTS.md) first. It is the shared, tool-neutral source for repository policy, architecture/documentation precedence, command boundaries, and repository hygiene.

For RVV Miniputt scraping, calendar collection, season planning, pipeline debugging, export, or publication work, read `.agents/skills/rvv/SKILL.md`.

## Claude project commands

Claude does not load Pi extensions directly. The project commands under `.claude/commands/rvv-miniputt/` are transport adapters over the repository CLI:

- `/rvv-miniputt:run`
- `/rvv-miniputt:publish`
- `/rvv-miniputt:status`
- `/rvv-miniputt:logs`
- `/rvv-miniputt:calendars`
- `/rvv-miniputt:scrape`
- `/rvv-miniputt:scrape-llm`
- `/rvv-miniputt:guide`

They must execute the repository-local launcher/capabilities and must not define independent scheduling, source-validity, or publication policy.

Harness-neutral entrypoints are:

- `scripts/rvv-miniputt ...`
- `python3 -m tournament_scheduler.cli.rvv_cli ...`

Pi `/rvv-miniputt ...` commands are extension commands, not shell binaries; never invoke them through Bash.
