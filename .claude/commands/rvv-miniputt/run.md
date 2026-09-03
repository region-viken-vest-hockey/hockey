---
name: "RVV Miniputt: Run"
description: "Run the RVV Miniputt pipeline stage-by-stage with checkpoint review between stages"
category: RVV
---

Run the RVV Miniputt season-scheduling pipeline by invoking each stage individually and reviewing the checkpoint before proceeding to the next stage.

For focused troubleshooting, use:
- `scripts/rvv-miniputt scrape --club <name>` for deterministic single-club scraping
- `scripts/rvv-miniputt scrape-llm --club <name>` for browser-enabled LLM recovery on blocked sources
- `scripts/rvv-miniputt recovery-targets` + `scripts/rvv-miniputt recovery-inject --source <name>` for terminal-only recovery after you have event JSON

## Rules

- Never run `/rvv-miniputt ...` as a shell command.
- Do NOT call `scripts/rvv-miniputt run` or `python3 -m tournament_scheduler.cli.rvv_cli run` as a black box — that bypasses the inter-stage review.
- Run each stage individually, read the checkpoint JSON after each stage, and only proceed if the output looks correct.
- If a stage fails (non-zero exit code), stop and report the error to the user.

## Stage-by-stage orchestration

### Stage 1 — Config

```bash
python3 -m tournament_scheduler.pipeline.stage1_config [--input input.xlsx] [--work-dir .pipeline]
```

After success, read `.pipeline/stage1_config.json` and verify:
- `teams` list is non-empty and contains all 9 RVV clubs
- `age_groups` is populated
- `target_tournament_count` is a reasonable number (≥ 1)

**Note:** `parallel_games` and `sources` are intentionally **not** stored in the Stage 1 checkpoint — they live exclusively in `input.xlsx` and are merged in dynamically by `load_effective_config` at runtime. Do not flag their absence as an error.

To verify sources are configured, check the workbook directly:
```python
python3 -c "from tournament_scheduler.pipeline.input_workbook import load_workbook_config; r = load_workbook_config('input.xlsx'); print(f\"{len(r.get('sources', []))} sources: {[s['name'] for s in r.get('sources', [])]}\")"
```

Stop and ask the user if any of the above look wrong before continuing.

### Stage 2 — Scraping

BookUp sources (Tønsberg, Sandefjord Penguins) need `BOOKUP_EMAIL`/`BOOKUP_PASSWORD`. These are stored encrypted in `.env.bookup` and only decrypted through `dotenvx` — unlike Pi slash commands, Claude invokes the python module directly, so it does **not** get these auto-loaded. Always wrap the Stage 2 call:

```bash
./node_modules/.bin/dotenvx run -f .env.bookup -- python3 -m tournament_scheduler.pipeline.stage2_scraping [--work-dir .pipeline] [--force-refresh] [--non-strict] [--allow-missing-sources]
```

(See the `run-dotenvx` / `calendars-refresh-dotenvx` Makefile targets for the equivalent CLI-wrapped invocations.)

After success, read `.pipeline/stage2_scraping.json` and verify:
- `sources` list contains scraped events for the expected clubs
- `blocked` list (sources that failed) is empty or explicitly approved by the user
- `cached` sources are noted for transparency

**Recovery check — blocked and empty sources:**
Inspect `data.blocked` and `data.sources` from the checkpoint:
1. **Blocked sources:** Any entry in `data.blocked`, or any source in `data.sources` where `blocked == true`. These failed entirely — no events were retrieved.
2. **Zero-event sources:** Any source in `data.sources` where `event_count == 0` and `blocked == false`. These scraped successfully but returned no calendar events (may indicate a layout change, empty season, or wrong URL).

For each blocked or zero-event source, report:
- Source name and URL
- `block_reason` (if blocked)
- Whether `llm_fallback` was attempted
- Suggested recovery: try `--force-refresh` for a transient failure; if the issue persists, check the source URL manually or add an LLM-assisted scraper for that source

Do **not** proceed to Stage 3 if any sources are blocked and `--non-strict` was not passed. For zero-event sources, ask the user whether to continue or investigate further.

**Recovery loop — attempt to fetch events for each problem source:**
For each source returned by `rvv-miniputt recovery-targets`:

```bash
python3 -m tournament_scheduler.cli.rvv_cli recovery-targets [--work-dir .pipeline]
```

Attempt recovery in this order:
1. Use WebFetch (or the browser tool) to retrieve the source's `url`.
2. Extract a list of calendar event objects from the HTML. Each object should have at minimum `title`, `start` (ISO 8601 date or datetime), and optionally `end`, `location`, `description`.
3. If events were extracted, inject them into the cache:
   ```bash
   echo '<JSON-array>' | python3 -m tournament_scheduler.cli.rvv_cli recovery-inject --source "SOURCE_NAME" [--work-dir .pipeline]
   ```
   On success (exit 0), the command prints `{"injected": N, "source": "...", "work_dir": "..."}` — mark this source as recovered.
