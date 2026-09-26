# RVV Miniputt: calendars

Generate the calendar/source report with:

```bash
scripts/rvv-miniputt calendars <user-args>
```

Fallback only if needed:

```bash
python3 -m tournament_scheduler.cli.rvv_cli calendars <user-args>
```

After any refresh, run the source-health capability before trusting the result:

```bash
scripts/rvv-miniputt sources status
```

This reads the fresh Stage 2 checkpoint and flags, per source: reachability/
block state, event count vs. expectation, cache age, duplicate-heavy output,
a scraper that fabricates a fallback start-time/duration instead of parsing
one from the source (every event landing on an identical duration and a
literal midnight start is the fingerprint of that bug, not a real booking
pattern), and -- for clubs whose registry entry documents a shared feed
covering more than one physical arena -- events an existing per-club
classifier tags as a non-schedulable arena alias, which need club/operator
confirmation rather than a guessed include/exclude. Report where the
refreshed output was written and surface every `warning`/`blocked` result
that requires follow-up. Source-validity policy belongs in
`.agents/skills/rvv/SKILL.md` and repository code, not in a harness adapter.
