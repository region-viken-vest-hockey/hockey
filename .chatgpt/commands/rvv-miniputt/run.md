# RVV Miniputt: Run (ChatGPT)

Run the RVV Miniputt season-scheduling pipeline stage-by-stage, reviewing a
structured decision context after each stage before deciding whether to
continue.

For focused troubleshooting, use:
- `scripts/rvv-miniputt scrape --club <name>` for deterministic single-club scraping
- `scripts/rvv-miniputt scrape-llm --club <name>` for browser-enabled LLM recovery on blocked sources
- `scripts/rvv-miniputt recovery-targets` + `scripts/rvv-miniputt recovery-inject --source <name>` for terminal-only recovery after you have event JSON

## Rules

- Do NOT invoke `stage1_config`/`stage2_scraping`/`stage3_planning`/`stage4_export` directly, and do NOT call `scripts/rvv-miniputt run` without `--interactive` — both bypass per-stage review. The canonical entrypoint for this command is `scripts/rvv-miniputt run --interactive`.
- Do NOT hand-roll proceed/abort criteria, recovery-loop rules, or a refinement-iteration cap here — those are owned by `scripts/rvv-miniputt` and `.agents/skills/rvv/SKILL.md`'s "Stage gating policy" section (issue #260 ADR 0002: this file is a thin transport/UI adapter, not a second policy source).

## The interactive loop

Each invocation runs **exactly one stage**, then prints a JSON
`DecisionContext` (facts, hard violations, warnings, `available_actions`)
and exits with code `2`. Read `.agents/skills/rvv/SKILL.md`'s "Stage gating
policy (soft judgment)" section for the matching stage before deciding —
that document, not this one, is the canonical source for what counts as
good enough to proceed.

```bash
scripts/rvv-miniputt run --interactive --resume-from 1 --input input.xlsx
```

Exit codes:
- `2` — paused after a stage; a `DecisionContext` was printed. Decide next.
- `1` — hard failure, or the pipeline aborted after a decision. Stop and report.
- (never `0` in `--interactive` mode — there is always a decision or a failure.)

### Deciding

Read the printed `DecisionContext`. Its `available_actions` lists what's
valid right now; `application/decisions.py` rejects anything else
deterministically, so don't guess — use only listed actions. Pass your
decision on the **next** invocation as `--decision-action '<JSON>'`
(`{"action_id": "proceed"}` at minimum; `rationale` is a concise one-line
summary, never chain-of-thought) together with `--resume-from` set to the
stage number **after** the one you're deciding:

```bash
scripts/rvv-miniputt run --interactive --resume-from 2 \
  --input input.xlsx \
  --decision-action '{"action_id": "proceed", "rationale": "9 sources, 0 blocked"}'
```

- `proceed` — run the next stage.
- `abort` — stop the run here (exit 1, nothing further runs).
- `retry_stage` — re-run the stage you're deciding on instead of advancing (e.g. after `--force-refresh` fixed something for Stage 2; pass `--force-refresh` on this same invocation too).
- `request_operator` — you need the user's input before deciding; ask them, then re-invoke with their answer folded into your actual decision.
- `recover_source` (Stage 2 only, offered when sources are blocked) — advisory: do the recovery first (see below), *then* invoke with `proceed` or `retry_stage` — this action by itself doesn't fetch anything.

Every decision is validated against a freshly rebuilt `DecisionContext` and
recorded in `.pipeline/run_manifest.json`'s `decision_log` before anything
runs — this is the audit trail, not this markdown file.

### Stage 1 — Configuration

```bash
scripts/rvv-miniputt run --interactive --resume-from 1 --input input.xlsx
```

`facts` includes `sources`, `start_date`/`end_date`, `age_groups`, `clubs`.
Sanity-check these look populated before deciding `proceed`.

### Stage 2 — Scraping

```bash
scripts/rvv-miniputt run --interactive --resume-from 2 --input input.xlsx \
  --decision-action '<your decision for Stage 1>'
```

`facts` includes `sources_scanned`, `blocked_count`, `blocked_sources`,
`llm_fallback_count`. If `blocked_count > 0`, `recover_source` is offered.

**Recovery, when offered:** for each name in `blocked_sources`, look up its
URL via:

```bash
python3 -m tournament_scheduler.cli.rvv_cli recovery-targets --work-dir .pipeline
```

Then, per source: fetch the URL, extract calendar events (`title`,
`start`, optionally `end`/`location`/`description`), and inject them:

```bash
echo '<JSON-array>' | python3 -m tournament_scheduler.cli.rvv_cli recovery-inject --source "SOURCE_NAME" --work-dir .pipeline
```

A source that can't be recovered this way just stays blocked — don't abort
the whole recovery pass over one source. After attempting recovery for
every blocked source, re-run Stage 2 (`retry_stage`) so the checkpoint
reflects the recovered data, then decide again from its fresh
`DecisionContext`.

### Stage 3 — Planning

```bash
scripts/rvv-miniputt run --interactive --resume-from 3 --input input.xlsx \
  --decision-action '<your decision for Stage 2>'
```

`facts` includes `tournaments_planned`, `warnings`, and `tone` (`rough` /
`mixed` / `strong`, from the plan's fairness/quality scores). This
single-stage-per-invocation mode runs Stage 3 **once** — it does not run
the multi-seed best-of-N retry loop or the post-export refinement pass the
non-interactive `scripts/rvv-miniputt run` command has. If `tone` is
`rough`, prefer `abort` and re-run the full (non-interactive)
`scripts/rvv-miniputt run --resume-from 3` for its retry/refinement
machinery, rather than looping this command by hand.

### Stage 4 — Export

```bash
scripts/rvv-miniputt run --interactive --resume-from 4 --input input.xlsx \
  --decision-action '<your decision for Stage 3>'
```

`facts` includes `files_written` (export kinds produced) and `errors`.
There's no Stage 5 — after deciding here, report the result to the user;
`/rvv-miniputt:publish` handles publication separately and is not part of
this command.

## Debugging log

Each stage appends one line to `stage_run.log` as it starts/finishes (in
`.pipeline/logs/` until Stage 4 has produced output, then in the export
timestamp folder). The `DecisionContext` for the most recently completed
stage is also written to that same log directory as `decision_context.json`
for reference. Check these if a stage seems stuck or behaved unexpectedly.

## Checkpoint review helper

To pretty-print any checkpoint in a compact human-readable form:

```bash
python3 -m tournament_scheduler.cli.checkpoint_printer stage1
python3 -m tournament_scheduler.cli.checkpoint_printer stage2
python3 -m tournament_scheduler.cli.checkpoint_printer stage3
python3 -m tournament_scheduler.cli.checkpoint_printer stage4
```

## Examples

- Start at Stage 1; continue the interactive loop above.
- Pass `--force-refresh` to the Stage 2 invocation to bypass cached calendar data.
- Pass `--non-strict` so warnings don't force an abort decision.
