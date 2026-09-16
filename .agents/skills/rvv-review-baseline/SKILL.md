---
name: rvv-review-baseline
description: Safely turn an already generated RVV Miniputt schedule/export into the canonical club-review baseline, then isolate later experiments so they cannot silently replace or overwrite the reviewed state.
---

# RVV review-baseline handoff

Use this skill when the operator says an existing generated schedule/export has moved from candidate/testing into real club review or ice-booking review, for example: "this export is now for review", "freeze yesterday's schedule", "promote this as the baseline", or "don't let test runs overwrite this plan".

This is a scenario/orchestration skill only. It does **not** define scheduling rules, verification semantics, approval semantics, export policy, or publication policy. Read `AGENTS.md`, `.agents/skills/rvv/SKILL.md`, and `.agents/commands/rvv-miniputt/season.md` first and delegate all real state changes to repository commands.

## Goal

Move one already verified review candidate across the lifecycle boundary:

```text
successful Stage 4 review export
        -> prove which run/export is being handed off
        -> promote the matching Stage 3 schedule
        -> verify canonical season state
        -> commit only canonical season files
        -> keep future experiments isolated
```

The result is a Git-backed `season/<season>/schedule.json` + `decisions.json` baseline that later planning must optimize around rather than silently replace.

## Safety rules

- Never promote "whatever is latest" without first identifying the Stage 4 export/run the operator means.
- Never use `--force` merely because canonical state already exists. Stop and inspect instead.
- Never hand-edit `schedule.json`, `decisions.json`, Stage checkpoints, or generated exports.
- Never mark an export `published` merely to protect it from retention. Public publication has its own meaning and requires the normal publication workflow.
- Never include unrelated working-tree changes in the baseline commit.
- Never delete or replace the review export as part of promotion.
- If run/export provenance is ambiguous, stop before mutation and report exactly what does not match.

## 1. Identify the review run and export

Default to `.pipeline` only when the operator has not named another work directory.

Inspect the current repository-owned state before promotion:

```bash
scripts/rvv-miniputt status --work-dir <work-dir>
```

Read `<work-dir>/stage4_export.json` and resolve the referenced `export_dir` / `output_files`. Require a successful Stage 4 result with no blocking export errors and require the referenced review output to still exist.

If the export contains `export_manifest.json`, record and report at least:

- `export_id` / `generated_at`;
- `export_fingerprint`;
- `source_run_id`;
- `lifecycle_status`;
- `canonical_season` / `canonical_revision` when present.

If the operator named a specific export directory/run, it must match the Stage 4 checkpoint used for promotion. Do not silently substitute a newer export.

When both the export manifest and current run manifest expose a run id, require the provenance to agree. A mismatch means a later run has changed `.pipeline`; do not promote until the intended review run is restored or explicitly identified.

The current `season promote` command promotes the verified Stage 3 candidate from the selected work directory and records Stage 4 provenance. Therefore Stage 3 and Stage 4 in that work directory must represent the same review handoff. If evidence suggests Stage 3 was rerun after the review export, stop rather than guessing.

## 2. Check whether canonical season state already exists

Determine the season from the plan or the operator's explicit season id. `season promote` can infer the id from the plan, so do not invent one when the repository can derive it.

Before creating state, check `season/<season>/`. If canonical state already exists:

- inspect it with `scripts/rvv-miniputt season status --season <season>`;
- if it represents this same reviewed baseline, report that promotion is already complete;
- if it differs, stop. Do **not** use `--force` unless the operator explicitly asks to replace the canonical baseline after reviewing the consequences.

## 3. Promote the reviewed schedule

For an unpromoted verified review run:

```bash
scripts/rvv-miniputt season promote --work-dir <work-dir>
```

Add `--season <season>` only when the operator supplied a deliberate season id that differs from/clarifies inference. Add `--actor <name>` when a meaningful operator identity is available.

Promotion must succeed through repository verification. Do not bypass a hard-verification failure.

## 4. Verify the canonical handoff

Immediately inspect the new canonical state:

```bash
scripts/rvv-miniputt season status --season <season>
scripts/rvv-miniputt season approvals --season <season>
```

Confirm and report:

- season id;
- canonical revision/fingerprint;
- tournament count;
- that approvals initially reflect the expected review state;
- the `promoted_from` Stage 3 / Stage 4 provenance, including the review export directory/fingerprint when recorded.

If the canonical revision/provenance does not represent the intended review schedule, stop before committing and investigate; do not paper over the mismatch.

## 5. Commit the canonical baseline only

Canonical season state is intended to be Git-backed. Inspect the worktree first:

```bash
git status --short
```

Stage only:

```text
season/<season>/schedule.json
season/<season>/decisions.json
```

Do not stage generated `export/`, `.pipeline/`, test-run state, or unrelated source/doc changes.

Use a concise commit such as:

```text
Promote <season> review baseline
```

Push the commit when the operator's request and active repository policy authorize updating the shared branch. If external-write confirmation is required by the active harness, obtain it at that boundary; do not leave the operator believing an unpushed local commit is already shared canonical state.

## 6. Isolate subsequent experiments

After promotion, ordinary matching Stage 3 runs are baseline-aware, but experiments should also avoid clobbering the review run's transient checkpoints and export directory.

Use dedicated scratch locations, for example:

```bash
scripts/rvv-miniputt run --interactive \
  --input input.xlsx \
  --work-dir .pipeline-test \
  --export-dir export-test
```

Use a more descriptive suffix when useful (`.pipeline-cp-sat-test`, `export-rebalance-test`, etc.). Scratch runs remain derived/transient state and must not become a second source of truth.

If an experiment produces a candidate worth adopting, compare/apply it through the canonical `season replan` / `season diff` / `season apply` workflow rather than replacing the baseline by copying files.

## Exact export artifact vs canonical schedule

Promotion protects the **schedule state**, not necessarily the exact timestamped export directory. Current export lifecycle statuses are `draft` and `published`; old draft exports may be pruned by normal draft retention, while published exports are protected.

Therefore:

- do not publish merely to protect a review artifact;
- do not run experiments into the review export root when the exact files are still needed;
- prefer isolated test export roots while review is active;
- if the exact review bundle itself must gain first-class retention protection, that requires a repository export-lifecycle capability (for example a future `review`/`protected` status), not an LLM-side rename or fake publication.

## Completion report

When finished, report concisely:

1. which Stage 4 export/run was identified as the review source;
2. canonical season id and revision;
3. whether canonical files were committed/pushed and the commit id if available;
4. whether the exact review export is still a draft or already published;
5. the scratch work/export directories to use for future experiments;
6. any unresolved provenance or retention caveat.
