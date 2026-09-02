# RVV Miniputt pipeline guide

## Overview

The season-planning workflow is checkpointed in `.pipeline/` and runs in four stages:

1. **Stage 1 — Config**: validate the standard `input.xlsx` workbook and expand the roster
2. **Stage 2 — Scraping**: fetch calendar events from all configured sources
3. **Stage 3 — Planning**: build the season plan
4. **Stage 4 — Export**: write Excel, CSV, iCal, HTML, and Spond outputs

The pipeline is designed so you can fix a blocked source, rerun the command, and keep working from the same work directory.

## Input workbook

`input.xlsx` is the standard pipeline input. `rvv-miniputt run` uses it by default:

```bash
rvv-miniputt run --input input.xlsx --export-dir export
```

Stage 1 imports the workbook sheets into the internal config dict and then runs the normal Norwegian-language validation.

### Required workbook sheets

- `Innstillinger` — scalar settings with columns `felt`, `verdi`
- `Lag` — team roster with columns `club`, `label`, `age_group`

### Optional workbook sheets

- `Aldersgrupper` — columns `age_group`, `parallel_games`, `round_length_minutes`, `deltakelser_per_lag_før_jul`, `deltakelser_per_lag_etter_jul`
- `Kilder` — columns `name`, `type`, `url`

### `Innstillinger` rows

Required rows:

- `start_date` — `YYYY-MM-DD`
- `end_date` — `YYYY-MM-DD`

Optional workbook data lives in the other sheets:

- `Aldersgrupper` kan i tillegg ha `deltakelser_per_lag_før_jul` og `deltakelser_per_lag_etter_jul` for per-age-group halvsesongmål.

### `Aldersgrupper` rows

Each row configures one age group:

- `age_group` — for example `U10` or `JU12`
- `parallel_games` — explicit number of simultaneous games
- `round_length_minutes` — optional override of round length

When present, the sheet's age groups are used for cross-checking against `Lag` and the age-group keyed fields.

### `Lag` rows

Each row configures one team:

- `club`
- `label`
- `age_group`

### SharePoint registration import

Club registrations should flow through Microsoft Forms/Power Automate into a reviewed private SharePoint List. After review, export the list as CSV or XLSX and rebuild only the `Lag` sheet in a controlled workbook snapshot:

```bash
scripts/rvv-miniputt registrations validate registrations.csv --input input.xlsx
scripts/rvv-miniputt registrations export registrations.csv --input input.xlsx --output input.updated.xlsx --dry-run
scripts/rvv-miniputt registrations export registrations.csv --input input.xlsx --output input.updated.xlsx
```

`validate` never writes files. `export --dry-run` validates and prints additions, removals, likely changes, unchanged teams, and rejected rows without creating the output workbook. Non-dry-run export copies `input.xlsx`, replaces only `Lag`, and writes `input.updated.registrations.audit.json` with the source SHA-256 fingerprint and included SharePoint item IDs. Contact/comment fields from the SharePoint export are not copied into the workbook or generated public outputs by default.

