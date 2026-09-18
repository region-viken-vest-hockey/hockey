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

Within one `Stage3Session` the interactive Stage 3 loop keeps a bounded portfolio of independently verified attempts with stable `candidate_ref`s (`stage3_interactive:attempt_N`, `pareto:N:i`, `stage3_cp_sat:*`). Generating a later, worse attempt therefore does not make an earlier good attempt unselectable: the `DecisionContext` exposes the retained refs (`facts.retained_candidates`, the `apply_candidate` enum) and the controller accepts `apply_candidate(candidate_ref=...)` for any retained attempt. Each portfolio record carries the candidate body plus its revision/fingerprint, source/search arguments, hard-verification result, shared quality/objective vector, hosting/manual-placement counts and concise A/B evidence. Adoption is still an explicit `select_candidate` transition: a retained attempt is re-validated against the current Stage 1/2 facts identity and the current hard verifier (locks/decisions included) and is rejected as stale rather than being trusted from its original verification.

### Stage 4 — export/review

Re-verify the selected candidate and write the timestamped review bundle. Hard verification failure blocks export. Remaining hosting-balance imbalances are non-hard review evidence: they keep the burden deficit/excess visible for operator follow-up unless verified alternatives have repaired it.

A normal export may contain season HTML/report, manual follow-up view, calendar/input views, Excel/CSV/iCal, Spond workbooks, per-club review packets and `export_manifest.json` lifecycle/provenance metadata.

## Refine before promotion

A reviewed but unpromoted candidate can be refined in place if the semantic audit (or the operator) finds a localized defect. This is the normal path and is deliberately not promotion, not a Stage 3 reset and not a Stage 1/2 rerun:

```bash
scripts/rvv-miniputt stage3 refine --work-dir .pipeline --json
scripts/rvv-miniputt stage3 refine --work-dir .pipeline --finding <finding-id> --json
scripts/rvv-miniputt stage3 refine --work-dir .pipeline --finding <finding-id> --option-id <option-id> --json
```

The explicit `refine_candidate` session transition reopens the exact reviewed candidate (which stays the baseline), the repository-owned providers enumerate finding-directed options, one verified option becomes a new candidate revision and Stage 4 re-runs. Stage 1/2 checkpoints and fingerprints are read-only inputs (no rescrape, no baseline rebuild). The replacement export records the reviewed export it `supersedes`; the reviewed export itself is marked `superseded`, kept as immutable history and protected from draft retention, and a published export is refused (that is the publication/rollback boundary). The result reports `audit_required` and the fresh export fingerprint so the same semantic-audit boundary is re-run over the new export before publication. `--dry-run` previews the verified delta without mutating anything.

A `REVIEW_REQUIRED` verdict is feedback, not automatically a human escalation. While repository-owned findings still map to a supported repair/search direction, refinement can continue autonomously over the bounded Pareto frontier instead of stopping on the first audit finding:

```bash
scripts/rvv-miniputt stage3 converge --work-dir .pipeline --json
scripts/rvv-miniputt stage3 converge --work-dir .pipeline --finding <finding-id> --max-epochs 2 --json
scripts/rvv-miniputt stage3 converge --work-dir .pipeline --dry-run --json
```

