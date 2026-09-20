# CI: required checks and branch protection

This documents the checks introduced for issue #16 — automatic, visible
evidence of test/reproducibility health on every PR and push to
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
check`, which delegates to it). With no arguments it runs the default
quick/PR tier. CI invokes phase selectors such as `scripts/check quick` and
`scripts/check cli-smoke` so GitHub can keep separate visible status checks
without duplicating the underlying command sequence in workflow YAML. See
[quick vs full](#quick-vs-full-when-to-use-which) below for the slower lanes.

| Job (status check name)                          | What it covers |
|----------------------------------------------------|----------------|
| `Python dependency lock freshness`                  | `scripts/check dependency-lock` — recreates `requirements.lock` from `pyproject.toml` with pip-tools and fails if the committed hash-checked lock would change. |
| `Python quick test suite`                           | `scripts/check quick` — the default `pytest` run (excludes `slow`, `integration`, `harness` and `live`-marked tests), covering the full fast unit/component suite. |
| `Operator manifest & escalation tests`              | `scripts/check operator` — `test_run_manifest.py`, `test_capability_result.py`, `test_escalation.py`, `test_operator_run.py` specifically, as their own visible check. |
| `Deterministic planner reproducibility`             | `scripts/check reproducibility` — `test_reproducibility.py`, where the same config/seeds must reproduce the same selected-candidate metadata and plan dates across two independent runs. |
| `CLI integration smoke test`                        | `scripts/check cli-smoke` then `scripts/check cli-contracts` — the real-subprocess CLI smoke test plus the public CLI contract suite (every maintained top-level command: usage/exit-code/stdout-stderr and machine-parseable `--json` output). |
| `Secret scanning (gitleaks)`                        | `gitleaks/gitleaks-action`, configured via the existing `.gitleaks.toml`. |

Python jobs install from the committed hash-checked `requirements.lock` with `pip install --require-hashes -r requirements.lock` and then install the repository with `pip install --no-deps -e .`; they do not resolve the broad dependency ranges in `pyproject.toml` during normal CI. Dependencies are cached via `actions/setup-python`'s built-in `cache: pip`, keyed on `requirements.lock` and `pyproject.toml`, across jobs that install the locked set.

`pyproject.toml` remains the canonical direct dependency declaration. Refresh the lock intentionally with `scripts/refresh-python-lock.sh` (or `make dependency-lock` to verify freshness after refresh). The refresh script uses pip-tools and generates the lock with all optional dependency groups, so test dependencies are pinned alongside runtime dependencies. Platform/browser assets that are not Python packages, such as Playwright Chromium binaries, remain managed by their existing installers.

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

## Slower/optional tier: full lane, live and harness

Unchanged by this issue, and intentionally *not* required on every PR:

- **`.github/workflows/release.yml`** — triggers only on a `v*.*.*` tag;
  builds and publishes the real macOS/Windows/Linux release artifacts.

### `scripts/check full` — the comprehensive hermetic lane

The quick tier intentionally skips `slow` and `integration` tests. To prove
those tests still work together (and to exercise the real canonical
`input.xlsx` planner path that would otherwise stay dark between releases),
run the full lane:

```bash
scripts/check full        # lint + full-tests + rules-report
scripts/check full-tests  # just: quick + slow + hermetic integration (one pytest run)
scripts/check slow        # just the hermetic slow tests (canonical workbook/planner)
scripts/check integration # just the hermetic/local subprocess + multi-stage tests
```

`scripts/check full` prints a per-phase timing summary and runs pytest with
`--durations=25`, so a deterministic-phase performance regression stays
attributable. The pipeline itself records coarse phase timings in the run
manifest (`timing`), keeping solver/search budgets
(`stage3_local_search_seconds`, `stage3_cp_sat_seconds`) separate from the
deterministic overhead (`stage3_verification_seconds`,
`stage3_decision_context_seconds`, ...).

The full lane is run by `.github/workflows/full-verification.yml` on a nightly
`schedule:` and on `workflow_dispatch:` — it is deliberately **not** a required
PR status check, because it includes the multi-minute canonical-planner tests.
Its failure is still a normal, visible, actionable workflow run with uploaded
artifacts.

### Test markers and lane separation

`pyproject.toml` registers four markers, and the default `addopts` selection
excludes all of them (quick = fast hermetic unit/component tests only):

| Marker | Meaning | Lane |
|---|---|---|
| `slow` | Deterministic but long-running (real canonical workbook/planner). | Included in `scripts/check full`; excluded from quick. |
| `integration` | Hermetic/local subprocess or multi-stage test (no network). | Included in `scripts/check full`; excluded from quick. |
| `harness` | Requires a specific external agent/tool runtime. | Only `scripts/check harness`; never quick or full. |
| `live` | Depends on a live external source/network service. | Only `scripts/check live` with `RVV_LIVE_TESTS=1`; never quick or full. |

This keeps `-m integration` predictable: it means "hermetic integration test
suitable for CI", not a mixture that also silently includes an unavailable
harness or a flaky upstream service. `harness`/`live` tests do not disappear
under a generic marker — they have their own selectable lanes and their own CI
job (`live`, `continue-on-error`).

BookUp (Tønsberg) is a public deterministic source: there is no
authentication/MFA/manual-login test category. Its browser/DOM parser behavior
is covered hermetically without credentials in
`tests/test_scraper_bookup_deterministic.py`; the live reachability check in
`tests/test_live_sources.py` is classified as an external `live` test because it
depends on the public upstream service.

### Quick vs full: when to use which

- **Normal change / PR:** `make check` (quick tier). Fast; this is the default
  agent and CI feedback loop.
- **Before review/publication or on the nightly schedule:** `scripts/check full`,
  so slow planner and integration coverage cannot rot unnoticed.
- **When touching a calendar source or its parser:** the hermetic tests always
  run; additionally run `scripts/check live` if you need to confirm the real
  upstream service is still reachable.

## Recommended branch protection

On the `main` branch, enable **Require status checks to pass before
merging** and require these exact check names (they're the job `name:`
values above, as GitHub renders them):

- `Python dependency lock freshness`
- `Python quick test suite`
- `Operator manifest & escalation tests`
- `Deterministic planner reproducibility`
- `CLI integration smoke test`
- `Secret scanning (gitleaks)`

Also recommended: **Require branches to be up to date before merging**, so
a stale PR can't merge past a check that has since caught a regression on
`main`. The slower/optional workflows above should **not** be added as
required checks — they're not triggered on every PR and would permanently
block merging.
