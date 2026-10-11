# RVV Miniputt: season — Changing approved tournaments and participants

Load from `../season.md` when routed here. Read `AGENTS.md` and `.agents/skills/rvv/SKILL.md` first; the lifecycle boundary and inspection path in `../season.md` still apply.

## Change an approved tournament

Do not bypass approval protection. First revoke the approval/locks explicitly:

```bash
scripts/rvv-miniputt season unapprove \
  --season <season> \
  --tournament-id <durable-id> \
  --note "<reason>"
```

Then apply the requested canonical move:

```bash
scripts/rvv-miniputt season move \
  --season <season> \
  --tournament-id <durable-id> \
  [--date YYYY-MM-DD] \
  [--arena "..."] \
  [--host-club "..."] \
  [--start-time HH:MM] \
  --request-id <request-id>
```

Do not alter participants or unrelated tournaments to make a targeted move fit unless the operator instead asked for replanning. A rejected move must leave canonical state unchanged.

A move is refused by default when it would newly place the tournament inside the host's `fixed_busy`/external-calendar interval, at a host with no trustworthy calendar, or in a host-controlled (`movable_busy`) slot that needs host confirmation -- even though `verify_candidate` may report such a placement as hard-valid manual work. This is deliberately stricter than hard validity: an automatic maintenance move must not trade a usable placement for known manual work. Use `--dry-run` to inspect `move_preview.operational_acceptability` (`regressions`, `requires_operational_opt_in`). Only when the operator explicitly asks for that exact provisional placement, repeat the command with `--allow-manual-placement` and/or `--allow-host-confirmation`; the opt-in is recorded in `decisions.json` history. Never infer the opt-in from a technically-successful verification.

Reapprove only when the new placement is actually confirmed/booked.


## Replace one tournament participant safely

Use this for a one-tournament substitution: "replace team A with registered same-age team B in this tournament". It keeps the date, time, arena and host unchanged, regenerates games, and reports consequences for the outgoing and incoming teams.

```bash
scripts/rvv-miniputt season replace-participant \
  --season <season> \
  --tournament-id <id> \
  --remove-team "<outgoing team>" \
  --add-team "<incoming team>" \
  --request-id <request-id> \
  --dry-run --json
```

Do not invent a second tournament for a plain substitution. Use `swap-participants` only when the operator requested a two-tournament exchange.

## Retire a team (cancel hosted tournaments + withdraw from away tournaments)

Use this when a team/hosting unit ceases both participation **and** future hosting responsibility
for an age group. This is a single canonical maintenance operation that:

- **cancels** all future tournaments for which the retiring team/club owns the hosting responsibility
  (preserving provenance that they were planned hosting obligations cancelled due to retirement);
- **withdraws** the retiring team from away tournaments hosted by other clubs, recording a
  durable age-group withdrawal so the team cannot be reintroduced later by repair/search;
- **analyzes rebalance** for affected away tournaments, proposing eligible same-age replacements
  that respect booked-reality fairness, spacing, travel, locks, protections and active requests.

Do **not** use `remove-participant` / `participation-withdrawal` / `batch cancel` for a genuine
retirement. Those paths preserve date/time/arena/host/booked occupancy and only mutate rosters,
which is operationally wrong for hosted tournaments: it leaves an active home tournament with
only visiting teams and silently transfers the hosting burden.

```bash
scripts/rvv-miniputt season retire-team \
  --season <season> \
  --club "<registered club identity>" \
  [--host-club "<literal host_club on home tournaments, when it differs from --club>"] \
  --team "<team label>" \
  --age-group <age> \
  --effective-from <YYYY-MM-DD> \
  --request-id <stable-request-id> \
  [--actor <operator>] [--note "<reason>"] \
  [--dry-run --json]
```

Use `--dry-run` first. The preview exposes:

- `classification.hosted_tournaments.cancel` — tournaments to be cancelled as hosting obligations
- `classification.away_participation.remove_from` — tournaments the team is withdrawn from
- `classification.rebalance.proposals` — candidate replacements for affected away tournaments
- `classification.rebalance.unresolved_vacancies` — away tournaments with no viable rebalance
- `classification.can_apply` — whether the retirement can be applied as-is (requires
  `verification.ok`, `hosting_responsibility.ok`, `change_protections.ok`,
  `request_constraints.acceptable`)

If `can_apply` is false because away tournaments are underfilled, review the rebalance
proposals, save them to a JSON file, and re-run with `--accept-rebalance` and
`--rebalance-proposals <file>`.

```bash
# preview
scripts/rvv-miniputt season retire-team --season 2026-2027 --club "Kongsberg/Tønsberg" \
  --team "Kongsberg/Tønsberg" --age-group Ju12 --effective-from 2026-09-01 \
  --request-id kongsberg-ju12-retire-2026 --dry-run --json

# apply with rebalance (using proposals from preview)
scripts/rvv-miniputt season retire-team --season 2026-2027 --club "Kongsberg/Tønsberg" \
  --team "Kongsberg/Tønsberg" --age-group Ju12 --effective-from 2026-09-01 \
  --request-id kongsberg-ju12-retire-2026 --accept-rebalance \
  --rebalance-proposals /tmp/rebalance.json
```

**Critical routing guidance:**

> Before processing a season/age-group withdrawal, determine whether the withdrawing team or
> club owns any future hosting obligations for that age group. A withdrawal from a hosted
> tournament is not a participant-removal problem. Future tournaments hosted on behalf of the
> retiring team/club must be cancelled or otherwise explicitly resolved as hosting obligations.
> Use participant removal only for the team's away appearances. After those changes, evaluate
> whether affected away tournaments should be rebalanced.

The harness must call the repository-owned `retire-team` preview/apply flow rather than
inferring hosted-vs-away semantics itself. The `host-club` parameter handles cooperative
teams whose hosting responsibility is assigned to one parent club (e.g. a team registered
as `Kongsberg/Tønsberg` that hosts as `Kongsberg`).


