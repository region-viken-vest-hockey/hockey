# RVV Miniputt shared runbook

This is the harness-independent operational runbook for the RVV Miniputt pipeline. Pi, Claude, Codex, ChatGPT, OpenCode, humans, and future adapters should use the same repository CLI and should not reimplement stage rules in their own command files.

## Ownership boundaries

- `.agents/skills/rvv/` owns shared operating policy and this runbook.
- `scripts/rvv-miniputt` plus `tournament_scheduler/` own executable behavior: stage semantics, source readiness, cache/recovery behavior, fairness gates, checkpoints, export, and failure handling.
- Harness adapters may add UI, progress, or browser capabilities. They must return recovered data to the Python pipeline and let Python decide whether the next stage is valid.
- Harness-specific files must not maintain their own lists of acceptable missing sources or their own Stage 1–4 implementations.

## Normal execution

Use the repository entry point:

```bash
scripts/rvv-miniputt run
```

Common operator options remain CLI options, for example:

```bash
scripts/rvv-miniputt run --force-refresh
scripts/rvv-miniputt run --resume-from 2
scripts/rvv-miniputt status
scripts/rvv-miniputt sources status
scripts/rvv-miniputt recovery-targets
```

`--allow-missing-sources` is an explicit broad operator override. Do not add it automatically from a harness.

Review the canonical checkpoints when diagnosing a run:

- `.pipeline/stage1_config.json`
- `.pipeline/stage2_scraping.json`
- `.pipeline/stage3_planning.json`
- `.pipeline/stage4_export.json`

## Stage 2 source readiness

Stage 2 distinguishes **unresolved** sources from sources that must **block planning**.

- `blocked` / per-source `blocked=true` records unresolved scrape failures for compatibility and recovery tooling.
- `blocking_sources` records unresolved sources that prevent Stage 3.
- `temporarily_unresolved_sources` records known temporary exceptions.
- `planning_ready` is the canonical Python decision for whether the current Stage 2 result can continue.

Temporary policy: **Tønsberg** and **Sandefjord Penguins** may currently remain unresolved without blocking planning because their BookUp authenticated calendars are not always available from Lima. They remain real calendar sources and must continue to appear as unresolved/recovery targets. This is not a permanent manual-calendar policy.

Every other unexpected unresolved calendar source remains blocking unless a human explicitly supplies `--allow-missing-sources`.

## Recovery contract

Recovery is an attempt to improve Stage 2 data, not a second implementation of Stage 2 validity.

1. Run deterministic Python scraping first.
2. Inspect the Stage 2 checkpoint/recovery targets.
3. A browser-capable harness may recover sources it knows how to navigate and inject the recovered events into the shared cache.
4. **Always rerun Python Stage 2 after browser recovery.** Python owns the final readiness decision.
5. If Python still reports an unexpected `blocking_source`, stop before Stage 3 unless a human explicitly approves the broad missing-source override.

Pi may keep progress notifications and `ScraperAgent` for browser-only sources. It must not hard-code a separate allowed-missing-source list.

## BookUp authentication across macOS and Lima

The preferred BookUp path is reusable Playwright authentication state, not repeated credential/MFA automation inside Lima.

The Python scraper stores BookUp browser state at:

```text
.pipeline/auth/bookup-storage-state.json
```

The path can be overridden with `RVV_BOOKUP_STORAGE_STATE`. `.pipeline/` is ignored by git; the auth-state file contains session material and must never be committed or shared.

When BookUp authentication expires, establish or refresh the state in a **visible browser on the macOS host**, where Vipps/SMS MFA can be completed. With the encrypted BookUp environment available through dotenvx, a convenient calendar-only refresh is:

```bash
RVV_BOOKUP_MANUAL_LOGIN=1 ./node_modules/.bin/dotenvx run -f .env.bookup -- scripts/rvv-miniputt calendars --refresh
```

Complete BookUp/Vipps/SMS in the visible browser when prompted. Python saves the resulting Playwright storage state under `.pipeline/auth/`. Subsequent headless Playwright runs inside Lima reuse that state automatically.

Normal Claude/Codex/ChatGPT runs inside Lima should **not** start their own dotenvx credential/MFA flow. They should use the shared CLI. If the saved state is missing or expired, report the BookUp recovery requirement and use the host flow above.

## Planning and fairness

Stage 3 and its fairness/quality gates are Python behavior. Harness instructions must not copy thresholds, retry algorithms, scoring rules, or approval logic. Use CLI options when an operator deliberately wants a different planning budget, for example `--iterations N`.

## Publication safety

Generating a plan is not approval to publish it. Use the repository operator/publish commands and their explicit public-confirmation safeguards. Harnesses must not bypass publication gates.

## Adapter rule of thumb

A good harness adapter should fit this shape:

> Read the shared RVV skill/runbook, invoke `scripts/rvv-miniputt`, add only capabilities unique to this harness, and hand control back to Python after those capabilities are used.

If a harness file starts explaining source policy, Stage 2 validity, fairness thresholds, or the Stage 1–4 algorithm in detail, that logic belongs here or in Python instead.
