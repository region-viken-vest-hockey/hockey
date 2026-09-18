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

## DecisionAction transport

`--decision-action` accepts **one JSON DecisionAction object**. Never translate fields from the returned template into separate CLI flags.

Use the exact matching entry from `DecisionContext.decision_action_template`, replace its placeholders, serialize that whole object, and pass it as the value of `--decision-action`.

The envelope is:

```json
{
  "action_id": "<one returned available action>",
  "arguments": {},
  "rationale": "<concise audit summary>"
}
```

- `rationale` is a top-level field **inside the JSON object**. There is no `--rationale` flag.
- Every action-specific parameter belongs under `arguments`. Do not turn declared parameters into sibling CLI flags.
- Do not invent `--action`, `--target`, `--rationale`, or action-specific CLI options when answering a `DecisionContext`.
- If the context's template contains additional required fields, preserve their nesting exactly.
- For complex quoting, write the filled template to a JSON file and use `--decision-action-file`; the JSON schema is identical.

Example — answering a Stage 1 `proceed` decision:

```bash
scripts/rvv-miniputt run --interactive --input input.xlsx --work-dir .pipeline \
  --resume-from 2 \
  --decision-action '{"action_id":"proceed","arguments":{},"rationale":"Stage 1 config valid: 9 sources, season window accepted, no hard violations."}'
```

Example — action with parameters:

```bash
scripts/rvv-miniputt run --interactive --input input.xlsx --work-dir .pipeline \
  --resume-from 4 \
  --decision-action '{"action_id":"apply_repair_option","arguments":{"option_id":"<returned option id>","candidate_fingerprint":"<returned fingerprint>"},"rationale":"Selected the lowest-cost verified repair returned by this context."}'
```

If a command fails because an action field was passed as a separate CLI flag, treat that as a harness-instruction/transport error; rebuild the command from the returned `decision_action_template` instead of probing `--help` and inventing another shape.

## Interactive resume contract

Do not infer `--resume-from` from whether an action sounds like it stays in or advances beyond a stage. Use the **DecisionContext capability** and the pipeline's explicit decision ownership model below.

For ordinary stage decisions, the decision belongs to the stage that just completed, so answer it by resuming at the **next** stage:

| Decision / capability being answered | Resume with |
|---|---|
| Stage 1 decision | `--resume-from 2` |
| Stage 2 decision | `--resume-from 3` |
| `stage3_interactive`, `stage3_optimize`, `stage3_pareto` | `--resume-from 4` |
| `host_team_missing_repair` | `--resume-from 4` |
| `underfilled_roster_repair` | `--resume-from 4` |
| `host_placement_repair` | `--resume-from 4` |
| `search_neighborhood_repair` | `--resume-from 4` |

A Stage 3 repair context is a **post-plan candidate decision**, even though applying the chosen action mutates/continues Stage 3 internally. Therefore answer it with `--resume-from 4`, exactly like `optimize_plan`/`apply_candidate`/`keep_baseline`. The resume number identifies which completed stage owns the pending decision; it does not mean the implementation must skip straight to export.

The Stage 3 attempt loop is special internally: submitting `optimize_plan` with `--resume-from 4` does **not** mean "skip optimization and export". The orchestrator records the Stage 3 decision and loops back into Stage 3 optimization itself, then emits a new Stage 3 decision context. `apply_candidate` / `keep_baseline` resolve that loop and allow Stage 4 to run.

Stage 3 also has true in-stage sub-decisions that are answered with the **same** stage number because Stage 3 has not produced the candidate decision boundary yet:

| In-Stage-3 capability | Resume with |
|---|---|
| `shared_host_assignment` | `--resume-from 3` |
| `arena_conflict_resolution` | `--resume-from 3` |

So the canonical distinction is based on the returned `capability`, not on an agent's interpretation of the wording:

- `shared_host_assignment` / `arena_conflict_resolution` → `--resume-from 3`;
- Stage 3 candidate/adoption/repair capabilities → `--resume-from 4`.

Never switch a Stage 3 `optimize_plan` or repair decision to `--resume-from 3` just because the chosen action will perform more Stage 3 work; the orchestrator owns that loop. Likewise, never answer a pending shared-host or arena-conflict context with `--resume-from 4`.

When uncertain, inspect the exact persisted `DecisionContext.capability` and follow this table rather than reasoning from checkpoint files, action names, or guessed state transitions.

## Inspecting pending Stage 3 state

Do not reconcile Stage 3 side files by hand. The active run's explicit session is the authoritative view:

```bash
scripts/rvv-miniputt stage3 session --work-dir .pipeline --json
```

Use it to confirm the current candidate revision/fingerprint, which decision is pending and whether it is run-scoped or candidate-scoped, and which transition types are legal next before you answer a pause.

Running `run --interactive --resume-from 3` with no `--decision-action` is a safe inspection/resume step: it re-renders the exact persisted pending Stage 3 decision and exits paused without rebuilding the candidate or starting a new attempt. Repeating it is idempotent. **Inspection with `--resume-from 3` does not imply that the eventual answer should also use 3**; use the capability table above for the action invocation.

If the rendered DecisionContext's candidate fingerprint differs from the authoritative Stage3Session current candidate fingerprint, stop and treat that as an engineering/session-identity defect. Do not retry with a different fingerprint, hand-edit an option, reset repeatedly, or patch an individual repair provider.

## Resetting an untrustworthy Stage 3 lineage

Use the reset capability only when an **engineering/lifecycle defect or a code change has made the current Stage 3 candidate/session lineage untrustworthy**. Examples include a pending decision created by known-buggy lifecycle code, corrupted candidate/session identity, or an explicitly diagnosed Stage 3 persistence defect.

Do **not** use reset as another optimization attempt, to evade a Stage 3 continuation safeguard, to bypass a hard verifier finding, or instead of answering a legitimate operator decision.

Never delete or reconcile Stage 3 JSON files by hand. Use:

```bash
scripts/rvv-miniputt stage3 reset --work-dir .pipeline --json
```

The reset command must:

- refuse to run unless Stage 1 and Stage 2 are complete and non-stale;
- preserve the Stage 1 configuration checkpoint and Stage 2 scrape/evidence checkpoint unchanged;
- clear the canonical `Stage3Session` and compatibility mirrors;
- remove Stage 3 and Stage 4 checkpoints;
- clear Stage 3 attempt evidence and the run-scoped CP-SAT cache;
- start a **new run id** so decisions from the superseded Stage 3 lineage are not mixed with the recovered run.

After a successful reset, follow the `next_command` returned by the command. Normally this is equivalent to:

```bash
scripts/rvv-miniputt run --interactive --input input.xlsx --work-dir .pipeline --resume-from 3
```

This reuses the already-normalized Stage 1/2 facts and does **not** re-scrape calendars. Once the new Stage 3 candidate has been emitted, ordinary resume semantics apply again.

Do not invoke internal `stageN_*` modules directly and do not recreate stage gates, scheduling policy, source-validity rules, or publication policy in a harness adapter.
