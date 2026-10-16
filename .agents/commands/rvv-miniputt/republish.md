# RVV Miniputt: export and republish a published/sealed season

Internal shared procedure for **replacing the public snapshot of an already published (sealed) season** after approved canonical maintenance **or presentation-only renderer/template/CSS/JS changes**. This is not a separate operator command: the operator asks to publish/republish through `operate.md`, which loads this procedure.

Load `audit-publication.md` for publication-scoped evidence review and recovery when the semantic audit reports historical season-wide obligations. Do not treat an incomplete full-season planning audit as a substitute for the public projection's actual safety checks, or bypass a blocking repository gate.

This procedure never replans. It exports the current canonical revision, proves what changed against the exact previously published revision and public presentation, pauses at the publication authority boundary when publication has not yet been authorized, and only then publishes with explicit authorization.

## Presentation-only republish contract

For a published/sealed season, presentation regeneration and republish are one continuous operator workflow with an explicit publication boundary:

1. generate a fresh presentation-only export;
2. prove canonical state and projected tournament facts are unchanged;
3. compare the generated public presentation with the authoritative active publication and show the public-facing delta automatically;
4. pause for publication confirmation if the operator has not already asked to publish; and
5. after confirmation, reuse that exact verified export when it is still fresh, run the required audit/gates, and publish.

The operator must not need to ask separately for "compare the generated HTML with the published one", restate the presentation-only safety contract, choose a resume stage, or name an internal command. The harness owns those checks. If the operator's initial request already explicitly includes publication, the explicit authority requirement is satisfied and the harness must not stop only to ask the same publication question again.

## Preconditions

- The season is `published_sealed` (`season lifecycle --season <season> --json`).
- The canonical schedule/decision state is the intended operational state (canonical maintenance moves/roster/approvals/booking evidence have already been applied through their own verified operations).
- Do not continue if `reconciliation.ok != true` or if `season publication-evidence --json` reports projection schema errors or an unexplained published-to-canonical delta.

## 1. Resolve the authoritative current publication

Never infer the current public version from an arbitrary export directory or the moving `latest/` path. Read it from canonical lifecycle/publication history:

```bash
scripts/rvv-miniputt season lifecycle --season <season> --json
scripts/rvv-miniputt season publication-evidence --season <season> --json
scripts/rvv-miniputt operator publish-history --json
```

The `active_publication` block is the authoritative record: publication id, canonical revision, publication time, projection fingerprint and the immutable Pages reference (`run_id` / bundle fingerprint / commit). `retained_evidence` lists the before/after records retained under `season/<season>/evidence/publications/<publication_id>/`.

## 2. Regenerate the exact current projection (export only)

```bash
scripts/rvv-miniputt season export --season <season>
```

This is the only regeneration step. Do **not** call `run`, `season replan`, planner `apply`, mutating `normalize-placements`, automatic rescrape, `reopen-planning` or any global regeneration. Export is schedule-preserving: if canonical state is inconsistent it fails closed and the operator must fix it through a proper canonical mutation, never by hand-editing canonical JSON.

When the change is presentation-only (renderer, template, CSS or JavaScript, with no intended season-state change), replace the export step above with:

```bash
scripts/rvv-miniputt season export --season <season> --presentation-only
```

The command prints a JSON report (`status`, `canonical_revision_before` / `canonical_revision_after`, `canonical_schedule_or_decisions_changed`, `artifact_parity`, `export_fingerprint`, `semantic_audit`). Only `status: PASS` (exit 0) is a clean regeneration. `FAIL` (exit 1) means the revision moved, the export was stale, or the new renderer does not faithfully project canonical facts; `NOT_CHECKABLE` (exit 2) means parity could not be verified. Report the revisions and parity result to the operator; do not publish from this report alone.

## 3. Prove the replacement delta

```bash
scripts/rvv-miniputt season publication-evidence --season <season> --json
```

Review `published_to_canonical_delta` by stable tournament id: added / removed, placement (date/start/arena/host), participants, occupied duration/end, cancellation and guest reservations. Unchanged tournaments keep their full operational state. `decision_changes` separately reports approval, booking, protection and request-constraint changes; these are evidence-only and never justify a schedule change.

For a presentation-only republish, also compare the generated public presentation with the exact immutable snapshot identified by `active_publication`; do not use moving `latest/` as the comparison authority. Compare `season_plan.html` and any separately published presentation assets that changed. Classify the differences into a compact operator-facing table with at least these categories:

| Area | Published | Generated | Effect |
|---|---|---|---|
| Canonical/operational tournament data | exact published projection | regenerated projection | must be unchanged for a presentation-only republish |
| Revision/metadata | previous public revision metadata | current canonical/export metadata | expected when applicable |
| Presentation behaviour/content | previous HTML/CSS/JS/text/order | regenerated HTML/CSS/JS/text/order | intended public-facing change |

Do not hide a tournament-data change inside a textual/HTML diff. Use canonical publication evidence and artifact parity to establish data equality, then use the presentation comparison to explain style/design/interaction differences. If canonical or operational tournament data changed unexpectedly, stop and route it as canonical maintenance rather than calling the result presentation-only.

