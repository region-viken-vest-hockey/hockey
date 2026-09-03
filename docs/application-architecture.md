# RVV Miniputt application architecture

This document records the migration direction for GitHub issue #44: adapters such
as CLI, Pi/Claude harness commands, and GitHub Actions should call
small typed application use cases instead of each reimplementing operator policy.

## Dependency rules

The intended dependency direction is:

```text
interfaces/adapters (CLI, desktop HTTP, harnesses, GitHub Actions)
        ↓
application use cases and DTOs
        ↓
domain policy + injectable ports
        ↓
infrastructure implementations (filesystem, git/Pages, network, keyring)
```

Rules for the current slice:

1. `tournament_scheduler.application` may import domain/pipeline modules while
   the migration is incremental, but it must not import transport/rendering
   modules.
2. Application modules must not import `tournament_scheduler.cli`,
   `tournament_scheduler.desktop_server`, `rich`, or `subprocess`.
3. Application functions return typed DTOs/results, not Rich console output,
   HTTP responses, process exit codes, or argparse namespaces.
4. Adapters own parsing, rendering, and exit-code mapping only. They should not
   decide persistence policy or duplicate operator-state rules.
5. New cross-adapter behavior should start as an application use case, then be
   wired into adapters.

A lightweight architecture test enforces the forbidden imports above. When the
application layer grows, add ports/tests before moving code that currently needs
real filesystem, git, source retrieval, or secret-store access.

## Current migrated slice

`rvv-miniputt operator questions|answer|promote|health` now goes through
`tournament_scheduler.application.operator_state`:

- `list_operator_questions(work_dir, include_all=False)`
- `record_operator_answer(work_dir, question_id, answer, decided_by=None)`
- `promote_operator_question(work_dir, question_id, scope, scope_key="", decided_by=None)`
- `check_operator_health(work_dir)`

The CLI still owns Norwegian text rendering and JSON serialization. The
application layer owns the typed in-process contract over existing durable
`RunManifest`/escalation state.

## Example: adding a new command/use case

Suppose volunteers need a browser and CLI command for "show publication status":

1. Add DTOs such as `PublicationStatus` to `tournament_scheduler.application.dto`
   or a dedicated application DTO module.
2. Add `inspect_publication_status(request: PublicationStatusRequest) ->
   PublicationStatus` under `tournament_scheduler.application`.
3. Keep filesystem/git/network operations behind explicit parameters or ports so
   the use case can be tested without a terminal, HTTP server, Git remote, or
   real network.
4. Wire `rvv-miniputt operator publish-status` to parse arguments, call the use
   case, render Rich output or JSON, and map the result to an exit code.
5. Wire desktop/harness/GitHub Actions adapters to the same use case rather than
   shelling out to the CLI.
6. Add one application test for the use case and one adapter test for argument
   parsing/rendering.

This keeps user-facing transports thin and makes future migrations possible in
small, independently testable slices.
