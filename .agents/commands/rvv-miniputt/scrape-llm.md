# RVV Miniputt: scrape-llm

Use this only after deterministic Stage 2 scraping identifies a source that needs browser/LLM-assisted recovery. Shared source-validity and recovery policy remains in `.agents/skills/rvv/SKILL.md`.

## Harness boundary

`sc​​rape-llm` is a **browser-capability workflow**, not a second scheduler CLI implementation.

- **Pi:** use `/rvv-miniputt scrape-llm` / `rvv_miniputt_scrape_llm`. Pi owns the browser interaction and active-model transport.
- **Other browser-enabled harnesses:** perform only the browser/navigation work in the harness.
- **Terminal/CI without browser control:** do not pretend the repository CLI can drive the browser. Use the recovery handoff described below.

The Python `rvv-miniputt scrape-llm` command may be used for capability/strategy diagnostics, but it intentionally does not implement browser automation itself.

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
