# RVV Miniputt: logs

Inspect pipeline/run logs with:

```bash
scripts/rvv-miniputt logs <user-args>
```

Fallback only if needed:

```bash
python3 -m tournament_scheduler.cli.rvv_cli logs <user-args>
```

Summarize the output and highlight actionable failures or repeated warnings. Do not invent a second interpretation of pipeline semantics in the harness adapter.

Common invocations include:

```bash
scripts/rvv-miniputt logs
scripts/rvv-miniputt logs show latest
scripts/rvv-miniputt logs stats
```
