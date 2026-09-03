# RVV Miniputt: Run

Read `.agents/skills/rvv/SKILL.md` and `.agents/skills/rvv/RUNBOOK.md` first. They are the canonical shared operating instructions.

Run the workflow through the repository CLI:

```bash
scripts/rvv-miniputt run
```

Forward any user-supplied run options to that command. Do not invoke Stage 1–4 modules individually and do not duplicate source gating, BookUp authentication/MFA behavior, fairness thresholds, recovery policy, or checkpoint semantics in this harness wrapper.

Use the harness only for capabilities unique to it. If browser recovery produces events, return them to the shared cache/recovery path and rerun Python Stage 2; Python owns the decision to continue planning. Headless Lima runs should reuse the saved BookUp Playwright state from `.pipeline/auth/` rather than starting a new credential/MFA flow.

For diagnosis, inspect `scripts/rvv-miniputt status`, `scripts/rvv-miniputt sources status`, or the canonical `.pipeline/stage*.json` checkpoints as described in the shared runbook.
