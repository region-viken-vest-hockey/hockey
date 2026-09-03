---
name: "RVV Miniputt: Run"
description: "Run the RVV Miniputt pipeline through the shared repository CLI and runbook"
category: RVV
---

Read `.agents/skills/rvv/SKILL.md` and `.agents/skills/rvv/RUNBOOK.md` first. They are the canonical shared operating instructions.

Run the pipeline through the repository entry point:

```bash
scripts/rvv-miniputt run $ARGUMENTS
```

Do not invoke Stage 1–4 modules individually and do not reproduce source gating, BookUp credential/MFA handling, fairness thresholds, recovery rules, or checkpoint semantics in this Claude command. Those behaviors belong to the shared runbook and Python/CLI implementation.

If browser-only recovery is needed, follow the shared runbook. In particular, Claude running inside Lima should reuse the saved BookUp Playwright state rather than starting a new visible-browser credential/MFA flow. After any external/browser recovery, rerun the shared Python path and let Stage 2 decide whether planning may continue.

Inspect the canonical checkpoints or `scripts/rvv-miniputt status` when diagnosing a failure, but do not replace the CLI orchestration with a second Claude-specific pipeline.
