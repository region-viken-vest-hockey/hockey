Scrape a single club's calendar with LLM-guided browser navigation. Use for sources blocked by deterministic scraping (BookUp SPA, Forumbooking, Sportello, StyledCalendar).

Rules:
- Never run `/rvv-miniputt ...` as a shell command.
- Use `scripts/rvv-miniputt scrape-llm --club "<name>" <user-args>`.
- Fallback if needed: `python3 -m tournament_scheduler.cli.rvv_cli scrape-llm --club "<name>" <user-args>`.
- `--club` is required (e.g. `Jar`, `Holmen`, `Jutul`, `Tønsberg`). Sandefjord has no scraper strategy (issue #261) — it's a `fixed_allocation` source, not scraped at all.
- Results are cached to `.pipeline/cache/scraped_data.json` by default.
- After scraping blocked sources, resume the pipeline with `scripts/rvv-miniputt run --resume-from 3`.

When to use:
Run this after stage 2 reports blocked sources, then resume from stage 3 to replan with the new data.

Flags:
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

Examples:
- `scripts/rvv-miniputt scrape-llm --club Jar`
- `scripts/rvv-miniputt scrape-llm --club Holmen --max-iterations 30`
