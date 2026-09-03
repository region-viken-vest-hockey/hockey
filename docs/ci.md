# CI and local verification

`scripts/check` is the canonical verification entry point. Run all checks locally with:

```bash
scripts/check
# or
make check
```

GitHub CI invokes the same phase selectors so local and hosted verification cannot silently drift:

```text
scripts/check dependency-lock
scripts/check quick
scripts/check operator
scripts/check reproducibility
scripts/check cli-smoke
```

The CI workflow keeps these as separate jobs for visible status checks, plus secret scanning. The retired Electron/desktop prototype is not built or tested in CI.

## Locked Python dependencies

`pyproject.toml` is the **canonical direct dependency declaration**. `requirements.lock` is generated from it with `pip-compile --all-extras`, so **all optional dependency groups** used by the repository are represented in deterministic installs.

Refresh the lock with:

```bash
scripts/refresh-python-lock.sh
```

Verify it without accepting changes:

```bash
scripts/check dependency-lock
make dependency-lock
```

CI and operator installation use:

```text
pip install --require-hashes -r requirements.lock
pip install --no-deps -e .
```

The hash lock covers Python packages. **Playwright browser binaries** are a separate runtime install and are not embedded in `requirements.lock`.

## Why the phases are split

- `dependency-lock` catches dependency drift before tests use a subtly different environment.
- `quick` runs the normal Python regression suite without coverage overhead.
- `operator` protects run-manifest, escalation, and operator control paths.
- `reproducibility` protects deterministic planning behavior.
- `cli-smoke` exercises the repository CLI across process boundaries.
- `secret-scan` checks the full repository history/configured scope for leaked secrets.

When a CI job fails, reproduce it with the corresponding `scripts/check <phase>` command rather than copying the YAML command sequence into a new local workflow.
