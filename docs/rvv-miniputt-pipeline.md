# RVV Miniputt pipeline

The four-stage pipeline turns the controlled planner workbook plus external calendar evidence into a deterministically verified, reviewable season plan. After deliberate promotion, a second operational phase maintains that season as stable canonical state while clubs review/book ice.

## Shared harness model

Every interactive harness uses the same repository-owned policy and procedures:

```text
AGENTS.md
  ↓
.agents/skills/rvv/SKILL.md
  ↓
.agents/commands/rvv-miniputt/<command>.md
  ↓
scripts/rvv-miniputt ...
```

Claude, Codex, ChatGPT, Pi and future harnesses should not maintain independent Stage 1–4 semantics, decision prompts, semantic-audit engines or browser scrapers. The active agent reads the shared instructions and reasons directly over repository output.

## Phase 1 — initial season creation

Run:

```bash
scripts/rvv-miniputt run --interactive --input input.xlsx
```

A pause returns a structured `DecisionContext` containing decision-relevant facts, hard violations, warnings, available actions, argument schema and a decision-action template. Choose only a declared action and follow `.agents/commands/rvv-miniputt/run.md` for resume semantics.

### Stage 1 — configuration

Validate and normalize `input.xlsx`. Invalid identities/date windows/configuration are input errors, not soft preferences.

### Stage 2 — source/calendar evidence

Collect configured calendar evidence, cache provenance and classify blocked/empty/suspicious sources. Browser recovery is optional and harness-neutral. If a source genuinely needs browser navigation, use the shared `scrape-llm` procedure: the browser-capable session extracts only the needed data, then returns it through repository `recovery-inject` and `scrape-merge` so Stage 2 validates it.

### Stage 3 — planning

Build/optimize candidate schedules through repository search/solver capabilities. Deterministic verification owns hard validity; agent judgment only chooses among exposed actions and soft tradeoffs. A localized hard defect is exposed as repository-generated repair options rather than fixed by a new procedural planner rule: the controller selects one stable option id, which is applied atomically and re-verified. This covers, for example, `host_team_missing` participant/rehost/remove repairs, `fill_participant`/`swap_participant` repairs for a tournament whose materialized roster is smaller than its independently derived `effective_team_count`, `replace_participants` roster substitutions that keep an already-valid placement for a double-booked/duplicated participant (`placement_preserving_roster_repair`), and `move_same_host_date`/`move_same_host_start_time`/`swap_compatible_tournament_placement`/`interpret_calendar_event_as_movable` repairs for a tournament the planner left as `MANUAL PLACEMENT REQUIRED`, each with explicit rejected alternatives. When none of those direct options verifies for a locally searchable hard finding, the controller falls back to a bounded neighborhood search (`search_neighborhood_repair`) over the affected age group(s): only participant re-pairing and represented-host reassignment inside that neighborhood are allowed, every tournament outside it is frozen, and only independently verified hard-improvements are exposed as option ids (with per-seed rejection evidence when the bounded search finds none). A tournament left as manual placement is soft evidence, so it still finalizes through the ordinary comparison context rather than being replaced by a solver retry. Run-scoped pre-plan choices such as shared-host assignment are reused for later Stage 3 attempts in the same logical run. Post-plan arena-conflict answers mutate the exact persisted Stage 3 candidate/checkpoint that produced the decision context, then recompute remaining collisions on that same candidate; only explicit optimize/search actions create a new candidate. Hosting fairness is recomputed from the current candidate whenever the plan is scored or verified: the evidence includes club × age-group proportional target, actual hosting count, deficit/excess and unresolved coverage so later rehosting/repair/search mutations cannot silently transfer one club's hosting burden to another.

### Stage 4 — export/review

Re-verify the selected candidate and write the timestamped review bundle. Hard verification failure blocks export. Remaining hosting-balance imbalances are non-hard review evidence: they keep the burden deficit/excess visible for operator follow-up unless verified alternatives have repaired it.

A normal export may contain season HTML/report, manual follow-up view, calendar/input views, Excel/CSV/iCal, Spond workbooks, per-club review packets and `export_manifest.json` lifecycle/provenance metadata.

