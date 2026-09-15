# RVV Miniputt: scrape-llm

Use this only when deterministic scraping has identified a source that needs browser/LLM-assisted recovery. Shared source-validity and recovery policy remains in `.agents/skills/rvv/SKILL.md`.

Canonical repository entrypoint:

```bash
scripts/rvv-miniputt scrape-llm --club "<name>" <user-args>
```

Fallback command surface:

```bash
python3 -m tournament_scheduler.cli.rvv_cli scrape-llm --club "<name>" <user-args>
```

The portable CLI does not itself create browser capability. Use the active harness's browser integration when available. If the active environment cannot perform live browser recovery, use the repository recovery path instead:

```bash
scripts/rvv-miniputt recovery-targets
python3 -m tournament_scheduler.cli.rvv_cli recovery-inject --source "<name>"
scripts/rvv-miniputt scrape-merge
```

Recovered events must return through repository validation/merge before they are trusted. Never write recovered browser data directly into authoritative checkpoints/cache structures in a harness-specific format.

After successful recovery/merge, continue the canonical run/resume flow rather than maintaining a separate harness-side pipeline.
