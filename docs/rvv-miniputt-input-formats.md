# RVV Miniputt input formats

## Canonical input

Root `input.xlsx` is the standard and only operator-maintained season-planning input for RVV Miniputt. JSON is used internally for checkpoints, caches, manifests, decisions, and exports; organizers should not maintain a parallel root JSON configuration.

This document describes the **canonical workbook contract**, not every legacy alias that old parser code may still recognize. Compatibility code must not be treated as permission to add obsolete fields back to `input.xlsx`.

## Workbook sheets

### `Innstillinger`

Two columns: `felt`, `verdi`.

Canonical settings:

| felt | Status | Meaning |
|---|---|---|
| `start_date` | Required | Season start, `YYYY-MM-DD`. |
| `end_date` | Required | Season end, `YYYY-MM-DD`. |
| `vekt_cap` | Optional | Caps absolute `preferanse_vekt` values so date preferences cannot dominate scoring unintentionally. |
| `max_hosting_days_per_month` | Optional | Soft cap on distinct hosting days for one club in one month. When omitted, this additional monthly cap is disabled. |

`deltakelser_per_lag` / workbook-level `target_tournament_count` are **not part of the canonical workbook anymore**. Participation targets belong per age group in `Aldersgrupper`.

The parser may temporarily retain compatibility with old workbook keys while old fixtures/migrations are cleaned up. Do not rely on those fallbacks for production planning.

### `Aldersgrupper`

One row per active age group. Canonical columns:

- `age_group`
- `parallel_games`
- `round_length_minutes`
- `rounds_per_tournament` (optional)
- `ice_time_minutes`
- `deltakelser_per_lag_før_jul`
- `deltakelser_per_lag_etter_jul`
- `preferanse_vekt` (optional)

The before/after participation values are the operator-facing participation configuration for that age group. Both halves should be present for every active age group; do not replace them with a season-wide global target.

English aliases such as `target_tournament_count_before_christmas` / `target_tournament_count_after_christmas` may be accepted for compatibility, but the Norwegian column names above are the canonical RVV workbook vocabulary.

`round_length_minutes` means actual round/game length. `rounds_per_tournament` means how many rounds are actually played in each tournament for that age group; omit it to keep the existing complete round-robin behavior. When `rounds_per_tournament` is set, Stage 1 requires a positive integer, the generator builds that many rounds directly, and same-physical-club matchups are avoided whenever a zero-same-club schedule is feasible.

`parallel_games` and `rounds_per_tournament` are independent dimensions. `parallel_games` sets the normal tournament participant capacity: `2 x parallel_games` teams can play simultaneously in one round, so that is the normal full-capacity cohort size. `rounds_per_tournament` only says how many rounds/games that cohort plays and is never inverted into a round-robin participant count. A tournament does not need to be a complete round robin, so when `rounds_per_tournament < 2 x parallel_games - 1` it is a deliberate limited-opponent schedule (for example U8 with 4 parallel games and 5 rounds is 8 teams / 20 games / 5 games per team, not a 6-team complete round robin). If the complete registered pool for an age group is smaller than `2 x parallel_games`, the effective shape adapts to the real teams and may reduce the effective round count when that many unique-opponent rounds are impossible; explicit exact-size overrides such as the U12/JU12 four-team rule still take precedence.

`ice_time_minutes` is the **total hall/arena ice booking window** for the age group, expressed as an integer number of minutes to avoid Excel time-format ambiguity. It is not a game-time base to which the planner may add more minutes. The value must already cover the complete tournament occupancy that RVV expects to book: played rounds, normal round/changeover time, ice preparation/maintenance and other event overhead. Stage 1 requires a positive `ice_time_minutes` value for every active age group; new age groups must be given an explicit operator-approved value.

Tournament occupancy/end-time calculation is canonical:

```text
tournament_occupancy_minutes = ice_time_minutes
```

Game-format timing is a **separate minimum-feasibility check**, not extra occupancy added to the booking:

```text
minimum_playing_requirement = number_of_rounds * (round_length_minutes + 5)
ice_time_minutes >= minimum_playing_requirement
```

The 5 minutes per round is the normal transition/changeover allowance. When `rounds_per_tournament` is configured, Stage 1 should reject a configured booking window that cannot fit the configured format; final/candidate verification must also check the actual generated round count because effective tournament shape may reduce the number of rounds. Any governing minimum booking allocation (for example NIHF's two-hour floor for the applicable U7–U11 3v3 series rounds) is an additional independent lower bound; it must not be implemented by adding minutes to `ice_time_minutes` after the fact.

Slot search, external-calendar conflict detection, arena overlap checks, optimizer feasibility and every export must use the same effective occupancy interval (see below). A code path that adds a per-round buffer on top of it violates this contract.

`ice_time_minutes` is the age group's **nominal** configured window (see the "Do not reduce these values" rule below); it is never edited by this adaptive policy. A single tournament *instance*'s actually booked/occupied minutes are the **feasible-round-adapted effective occupancy**, owned by `tournament_scheduler.occupancy.effective_required_ice_minutes`: when that instance's own feasible round count (derived from the finalized participating teams -- fewer teams than the age group's nominal round count supports) falls short of `rounds_per_tournament`, the booked window is reduced by exactly the ice time the missing rounds would have consumed:

```text
removed_rounds = nominal_round_count - feasible_round_count
effective_requested_minutes = max(
    ice_time_minutes - removed_rounds * (round_length_minutes + 5),
    minimum_playing_requirement(round_length_minutes, feasible_round_count),
    governing_minimum(age_group),
)
```

