---
name: "RVV Miniputt: Scrape LLM"
description: "Use Claude browser capability for one RVV recovery target, then return control to Python"
category: RVV
---

Read `.agents/skills/rvv/SKILL.md` and `.agents/skills/rvv/RUNBOOK.md` first. They define the shared recovery and BookUp boundaries.

Use the repository recovery command for the requested source:

```bash
scripts/rvv-miniputt scrape-llm --club "<name>" <user-args>
```

This command is capability-gated. If Claude has browser automation available, use it only to recover source data that the deterministic path could not obtain. Do not copy source-readiness policy or maintain a separate list of acceptable missing sources here.

For BookUp/Tønsberg/Sandefjord, normal Lima execution must reuse the saved Playwright auth state under `.pipeline/auth/`. Do not start a new credential/Vipps/SMS flow inside Lima. If the state is absent or expired, follow the macOS-host recovery procedure in the shared runbook.

If browser recovery produces event JSON, return it through the shared recovery bridge, for example:

```bash
cat recovered-events.json | scripts/rvv-miniputt recovery-inject --source "<name>"
scripts/rvv-miniputt scrape-merge
scripts/rvv-miniputt run --resume-from 2
```

The final command deliberately resumes from **Stage 2**, not Stage 3. Python must re-evaluate the complete checkpoint and decide whether planning may continue.

If the harness cannot perform browser navigation, report the recovery target and use `scripts/rvv-miniputt recovery-targets` rather than pretending the source was resolved.
