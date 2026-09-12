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
- `deltakelser_per_lag_før_jul`
- `deltakelser_per_lag_etter_jul`
- `preferanse_vekt` (optional)

The before/after participation values are the operator-facing participation configuration for that age group. Both halves should be present for every active age group; do not replace them with a season-wide global target.

English aliases such as `target_tournament_count_before_christmas` / `target_tournament_count_after_christmas` may be accepted for compatibility, but the Norwegian column names above are the canonical RVV workbook vocabulary.

`round_length_minutes` means actual round/game length. Transition/setup time between rounds is modeled separately by the scheduler and must not be folded into this value.

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
