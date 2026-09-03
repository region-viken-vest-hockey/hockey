# RVV Miniputt pipeline guide

The executable RVV Miniputt workflow is owned by `scripts/rvv-miniputt` and the Python package under `tournament_scheduler/`. Harness-specific commands are adapters, not alternative pipeline implementations.

For canonical operational policy—including Stage 2 source readiness, recovery, BookUp authentication, and harness boundaries—read:

- `.agents/skills/rvv/SKILL.md`
- `.agents/skills/rvv/RUNBOOK.md`

## Overview

The season-planning workflow is checkpointed in `.pipeline/` and runs in four stages:

1. **Stage 1 — Config**: validate the standard `input.xlsx` workbook and expand the roster.
2. **Stage 2 — Scraping**: fetch/cache calendar events and decide whether source data is ready for planning.
3. **Stage 3 — Planning**: generate and evaluate the season plan.
4. **Stage 4 — Export**: write review/publication artifacts.

The CLI owns orchestration, resume behavior, checkpoints, retries, gates, and failure semantics. Checkpoints live under `.pipeline/stage*.json`.

## Input workbook

`input.xlsx` is the standard pipeline input. The important sheets are:

- `Innstillinger` — season dates and scalar settings.
- `Lag` — `club`, `label`, `age_group` roster rows.
- `Aldersgrupper` — optional per-age-group parallel-game, round-length, and participation settings.
- `Kilder` — optional calendar source definitions.

Detailed schemas and registration-import contracts are documented in [`rvv-miniputt-input-formats.md`](rvv-miniputt-input-formats.md).

Reviewed SharePoint registrations can be validated/exported without rewriting administrative workbook sheets:

```bash
scripts/rvv-miniputt registrations validate registrations.csv --input input.xlsx
scripts/rvv-miniputt registrations export registrations.csv --input input.xlsx --output input.updated.xlsx --dry-run
scripts/rvv-miniputt registrations export registrations.csv --input input.xlsx --output input.updated.xlsx
```

## Normal operator flow

```bash
make operator-run
make status
make logs
```

or directly:

```bash
scripts/rvv-miniputt operator run
scripts/rvv-miniputt status
scripts/rvv-miniputt logs list
```

For browser-only GitHub operation, `season-validate.yml`, `season-review-bundle.yml`, `season-publish.yml`, and `season-rollback.yml` are thin wrappers around the same repository CLI. Generation/review does not imply publication; public writes remain behind the protected publication flow and explicit confirmation.

## Stage 2 source readiness

Stage 2 distinguishes unresolved sources from sources that must block planning. Python writes `blocked`, `blocking_sources`, `temporarily_unresolved_sources`, and `planning_ready`; adapters must use those results rather than recalculating them.

Tønsberg and Sandefjord Penguins are currently temporary non-blocking BookUp exceptions when authenticated calendars cannot be reached. They remain unresolved recovery targets. Other unexpected calendar failures remain blocking unless a human explicitly chooses `--allow-missing-sources`.

A source can also be technically reachable yet suspiciously sparse. Review `event_expectation_warnings`/source-health output before trusting a plan.

### BookUp authentication

The preferred BookUp path is reusable Playwright authentication state under:

```text
.pipeline/auth/bookup-storage-state.json
```

When authentication expires, establish/refresh the state in a visible browser on the macOS host so Vipps/SMS MFA can be completed. A convenient host-side refresh with the encrypted dotenvx environment is:

```bash
RVV_BOOKUP_MANUAL_LOGIN=1 ./node_modules/.bin/dotenvx run -f .env.bookup -- scripts/rvv-miniputt calendars --refresh
```

Headless Lima runs reuse the saved Playwright state. Normal Claude/Codex/ChatGPT runs inside Lima should not start their own credential/MFA flow.

## Browser recovery boundary

Deterministic scraping belongs to Python. A browser-capable harness such as Pi may recover a browser-only source, but recovered events must be written back to the shared cache and Python Stage 2 rerun before planning continues.

For terminal-only recovery:

```bash
scripts/rvv-miniputt recovery-targets
cat recovered-events.json | python3 -m tournament_scheduler.cli.rvv_cli recovery-inject --source "<source>"
scripts/rvv-miniputt scrape-merge
scripts/rvv-miniputt run --resume-from 2
```

`recovery-inject` reads a JSON array from stdin. `recovery-inject`/`scrape-merge` do not create a separate readiness policy; the final Python Stage 2 rerun owns the continue/stop decision.

## Planning and fairness

