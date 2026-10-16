# Windows distribution layout

This document describes the self-contained Windows packaging of `rvv-miniputt`
(epic #364, sub-issue #618). The packaged CLI runs the same
`tournament_scheduler.cli.rvv_cli.main` as the `rvv-miniputt` console script and
`scripts/rvv-miniputt`. Packaging adds no scheduling, verification or
publication logic.

## Chosen layout

| Concern | Choice | Why |
|---|---|---|
| Bundler | PyInstaller, **onedir** mode, spec at `packaging/windows/rvv-miniputt.spec` | Targets the existing entry point. Onedir starts faster than onefile and keeps the app folder replaceable as a whole. |
| Entry point | `packaging/windows/rvv_miniputt_launcher.py` | Thin wrapper: places mutable data, then calls `main()`. |
| Install (application) | `%LOCALAPPDATA%\Programs\RVV Miniputt\app\rvv-miniputt\` | Per-user, no administrator rights. Replaceable on update. |
| Mutable data | `%LOCALAPPDATA%\RVV Miniputt\data\` (override: `RVV_MINIPUTT_DATA_DIR`) | Outside the install folder, so updates and uninstalls cannot touch it. |
| Version metadata | `tournament_scheduler/distribution.py::version_metadata()` | Deterministic: name, version and `distribution` (`packaged` or `source`). No timestamps, host or git state. |

The launcher does three things before handing over to the CLI:

1. It refuses to continue if the data root is inside the application directory.
2. It creates the data root and changes into it. The application resolves
   `season/`, `export/` and `.pipeline/` relative to the current directory, so
   all mutable state lands in the data root without changes to application code.
3. It sets `RVV_CANONICAL_SEASON_ROOT` to `<data root>\season` unless the
   operator has already set it.

It also reconfigures stdout and stderr to UTF-8 so Norwegian characters and
status glyphs do not crash a Windows console code page.

Resulting tree:

```text
%LOCALAPPDATA%\Programs\RVV Miniputt\          <- uninstall removes this folder only
  app\rvv-miniputt\rvv-miniputt.exe
  app\rvv-miniputt\_internal\...
%LOCALAPPDATA%\RVV Miniputt\data\              <- operator data; kept on uninstall
  season\  export\  .pipeline\
```

## Playwright and Chromium

- The Python `playwright` package is bundled. The calendar scraper imports it
  at module load, so excluding it would break the CLI.
- Chromium browser binaries are **not** bundled. Browser recovery is an
  optional, advanced step. It needs network access and writes per user to
  `%LOCALAPPDATA%\ms-playwright`, with no administrator rights.

## Build

Run from the repository root in a build virtualenv outside the repo. Use the
locked dependency set, not the floating `requirements.txt`:

```bat
py -3.12 -m venv C:\rvv-build\venv
C:\rvv-build\venv\Scripts\python -m pip install -r requirements.lock pyinstaller
C:\rvv-build\venv\Scripts\pyinstaller --noconfirm --workpath C:\rvv-build\work --distpath C:\rvv-build\dist packaging\windows\rvv-miniputt.spec
```

Optional CP-SAT (`ortools`) and test tooling are excluded on purpose.

## Install, launch and uninstall (operator)

- Install: copy `dist\rvv-miniputt\` to `%LOCALAPPDATA%\Programs\RVV Miniputt\app\rvv-miniputt\`.
- Launch: run `rvv-miniputt.exe status` (or any CLI command) from a console.
  Operator data is read and written under `%LOCALAPPDATA%\RVV Miniputt\data`.
- Uninstall: delete `%LOCALAPPDATA%\Programs\RVV Miniputt\`. This removes only
  application files. Operator data stays. Delete `%LOCALAPPDATA%\RVV Miniputt\`
  only after the operator has copied any exports they want to keep.

## Update implications

- An update replaces only the application folder. Keep the previous folder
  until the new build has run `status` successfully, then delete it.
- Canonical season state and exports are not part of the application folder.
  A newer application must read state written by older versions. Schema and
  revision compatibility belongs to the application's canonical store, not to
  packaging.
- Update checks are not implemented. The version metadata is the input for one.
- Because the data root is outside the install folder, the launcher's guard
  must stay in place. Changing `RVV_MINIPUTT_DATA_DIR` to a path inside the
  install folder makes the launcher exit with an error.

## Verification status

| Check | Status |
|---|---|
| Unit tests for data-root selection, separation guard, working-root preparation, version metadata (`tests/test_distribution.py`) | Run in the repository gate. |
| PyInstaller analysis and bundling of the spec | Verified on Linux with a scratch build (not Windows). All 30 HTML templates are bundled. |
| Frozen binary: `--help`, `status`, separation guard | Verified on Linux. |
| Windows binary, no-admin install/launch/uninstall, restricted user rights | **Not verified here.** Requires a Windows machine. |
| Norwegian filenames, Excel/CSV/iCal exports opening on Windows | **Not verified here.** |
| Representative read-only RVV commands against copied canonical data on Windows | **Not verified here.** |
