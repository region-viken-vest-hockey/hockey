# RVV Miniputt: Run (OpenCode)

This file is an OpenCode transport adapter only. Read `AGENTS.md` and `.agents/skills/rvv/SKILL.md`; they own shared project and RVV operational policy.

Run:

```bash
scripts/rvv-miniputt run --interactive --input input.xlsx
```

At each pause, use only the returned `DecisionContext.available_actions`, fill its matching `decision_action_template`, and follow the shared RVV skill for recovery, refinement, shared-host, and resume semantics. Re-run the same capability until completion or human escalation.

Do not call internal `stageN_*` modules directly and do not duplicate stage gates, scheduling policy, source-validity rules, or publication policy here. If this adapter conflicts with the CLI or shared skill, update the adapter rather than forking the policy.
