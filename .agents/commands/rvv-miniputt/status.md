# RVV Miniputt: status

Show the current pipeline status with:

```bash
scripts/rvv-miniputt status <user-args>
```

Fallback only if needed:

```bash
python3 -m tournament_scheduler.cli.rvv_cli status <user-args>
```

Report the status concisely and call out blocked/stale/failed stages that need action. Shared interpretation of pipeline state belongs in `.agents/skills/rvv/SKILL.md`, not in harness adapters.
