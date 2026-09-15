# RVV Miniputt: calendars

Generate the calendar/source report with:

```bash
scripts/rvv-miniputt calendars <user-args>
```

Fallback only if needed:

```bash
python3 -m tournament_scheduler.cli.rvv_cli calendars <user-args>
```

Report where the output was written and surface source-health/trust problems that require follow-up. Source-validity policy belongs in `.agents/skills/rvv/SKILL.md` and repository code, not in a harness adapter.