This is never a team-count ratio and never overwrites the configured age-group `ice_time_minutes` value. Full participation (`feasible_round_count >= nominal_round_count`) keeps the configured value unchanged, and an already-confirmed/manual external booking is never shortened by this reduction. Recompute this per instance whenever its participants change (withdrawal, replacement, swap); the existing calendar-booking staleness comparison (`tournament_occupancy_interval_facts`) picks up the change automatically because it recomputes the effective duration fresh.

Migrated 2025–2026 `Istid` values are historical booking-window evidence used when establishing the configuration; they are references to review, not "base ice" values that receive another automatic round buffer.

For 2026–2027, RVV deliberately keeps the longer windows that the published plan/clubs effectively used. The difference between the game-format minimum and `ice_time_minutes` is **intentional operational headroom**, not unused planner slack. It may be used for preparing/resurfacing the ice (including Zamboni time), placing/removing dividers and goals, getting teams on/off the ice, small delays, and other event turnover. That headroom is already part of the booked interval and must never be added again by a planner/export path.

U7/JU7, U8/JU8, U9, U10/JU10 and U11 also have a 120-minute governing minimum per series round. The approved 2026–2027 booking windows (and the values root `input.xlsx` should carry) are:

| Age group | `ice_time_minutes` | Format minimum | Intentional operational headroom |
|---|---:|---:|---:|
| U7 | 155 | 100 | 55 min; also exceeds the 120-minute governing floor |
| U8 | 155 | 100 | 55 min; also exceeds the 120-minute governing floor |
| JU8 | 155 | 100 | 55 min; also exceeds the 120-minute governing floor |
| U9 | 155 | 100 | 55 min; also exceeds the 120-minute governing floor |
| U10 | 140 | 100 | 40 min; also exceeds the 120-minute governing floor |
| JU10 | 140 | 100 | 40 min; also exceeds the 120-minute governing floor |
| U11 | 140 | 100 | 40 min; also exceeds the 120-minute governing floor |
| U12 | 100 | 60 | 40 min |
| JU12 | 100 | 60 | 40 min |

Do not reduce these values merely because the nominal rounds fit in less time. A shorter window is a new operator policy decision and may also conflict with ice already booked from the published season plan.

When `Aldersgrupper` is present, its rows define the declared age groups used to validate `Lag` and age-group-specific configuration.

### `Lag`

Canonical columns:

- `club`
- `label`
- `age_group`

Do not add a normal per-team `target_tournament_count` override. Normal participation policy belongs to the team's age group through the before/after fields in `Aldersgrupper`. A future exceptional team-specific policy should be introduced explicitly and reported as an operator exception rather than hidden in the standard roster schema.

Empty rows are ignored. Duplicate `label` values may exist across different age groups, but not as duplicate team identities within the same age group.

### Reviewed SharePoint registration exports

`input.xlsx` remains the controlled planner input, but `Lag` can be rebuilt from a reviewed SharePoint List export so volunteers do not copy registrations manually.

Supported interchange formats:

- CSV (`.csv`, UTF-8/UTF-8-BOM)
- Excel (`.xlsx` / `.xlsm`, first worksheet)

Required source fields and common aliases:

| Canonical field | Typical aliases | Meaning |
|---|---|---|
| `sharepoint_id` | `SharePoint ID`, `Item ID`, `ID`, `list_item_id` | Stable identity for audit/duplicate detection. |
| `club` | `club`, `Klubb`, `Forening` | Must resolve to a controlled club identity. |
| `label` | `Lag`, `Lagnavn`, `team label`, `team_name`, `label` | Team label written to `Lag.label`. |
| `age_group` | `Aldergruppe`, `age group`, `klasse` | Must be declared in `Aldersgrupper` when that sheet is used. |
| `status` | `Status`, `approval_state`, `Godkjenningsstatus` | Determines whether the registration becomes active. |

Accepted active statuses are `approved`, `current`, `active`, `accepted`, `godkjent`, `aktiv`, and `gjeldende`. Rejected statuses include `rejected`, `withdrawn`, `duplicate`, `incomplete`, `avvist`, `trukket`, `duplikat`, and `ufullstendig`.

Unknown statuses, missing required fields, unknown clubs/age groups, duplicate SharePoint IDs, and duplicate team identities block import with actionable errors. Contact/comment fields may exist in the private SharePoint export but are not copied into the planning workbook or public output.

```bash
scripts/rvv-miniputt registrations validate registrations.csv --input input.xlsx
scripts/rvv-miniputt registrations export registrations.csv --input input.xlsx --output input.updated.xlsx --dry-run
scripts/rvv-miniputt registrations export registrations.csv --input input.xlsx --output input.updated.xlsx
```

The export copies the controlled workbook and replaces only `Lag`. `Innstillinger`, `Aldersgrupper`, `Kilder`, `Datopreferanser`, and other administrative sheets remain controlled data. A non-dry-run import writes `input.updated.registrations.audit.json` with source fingerprint, included SharePoint IDs, and the diff summary.

### `Kilder`

Columns:

- `name`
- `type`
- `url`

Empty rows are ignored and sources with empty URLs are dropped. This is the normal place to declare calendar sources consumed by Stage 2.

### `Datopreferanser`

Columns:

- `fra`
- `til`
- `vekt`

Positive values penalize dates; negative values reward them. Date cells and common date strings are accepted. Preference values beyond `vekt_cap` produce a warning.

## Source-of-truth rules

- Root `input.xlsx` is the planner input; there is no parallel operator-maintained JSON config.
- `Lag` contains identities, not hidden scheduling-policy overrides.
- Participation configuration belongs in `Aldersgrupper`, split before/after New Year.
- Registration import replaces only `Lag`; it must not overwrite planning settings.
- Generated output is derived data and should never become a new source of truth.
- If parser compatibility behavior contradicts this canonical workbook contract, treat that as implementation debt to remove rather than documentation to preserve.
