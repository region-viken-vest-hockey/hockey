# RVV Miniputt: run

Read `AGENTS.md` and `.agents/skills/rvv/SKILL.md` before operating the pipeline.

Use this procedure for a genuine Stage 1–4 pipeline run: initial season creation, refreshed upstream input/calendar evidence, or another case where the full canonical pipeline is required. If the user's goal is instead to approve/unapprove, move, replan, diff/apply or re-export an already promoted canonical season, use `.agents/commands/rvv-miniputt/season.md` rather than treating ordinary club feedback as a request to regenerate the season.

Use the canonical interactive repository command:

```bash
scripts/rvv-miniputt run --interactive --input input.xlsx <user-args>
```

When a matching canonical season already exists, Stage 3 is baseline-aware by default and adopts that durable schedule rather than regenerating the season from scratch.

At each pause:

1. treat the returned `DecisionContext` as authoritative;
2. choose only from `available_actions`;
3. fill the matching `decision_action_template` / declared action schema;
4. follow the shared RVV skill for recovery, refinement, shared-host, resume, audit, and escalation semantics;
5. continue through the same canonical command with the required resume/decision arguments until completion or human escalation.

## Interactive resume contract

Do not infer `--resume-from` from whether an action sounds like it stays in or advances beyond a stage. Use the pipeline's explicit decision ownership model below.

For ordinary stage decisions, the decision belongs to the stage that just completed, so answer it by resuming at the **next** stage:

| Decision being answered | Resume with |
|---|---|
| Stage 1 decision | `--resume-from 2` |
| Stage 2 decision | `--resume-from 3` |
| Stage 3 attempt decision (`optimize_plan`, `apply_candidate`, `keep_baseline`, `request_operator`) | `--resume-from 4` |

The Stage 3 attempt loop is special internally: submitting `optimize_plan` with `--resume-from 4` does **not** mean "skip optimization and export". The orchestrator records the Stage 3 decision and loops back into Stage 3 optimization itself, then emits a new Stage 3 decision context. `apply_candidate` / `keep_baseline` resolve that loop and allow Stage 4 to run.

Stage 3 also has in-stage sub-decisions that are answered with the **same** stage number because Stage 3 has not logically completed yet:

| In-Stage-3 sub-decision | Resume with |
|---|---|
| shared/joint-host assignment | `--resume-from 3` |
| internal arena/time conflict resolution | `--resume-from 3` |

So the canonical distinction is:

- **Stage 3 sub-decision** → `--resume-from 3`;
- **Stage 3 attempt/adoption decision** → `--resume-from 4`.

Never switch a Stage 3 `optimize_plan` decision to `--resume-from 3` just because optimization will run Stage 3 again; the orchestrator owns that loop. Likewise, never answer a pending shared-host or arena-conflict sub-decision with `--resume-from 4`.

When uncertain, identify which `DecisionContext` is pending and follow this table rather than reasoning from checkpoint files or guessed state transitions.

## Inspecting pending Stage 3 state

Do not reconcile Stage 3 side files by hand. The active run's explicit session is the authoritative view:

```bash
scripts/rvv-miniputt stage3 session --work-dir .pipeline --json
```

Use it to confirm the current candidate revision/fingerprint, which decision is pending and whether it is run-scoped or candidate-scoped, and which transition types are legal next before you answer a pause.

Do not invoke internal `stageN_*` modules directly and do not recreate stage gates, scheduling policy, source-validity rules, or publication policy in a harness adapter.
