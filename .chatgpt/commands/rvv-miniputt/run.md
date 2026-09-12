# RVV Miniputt: Run (ChatGPT)

This file is a ChatGPT transport adapter only. Shared RVV policy belongs in `AGENTS.md` and `.agents/skills/rvv/SKILL.md`.

Read those files, then run:

```bash
scripts/rvv-miniputt run --interactive --input input.xlsx
```

At each pause, use only the returned `DecisionContext.available_actions`, fill its matching `decision_action_template`, and follow the shared RVV skill for recovery, refinement, shared-host, and resume semantics. Re-run the canonical command with the required `--resume-from` and `--decision-action` until completion or human escalation.

Do not call internal `stageN_*` modules directly and do not duplicate stage gates, scheduling policy, source-validity rules, or publication policy here. If this adapter conflicts with the CLI or shared skill, update this adapter rather than forking the policy.