4. If WebFetch returns no usable content or event extraction fails, log a warning for this source and continue with the next one. Do **not** abort the entire recovery loop on a single-source failure.

If any sources are blocked and `--non-strict` was not passed, stop and report which sources failed before continuing.

**Proceed/abort decision after the recovery loop:**
After attempting recovery for all problem sources:

1. Re-check recovery targets:
   ```bash
   python3 -m tournament_scheduler.cli.rvv_cli recovery-targets [--work-dir .pipeline]
   ```
2. Count remaining blocked/zero-event sources in the output:
   - **All recovered (empty array):** Proceed to Stage 3.
   - **Some still blocked/empty, but `--allow-missing-sources` is acceptable:** Proceed to Stage 3 with a warning listing the unrecovered sources and their URLs.
   - **Some still blocked/empty, and strict mode is required:** Abort. Report each unrecovered source by name and URL, state the reason (`blocked` or `zero_events`), and tell the user to check the URL manually or wait for the source to become available before re-running Stage 2.

If any sources are blocked and `--non-strict` was not passed, stop and report which sources failed before continuing.

### Stage 3 — Planning

```bash
python3 -m tournament_scheduler.pipeline.stage3_planning [--work-dir .pipeline]
```

After success, read `.pipeline/stage3_planning.json` and verify:
- `plan` is present and contains a non-empty list of tournaments
- Each tournament has a date, host club, and age group
- No two tournaments with overlapping player pools are scheduled on the same weekend
- `rules_report` section (if present) shows no critical violations

If the plan is invalid (empty plan, critical rule violations, or structural errors), stop and report the issue to the user.

If the plan is valid, run the verdict command to capture the judgment tone:

```bash
python3 -m tournament_scheduler.cli.rvv_cli verdict --work-dir .pipeline
```

Parse the `tone=` line from the output. The possible values are `rough`, `mixed`, and `strong`.

Store:
- `initial_tone` — the tone value read here
- `refinement_iterations` — counter, start at 0

**Tone-gated refinement loop (up to 3 iterations):**

While `tone == "rough"` and `refinement_iterations < 3`:

1. Run auto-adjust:

   ```bash
   python3 -m tournament_scheduler.cli.rvv_cli auto-adjust --work-dir .pipeline --max-iterations 3
   ```

2. Re-run Stage 4 export:

   ```bash
   python3 -m tournament_scheduler.pipeline.stage4_export [--work-dir .pipeline] [--export-dir export]
   ```

3. Re-check tone:

   ```bash
   python3 -m tournament_scheduler.cli.rvv_cli verdict --work-dir .pipeline
   ```

   Parse the new `tone=` value.

4. Increment `refinement_iterations`.

5. If `tone` is no longer `"rough"`, exit the loop early.

If `tone` is `mixed` or `strong` (either initially or after refinement), proceed to Stage 4.

**After the refinement loop, report to the user:**

- Initial tone: `<initial_tone>`
- Refinement iterations run: `<refinement_iterations>` (0 if no refinement was needed)
- Final tone: `<tone>`
- If `refinement_iterations >= 3` and `tone` is still `"rough"`, note that the cap was reached and the plan may still need manual review.

### Stage 4 — Export

```bash
python3 -m tournament_scheduler.pipeline.stage4_export [--work-dir .pipeline] [--export-dir export]
```

After success, read `.pipeline/stage4_export.json` and report:
- List of files written under `export/` (or the timestamped subfolder)
- Any `errors` present in the checkpoint

## Debugging log

Each stage appends one line to `stage_run.log` as it starts/finishes (in `.pipeline/logs/` until Stage 4 has produced output, then in the export timestamp folder). This is the only run trail this stage-by-stage flow produces — the `pipeline_run_*.log` file only exists for `rvv-miniputt run`/`operator run` invocations. Check it if a stage seems stuck or behaved unexpectedly.

## Checkpoint review helper

To pretty-print any checkpoint in a compact human-readable form:

```bash
python3 -m tournament_scheduler.cli.checkpoint_printer stage1
python3 -m tournament_scheduler.cli.checkpoint_printer stage2
python3 -m tournament_scheduler.cli.checkpoint_printer stage3
python3 -m tournament_scheduler.cli.checkpoint_printer stage4
```

## Resuming from a specific stage

To skip earlier stages whose checkpoints already exist, pass `--resume-from N` to the full pipeline runner (stages 1 through N-1 will be skipped):

```bash
python3 -m tournament_scheduler.cli.rvv_cli run --resume-from 3
```

Use this only when the earlier checkpoint files in `.pipeline/` are already valid and you want to re-run from a specific stage.

## Examples

- `/rvv-miniputt:run` — run all four stages with checkpoint review between each
- `/rvv-miniputt:run --force-refresh` — pass `--force-refresh` to Stage 2 to bypass cached calendar data
- `/rvv-miniputt:run --non-strict` — pass `--non-strict` to Stage 2 to continue if some sources are blocked
