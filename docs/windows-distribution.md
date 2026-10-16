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

Implemented in `tournament_scheduler/distribution_update/` (epic #364, sub-issue #622)
and tested offline in `tests/test_distribution_update.py`. Network access is injected
through a `Transport`, so no test touches the network.

### Release manifest

The release channel publishes one JSON manifest per channel. Only release artifacts
and this metadata come from the release source; there is no hosted backend.

```json
{
  "schema": 1,
  "package": "rvv-miniputt",
  "channel": "windows-stable",
  "latest_version": "2.1.0",
  "minimum_write_version": "2.0.0",
  "minimum_supported_version": "1.9.0",
  "artifact": {
    "filename": "rvv-miniputt-2.1.0-windows-x64.zip",
    "url": "https://<release host>/rvv-miniputt-2.1.0-windows-x64.zip",
    "sha256": "<64 lowercase hex characters>",
    "size": 123456789
  }
}
```

Rules enforced by the parser: `https` artifact URL only; plain `.zip` file name; valid
SHA-256 and positive size; `minimum_supported_version <= minimum_write_version <= latest_version`.
Unknown extra keys are ignored. The manifest is never part of season or publication state.

### Classification

| Status | Condition | Canonical writes | Operator message |
|---|---|---|---|
| `current` | installed >= `latest_version` | allowed | none |
| `update-available` | minimum write <= installed < latest | allowed (optional update) | update is optional |
| `update-required` | minimum supported <= installed < `minimum_write_version` | **blocked** | install latest before writing |
| `unsupported` | installed < `minimum_supported_version` | **blocked** (reads may continue) | reinstall from a full package |

### Write-compatibility gate

`distribution_update.write_decision()`, `require_write_allowed()` and
`write_decision_from_cache()` are the gate for canonical mutation paths. They fail closed:

- a version below a declared minimum is blocked;
- a corrupt cached manifest blocks writes (it is not ignored);
- with no cached manifest there is no declared minimum, so writes are allowed and the
  decision reason says the minimum is unknown. Run an update check to establish one.

**Not yet wired.** No commit, push or apply path calls the gate today. Wiring it into the
canonical mutation facade is the next step, and it belongs in the application layer, not in
scheduler policy. Until then the gate is a callable contract only.

### Check, download and activation

1. **Check** (`check_for_update`): reads the manifest through the transport, validates it,
   classifies the installed version, and caches the manifest only after it validates. An
   offline or invalid response returns an error result and leaves the previous cache in place.
2. **Stage** (`download_verified`): writes `<name>.partial` under the staging directory,
   checks size and SHA-256, and renames to the final name only after both match. Any failure
   removes the partial file.
3. **Extract** (`extract_release`): rejects absolute, `..`, backslash and drive-prefixed
   members; requires an `rvv-miniputt/` application folder.
4. **Activate** (`activate_release`): all renames stay inside the install root. The active
   release moves to `app\rvv-miniputt.previous`, the staged release moves into place, and if
   that move fails the previous release is moved back. An interrupted swap (slot missing,
   previous present) is recovered on the next activation.
5. **Confirm or roll back**: after the new release passes its post-update check, call
   `confirm_release` to delete the previous release; if it fails, call `rollback_release`.

The data root is checked with `ensure_data_outside_application` before any rename. Staging,
activation and rollback never touch `%LOCALAPPDATA%\RVV Miniputt\data`.

Install layout used by activation:

```text
%LOCALAPPDATA%\Programs\RVV Miniputt\
  app\rvv-miniputt\            <- active release
  app\rvv-miniputt.previous\   <- kept until confirmed
  updates\<version>\           <- staging (download, extraction)
```

### Operator-facing surface

`rvv-miniputt version [--json] [--manifest PATH]` is read-only. It prints the installed
version and, with a local manifest file, the update status and whether writes are allowed.
It makes no network request. A check against the live release channel is not exposed in the
CLI yet, because the channel URL is not decided.

### Migrations

The updater runs no migrations and does not write canonical state. Canonical store schema and
revision compatibility belong to the application's store (see #649). A release that needs a
state migration must declare it through `minimum_write_version`, and the migration must be an
explicit, tested application command that the operator runs, not an updater side effect.

### Not implemented

- **Signature verification.** The manifest and archive are checked by SHA-256 only. The
  manifest itself is unsigned, so a tampered manifest could lower the declared minimum. This
  must be closed before the gate is a security guarantee. It needs a signing key decision and
  is not verifiable here.
- **GitHub Releases publication workflow.** Not built. Release channel URL and artifact
  upload are undecided.
- **Gate wiring** into canonical mutation paths (see above).
- **Post-update smoke check** that calls `confirm_release` or `rollback_release` automatically.
  The functions exist; the orchestration does not.
- **Restricted-user install on Windows.** Activation uses only per-user directory renames.
  Not verified on Windows.

## Verification status

| Check | Status |
|---|---|
| Unit tests for data-root selection, separation guard, working-root preparation, version metadata (`tests/test_distribution.py`) | Run in the repository gate. |
| Manifest, classification, write gate, cache, staged download and integrity, extraction, activation and rollback (`tests/test_distribution_update.py`) | Run in the repository gate, offline with fixtures. Activation is tested on Linux only. |
| PyInstaller analysis and bundling of the spec | Verified on Linux with a scratch build (not Windows). All 30 HTML templates are bundled. |
| Frozen binary: `--help`, `status`, separation guard | Verified on Linux. |
| Windows binary, no-admin install/launch/uninstall, restricted user rights | **Not verified here.** Requires a Windows machine. |
| Norwegian filenames, Excel/CSV/iCal exports opening on Windows | **Not verified here.** |
| Representative read-only RVV commands against copied canonical data on Windows | **Not verified here.** |
