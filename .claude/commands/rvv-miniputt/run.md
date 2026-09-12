---
name: "RVV Miniputt: Run"
description: "Run the canonical RVV pipeline through the shared interactive decision loop"
category: RVV
---

This file is a Claude transport adapter only. Shared RVV policy belongs in `AGENTS.md` and `.agents/skills/rvv/SKILL.md`.

Read those files, then run:

```bash
scripts/rvv-miniputt run --interactive --input input.xlsx
```

At each pause, treat the returned `DecisionContext` as authoritative: choose only from `available_actions`, fill the matching `decision_action_template`, and follow the shared RVV skill for recovery, refinement, shared-host, and resume semantics. Re-run the same canonical command with the required `--resume-from` and `--decision-action` until completion or human escalation.

Do not call internal `stageN_*` modules directly and do not duplicate stage gates, scheduling policy, source-validity rules, or publication policy here. If this adapter conflicts with the repository CLI or shared skill, update this adapter rather than forking the policy.