## Promote the operational baseline

When the verified schedule is accepted for club review/ice booking, promote it explicitly:

```bash
scripts/rvv-miniputt season promote --work-dir .pipeline --season 2026-2027
```

This creates:

```text
season/2026-2027/schedule.json
season/2026-2027/decisions.json
```

Promotion verifies the exact reviewed candidate against the verification context that
accepted the Stage 4 export, not against whatever Stage 1/2 state happens to be in
`.pipeline` at promotion time. The Stage 4 checkpoint carries a versioned
`verification_context` (source run id, candidate/export fingerprint, the normalized
planning problem and its fingerprint) plus the reviewed final plan snapshot after
Stage 4 reconciliation. Promotion refuses with an explicit stale/missing-provenance
error if the Stage 4 export is incomplete/stale, the selected candidate no longer
matches the reviewed export, the source run differs, or the verification context is
missing -- it never silently falls back to context-free verification. `schedule.json`
records the reviewed plan snapshot, `promoted_from` records the source run,
reviewed export/candidate fingerprint and verification-context fingerprint, and the
canonical state retains the verification-context snapshot needed for later canonical
exports. Legacy reviewed exports that lack either verification context or the reviewed
plan snapshot are not promotable in-place; re-run Stage 4 for the same candidate to
create a new provenance-bound reviewed handoff while preserving the old export as
historical evidence.

Promotion is deliberate and exclusive. An ordinary later Stage 3 run whose window matches the promoted season adopts that canonical schedule as its baseline rather than silently regenerating it.

## Phase 2 — promoted-season maintenance

Useful commands:

```bash
scripts/rvv-miniputt season status --season 2026-2027
scripts/rvv-miniputt season approvals --season 2026-2027
scripts/rvv-miniputt season findings --season 2026-2027
scripts/rvv-miniputt season repair-options --season 2026-2027 --finding <finding-id>
scripts/rvv-miniputt season search --season 2026-2027 --finding <finding-id>
scripts/rvv-miniputt season apply-repair --season 2026-2027 --option-id <id> --expected-revision <rev>
scripts/rvv-miniputt season approve --season 2026-2027 --tournament-id <id> --note "ice booked"
scripts/rvv-miniputt season unapprove --season 2026-2027 --tournament-id <id> --note "booking changed"
scripts/rvv-miniputt season move --season 2026-2027 --tournament-id <id> --date 2026-10-18
scripts/rvv-miniputt season replan --season 2026-2027 --iterations 4000
scripts/rvv-miniputt season diff --season 2026-2027 --candidate <candidate.json>
scripts/rvv-miniputt season apply --season 2026-2027 --candidate <candidate.json>
scripts/rvv-miniputt season export --season 2026-2027
```

### Finding-directed maintenance

`season findings` recomputes the current canonical plan with the independent verifier and reports fresh, revision-bound facts (unresolved hosting obligations, hosting-balance deficits, hard violations, manual placements, participation strong-goal deviations with their `avoidability`). These are never the snapshot promoted with the season, and they are independent facts rather than a mandatory processing queue.

For one selected finding, `season repair-options` enumerates the repository's verified direct/coupled alternatives and `season search` runs the bounded finding-directed neighborhood search when those are insufficient. `season apply-repair` applies exactly one option against the current canonical revision: a stale `--expected-revision` is rejected, the mutation must pass locks/approvals plus full verification, and the result carries the new revision and a repository-computed before/after delta. No Stage 1/2 rerun is required to repair an already-promoted season.

### Stable identity

Tournament IDs are durable identity, not hashes of mutable date/host/arena/participants. Ordinary moves/rehosting/participant edits retain the ID; true split/merge/replacement creates new identity with lineage where applicable.

### Approvals and locks

Approval lives in `decisions.json`, separate from schedule facts.

- `season approve` re-verifies the current placement; approval is not a hard-rule waiver.
- Approved placement/participant locks become hard-preserve constraints for all baseline-aware planning paths.
- `season unapprove` is the explicit route back to editability.
- A changed protected-fields fingerprint becomes `stale_approval`; the stale lock is dropped and explicit reapproval is required.
- Approval/unapproval history remains durable in canonical decision state.

