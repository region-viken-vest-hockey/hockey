# RVV Miniputt: scrape

Use this for deterministic troubleshooting of one configured calendar source.

```bash
scripts/rvv-miniputt scrape --club "<name>" <user-args>
```

Fallback only if needed:

```bash
python3 -m tournament_scheduler.cli.rvv_cli scrape --club "<name>" <user-args>
```

`--club` is required and should match a configured source identity from the controlled input. Report the event count, blocked/source status, and any canonical recovery hint returned by the command.

If deterministic scraping reports that browser/LLM-assisted recovery is required, follow `.agents/commands/rvv-miniputt/scrape-llm.md` and the shared RVV runbook. Do not invent source-specific recovery policy in a harness adapter.
