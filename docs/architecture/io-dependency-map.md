# Planning I/O dependency map

Current Git/path/transport assumptions, grouped by the five kinds of I/O, with the authoritative owner of each and the port that will replace the coupling. This is the maintained inventory behind [ADR 0009](../adr/0009-planning-io-ports-and-backend-neutral-readiness.md). It maps ownership; it adds no behavior. Contracts live in [`tournament_scheduler/application/ports/`](../../tournament_scheduler/application/ports/), and [`tests/test_io_coupling_guard.py`](../../tests/test_io_coupling_guard.py) enforces that core code gains no new coupling.

Method: import/AST and path-literal search over `tournament_scheduler/`, `scripts/`, `.github/workflows/` and the `operate` procedures. Re-run the guard test rather than trusting a count here.

## Enforced facts

- The domain packages (`rules`, `planning`, `participation`, `placement`, `repair`, `season`, `booking`, `hosting`, `scoring`, `stage3`, `shape`, `models*.py`) import no subprocess, HTTP, browser, CLI or Git-adapter module and never call `Path.cwd()`/`os.getcwd()`.
- The application layer has exactly one reviewed edge into an I/O adapter (`calendars/source_policy.py` -> `pipeline.scraper_strategies`). It also never calls `cwd`.
- Default roots such as `DEFAULT_SEASON_ROOT = "season"` and the `RVV_CANONICAL_SEASON_ROOT` / `RVV_MINIPUTT_DATA_DIR` overrides already make the data location injectable. `distribution.prepare_working_root` still changes the process cwd so that relative `export/` and `.pipeline/` resolve; that is a transport convenience, not a contract.

## 1. Input facts and configuration

| Assumption | Where | Owner | Target |
|---|---|---|---|
| `input.xlsx` sheets `Aldersgrupper`, `Innstillinger`, `Kilder`, `Datopreferanser`, `Lag` | `pipeline/input_workbook.py`, `pipeline/stage1_config.py`, `cli/parsers/operator_run.py` (default path) | input normalization | Normalized season/problem DTO (#958); workbook becomes one adapter |
| Registrations via SharePoint/Power Automate into the `Lag` sheet | `intake/registrations.py`, `pipeline/registration_sync.py`, `sharepoint-*` workflows | intake | Normalized participant input (#958) |
| Cwd-relative default paths in CLI defaults | `cli/parsers/*`, `tools/calendar_compare.py` | CLI transport | Resolved by the adapter, passed in |

## 2. Source and evidence acquisition and trust

| Assumption | Where | Owner | Target |
|---|---|---|---|
| Live scrapers (requests/Playwright) per booking system | `pipeline/scraper_*.py`, `data_sources/` | scraper adapters | `IceTimeSourcePort` live adapter (#968) |
| Fixed allocation from the `Kilder` sheet | `pipeline/fixed_allocation_source.py` | source normalization | Allocation windows (a) from a manual/file adapter (#968) |
| Calendar events, coverage, bookings | `booking/calendar_evidence.py`, `booking/calendar_bookings.py` | booking domain | Observed events/coverage (b) and assertions (c), kept distinct |
| Source health and fabricated-interval detection | `pipeline/source_health.py`, `pipeline/source_integrity.py` | evidence owner | Unchanged; consumed through typed coverage |

## 3. Revisioned canonical state

| Assumption | Where | Owner | Target |
|---|---|---|---|
| `season/<season>/schedule.json` + `decisions.json`, atomic directory swap, advisory lock | `infrastructure/canonical_season_store.py` and `canonical_season_*` | `CanonicalSeasonStore` | `CanonicalStorePort` (reference adapter, unchanged); local workspace adapter (#959) |
| Revision CAS | `CanonicalRevisionConflictError`, `expected_revision` | store | `PortError(REVISION_CONFLICT)` mapping |
| Revision history resolved from `revisions/` and `git rev-list` | `infrastructure/canonical_revision_history.py` (subprocess, `Path.cwd()`) | revision history | `RevisionHistoryPort`; Git lookup stays a legacy adapter |
| Season checkout freshness, `origin/main`, commit and push | `infrastructure/repository_sync/`, `scripts/operate-preflight.py`, `make handover` | repository sync | Backend-neutral readiness (`ReadinessReport`); Git is one backend |
| Transient run state in `.pipeline/` | `pipeline/state.py`, `run_manifest.py`, `application/stage3_session_store.py` | run/session stores | Workspace-relative, adapter-resolved |

## 4. Export formats

| Assumption | Where | Owner | Target |
|---|---|---|---|
| XLSX/HTML/JSON bundle under `export/<id>/` | `pipeline/stage4_export*.py`, `excel/plan_exporter.py`, `html/` | export | `ExportBundle` with content-addressed artifacts (#960) |
| Export parity and freshness gate (reads published bytes with `git`) | `pipeline/export_parity/` | parity owner | Bundle-fingerprint comparison; Git read stays an adapter |
| Spond XLSX | `spond/spond_exporter.py`, `spond_import.py` | export format | One more artifact format |

## 5. Output and publication destinations

| Assumption | Where | Owner | Target |
|---|---|---|---|
| `gh-pages` branch, `latest/`, history | `pipeline/pages_*.py`, `publication/`, `season-publish.yml` | publication | `PublishDestinationPort`; `PublishIntent` bound to a bundle and an explicit authorization (#960) |
| Activity/registration publishing | `pipeline/activity_publish.py`, `activity_publication.py` | activity publication | Same destination port |
| CI/Actions (`ci.yml`, `season-*.yml`) | `.github/workflows/` | CI | Out of scope; Git-backed RVV keeps them |
| Operator/agent startup assumes Git and `gh` | `.agents/commands/rvv-miniputt/{operate,guide,handover}.md` | shared procedures | Readiness contract; Git-backed RVV keeps `make operate-preflight` |
