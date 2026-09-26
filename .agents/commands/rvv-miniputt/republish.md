# RVV Miniputt: export and republish a published/sealed season

Internal shared procedure for **replacing the public snapshot of an already
published (sealed) season** after approved canonical maintenance. This is not a
separate operator command: the operator asks to publish/republish through
`operate.md`, which loads this procedure.

This procedure never replans. It exports the current canonical revision, proves
what changed against the exact previously published revision, and only then
publishes with explicit authorization.

## Preconditions

- The season is `published_sealed` (`season lifecycle --season <season> --json`).
- The canonical schedule/decision state is the intended operational state
  (canonical maintenance moves/roster/approvals/booking evidence have already
  been applied through their own verified operations).
- Do not continue if `reconciliation.ok != true` or if
  `season publication-evidence --json` reports projection schema errors or an
  unexplained published-to-canonical delta.

## 1. Resolve the authoritative current publication

Never infer the current public version from an arbitrary export directory or the
moving `latest/` path. Read it from canonical lifecycle/publication history:

```bash
scripts/rvv-miniputt season lifecycle --season <season> --json
scripts/rvv-miniputt season publication-evidence --season <season> --json
scripts/rvv-miniputt operator publish-history --json
```

The `active_publication` block is the authoritative record: publication id,
canonical revision, publication time, projection fingerprint and the immutable
Pages reference (`run_id` / bundle fingerprint / commit). `retained_evidence`
lists the before/after records retained under
`season/<season>/evidence/publications/<publication_id>/`.

## 2. Regenerate the exact current projection (export only)

```bash
scripts/rvv-miniputt season export --season <season>
```

This is the only regeneration step. Do **not** call `run`, `season replan`,
planner `apply`, mutating `normalize-placements`, automatic rescrape,
`reopen-planning` or any global regeneration. Export is schedule-preserving: if
canonical state is inconsistent it fails closed and the operator must fix it
through a proper canonical mutation, never by hand-editing canonical JSON.

## 3. Prove the replacement delta

```bash
scripts/rvv-miniputt season publication-evidence --season <season> --json
```

Review `published_to_canonical_delta` by stable tournament id: added / removed,
placement (date/start/arena/host), participants, occupied duration/end,
cancellation and guest reservations. Unchanged tournaments keep their full
operational state. `decision_changes` separately reports approval, booking,
protection and request-constraint changes; these are evidence-only and never
justify a schedule change.

The publication boundary refuses to publish when:

- canonical state no longer reconciles to the published baseline plus recorded
  mutations (unexplained drift);
- the export was generated from a stale canonical revision or no longer matches
  the current canonical projection;
- the projection schema is missing/incomplete;
- the previous published bundle has no immutable reference, or its verified
  `/runs/<run_id>/` snapshot is missing (the version being replaced would not be
  recoverable).

## 4. Verify before publication

- hard verification, artifact parity and freshness are part of the canonical
  export/publish gates and must be `PASS`;
- run the semantic safety-net audit for the exact fresh export fingerprint
  (`operator audit-context` / `operator audit-evidence` / `operator
  audit-submit`, or the documented headless `operator audit-run`).

Export alone never publishes. Publication requires the operator's explicit
publication request.

## 5. Publish

Only when the operator explicitly authorized publication:

```bash
scripts/rvv-miniputt operator publish --confirm-public
```

The publication boundary builds the sanitized public bundle, re-verifies parity,
publishes to `/latest/` and the immutable `/runs/<run_id>/`, promotes the source
export lifecycle, and appends the immutable publication baseline and the
retained before/after evidence. It then verifies the public output.

On success report: published URL, run id, canonical revision and the retained
evidence path. On failure, published output is left unchanged; report the exact
blocking gate and the operator question instead of improvising around it.

## Rollback

The previous published run stays reachable at `/runs/<run_id>/` and through the
retained evidence. If the replacement must be undone:

```bash
scripts/rvv-miniputt operator publish-history
scripts/rvv-miniputt operator rollback <previous-run-id> --confirm-public
```