Planning/fairness rules are Python behavior. Do not copy thresholds, retry policy, or missing-source lists into Claude/Pi/Codex/ChatGPT command files.

Useful options include `--iterations N` for a wider deterministic Stage 3 search and `--mid-planning-critic-iterations N` for an opt-in pre-export critic loop. Publication remains a separate operation.

## Outputs

A normal run can produce:

- `season_plan.xlsx`
- `season_plan.csv`
- `season_plan_overview.csv`
- `season_plan.ics`
- `season_plan.html`
- `season_plan_report.html`
- `season_plan_spond.xlsx`
- `calendars.html`
- `input.html`
- `activities.json`
- `activities/index.html`

With `SOURCE_DATE_EPOCH`, Stage 4 can pin the content timestamp so identical input/Stage 3 content produces reproducible export bytes and a stable Pages bundle fingerprint. Operational audit timestamps remain separate.

## Human/operator command equivalents

| Operation | Make target | Direct command |
|---|---|---|
| Canonical verification | `make check` | `scripts/check` |
| Raw pipeline run | `make run ARGS='--input input.xlsx'` | `scripts/rvv-miniputt run --input input.xlsx` |
| Goal-oriented run | `make operator-run` | `scripts/rvv-miniputt operator run` |
| Status/logs | `make status`, `make logs` | `scripts/rvv-miniputt status`, `scripts/rvv-miniputt logs list` |
| Calendars | `make calendars`, `make calendars-refresh` | `scripts/rvv-miniputt calendars [--refresh]` |
| Source health | `make sources-status` | `scripts/rvv-miniputt sources status` |
| Questions | `make questions`, `make questions-all` | `scripts/rvv-miniputt operator questions [--all]` |
| Answer | `make answer ID=<id> ANSWER='<answer>'` | `scripts/rvv-miniputt operator answer <id> '<answer>'` |
| Publish preview | `make publish-preview` | `scripts/rvv-miniputt operator publish --dry-run` |
| Publish | `make publish CONFIRM_PUBLIC=1` | `scripts/rvv-miniputt operator publish --confirm-public` |
| Verify/history/rollback | `make verify-publish`, `make publish-history`, `make rollback RUN_ID=<id> CONFIRM_PUBLIC=1` | matching `operator` commands |

The retired desktop application is not an operator surface. Richer future interfaces must remain thin adapters over the same CLI/application capabilities.

## Standalone Påmeldte lag update

Reviewed registrations can be published without rerunning Stage 1–4 or regenerating unrelated Pages content:

```bash
make registered-teams CSV=downloads/Miniputt-26-27.csv
make registered-teams-publish CSV=downloads/Miniputt-26-27.csv CONFIRM_PUBLIC=1
```

The standalone public page is:

- `https://niclas-lindgren.github.io/hockey/latest/registered-teams/pameldte-lag.html`

The command validates public `club,label,age_group` fields, ignores private/administrative columns, and stages the result on top of the existing `/latest/` snapshot so unrelated season-plan and activity files remain intact.

## Embed the Aktivitetskalender in WordPress

The published activity bundle includes a standalone interactive page whose desktop/tablet default is `Sesongsløp`: age-group swimlanes with each activity represented by a compact point-in-time **markør**. On narrow screens it opens the chronological `Liste` view. The old `Årshjul` view is removed because it did not encode a useful age-group comparison grammar.

Use the standalone page as a responsive WordPress iframe:

```html
<iframe
  id="rvv-activities-frame"
  src="https://niclas-lindgren.github.io/hockey/latest/activities/?frame=rvv-activities-frame"
  title="Aktivitetskalender for Region Viken Vest"
  loading="lazy"
  style="width:100%;min-height:320px;height:420px;border:0;display:block;overflow:hidden"
></iframe>
```

The generated page sends schema-versioned height messages such as:

```js
{
  type: 'rvv-activities-height',
  namespace: 'rvv.activities',
  schema_version: 1,
  iframe_id: 'rvv-activities-frame',
  height: 742
}
```

A WordPress parent listener should validate origin, namespace/schema, iframe identity, source window, and numeric bounds before resizing:

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

If the theme does not allow the listener, use a conservative fixed height. Theme/template changes such as switching the page to a **full-width** layout or removing a sidebar are **manual WordPress follow-ups** and are intentionally outside the repository pipeline.

## Verification

```bash
make check
make dependency-lock
```

See [`ci.md`](ci.md) for the canonical check phases and locked dependency workflow, and [`ownership-and-handover.md`](ownership-and-handover.md) for operational ownership and handover.