Each epoch addresses one actionable finding direction, measures the provider's options on the shared objective vector, folds every independently verified non-dominated candidate into a bounded frontier (`Stage3Session.pareto_archive`) and commits one accepted mutation as a new candidate revision. Internal epochs persist verified planning state only: `stage3 converge` materializes exactly one timestamped Stage 4 review export at the batch/audit boundary, and only that handoff marks `audit_required`. A batch that committed revisions but could not materialize a handoff (`--no-export` or a failed export that keeps the verified candidate) reports `export_required` and leaves the prior audit valid. The bounded verified-attempt portfolio retains the candidate bodies of the retained frontier candidates, so a later worse attempt never makes an earlier non-dominated candidate unselectable; `stage3 adopt --candidate-ref <ref>` re-validates and adopts any retained frontier candidate (new revision + re-export, rejected as stale if the Stage 1/2 facts or the hard verifier changed). Every finding family carries the same `search_coverage` view, so a bounded search that actually ran and found nothing reports `bounded_search_exhausted` instead of appearing untried until the epoch budget runs out. Direction selection is a fair controller round: a committed mutation refreshes candidate-scoped finding/coverage evidence without resetting which direction families the current round already explored, so one direction that keeps producing small non-dominated changes cannot monopolize the epoch budget. Automatic refinement stops only on an explicit terminal: `pass`, `operator_required` (an operator policy/waiver/input question), `bounded_search_exhausted` or a bounded plateau (`pareto_stable`). An epoch-budget stop is a resumable pause (`pause_reason: budget_exhausted`, `resumable: true`), not a terminal: the report leaves `terminal_reason` empty and a later invocation with a larger `--max-epochs` continues from the persisted epoch, frontier and direction-round state without a candidate mutation, forced `--finding` or manual reset. Terminal/pause wording is deliberately relative to the explored neighborhoods and configured budgets -- the report sets `globally_optimal: false` and never calls a bounded search `proven_infeasible` or a fixed attempt count proof that human input is required. The convergence state (`convergence`) lives on the Stage 3 session, not a parallel side file. `stage3 session --json` reports the frontier and the current terminal or pause state.

A `REVIEW_REQUIRED` verdict is not a terminal run state. `operator audit-submit` automatically enters bounded convergence over the reviewed candidate (opt out with `--no-refine`), and `operator audit-run` loops audit → bounded refinement → re-audit within its bounded rounds. `stage3 converge` also auto-reads a fresh persisted audit result for the current export (`--ignore-audit` to opt out): a material audit finding whose checklist item maps to a direction the repository currently has an actionable finding for is covered by the ordinary loop, while an open-ended checklist item or a mapped direction the repository cannot act on becomes an explicit operator question rather than a blind search.

