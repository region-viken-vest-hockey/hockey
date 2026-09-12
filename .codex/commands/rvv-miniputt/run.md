# RVV Miniputt: Run (Codex)

This file is a Codex transport adapter only. Read `AGENTS.md` and `.agents/skills/rvv/SKILL.md`; they own shared project and RVV operational policy.

Run:

```bash
scripts/rvv-miniputt run --interactive --input input.xlsx
```

At each pause, treat the returned `DecisionContext` as authoritative: choose only an `available_action`, fill its `decision_action_template`, and follow the shared RVV skill for the correct recovery, refinement, shared-host, and resume behavior. Continue through the same canonical command until completion or human escalation.

Never run Pi slash commands through a shell, call internal `stageN_*` modules directly, or copy scheduling/source/publication policy into this adapter. If this file conflicts with the repository capability or shared skill, update the adapter.
