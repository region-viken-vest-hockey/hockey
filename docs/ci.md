# CI: required checks and branch protection

This documents the checks introduced for issue #16 — automatic, visible
evidence of test/reproducibility/packaging health on every PR and push to
`main`, since an autonomous operator needs independently enforced evidence
before changes are trusted or merged, not just a local "hundreds of tests
passed" claim in a commit message.

## Required-fast tier: `.github/workflows/ci.yml`

Triggers on every pull request and every push to `main`. Every job here is
designed to run in low single-digit minutes and makes **no live external
calendar/network call** — all fixtures are synthetic or in-memory, so the
whole tier is safe to require as a merge gate without flaking on a real
club's calendar site being slow or down.

The canonical local verification entrypoint is `scripts/check` (or `make
check`, which delegates to it). With no arguments it runs the complete local
suite. CI invokes phase selectors such as `scripts/check quick` and
`scripts/check cli-smoke` so GitHub can keep separate visible status checks
without duplicating the underlying command sequence in workflow YAML.

| Job (status check name)                          | What it covers |
|----------------------------------------------------|----------------|
| `Python dependency lock freshness`                  | `scripts/check dependency-lock` — recreates `requirements.lock` from `pyproject.toml` with pip-tools and fails if the committed hash-checked lock would change. |
| `Python quick test suite`                           | `scripts/check quick` — the default `pytest` run (excludes `slow`/`integration`-marked tests), covering the full unit/component suite. |
| `Operator manifest & escalation tests`              | `scripts/check operator` — `test_run_manifest.py`, `test_capability_result.py`, `test_escalation.py`, `test_operator_run.py` specifically, as their own visible check. |
| `Deterministic planner reproducibility`             | `scripts/check reproducibility` — `test_reproducibility.py`, where the same config/seeds must reproduce the same selected-candidate metadata and plan dates across two independent runs. |
| `CLI integration smoke test`                        | `scripts/check cli-smoke` — `test_cli_smoke.py` (marked `integration`), a real `rvv-miniputt` subprocess invocation (`operator run`, `status`, `sources status`, `operator questions`, `candidates`) against a synthetic workbook with zero calendar sources. |
| `Desktop backend API smoke test`                    | `scripts/check desktop-backend` — a real HTTP round-trip against `desktop_server.Handler` (manifest, questions, answer, and confirms the dead `/run` route stays gone). |
| `Desktop packaging config validation`               | `scripts/check desktop-packaging` — static checks on `apps/desktop/package.json` and `release.yml` (no Electron download, no build, no publish). Guards against the wrong-owner and missing-keyring bugs found in issue #7. |
| `Secret scanning (gitleaks)`                        | `gitleaks/gitleaks-action`, configured via the existing `.gitleaks.toml`. |

Python jobs install from the committed hash-checked `requirements.lock` with `pip install --require-hashes -r requirements.lock` and then install the repository with `pip install --no-deps -e .`; they do not resolve the broad dependency ranges in `pyproject.toml` during normal CI. Dependencies are cached via `actions/setup-python`'s built-in `cache: pip`, keyed on `requirements.lock` and `pyproject.toml`, across jobs that install the locked set.

`pyproject.toml` remains the canonical direct dependency declaration. Refresh the lock intentionally with `scripts/refresh-python-lock.sh` (or `make dependency-lock` to verify freshness after refresh). The refresh script uses pip-tools and generates the lock with all optional dependency groups, so test dependencies and desktop packaging tools (`keyring`, `PyInstaller`, and their transitives) are pinned alongside runtime dependencies. Platform/browser assets that are not Python packages, such as Playwright Chromium binaries and Electron/npm dependencies, remain managed by their existing installers/lockfiles.

Failure artifacts: the quick-suite job uploads its `htmlcov/` coverage
report; the reproducibility and CLI-smoke jobs upload their `--basetemp`
directory (generated run manifests, checkpoints, logs) on failure so a
human or agent can inspect exactly what state a failing run produced.

## Browser-based operator workflows

The repository also contains manual, browser-dispatched workflows for trained
volunteers. They are not PR checks; they are operational entrypoints under the
GitHub **Actions** tab and remain thin wrappers over `scripts/rvv-miniputt`.

| Workflow file | Browser name | Main use | Mutation boundary |
|---|---|---|---|
| `.github/workflows/season-validate.yml` | `Sesong: valider inndata` | Validate a supplied workbook path, run the canonical quick check/operator validation path, and upload `input-fingerprint.json`, status, logs, manifest, and validation/export artifacts. | `contents: read`; no publish flags. |
| `.github/workflows/season-review-bundle.yml` | `Sesong: lag vurderingspakke` | Generate the review bundle from a workbook, create a publish dry-run/privacy report, upload review HTML/exports/logs/manifest, and optionally create/comment on a GitHub issue. | `contents: read`, `issues: write`; no `--confirm-public`. |
| `.github/workflows/season-publish.yml` | `Sesong: publiser godkjent pakke` | Download the approved review artifact, rerun publish dry-run, verify the exact approved `bundle_fingerprint`, then call `operator publish --confirm-public`. | `contents: write`, `actions: read`, protected `pages-publication` environment, `PUBLISER` confirmation. |
| `.github/workflows/season-rollback.yml` | `Sesong: rull tilbake publisering` | Roll `/latest/` back to a selected published `run_id` through `operator rollback --confirm-public`. | `contents: write`, protected `pages-publication` environment, `RULL_TILBAKE` confirmation. |

Repository maintainers should configure the `pages-publication` environment
with required reviewers for the publisher/approver role. Generation and
publication intentionally live in different workflows and permissions: a
review-bundle run can never publish as a hidden side effect, and publish cannot
run unless the operator supplies the review artifact id, exact run id, and exact
bundle fingerprint from the reviewed preview.

Regression coverage lives in
`tests/test_github_actions_operator_workflows.py`. It statically parses these
workflow files and verifies manual dispatch, permission boundaries, canonical
CLI delegation, required artifacts, protected publish/rollback environments,
and the absence of direct `gh-pages`/pipeline-module publishing logic.

## Slower/optional tier

Unchanged by this issue, and intentionally *not* required on every PR:

- **`.github/workflows/desktop-build.yml`** — manual (`workflow_dispatch`) or
  on a push touching desktop-relevant paths; builds a real unsigned macOS
  app via `electron-builder`. Slow (full Electron + PyInstaller build) and
  consumes meaningfully more Actions minutes, hence optional.
- **`.github/workflows/release.yml`** — triggers only on a `v*.*.*` tag;
  builds and publishes the real macOS/Windows/Linux release artifacts.

## Recommended branch protection

On the `main` branch, enable **Require status checks to pass before
merging** and require these exact check names (they're the job `name:`
values above, as GitHub renders them):

- `Python dependency lock freshness`
- `Python quick test suite`
- `Operator manifest & escalation tests`
- `Deterministic planner reproducibility`
- `CLI integration smoke test`
- `Desktop backend API smoke test`
- `Desktop packaging config validation`
- `Secret scanning (gitleaks)`

Also recommended: **Require branches to be up to date before merging**, so
a stale PR can't merge past a check that has since caught a regression on
`main`. The slower/optional workflows above should **not** be added as
required checks — they're not triggered on every PR and would permanently
block merging.
