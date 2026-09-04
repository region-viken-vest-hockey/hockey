Run the RVV Miniputt pipeline from this repository, stage-by-stage, reviewing a structured decision context after each stage.

Rules:
- Never run `/rvv-miniputt ...` as a shell command.
- Use `scripts/rvv-miniputt run --interactive <user-args>`. If unavailable, retry with `python3 -m tournament_scheduler.cli.rvv_cli run --interactive <user-args>`.
- Do not reimplement the pipeline by calling `tournament_scheduler.pipeline.stageN_*` modules directly, and do not run `run` without `--interactive` — both bypass per-stage review.
- Do not hand-roll proceed/abort criteria here — read `.agents/skills/rvv/SKILL.md`'s "Stage gating policy" section, which is canonical.

Each invocation runs exactly one stage, then prints a JSON `DecisionContext` (facts, hard violations, warnings, `available_actions`) and exits with code `2`. Decide using only an action listed in `available_actions`, then re-invoke with `--resume-from` set to the next stage and `--decision-action '<JSON>'` (e.g. `{"action_id": "proceed", "rationale": "..."}`). Exit `1` means a hard failure or an abort decision — stop and report.

If stage 2 reports blocked sources (`recover_source` offered), use `recovery-targets`/`scrape-llm`/`recovery-inject` per blocked club, then decide `retry_stage` to re-run stage 2 against the recovered data before proceeding.

Report the actual command used and summarize the result.

Flags:
```
--input <path>              Input workbook (default: input.xlsx)
--work-dir <path>           Pipeline work directory (default: .pipeline)
--resume-from <N>           Resume from stage N or alias (1-4, config, scraping, planning, export)
--export-dir <path>         Export directory (default: export)
--log-level <level>         info | verbose (default: info)
--force-refresh             Clear calendar cache before stage 2
--non-strict                Continue on blocked sources or warnings
--allow-missing-sources     Treat blocked sources as operator-approved and keep partial results
--timestamped-export        Write exports to a timestamped subfolder
--decision-action <JSON>    Decision for the stage being resumed past
```

Examples:
- `scripts/rvv-miniputt run --interactive`
- `scripts/rvv-miniputt run --interactive --resume-from 2 --decision-action '{"action_id": "proceed"}'`
- `scripts/rvv-miniputt run --interactive --non-strict --allow-missing-sources`
