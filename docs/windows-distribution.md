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

**Wired into the repository adapter only.** `commit_canonical_season()` (see below) calls
`require_write_allowed` on the cached manifest before it stages, commits or pushes anything, and
a corrupt cached manifest blocks writes. The other canonical mutation paths (CLI apply and
publish flows, which today rely on the operator's own Git commits) do not call the gate yet.
Wiring them is the next step, and it belongs in the application layer, not in scheduler policy.

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
- **Gate wiring** into the remaining canonical mutation paths (the repository adapter is wired;
  see above).
- **Post-update smoke check** that calls `confirm_release` or `rollback_release` automatically.
  The functions exist; the orchestration does not.
- **Restricted-user install on Windows.** Activation uses only per-user directory renames.
  Not verified on Windows.

## Repository adapter and sign-in (sub-issue #619)

Implemented in `tournament_scheduler/infrastructure/repository_sync/` and tested offline in
`tests/test_repository_sync_*.py` against temporary local repositories (a bare remote plus a
clone). Git transport lives only in this infrastructure package. Scheduling, verification and
publication policy are not in it.

### Checkout states

`inspect_checkout()` refreshes the remote-tracking `origin/main` (never the local branch) and
classifies the checkout. `classify_state()` is pure and fail-closed in this order:

| State | Meaning |
|---|---|
| `not-cloned` | No git work tree at the configured path. A plain directory inside another repository also counts. |
| `unavailable` | Git cannot run, the fetch failed (including auth rejection, reported as `auth_rejected`), or the checkout cannot be read. |
| `not-on-main` | Detached HEAD or another branch. |
| `local-changes` | Tracked files or the index differ from HEAD. Untracked files are ignored. |
| `diverged` | Local `main` and `origin/main` both have commits the other lacks. |
| `behind` | Local `main` is behind `origin/main` only. |
| `ahead` | Local `main` has commits not on `origin/main` only. |
| `ready` | Local `main` equals `origin/main` and the tracked tree is clean. |

### Refresh and clone

- `refresh_main()` fast-forwards only, and only from `behind` (`git merge --ff-only`). Every other
  state is refused with no change. Divergent work is never overwritten, and there is no reset,
  clean, stash, force push or branch switch.
- `clone_repository()` clones `main` over `https` only. It refuses a non-empty destination.

### Canonical commit and push

`commit_canonical_season()` is the only Git write path. It runs in this order:

1. the write gate (`require_write_allowed` on the cached manifest; corrupt cache blocks);
2. the checkout must be `ready` or `local-changes` with no ahead or behind commits;
3. every tracked change must lie under the season root, and the index must be empty;
4. `git add -A -- <season root>`, then a commit of the staged season state only;
5. `git push origin HEAD:refs/heads/main`. This is a plain push, never forced. If it is refused,
   the local commit is kept and reported, and the checkout becomes `ahead`. The operator must
   refresh or reconcile before retrying.

The commit payload is what the canonical store already wrote under the season root. The adapter
does not decide season content, and it does not replace `CanonicalSeasonStore`'s revision checks.

### Credentials and sign-in state

- `CredentialStore` is the interface. `InMemoryCredentialStore` is the test double.
- `WindowsCredentialManagerStore` is a **seam only**. It raises `CredentialStoreError` on every
  call, so a build without the real backend fails closed and never falls back to a file.
  Target name: `RVV Miniputt/github`.
- Tokens reach git only through the child environment (`https_auth_env()`, an
  `http.extraheader` via `GIT_CONFIG_*`). They never appear in argv, `.git/config`, remote URLs
  or files. `Credential.__repr__` redacts the token.
- `resolve_auth_state()` reports signed out, authenticated, unauthorized or expired. Expired and
  rejected credentials are cleared from the store, and the state becomes signed out with
  `recovered_from` set. The checkout is never touched.
- **Requested scopes (decision pending):** the minimum for a private repository is repository
  contents read and write on this one repository (fine-grained token), or the classic `repo`
  scope if the OAuth app cannot use fine-grained permissions. Confirm before the device flow exists.

### Not implemented in this slice

- **GitHub browser sign-in (OAuth device flow) is not done.** Token acquisition, the GitHub
  `GET /user` probe that feeds `ProbeResult`, and the operator sign-in command all need the
  network and the decided OAuth app, so they could not be tested offline here.
- **Windows Credential Manager backend** (`CredReadW`/`CredWriteW`/`CredDeleteW`) is not written.
  It cannot be exercised on this Linux VM.
- **Operator CLI surface** for clone, refresh, status and commit is not exposed yet.
- **Gate wiring** into the other canonical mutation paths (see above).

## Claude runtime readiness and dispatch (sub-issue #620)

Implemented in `tournament_scheduler/infrastructure/claude_runtime/` and tested offline in
`tests/test_claude_runtime_*.py`. A fake runner stands in for the Claude executable, so no test
starts Claude, logs in or reaches the network.

### Readiness states

`probe_runtime(runner)` runs `claude --version`, then `claude auth status --json`, and stops at the
first failing check (fail closed):

| State | Condition | Operator recovery |
|---|---|---|
| `not-installed` | executable not on PATH | install the supported Claude Code CLI |
| `incompatible-version` | version outside the contract range | install a supported version |
| `unverifiable` | version or auth status missing or unparseable | run the check again |
| `not-authenticated` | `loggedIn` is false | sign in (`claude auth login`), no reinstall |
| `auth-expired` | a dispatched run is rejected for authentication | sign in again, no reinstall |
| `ready` | in range and signed in | none |

`RuntimeReadiness.to_dict()` is the operator-safe view. It holds state, message, version, the
auth method and the recovery text. It never holds the account email, organisation id or any token.

### Compatibility contract

`SUPPORTED_CONTRACT` (`contract.py`) declares the supported Claude Code range,
`>= 2.1.0` and `< 3.0.0`, and the agent protocol version `1`. The range is the 2.1.x line that was
verified on this build VM (2.1.296). Major version 3 is excluded until it has been assessed.

### Dispatch (fail closed before mutation)

`dispatch_operate(request, readiness=..., runner=..., repo_root=...)`:

1. refuses unless the readiness is `ready` and the package protocol equals the contract protocol;
2. only then starts one process: `claude -p "/rvv-miniputt:operate <request>"` in the checkout.

The existing Claude transport (`.claude/commands/rvv-miniputt/operate.md`) loads the shared
procedure. The `/rvv-miniputt:operate` argument is the operator request as intent. No scheduling,
verification, prompt policy or audit logic is in the Windows adapter. The refusal happens before the
runner is called, so no canonical mutation is dispatched when the runtime is missing, signed out,
expired, incompatible or on a mismatched protocol. A dispatch failure that looks like an
authentication rejection is reported as `auth-expired`.

Tokens and account identity are never read by this package. Claude stores its own credentials outside
the repository. The package has no logging. Its errors and readiness never contain probe or dispatch
output.

### Sign-in

`launch_login()` starts `claude auth login` as a subprocess attached to the console. The Claude CLI
performs the browser or OAuth flow. Nothing is captured.

### Not implemented in this slice

- **No operator CLI or UI surface.** Nothing calls `probe_runtime` or `dispatch_operate` from the
  `rvv-miniputt` command or the packaged application yet, so the states are not shown to an operator.
- **Protocol check against the repository.** The protocol version is declared in the package. It
  is not yet compared with a repository-declared protocol marker. The caller supplies the version.
- **Auth-expiry detection is heuristic.** The probe does not see expiry directly. `auth-expired` comes
  from matching authentication-rejection markers in a failed dispatch. The exact expiry signal of
  `claude auth status` was not verified here.
- **Windows packaging of Claude.** Whether the Claude CLI can be installed or run under a restricted
  Windows account is not decided or verified. The package does not bundle Claude.
- **Dispatch permissions.** `claude -p` runs with the CLI's normal permission prompts. Non-interactive
  tool approval for the operate procedure is not configured here.
- **Login verification.** The interactive sign-in was not run. Only the argv is tested.

## Verification status

| Check | Status |
|---|---|
| Unit tests for data-root selection, separation guard, working-root preparation, version metadata (`tests/test_distribution.py`) | Run in the repository gate. |
| Manifest, classification, write gate, cache, staged download and integrity, extraction, activation and rollback (`tests/test_distribution_update.py`) | Run in the repository gate, offline with fixtures. Activation is tested on Linux only. |
| Checkout states, fast-forward refresh, refused divergent/dirty refresh, write-gated commit and push, refused push, credential model and auth recovery (`tests/test_repository_sync_*.py`) | Run in the repository gate, offline against temporary local git repositories on Linux. Not verified against GitHub or Windows Git. |
| PyInstaller analysis and bundling of the spec | Verified on Linux with a scratch build (not Windows). All 30 HTML templates are bundled. |
| Frozen binary: `--help`, `status`, separation guard | Verified on Linux. |
| Windows binary, no-admin install/launch/uninstall, restricted user rights | **Not verified here.** Requires a Windows machine. |
| Claude readiness states, compatibility gate, fail-closed dispatch, credential-free output (`tests/test_claude_runtime_*.py`) | Run in the repository gate, offline with a fake runner. The real Claude CLI 2.1.296 was probed on this VM (read-only `--version` and `auth status`). |
| Interactive Claude sign-in, real operate dispatch from the packaged app, Windows Claude install | **Not verified here.** Needs an operator login and a Windows machine. |
| Norwegian filenames, Excel/CSV/iCal exports opening on Windows | **Not verified here.** |
| Representative read-only RVV commands against copied canonical data on Windows | **Not verified here.** |