A plain `run` must not destroy this reviewed handoff. While a finalized, exported, unpromoted candidate exists and the export lifecycle manifest is not superseded, `run --interactive --resume-from 1` (and the non-interactive full run) is refused before Stage 1 restarts and before the Stage 3 session is cleared; the refusal names `stage3 refine` for ordinary post-audit improvement and requires an explicit `run --new-full-run` for a deliberately new full run. Once the candidate is promoted (its `promoted_from` provenance matches the canonical season), a new full run is no longer guarded.

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
scripts/rvv-miniputt season accept-deviation --season 2026-2027 --finding <finding-id> --note "ice unavailable"
scripts/rvv-miniputt season revoke-acceptance --season 2026-2027 --finding <finding-id>
scripts/rvv-miniputt season approve --season 2026-2027 --tournament-id <id> --note "ice booked"
scripts/rvv-miniputt season unapprove --season 2026-2027 --tournament-id <id> --note "booking changed"
scripts/rvv-miniputt season move --season 2026-2027 --tournament-id <id> --date 2026-10-18
scripts/rvv-miniputt season replan --season 2026-2027 --iterations 4000
scripts/rvv-miniputt season diff --season 2026-2027 --candidate <candidate.json>
scripts/rvv-miniputt season apply --season 2026-2027 --candidate <candidate.json>
scripts/rvv-miniputt season export --season 2026-2027
```

### Finding-directed maintenance

`season findings` recomputes the current canonical plan with the independent verifier and reports fresh, revision-bound facts (unresolved hosting obligations, hosting-balance deficits, hard violations, manual placements, participation strong-goal deviations with their `avoidability`). A genuine unplaced obligation is surfaced from `unresolved_tournament_placements` with its own stable finding id -- it has no tournament, because a placement search that found no legal slot is never materialized as a fake scheduled tournament -- while a tournament the planner marked as an unresolved manual placement is surfaced even when its provisional slot happens to be calendar-free, because the marker is a plan-level fact verification cannot rediscover; when the responsible host has a trusted calendar and host-controlled movable ice, that opportunity is additionally exposed as its own `movable_capacity` finding (`requires_host_confirmation: true`) so the harness can select the ice directly. These are never the snapshot promoted with the season, and they are independent facts rather than a mandatory processing queue.

For one selected finding, `season repair-options` enumerates the repository's verified direct/coupled alternatives and `season search` runs the bounded finding-directed neighborhood search when those are insufficient. `season apply-repair` applies exactly one option against the current canonical revision: a stale `--expected-revision` is rejected, the mutation must pass locks/approvals plus full verification, and the result carries the new revision and a repository-computed before/after delta. No Stage 1/2 rerun is required to repair an already-promoted season.

Both `repair-options` and `search` measure every returned option on the same canonical, "lower is better" objective vector -- hard violations, unresolved hosting obligations, hosting-balance imbalances, manual placements, participation deviations and avoidable deviations, host-confirmation dependencies, and changed-tournament count, merged with the shared Stage-3 quality objectives (`quality_objectives`: participation deviation/avoidability, opponent diversity, turnaround spacing, same-club clustering, hosting spread, temporal coverage) and canonical travel (`total`/`max per team`) -- by reproducing the option and re-verifying its committed candidate. The report marks each option's `objectives`/`non_dominated` and carries a bounded `pareto` set (`non_dominated_option_ids` plus a small per-objective-extreme `representative_option_ids`), so a verified option that is strictly worse everywhere than another is not presented as an independent trade-off. Each option also carries `quality_vs_current` (the same `compare_quality_scores` comparison Stage 3's promotion gate uses) and its `travel` metrics; the apply delta returns the same quality/travel before/after evidence. The dominance arithmetic itself is the shared `pareto` module used by Stage 3's multi-objective search, and the quality vector is the shared `quality_objectives` module.

A participation strong-goal deviation may instead be explicitly accepted by the operator: `season accept-deviation` persists an `operator_accepted` decision (provenance, the accepted deviation and its target) in `decisions.json` and injects it into the same independent verifier, without editing the schedule or the configured target. The acceptance is bounded to its scope/target/deviation, so a later target change or a worse deviation deterministically re-surfaces the deviation as a normal finding (`acceptance_stale`), and `season revoke-acceptance` reopens it. Accepting a deviation is a durable operator decision, not a planner outcome.

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

Hosting responsibility is recomputed after candidate-changing operations as a club/shared-registration × age-group ledger: proportional target, assigned responsibility, automatic placements, manual/unplaced responsibility and actual physical hosting. Missing trustworthy ice first triggers bounded responsibility-preserving repair (for example another legal date/slot for the same responsible host, or a roster alternative that still represents that host). If the bounded repair budget finds no verified placement, the obligation remains with the intended club as an unresolved placement finding -- kept out of `plan.tournaments` and every season export, but visible in `manual_schedule.html`/findings/audit -- rather than as a fake scheduled tournament; another club's convenient slot is reported as physical excess rather than silently absorbing the responsibility.

`season normalize-placements` upgrades an already-generated canonical plan to the same placed/provisional/unplaced state model without rerunning Stage 1-3: a tournament whose concrete placement provably overlaps a trusted/fixed external booking, or a legacy exhausted-search placeholder, is moved out of `plan.tournaments` into a stable `unresolved_tournament_placements` obligation, while approved/locked placements, movable-ice host-confirmation candidates and unavailable-calendar provisional placements are preserved. It re-verifies the full plan and reconciles the derived projections before committing. Add `--dry-run` to report the classification without writing. Stage 4 export applies the same normalization automatically before verification and serialization, so an older pre-fix plan is corrected on the next `season export` even if canonical state has not been normalized yet.

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