The expected source columns and status vocabulary are documented in [RVV Miniputt input formats](rvv-miniputt-input-formats.md#reviewed-sharepoint-registration-exports). Administrative sheets such as `Innstillinger`, `Aldersgrupper`, and `Kilder` remain controlled workbook data and are never replaced by public form submissions.

### `Kilder` rows

Each row configures one calendar source:

- `name`
- `type` — for example `outlook` or `ical`
- `url`

Empty rows are ignored.

## Calendar sources

Stage 2 supports multiple source types:

- `outlook` / `html` — Playwright-based browser scraping
- `ical` / `google` — HTTP/iCal scraping
- JS-heavy sites that fail deterministic scraping — use the Pi ScraperAgent (`.pi/lib/scraper-agent.ts`) or another browser-enabled harness for LLM-guided scraping. Holmen's Sportello calendar is the exception: it now has a deterministic GraphQL scraper and no longer depends on browser-only recovery. In a plain terminal, `scripts/rvv-miniputt scrape-llm` reports the browser-tool boundary for the remaining browser-only sources instead of pretending it can drive the page itself.

### Browser-capability boundary

`scripts/rvv-miniputt scrape-llm --club <name>` is a capability probe, not a guaranteed scraper. It can actually recover a source only in an environment that exposes browser control:

- Pi: the `rvv_miniputt_scrape_llm` extension tool / `/rvv-miniputt scrape-llm`
- Browser-enabled harnesses: Claude Code, OpenCode, Codex, or similar only when they have a Playwright/browser controller wired in
- Plain terminal or CI: no browser control; the command should immediately explain the missing capability and exit

If you only have a terminal, use the recovery bridge instead:

```bash
scripts/rvv-miniputt recovery-targets
# recover or prepare event JSON for the blocked source
python3 -m tournament_scheduler.cli.rvv_cli recovery-inject --source "<navn>" < recovered-events.json
scripts/rvv-miniputt scrape-merge
scripts/rvv-miniputt calendars
```

That flow is scriptable and does not require a live browser controller.

### BookUp credentials

Some BookUp calendars require authentication before scraping works.
For those sources, set the credentials expected by the configured strategy, typically:

- `BOOKUP_EMAIL`
- `BOOKUP_PASSWORD`

With credentials in place, Stage 2 can scrape the source and cache the events. Locally, prefer the dotenvx-backed Make targets so credentials are loaded from the encrypted `.env.bookup` file instead of being passed on the command line:

```bash
make run-dotenvx ARGS='--resume-from 2'
make calendars-refresh-dotenvx
```

`DOTENVX_ENV_FILE=/path/to/file` can point the Make targets at a different dotenvx file. Keep `.env.keys` local and out of git.

### Sparse event-count warnings

Stage 2 now adds a non-blocking `event_expectation` object to each source in
`.pipeline/stage2_scraping.json`. The estimate is derived from the scrape date
range and active age groups, and is meant as a coarse lower-bound sanity check.
If a source returns events but far fewer than expected, Stage 2 records it in the
top-level `event_expectation_warnings` list and `rvv-miniputt status` prints a
"Mistenkelig få kalenderhendelser" summary.

This is different from a blocked source: the pipeline may continue, but the
source should be prioritized for recovery or manual review before trusting the
plan. Typical example: a club calendar returning 2-3 events for a full season
when the date range and age-group setup suggest roughly 16+ bookings.

## Outputs

A normal run can produce:

- `season_plan.xlsx`
- `season_plan.csv`
- `season_plan_overview.csv`
- `season_plan.ics`
- `season_plan.html`
- `season_plan_report.html`
- `input.html` — read-only "Påmeldte lag" overview of registered clubs/teams (Lag sheet only; generated when Stage 1 recorded an input workbook path)
- `activities.json` — normalized public Aktivitetskalender data from a supported activity sheet (`Aktiviteter`, `Aktivitetsplan`, `Årshjul`, etc.) when present. Schema version 2 separates `date`, `age_groups`, `category`, `title`, `location`, `description`, and `url`, includes a documented `category_vocabulary`, and records validation warnings for unknown categories/age groups.
- `activities/index.html` — standalone interactive Aktivitetskalender with a marker-based `Sesongsløp` overview and month-grouped `Liste` view for GitHub Pages and WordPress iframe embedding
- `season_plan_spond.xlsx`
- `calendars.html`

With `--timestamped-export`, the same files are written into a timestamped subdirectory under `export/`.

### Reproducible Stage 4 exports

Stage 4 treats `generated_at` as content metadata. By default it uses the current UTC wall clock, but reproducible builds can pin the canonical content timestamp with `SOURCE_DATE_EPOCH`:

```bash
SOURCE_DATE_EPOCH=1735732800 scripts/rvv-miniputt run --resume-from 4 --export-dir export
```

The standalone Stage 4 entrypoint also accepts an explicit ISO-8601 or epoch value:

```bash
python3 -m tournament_scheduler.pipeline.stage4_export --build-timestamp 2025-01-01T12:00:00Z
```

That timestamp is used consistently for `generated_at`, timestamped export folder naming, and XLSX/ZIP metadata normalization. Identical Stage 3/input content plus the same build timestamp should produce identical exported public bytes and the same Pages bundle fingerprint; meaningful plan/content changes still change the affected file hashes. Operational audit times — run logs, publish history, approvals, git commit times, and rollback records — remain separate from this content identity and continue to record when an operation actually happened.

## Operator flows

### Cross-harness entrypoints

Use whichever entrypoint your environment supports. For non-LLM human operation, prefer `make help` as the discoverable menu; the targets delegate to the same portable commands shown below.

```bash
# Human Make menu / thin adapters
make help
make operator-run ARGS='--log-level verbose'
make status
make publish-preview

# Portable, repo-local launcher
scripts/rvv-miniputt status
scripts/rvv-miniputt run --resume-from 2 --log-level verbose

# Direct Python CLI
python3 -m tournament_scheduler.cli.rvv_cli logs list --count 5
```

In Pi, the slash commands remain available and map onto the same repo workflow surface where possible:

```bash
/rvv-miniputt status
/rvv-miniputt logs show latest
/rvv-miniputt run --resume-from 2 --log-level verbose
```

Pi-only features remain the interactive guide and extension-managed tool wrappers (`rvv_miniputt_run`, `rvv_miniputt_publish`, `rvv_miniputt_status`, etc.). Those harness-only capabilities are intentionally not exposed as Make targets.

### Make target equivalents for human operators

| Operation | Make target | Direct command |
|---|---|---|
| Canonical verification | `make check` | `scripts/check` |
| Raw pipeline run | `make run ARGS='--input input.xlsx'` | `scripts/rvv-miniputt run --input input.xlsx` |
| Goal-oriented run | `make operator-run` | `scripts/rvv-miniputt operator run` |
| Forced goal-oriented run | `make operator-run-force` | `scripts/rvv-miniputt operator run --force` |
| Status and logs | `make status`, `make logs` | `scripts/rvv-miniputt status`, `scripts/rvv-miniputt logs list` |
| Calendar reports | `make calendars`, `make calendars-refresh` | `scripts/rvv-miniputt calendars`, `scripts/rvv-miniputt calendars --refresh` |
| Calendar refresh with dotenvx credentials | `make calendars-refresh-dotenvx` | `dotenvx run -f .env.bookup -- scripts/rvv-miniputt calendars --refresh` |
| Påmeldte lag page | `make registered-teams CSV=downloads/Miniputt-26-27.csv` | `scripts/rvv-miniputt registered-teams --csv downloads/Miniputt-26-27.csv` |
| Publish Påmeldte lag | `make registered-teams-publish CSV=downloads/Miniputt-26-27.csv CONFIRM_PUBLIC=1` | `scripts/rvv-miniputt registered-teams --csv downloads/Miniputt-26-27.csv --publish --confirm-public` |
| Source health | `make sources-status` | `scripts/rvv-miniputt sources status` |
| Human questions | `make questions`, `make questions-all` | `scripts/rvv-miniputt operator questions [--all]` |
| Human answer | `make answer ID=<id> ANSWER='<answer>'` | `scripts/rvv-miniputt operator answer <id> '<answer>'` |
| Promote answer | `make promote ID=<id> SCOPE=workspace` | `scripts/rvv-miniputt operator promote <id> workspace` |
| Publish preview | `make publish-preview` | `scripts/rvv-miniputt operator publish --dry-run` |
| Publish latest | `make publish CONFIRM_PUBLIC=1` | `scripts/rvv-miniputt operator publish --confirm-public` |
| Verify/history/rollback | `make verify-publish`, `make publish-history`, `make rollback RUN_ID=<id> CONFIRM_PUBLIC=1` | `operator verify`, `operator publish-history`, `operator rollback <id> --confirm-public` |
| Desktop app | `make desktop-start`, `make desktop-clean`, `make build-mac`, `make build-windows`, `make build-linux` | desktop npm/package scripts |
| Release | `make release-dry-run TAG=vX.Y.Z`, `make release TAG=vX.Y.Z` | `scripts/release --dry-run vX.Y.Z`, `scripts/release vX.Y.Z` |

Publication and rollback default to non-mutating preview paths unless `CONFIRM_PUBLIC=1` is supplied to the mutating target. Release tagging is handled by `scripts/release`; the Makefile does not call raw `git tag` or `git push`.

### Standalone Påmeldte lag update

Reviewed SharePoint registrations can be published without running Stage 1–4, changing `input.xlsx`, or regenerating the activity calendar:

```bash
make registered-teams CSV=downloads/Miniputt-26-27.csv
make registered-teams-publish CSV=downloads/Miniputt-26-27.csv CONFIRM_PUBLIC=1
```

The command validates the public `club,label,age_group` CSV fields, ignores extra SharePoint/contact/status/comment columns, writes review artifacts under `registered-teams/`, and stages the result on top of the current Pages `/latest/` snapshot so unrelated season-plan and activity files remain in place. The public page path for WordPress links or iframes is:

- `https://niclas-lindgren.github.io/hockey/latest/registered-teams/pameldte-lag.html`

### Browser-based GitHub Actions flow

For volunteers who should operate entirely in GitHub's browser UI, use the manual workflows under the repository **Actions** tab:

1. **`Sesong: valider inndata`** (`season-validate.yml`) — supply the workbook path, run validation/quick checks, and download the artifact containing input fingerprint, status JSON, logs, manifest, and validation outputs.
2. **`Sesong: lag vurderingspakke`** (`season-review-bundle.yml`) — generate the candidate plan/review bundle and publish dry-run. Download the artifact containing the exported HTML/Excel/CSV/iCal files, `run_manifest.json`, run logs, `publish-preview.json`, and `public_bundle/pages_privacy_report.json`. Share the optional GitHub issue summary with reviewers.
3. **`Sesong: publiser godkjent pakke`** (`season-publish.yml`) — after review, provide the review workflow run id, artifact name, exact `run_id`, exact `bundle_fingerprint` from the preview, and `PUBLISER`. This job runs in the protected `pages-publication` environment, rechecks the fingerprint, then calls `scripts/rvv-miniputt operator publish --confirm-public`.
4. **`Sesong: rull tilbake publisering`** (`season-rollback.yml`) — provide a previously published `run_id` and `RULL_TILBAKE`. This also runs in the protected `pages-publication` environment and delegates to `operator rollback --confirm-public`.

These workflows are wrappers around the same commands listed above. They do not embed scheduling logic in YAML, and generation/review jobs do not include `--confirm-public` or any direct `gh-pages` manipulation.

### Full run

```bash
rvv-miniputt run --input input.xlsx --export-dir export
```

Useful flags:

- `--non-strict` — continue past some stage failures
- `--allow-missing-sources` — keep partial Stage 2 results and continue
- Empty input shortcut — when `input.xlsx` has 0 registered teams, Stage 2 writes a skipped checkpoint instead of scraping calendars, and Stage 3/4 publish the not-started placeholder exports
- `--iterations N` — run the Stage 3 planner with multiple random seeds and keep the best plan for that Stage 3 attempt (default: 1 everywhere). Seeds after the first inherit penalty hints from the best-scoring seed found so far's weak fairness metrics, rather than each seed being an equally blind restart — a seed that already passes the fairness gate stops the search early instead of burning the remaining budget. Increase this manually when plan quality needs a wider search.
- `--mid-planning-critic-iterations N` — opt into a pre-export critic loop after Stage 3 and before Stage 4; the loop inspects `.pipeline/stage3_planning.json`, stores structured `planning_critic_hints`, reruns Stage 3 with numeric penalty hints from those findings, and repeats up to `N` times before export
- `--timestamped-export` — write diffable exports into a timestamped folder only
- `SOURCE_DATE_EPOCH=<epoch-seconds>` — pin Stage 4 content timestamps for reproducible export bytes and stable public bundle fingerprints

### Pre-export planning critic vs post-export refinement

`--mid-planning-critic-iterations N` runs before any Stage 4 artifacts exist. It is checkpoint-driven: the pipeline reads the Stage 3 plan, asks the deterministic plan critic/fairness metrics for issues, persists the structured hint payload in the next Stage 3 checkpoint as `planning_critic_hints`, and reruns Stage 3 with the extracted numeric `penalty_hints` baked into the config. Default is `0`, so existing runs are unchanged.

This is separate from the post-Stage-4 refinement loop. Post-export refinement starts only after export, applies targeted manual-adjustment moves to an already materialized plan, and may re-export improved artifacts. The mid-planning loop instead tries to improve the planner search before export and does not create or patch export files by itself.

### Embed the Aktivitetskalender in WordPress

When the input workbook contains a supported public activity table, Stage 4 writes both timestamped exports and the published `latest/` bundle with:

- `https://niclas-lindgren.github.io/hockey/latest/activities.json`
- `https://niclas-lindgren.github.io/hockey/latest/activities/`

Use the standalone page as a responsive WordPress iframe; it loads only the small JSON export and does not parse the workbook in the browser. On desktop/tablet the default view is `Sesongsløp` (age-group swimlanes) where each activity is a compact point-in-time markør. Rows answer which age group, horizontal position answers when, and marker code/shape/color answers category. Full names, location, description, and links open in an overlay dialog and are always available in the chronological list. On mobile it opens in the month-grouped `Liste` view to avoid squeezing twelve-month swimlanes into a narrow iframe. The old decorative `Årshjul` view is removed rather than retained as a secondary view, because it did not encode a meaningful age-group comparison grammar; do not re-add it unless it gets labelled age-group rings, date-angle geometry, category cues, and accessibility coverage.

The category vocabulary is canonicalized during export rather than inferred by browser JavaScript:

| Code | Category id | Norwegian label |
|---|---|---|
| `SU` | `spillerutviklingssamling` | Spillerutviklingssamling |
| `RS` | `regionslagssamling` | Regionslagssamling |
| `RM` | `regionsmesterskap` | Regionsmesterskap |
| `RT` | `regionsturnering` | Regionsturnering |
| `AN` | `annet` | Annen aktivitet |
| `?` | `unknown` | Ukjent aktivitetstype |

Known legacy values such as `IA`, `RS`, `regionsturnering`, and `regionsturneringju16` are mapped during export. Unknown explicit values produce `validation_warnings` and the deterministic `unknown` fallback. `Alle` remains a filter label only; it is not rendered as a synthetic age-group lane. Activities that apply to every group use `age_groups: ["ALL"]` and are duplicated across visible age-group lanes by the renderer.

```html
<iframe
  id="rvv-activities-frame"
  src="https://niclas-lindgren.github.io/hockey/latest/activities/?frame=rvv-activities-frame"
  title="Aktivitetskalender for Region Viken Vest"
  loading="lazy"
  style="width:100%;min-height:320px;height:420px;border:0;display:block;overflow:hidden"
></iframe>
```

The generated page measures its rendered height after initial load, filter/view changes, breakpoint changes, details open/close, and font/layout changes. It uses `ResizeObserver` where supported, coalesces updates with `requestAnimationFrame`, clamps heights to a safe range, and sends schema-versioned messages like:

```js
{
  type: 'rvv-activities-height',
  namespace: 'rvv.activities',
  schema_version: 1,
  iframe_id: 'rvv-activities-frame',
  height: 742,
  reason: 'resize-observer',
  source_path: '/hockey/latest/activities/'
}
```

Add this parent-side listener in WordPress (for example in a Custom HTML block near the iframe, or in the theme's allowed custom script area) when dynamic iframe height is available. It validates the GitHub Pages origin, namespace/schema, intended iframe id, and numeric bounds before changing height, so multiple exported iframes can coexist without cross-updating:

```html
<script>
(function () {
  var EXPECTED_ORIGIN = 'https://niclas-lindgren.github.io';
  var NAMESPACE = 'rvv.activities';
  var MESSAGE_TYPE = 'rvv-activities-height';
  var SCHEMA_VERSION = 1;
  var MIN_HEIGHT = 320;
  var MAX_HEIGHT = 6000;

  window.addEventListener('message', function (event) {
    var data = event.data || {};
    if (event.origin !== EXPECTED_ORIGIN) return;
    if (data.type !== MESSAGE_TYPE) return;
    if (data.namespace !== NAMESPACE || data.schema_version !== SCHEMA_VERSION) return;
    if (typeof data.iframe_id !== 'string' || !data.iframe_id) return;

    var frame = document.getElementById(data.iframe_id);
    if (!frame || frame.contentWindow !== event.source) return;

    var height = Number(data.height);
    if (!Number.isFinite(height)) return;
    height = Math.max(MIN_HEIGHT, Math.min(MAX_HEIGHT, Math.ceil(height)));
    frame.style.height = height + 'px';
    frame.style.minHeight = MIN_HEIGHT + 'px';
  });
})();
</script>
```

If the WordPress theme does not allow the listener, keep a conservative fixed `height`/`min-height`. With the listener installed, avoid a large permanent blank area: start around `height:420px` and let the child document resize the iframe as soon as it renders. Theme/template changes such as switching the page to a full-width template or removing an archive/sidebar column are manual WordPress follow-ups and are intentionally not coupled to the repository export.

### Rebuild calendar HTML

```bash
rvv-miniputt calendars
rvv-miniputt calendars --refresh
```

### Inspect progress

In Pi, use the slash commands:

- `/rvv-miniputt status`
- `/rvv-miniputt logs`

Outside Pi, use the portable equivalents:

- `scripts/rvv-miniputt status`
- `scripts/rvv-miniputt logs list`

Run logs (`run-*.jsonl`) live in the export tree alongside the exported artifacts; `.pipeline/logs/` is only a legacy fallback when no export folder exists yet.

When stages are invoked individually rather than through `rvv-miniputt run`/`operator run` — e.g. an agent following the stage-by-stage flow in `run.md` — no `pipeline_run_*.log` gets written, since that file is only produced by the single-process `operator run` orchestration. Each of the four stage scripts instead appends one line per invocation to `stage_run.log`, using the same location resolution as the JSONL run logs above (export tree once Stage 4 has run, `.pipeline/logs/` before that). It is the only debugging trail for a stage-by-stage session, so check it there before assuming a run left no record.

### Recover from blocked sources

Typical recovery loop for a blocked JS-only source:

1. fix `input.xlsx` or source credentials
2. rerun `rvv-miniputt run`
3. if a JS source is still blocked, use Pi, Claude Code, Codex, or OpenCode for LLM-driven scraping; in a plain terminal, first run `scripts/rvv-miniputt recovery-targets` to confirm the blocked source, then gather event JSON with WebFetch or your own script
4. inject the recovered events into the cache with `python3 -m tournament_scheduler.cli.rvv_cli recovery-inject --source "<navn>" < recovered-events.json`
5. normalize the Stage 2 checkpoint with `scripts/rvv-miniputt scrape-merge`
6. rebuild calendars with `scripts/rvv-miniputt calendars`

Holmen's Sportello calendar now uses the deterministic GraphQL scraper, so it should not need this browser-only recovery path.

## Headless / CI usage

For cron jobs or CI pipelines, configure a headless judge backend so inter-stage judgment still runs.

Set `RVV_JUDGE_BACKEND` before running the pipeline:

```bash
# Use the Anthropic Claude API as the judge
export RVV_JUDGE_BACKEND=claude
export ANTHROPIC_API_KEY=sk-ant-...
scripts/rvv-miniputt run --input input.xlsx --export-dir export

# Use the OpenAI API as the judge
export RVV_JUDGE_BACKEND=openai
export OPENAI_API_KEY=sk-...
scripts/rvv-miniputt run --input input.xlsx --export-dir export

# Use a locally-running LLM via LM Studio / llm-bridge (no API key required)
export RVV_JUDGE_BACKEND=llm_bridge
scripts/rvv-miniputt run --input input.xlsx --export-dir export
```

If `RVV_JUDGE_BACKEND` is not set, the pipeline logs a warning and continues.

### Required environment variables per backend

| `RVV_JUDGE_BACKEND` | Required env var | Notes |
|---------------------|-----------------|-------|
| `claude`            | `ANTHROPIC_API_KEY` | Uses Anthropic Messages API |
| `openai`            | `OPENAI_API_KEY`    | Uses OpenAI Chat Completions API |
| `llm_bridge`        | — | Requires LM Studio running at `host.lima.internal:1234` |

## Notes

- The scheduler is season-based, not a single-tournament planner.
- Stage checkpoints live in `.pipeline/` and make reruns idempotent where possible.
- HTML reports and Spond export are part of the standard Stage 4 output.
