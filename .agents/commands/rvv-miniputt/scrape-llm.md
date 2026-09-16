# RVV Miniputt: scrape-llm

Use this only after deterministic Stage 2 scraping identifies a source that needs browser/LLM-assisted recovery. Shared source-validity and recovery policy remains in `.agents/skills/rvv/SKILL.md`.

## Harness boundary

`scrape-llm` is a **browser-capability workflow**, not a harness-specific scheduler or scraper implementation.

- **Browser-enabled harness:** perform only the browser/navigation work needed to recover the source data.
- **Harness without browser control / terminal / CI:** do not pretend the repository CLI can drive a browser. Use an external browser-capable session and the recovery handoff below.

The Python `rvv-miniputt scrape-llm` command may be used for capability/strategy diagnostics, but it intentionally does not implement browser automation itself. Do not add a dedicated Pi/Claude/Codex/ChatGPT scraper to this repository.

## Canonical recovery handoff

List the blocked sources from repository state:

```bash
scripts/rvv-miniputt recovery-targets
```

After the harness/browser has recovered event data, return it through the repository validation path:

```bash
scripts/rvv-miniputt recovery-inject --source "<name>"
scripts/rvv-miniputt scrape-merge
```

Recovered events must pass repository validation/merge before they are trusted. Never write browser output directly into authoritative checkpoints or cache structures in a harness-specific format.

After successful recovery, continue the canonical shared `run`/resume flow so Python reassesses Stage 2 and owns all later decisions.