### Targeted changes

Use `season move` for one known placement change. It preserves durable ID and rejects locked tournaments until explicitly unapproved. The move clones canonical state, applies only the requested date/arena/host/start-time fields, rejects default cross-half moves, verifies the complete candidate before writing, and records old/new placement plus before/after fingerprints in decisions history. Add `--dry-run` to perform the same validation without mutating `schedule.json` or `decisions.json`.

For broader quality/placement repair, use `season replan`, inspect `season diff`, then `season apply`. Search starts from canonical state, honors locks and includes weighted change cost so published-but-unapproved tournaments are not churned gratuitously.

Hosting responsibility is recomputed after candidate-changing operations as a club/shared-registration × age-group ledger: proportional target, assigned responsibility, automatic placements, manual/unplaced responsibility and actual physical hosting. Missing trustworthy ice first triggers bounded responsibility-preserving repair (for example another legal date/slot for the same responsible host, or a roster alternative that still represents that host). If the bounded repair budget finds no verified placement, the obligation remains with the intended club as manual placement; another club's convenient slot is reported as physical excess rather than silently absorbing the responsibility.

Canonical writes are transactional: rejected verification or write failure must not leave mixed `schedule.json` / `decisions.json` state.

## Export lifecycle

Timestamped exports start as `draft`. `export_manifest.json` records export/candidate fingerprint, source run and (when applicable) canonical season/revision. Draft retention may prune old drafts; published exports and unclassified legacy exports are protected from the draft rolling window.

`season export` regenerates review output from canonical state and records the current canonical revision in the Stage 4 checkpoint/manifest and generated HTML metadata. It verifies with the durable verification context stored in canonical `schedule.json` and does not silently reload mutable Stage 1/2 checkpoints or scrape cache from `.pipeline`; deleting or replacing `.pipeline` must not change what the canonical revision means.

After canonical schedule/decision state changes, regenerate with `season export` before audit/publication. Do not knowingly publish an older projection of newer canonical state.

## Semantic safety-net audit

Interactive harnesses perform the audit in the active conversation/model after export:

```bash
scripts/rvv-miniputt operator audit-context
```

The default context is a bounded overview. Query exact supporting evidence only where needed:

```bash
scripts/rvv-miniputt operator audit-evidence --item 2
scripts/rvv-miniputt operator audit-evidence --tournament <id>
scripts/rvv-miniputt operator audit-evidence --club Kongsberg
scripts/rvv-miniputt operator audit-evidence --category participation_shortfalls
scripts/rvv-miniputt operator audit-evidence --unresolved
```

The harness then submits PASS / REVIEW_REQUIRED / FAIL through `operator audit-submit`. Detailed evidence is fingerprint-bound to the same export/run as the bounded overview. The interactive harness must not create another nested model/audit implementation. Headless CI may use the documented `operator audit-run --backend <name>` path instead.

The audit is an independent semantic safety net, not a second Python rules engine. It looks for suspicious operational patterns, missing rules, export inconsistency and defects the deterministic verifier may share with the scheduler.

A `REVIEW_REQUIRED` verdict raises a distinct operator-review question. That question is bound to the reviewed export's fingerprint and audit id, so an approval given for one export never satisfies the review of a later export with different content; each new export needs its own review approval.

## Publication

Planning/export never implies publication. After a fresh audit of the exact intended export:

```bash
make publish-preview
make publish CONFIRM_PUBLIC=1
make verify-publish
```

Publication builds a separate allowlisted/privacy-checked public bundle and promotes the exact source export lifecycle from `draft` to `published`. Rollback is explicit through publication history.

## Runtime state

- `.pipeline/` — transient run/checkpoint/cache/log state;
- `season/<season>/` — durable canonical schedule + approval/lock state;
- `export/<timestamp>/` — generated review/export projections;
- `gh-pages` branch — published snapshots.

Generated artifacts are derived data. Correct controlled input, code or canonical season state and regenerate rather than hand-maintaining generated output.
