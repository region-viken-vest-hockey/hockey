---
name: rvv
description: Canonical shared operating policy for the RVV Miniputt hockey planning pipeline. Use for config, calendar scraping/recovery, season planning, fairness checks, export, publishing, and harness integration.
---

# RVV Miniputt

This skill is the shared policy layer for the RVV Miniputt pipeline. Read [`RUNBOOK.md`](./RUNBOOK.md) before running or changing the workflow.

## Canonical boundaries

1. **Shared policy/runbook:** `.agents/skills/rvv/`
2. **Implementation:** `scripts/rvv-miniputt` and `tournament_scheduler/`
3. **Harness adapters:** Pi, Claude, Codex, ChatGPT, OpenCode, etc. may add UI/browser integration only; they must not copy Stage 1–4 policy or maintain independent source-readiness/fairness rules.

When policy and a harness command disagree, the shared runbook and Python implementation win. Fix the adapter rather than duplicating the rule.

## Non-Pi / cross-harness usage

Use the repository CLI outside Pi:

```bash
scripts/rvv-miniputt run
scripts/rvv-miniputt status
scripts/rvv-miniputt sources status
scripts/rvv-miniputt logs list --count 5
```

Do not invoke individual stage modules merely because a harness supports shell commands. The repo CLI owns orchestration, checkpoints, retries, gates, and failure semantics.

## Pi adapter

Pi may use its registered tools/slash commands for richer progress and browser recovery:

| Tool | Slash command |
|---|---|
| `rvv_miniputt_run` | `/rvv-miniputt run` |
| `rvv_miniputt_publish` | `/rvv-miniputt publish` |
| `rvv_miniputt_status` | `/rvv-miniputt status` |
| `rvv_miniputt_logs` | `/rvv-miniputt logs` |
| `rvv_miniputt_calendars` | `/rvv-miniputt calendars` |
| `rvv_miniputt_scrape` | `/rvv-miniputt scrape` |
| `rvv_miniputt_scrape_llm` | `/rvv-miniputt scrape-llm` |

Pi-specific browser recovery must write recovered data back to the shared cache and rerun Python Stage 2. Python—not Pi—decides whether planning may continue.

## Source-readiness rule

The executable policy lives in Python. At present Tønsberg and Sandefjord Penguins are temporary non-blocking BookUp exceptions when their authenticated calendars cannot be reached; they remain unresolved sources and recovery targets. Other unexpected unresolved sources remain blocking unless a human explicitly uses the broad `--allow-missing-sources` override.

Do not copy that source list into harness adapters. See `RUNBOOK.md` for the operational details and BookUp authentication-state workflow.

## BookUp

Normal headless runs reuse Playwright storage state from `.pipeline/auth/`. When authentication expires, refresh it in a visible browser on the macOS host as documented in `RUNBOOK.md`. Claude/Codex/ChatGPT running inside Lima should not default to performing a credential/MFA flow themselves.

## Changes to the pipeline

When changing stage behavior, source gating, BookUp handling, fairness, recovery, or checkpoint semantics:

- change Python/CLI once;
- update this shared runbook if operator policy changes;
- keep harness-specific wrappers thin;
- add tests at the shared implementation boundary rather than duplicating tests per harness unless the harness integration itself changed.
