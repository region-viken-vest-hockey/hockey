---
name: "RVV Miniputt: Run"
description: "Run the RVV Miniputt pipeline stage-by-stage with checkpoint review between stages"
category: RVV
---

Run the RVV Miniputt season-scheduling pipeline stage-by-stage, reviewing a
structured decision context after each stage before deciding whether to
continue.

For focused troubleshooting, use:
- `scripts/rvv-miniputt scrape --club <name>` for deterministic single-club scraping
- `scripts/rvv-miniputt scrape-llm --club <name>` for browser-enabled LLM recovery on blocked sources
- `scripts/rvv-miniputt recovery-targets` + `scripts/rvv-miniputt recovery-inject --source <name>` for terminal-only recovery after you have event JSON

## Rules

- Never run `/rvv-miniputt ...` as a shell command.
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

See `.agents/skills/rvv/SKILL.md`'s "Interactive `DecisionContext` reference"
for what `facts` contains and when `recover_source` is offered — canonical,
not repeated here.

**If a source is (and will remain) blocked and you're proceeding anyway,**
pass `--allow-missing-sources` on the Stage 2 invocation itself (the one
above, that actually runs the scrape) — not just on later invocations. The
checkpoint's `status` reflects the raw scrape outcome (`failed` when a
source is blocked); without the flag on the run itself, a later replay of
Stage 2 re-derives `failed` and any subsequent decision you submit gets
validated against Stage 2 again instead of the stage you meant, rejected
as `decision_action_not_available`. See SKILL.md for the full mechanic.

**Recovery, when offered:** for each name in `blocked_sources`, look up its
URL via:

```bash
python3 -m tournament_scheduler.cli.rvv_cli recovery-targets --work-dir .pipeline
```

Then, per source: use WebFetch (or the browser tool) to retrieve the URL,
extract calendar events (`title`, `start`, optionally `end`/`location`/`description`),
and inject them:

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

**Branch on the printed `DecisionContext.capability` — do not infer the
next `--resume-from` from "this looks like Stage 3":**

| `capability` | What it means | Next command's `--resume-from` |
|---|---|---|
| `shared_host_assignment` | Pre-Stage-3 pause (issue #274) — a joint-club registration (e.g. `"Kongsberg/Tønsberg"`) needs a hosting decision *before* Stage 3 has run at all. | **3** (same stage you're already on) |
| `stage3_interactive` | Stage 3 baseline (or a re-run's) tone judgment. | 4 |
| `stage3_pareto` | Stage 3 multi-candidate comparison. | 4 |

**If `capability` is `shared_host_assignment`:** answer with
`assign_shared_host` (`arguments.chosen_club` set to one of the listed
`constituents`) or `request_operator`, and resubmit at the **same**
`--resume-from 3` — not 4:

```bash
scripts/rvv-miniputt run --interactive --resume-from 3 --input input.xlsx \
  --decision-action '{
    "action_id": "assign_shared_host",
    "arguments": {"chosen_club": "Tønsberg"},
    "rationale": "Kongsberg already hosts materially more this season."
  }'
```

If more joint registrations are still pending, that same `--resume-from 3`
invocation pauses again with another `shared_host_assignment` context —
keep answering at `--resume-from 3` until none remain. Only once every
joint registration is resolved does that invocation fall straight through
into a **fresh** Stage 3 run and print a `stage3_interactive` (or
`stage3_pareto`) context — that fresh context's `facts` should include
`cp_sat_shadow` when the automatic CP-SAT shadow evaluation ran.

**Once `capability` is `stage3_interactive` or `stage3_pareto`, every
decision about that context — the baseline tone judgment and every later
candidate comparison — uses `--resume-from 4`:**

```bash
scripts/rvv-miniputt run --interactive --resume-from 4 --input input.xlsx \
  --decision-action '<your decision for Stage 3>'
```

The CLI validates a decision against the context for stage `resume_from -
1`, so a `stage3_interactive`/`stage3_pareto` decision sent with
`--resume-from 3` is silently misrouted (it validates against Stage 2's or
the shared-host context instead) and rejected as
`decision_action_not_available`. When you pass `optimize_plan` via
`--resume-from 4`, the orchestrator internally reruns Stage 3 and pauses
again with a new candidate context — decide on *that* again via
`--resume-from 4` too. Only `keep_baseline`/`apply_candidate` resolves the
loop and lets that same `--resume-from 4` invocation continue on into
Stage 4.

Stage 3 is a **nested decision loop**, not a single-attempt gate — see
`.agents/skills/rvv/SKILL.md`'s "Interactive `DecisionContext` reference"
for the full `optimize_plan`/`apply_candidate`/`keep_baseline` mechanics
and the shared-host exception, canonical and not repeated here. Do not
fall back to the non-interactive `scripts/rvv-miniputt run --resume-from 3`
for retry/refinement — drive the loop from here instead.

### Stage 4 — Export

```bash
scripts/rvv-miniputt run --interactive --resume-from 4 --input input.xlsx \
  --decision-action '<your decision for Stage 3>'
```

See `.agents/skills/rvv/SKILL.md`'s "Interactive `DecisionContext`
reference" for what `facts` contains. There's no Stage 5 — after deciding
here, report the result to the user; `/rvv-miniputt:publish` handles
publication separately and is not part of this command.

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

- `/rvv-miniputt:run` — start at Stage 1; continue the interactive loop above
- `/rvv-miniputt:run --force-refresh` — pass `--force-refresh` to Stage 2 to bypass cached calendar data (add it to the Stage 2 invocation)
- `/rvv-miniputt:run --non-strict` — pass `--non-strict` so warnings don't force an abort decision
