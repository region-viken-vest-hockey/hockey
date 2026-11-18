# ADR 0009: Planning I/O ports and backend-neutral readiness

Status: Accepted (2026-11-17). Tracked by [#957](https://github.com/region-viken-vest-hockey/hockey/issues/957) under the decoupling epic [#949](https://github.com/region-viken-vest-hockey/hockey/issues/949). Implementation owners: [#958](https://github.com/region-viken-vest-hockey/hockey/issues/958) inputs, [#959](https://github.com/region-viken-vest-hockey/hockey/issues/959) store, [#960](https://github.com/region-viken-vest-hockey/hockey/issues/960) outputs, [#968](https://github.com/region-viken-vest-hockey/hockey/issues/968) ice-time sources, [#961](https://github.com/region-viken-vest-hockey/hockey/issues/961) acceptance. The current coupling inventory is [`architecture/io-dependency-map.md`](../architecture/io-dependency-map.md).

## Context

Planning, verification and season maintenance already sit below the adapters, but the system assumes one deployment: a Git checkout of this repository, `input.xlsx`, `season/`, `export/`, `.pipeline/`, `gh-pages` and a logged-in `gh`. The `operate` startup procedure hard-codes that deployment. A local installation (browser workbench, no Git checkout) needs the same use cases without those assumptions, and must not get a second domain model, verifier or source policy.

## Decision

1. **Five kinds of I/O are separate.** (1) input facts/configuration, (2) source/evidence acquisition and trust, (3) revisioned canonical state, (4) export formats, (5) destinations/publication. Each has a typed contract in `tournament_scheduler/application/ports/`; adapters implement them and the application receives them by injection.
2. **Contracts only.** The package holds frozen DTOs, enums, `PortError` codes and structural `Protocol` ports. It has no planning, verification or maintenance policy, imports only the standard library, and names no RVV path or Git ref. It is not a plugin SDK: there is no registry, discovery or dynamic loading.
3. **One owner per concern stays.** `CanonicalSeasonService` remains the only mutation owner; a store port persists a complete snapshot atomically (optionally compare-and-swap on revision) and reports typed failures. `verify_candidate` stays the independent hard-legality owner. Reports and exports derive from authoritative state.
4. **Domain and application do not import transport.** Git, HTTP, browsers, the CLI, `cwd` and RVV layout stay in adapters. `tests/test_io_coupling_guard.py` enforces this for the domain packages (no exceptions) and the application layer (one reviewed edge that may only shrink).
5. **Backend-neutral readiness.** `ReadinessReport` is `READY` or `REVIEW_REQUIRED` with one stable `ReadinessReason`, a `workflow_version` (exactly the contract the session uses; only on `READY`) and an optional `canonical_revision`. Readiness vouches for the workflow contract only; canonical lifecycle, revision and reconciliation stay with the separate fail-closed lifecycle gate (`season lifecycle` / handover), so a `READY` without a revision never means "state verified". Fail-closed semantics are unchanged: a check never stashes, resets, cleans or otherwise discards local work to become green, and an unverifiable fact is `REVIEW_REQUIRED`. The Git-backed RVV backend keeps `make operate-preflight` and reports `workflow_version` equal to its `workflow_commit`; a local installation reports the same contract from its own check and requires no Git checkout, `origin/main` or `gh` login.
6. **Ice-time sources are four distinct fact kinds.** (a) venue allocation/availability windows, (b) observed calendar events plus source coverage, (c) exact booked-tournament assertions with canonical trust and association, and (d) the effective planning/verification facts derived from them by the canonical projection owner. Every fact carries a `SourceRef` (source id, manual/file/live kind, accountable actor, document, content fingerprint, revision, IANA timezone, capture time). Coverage records scope, completeness and freshness; conflict and uncertainty are explicit values. Free ice and an empty or partial scraper result are never booking authority; only a `BookingAssertion` with an accountable or fingerprinted source can be `CONFIRMED`. A manual-only mode needs no URL or network. The live RVV scraper is one adapter beside manual and file adapters in the same region. #968 owns the implementation; this ADR adds no second source policy.
7. **Export is not publication.** An `ExportBundle` is content-addressed and bound to one canonical revision. A `PublishIntent` names a destination, one exact bundle fingerprint and the operator's explicit authorization, and is refused without it. Producing an export never publishes.

## Compatibility and errors

- The port contract is versioned (`PORT_CONTRACT_VERSION`); `PortCapabilities.require` refuses an adapter that implements another version or lacks a needed feature, and `SchemaCompatibility.require` refuses an out-of-range persisted schema. Persisted canonical schema versions stay owned by the canonical store layout.
- Failures use `PortErrorCode`: `not_found`, `unavailable`, `revision_conflict`, `schema_incompatible`, `unsupported_feature`, `authorization_required`. Adapters map native errors onto them; callers fail closed and never infer a default. The unchanged legacy store satisfies the store port's behavior but still raises its own exceptions until the phase-2 adapter translates them, so the normalized error contract is a target, not yet a guarantee of every implementation.
- Existing CLI output, JSON reports and fingerprints do not change. `operate-preflight` adds `reason_code`, `backend`, `workflow_version` and `readiness_schema_version` and keeps every previous field and message.

## Phases

| Phase | Change | Owner |
|---|---|---|
| 0 (this ADR) | Contracts, dependency map, coupling guard, readiness codes in the Git gate, backend-neutral startup wording | #957 |
| 1 | Normalized season/problem and calendar-evidence adapters (workbook, manual, file) | #958, #968 |
| 2 | Local durable workspace store beside the legacy store; map `CanonicalRevisionConflictError` and layout errors to `PortError`; inject the store, not a root path | #959 |
| 3 | Export bundle and destination separation; Pages/Git publication becomes one destination | #960 |
| 4 | Git-free fixture, adapter parity, migration and offline acceptance | #961 |

Phase 0 changes no season state and performs no physical store migration.

## Rollback

Every phase is additive behind a port. The legacy Git-backed adapters remain the default until an acceptance run proves parity on a frozen fixture (never a live season). To roll back a phase, stop selecting the new adapter; no canonical data is rewritten in place, so the legacy store and workflows continue unchanged. If the coupling guard fails, add a port or revert the offending import; do not widen an allowlist.

## Consequences

- A new I/O dependency in core fails a test instead of relying on review.
- Backend-specific startup is one reviewable contract rather than prose in three procedures.
- Some adapters (revision history, parity checks) still shell out to Git; they remain adapters until their phase moves them.
