---
name: "RVV Miniputt: Scrape LLM"
description: "Scrape a single club's calendar with LLM-guided browser navigation"
category: RVV
---

Scrape a single club's calendar using LLM-guided browser navigation. Use this for sources that are blocked by deterministic scraping (BookUp SPA, Forumbooking, Sportello, StyledCalendar).

## Rules

- Never run `/rvv-miniputt ...` as a shell command.
- Use the harness-neutral repo entrypoint:

```bash
scripts/rvv-miniputt scrape-llm --club "<name>" <user-args>
```

- Fallback if the launcher is unavailable:

```bash
python3 -m tournament_scheduler.cli.rvv_cli scrape-llm --club "<name>" <user-args>
```

- The portable CLI still needs browser automation to actually scrape.
  - Pi: available through the extension tool / slash-command path.
  - Browser-enabled harnesses: only when they provide their own Playwright/browser controller.
  - Plain terminal or CI: unsupported for live browser navigation; use `scripts/rvv-miniputt recovery-targets` to list blocked sources, recover the event JSON with WebFetch or your own script, then pipe it into `python3 -m tournament_scheduler.cli.rvv_cli recovery-inject --source "<navn>"` and finish with `scripts/rvv-miniputt scrape-merge`.
- `--club` is required. Must match a source that has an LLM scraper strategy (e.g. `Jar`, `Holmen`, `Jutul`, `Tønsberg`). Sandefjord has no scraper strategy (issue #261) — it's a `fixed_allocation` source, not scraped at all.
- Results are cached to `.pipeline/cache/scraped_data.json` by default (`--cache-results` is on).
- After scraping, suggest running `/rvv-miniputt:run --resume-from 3` to replan with the new data.

## When to use

Run this after stage 2 reports blocked sources:
```
⚠ Jar — blocked
⚠ Holmen — blocked
```
Then scrape each blocked source with this command before resuming from stage 3.

## Flags

```
--club <name>           Source name (required)
--work-dir <path>       Pipeline work directory (default: .pipeline)
--export-dir <path>     Export directory for debug screenshots (default: export)
--endpoint <url>        LLM API endpoint (default: http://host.lima.internal:1234)
--model <name>          LLM model name (default: qwen2.5-32b-instruct)
--max-iterations <N>    Max browser interaction cycles (default: 20)
--cache-results         Cache scraped events (default: true)
--debug-screenshots     Save PNG screenshots at each step to export/debug-screenshots/
```

## Examples

- `scripts/rvv-miniputt scrape-llm --club Jar`
- `scripts/rvv-miniputt scrape-llm --club Holmen --max-iterations 30`
