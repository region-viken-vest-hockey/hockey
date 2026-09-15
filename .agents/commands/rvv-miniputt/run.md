# RVV Miniputt: run

Read `AGENTS.md` and `.agents/skills/rvv/SKILL.md` before operating the pipeline.

Use the canonical interactive repository command:

```bash
scripts/rvv-miniputt run --interactive --input input.xlsx <user-args>
```

At each pause:

1. treat the returned `DecisionContext` as authoritative;
2. choose only from `available_actions`;
3. fill the matching `decision_action_template` / declared action schema;
4. follow the shared RVV skill for recovery, refinement, shared-host, resume, audit, and escalation semantics;
5. continue through the same canonical command with the required resume/decision arguments until completion or human escalation.

Do not invoke internal `stageN_*` modules directly and do not recreate stage gates, scheduling policy, source-validity rules, or publication policy in a harness adapter.