If the operator asked only to regenerate, refresh, rebuild, preview or review the presentation, this comparison is the required pre-publication checkpoint. Report whether the result is ready to publish and pause for explicit publication confirmation. If the operator explicitly asked to publish/republish in the current request, continue without asking for duplicate authorization.

The publication boundary refuses to publish when:

- canonical state no longer reconciles to the published baseline plus recorded mutations (unexplained drift);
- the export was generated from a stale canonical revision or no longer matches the current canonical projection;
- the projection schema is missing/incomplete;
- the previous published bundle has no immutable reference, or its verified `/runs/<run_id>/` snapshot is missing (the version being replaced would not be recoverable).

## 4. Verify before publication

- hard verification, artifact parity and freshness are part of the canonical export/publish gates and must be `PASS`;
- the semantic safety-net audit must be bound to the exact fresh export fingerprint.

### Active harness: semantic audit is in-session

When an interactive agent harness is orchestrating the republish, **do not** launch `operator audit-run` and do not require a `BACKEND`. That command is the headless cron/CI path and would create a nested model judgment. The active harness owns the semantic review:

```bash
scripts/rvv-miniputt operator audit-context
scripts/rvv-miniputt operator audit-evidence --item <id>   #/selectors as needed
scripts/rvv-miniputt operator audit-submit --result-file <result.json>
```

The harness must inspect the complete published-to-canonical delta and retrieve bounded evidence for every material concern before submitting its real verdict. It must preserve FAIL/INCOMPLETE evidence rather than replacing it merely to enable publication.

For unattended cron/CI only, the documented `operator audit-run --backend <claude|openai|llm_bridge>` path may perform the same semantic-audit transport headlessly.

The helper `make season-republish SEASON=<season>` prepares the fresh canonical export/evidence and stops at this harness handoff. After the harness submits the audit for that exact export, resume without regenerating it:

```bash
make season-republish SEASON=<season> RESUME_AFTER_AUDIT=1
```

Add `CONFIRM_PUBLIC=1` only when the operator explicitly authorized the public replacement.

For a presentation-only preview that was already generated and compared before the operator confirmed publication, reuse that exact export when its canonical revision, export fingerprint and active-publication baseline are still current. Re-check those bindings before audit/publish. If they changed, regenerate/recompare; if they did not, do not create a gratuitous second export just because authorization arrived in a later turn.

Export alone never publishes. Publication requires the operator's explicit publication request.

## 5. Publish

Only when the operator explicitly authorized publication:

```bash
scripts/rvv-miniputt operator publish --confirm-public
```

The publication boundary builds the sanitized public bundle, re-verifies parity, publishes to `/latest/` and the immutable `/runs/<run_id>/`, promotes the source export lifecycle, and appends the immutable publication baseline and the retained before/after evidence. It then verifies the public output.

On success report: published URL, run id, canonical revision and the retained evidence path. On failure, published output is left unchanged; report the exact blocking gate and the operator question instead of improvising around it.

## 6. Reconcile and checkpoint expected post-publication bookkeeping

A successful publish is expected to mutate durable publication bookkeeping even when tournament operations are unchanged. Do not make the operator repeatedly investigate these expected changes as if they were suspicious canonical drift.

After successful publication, inspect and classify the working-tree delta. The normal publication-bookkeeping envelope is:

- `season/<season>/season.json`: canonical revision and partition hash movement caused by publication bookkeeping;
- `season/<season>/decisions/operations.json`: appended publication history / published baseline / publication lifecycle metadata;
- `season/<season>/evidence/publications/<publication_id>/`: retained before/after publication evidence and rollback provenance;
- `season/<season>/change-log.md`: regenerated deterministic projection reflecting the post-publication canonical revision;
- the exact export directory used for publication, when export artifacts are repository-tracked by the current workflow.

For a presentation-only republish, verify before checkpointing that `schedule.json`, tournament placements, participants, booking evidence and the ordinary decisions partition are unchanged from the pre-publish canonical state. A revision change caused solely by the operations/publication partition is expected and is not a schedule mutation.

Always reconcile `change-log.md` through the repository-owned generator before judging the final post-publish diff. If the file is missing, stale or was transiently removed during publication, regenerate it canonically rather than escalating the absence by itself. A clean regenerated ledger whose only relevant change is the expected post-publication revision/bookkeeping is part of the normal checkpoint.

When the remaining delta is limited to that expected envelope and the operational-state verification passes, treat it as the successful publication checkpoint: report it as expected bookkeeping and commit it coherently without asking the operator to decide file-by-file or re-investigate why publication records changed the canonical revision. Do not publish again as part of this checkpoint.

Stop and investigate only when the post-publish delta escapes the expected envelope, including any unexplained change to schedule/tournament facts, participants, bookings, ordinary decisions/protections/requests, unrelated canonical files, or a `change-log.md` regeneration that reveals more than expected publication bookkeeping.

## Rollback

The previous published run stays reachable at `/runs/<run_id>/` and through the retained evidence. If the replacement must be undone:

```bash
scripts/rvv-miniputt operator publish-history
scripts/rvv-miniputt operator rollback <previous-run-id> --confirm-public
```
