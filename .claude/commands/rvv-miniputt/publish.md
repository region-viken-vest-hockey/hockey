---
name: "RVV Miniputt: Publish"
description: "Publish the most recently generated RVV Miniputt export to GitHub Pages, auto-confirmed, without rerunning the pipeline"
category: RVV
---

Publish the season plan from the **last Stage 4 export already on disk** to GitHub Pages, without pausing for a manual approval step. This command never runs or reruns the pipeline — if you want a fresh run first, use `/rvv-miniputt:run` (interactive, stage-by-stage) to produce/refresh the export, then invoke this command to publish it.

## What this does differently from `/rvv-miniputt:run`

`/rvv-miniputt:run` reviews each stage's checkpoint before proceeding and never publishes. This command does the opposite: it never runs pipeline stages, it only publishes whatever `.pipeline/stage4_export.json` currently points to, via `operator publish --confirm-public` — the approval gate auto-confirmed, everything else (bundle sanitization, structured per-run logging) identical to a manual `operator publish`.

**This intentionally skips the approval gate that `operator publish` alone (without `--confirm-public`) leaves in place** (see `_execute_operator_publish` in `tournament_scheduler/cli/pipeline_orchestrator.py`). Use `--confirm-public` only when you want that gate skipped for this run. If you want the safer default (a human explicitly approves before publish), run `operator publish` without `--confirm-public` and let it raise the approval question instead.

**What still stops this command even in auto-confirm mode:** hard validation failures are not bypassed by `--confirm-public` — it only skips the *approval* step, not correctness checks. If the last Stage 4 export recorded errors, or the export bundle fails sanitization, `operator publish` refuses to publish. If there is no prior successful export at all, stop and tell the user to run `/rvv-miniputt:run` first — do not fall back to running the pipeline yourself.

## Steps

1. Confirm there is a usable prior export: read `.pipeline/stage4_export.json` and check `data.output_files` is present and `data.errors` is empty. Check `data`, not the checkpoint's outer `status`/`stale` fields — those only reflect whether *later* pipeline stages have since been rerun (e.g. a fresh scrape), which is irrelevant here since this command never touches the pipeline; `operator publish` itself resolves the export directory straight from `data.output_files.html`'s parent, regardless of outer staleness. If `data.output_files` is missing or `data.errors` is non-empty, stop and tell the user no export is available to publish — do not run the pipeline to produce one.

2. Publish the existing export, without rerunning any stage:

   ```bash
   python3 -m tournament_scheduler.cli.rvv_cli operator publish --confirm-public
   ```

   This also runs post-publish reachability verification by default (issue #20) — do not pass `--no-verify` unless the user asks.

3. Check the exit code. If non-zero, report the failure — print the errors and any pending escalation questions (`operator questions`).

4. Report to the user:
   - Which export bundle was published (path/run id)
   - Whether publish succeeded, and the published URL
   - Any warnings surfaced (e.g. fairness-gate warn-level metrics) even if they didn't block publish

## If something goes wrong after publishing

To roll `/latest/` back to the previous published run:

```bash
python3 -m tournament_scheduler.cli.rvv_cli operator publish-history
python3 -m tournament_scheduler.cli.rvv_cli operator rollback <run_id> --confirm-public
```

Confirm the target `run_id` with the user before rolling back — this is also a public-facing change.

## Examples

- `/rvv-miniputt:publish` — publish the last generated export, no pause
