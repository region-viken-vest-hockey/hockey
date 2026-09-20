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

Each epoch addresses one actionable finding direction, measures the provider's options on the shared objective vector, folds every independently verified non-dominated candidate into a bounded frontier (`Stage3Session.pareto_archive`) and commits one accepted mutation as a new candidate revision. Internal epochs persist verified planning state only: `stage3 converge` materializes exactly one timestamped Stage 4 review export at the batch/audit boundary, and only that handoff marks `audit_required`. A batch that committed revisions but could not materialize a handoff (`--no-export` or a failed export that keeps the verified candidate) reports `export_required` and leaves the prior audit valid. The bounded verified-attempt portfolio retains the candidate bodies of the retained frontier candidates, so a later worse attempt never makes an earlier non-dominated candidate unselectable; `stage3 adopt --candidate-ref <ref>` re-validates and adopts any retained frontier candidate (new revision + re-export, rejected as stale if the Stage 1/2 facts or the hard verifier changed). Every finding family carries the same `search_coverage` view, so a bounded search that actually ran and found nothing reports `bounded_search_exhausted` instead of appearing untried until the epoch budget runs out. The coverage record also names the capability/version/fingerprint of the search that produced it, so a repository change that widens the repair neighborhood makes previously recorded exhaustion stale and the finding eligible for the new search. The retained frontier is reported ranked by material audit priority (unresolved placements first) as `review_frontier`/`recommended_review_candidate_ref`, and the report records the chosen `review_selection`, so the review handoff is an explicit comparison rather than the last mutation; `stage3 converge --review-candidate <ref>` adopts a retained candidate deliberately. Direction selection is a fair controller round: a committed mutation refreshes candidate-scoped finding/coverage evidence without resetting which direction families the current round already explored, so one direction that keeps producing small non-dominated changes cannot monopolize the epoch budget. Automatic refinement stops only on an explicit terminal: `pass`, `operator_required` (an operator policy/waiver/input question), `bounded_search_exhausted` or a bounded plateau (`pareto_stable`). An epoch-budget stop is a resumable pause (`pause_reason: budget_exhausted`, `resumable: true`), not a terminal: the report leaves `terminal_reason` empty and a later invocation with a larger `--max-epochs` continues from the persisted epoch, frontier and direction-round state without a candidate mutation, forced `--finding` or manual reset. Terminal/pause wording is deliberately relative to the explored neighborhoods and configured budgets -- the report sets `globally_optimal: false` and never calls a bounded search `proven_infeasible` or a fixed attempt count proof that human input is required. The convergence state (`convergence`) lives on the Stage 3 session, not a parallel side file. `stage3 session --json` reports the frontier and the current terminal or pause state.

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
season/2026-2027/export_context.json   # when the reviewed handoff carried source/public metadata
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

Promotion also persists the reviewed handoff's immutable public/source presentation
context (`export_context.json`: scrape source/event counts, blocked sources,
cluster/calendar viewer payload, the whitelisted registered-team snapshot, the public
activity payload and its fingerprint). Verification and presentation context are
deliberately separate: canonical export still verifies only against the promoted
verification problem, but rebuilds `calendars.html`, `input.html`, the activity
artifacts and the navbar source/event status from this frozen snapshot instead of
re-reading mutable `.pipeline` scrape state. The snapshot is bound to
`promoted_from.public_export_context_fingerprint`; a mismatch is refused. Seasons
promoted before this context existed simply export without source metadata until they
are re-promoted.

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
scripts/rvv-miniputt season guest-report --season 2026-2027
scripts/rvv-miniputt season guest-candidates --season 2026-2027 --age-groups JU10,JU12
scripts/rvv-miniputt season guest-reserve --season 2026-2027 --tournament-id <id> --note "external league team may apply"
scripts/rvv-miniputt season guest-fill --season 2026-2027 --tournament-id <id> --external-club "<club>" --external-label "<team>"
scripts/rvv-miniputt season guest-release --season 2026-2027 --tournament-id <id> --replacement-label "<rvv team>"
scripts/rvv-miniputt season replan --season 2026-2027 --iterations 4000
scripts/rvv-miniputt season diff --season 2026-2027 --candidate <candidate.json>
scripts/rvv-miniputt season apply --season 2026-2027 --candidate <candidate.json>
scripts/rvv-miniputt season export --season 2026-2027
```

### Finding-directed maintenance

`season findings` recomputes the current canonical plan with the independent verifier and reports fresh, revision-bound facts (unresolved hosting obligations, hosting-balance deficits, hard violations, manual placements, participation strong-goal deviations with their `avoidability`). An aggregate-complete multi-team player pool whose labels are uneven is classified `intra_club_distribution`: it stays informational -- it is not an unresolved participation shortfall and never blocks publication -- but it *is* exposed as its own actionable quality finding and repair family (see below). A genuine unplaced obligation is surfaced from `unresolved_tournament_placements` with its own stable finding id -- it has no tournament, because a placement search that found no legal slot is never materialized as a fake scheduled tournament -- while a tournament the planner marked as an unresolved manual placement is surfaced even when its provisional slot happens to be calendar-free, because the marker is a plan-level fact verification cannot rediscover; when the responsible host has a trusted calendar and host-controlled movable ice, that opportunity is additionally exposed as its own `movable_capacity` finding (`requires_host_confirmation: true`) so the harness can select the ice directly. These are never the snapshot promoted with the season, and they are independent facts rather than a mandatory processing queue.

For one selected finding, `season repair-options` enumerates the repository's verified direct/coupled alternatives and `season search` runs the bounded finding-directed neighborhood search when those are insufficient. `season apply-repair` applies exactly one option against the current canonical revision: a stale `--expected-revision` is rejected, the mutation must pass locks/approvals plus full verification, and the result carries the new revision and a repository-computed before/after delta. No Stage 1/2 rerun is required to repair an already-promoted season.

A `temporal_clustering` finding reports a team whose own tournaments fall a few days apart. Its `repair-options`/`search` enumerate a bounded `coupled_placement` family: a cross-age placement exchange between a clustered tournament and another scheduled tournament plus -- when the exchange double-books a displaced tournament's roster -- a bounded same-age participant reselection for that tournament, with games regenerated and the whole coupled state independently verified. A hard-valid placement exchange that is instead rejected only because a retained participant of a **multi-team club** would gain a material <7/<14-day regression also triggers a bounded **same-club, same-age sibling substitution** in the affected moved tournament (`roster_repair_trigger: consequence_regression`): the individual team rule is never relaxed, the club's sibling pool is asked to absorb the nearby load, and the substituted candidate is accepted only if the complete changed-team consequence set, `#401` operational acceptability and the affected club-pool participation are all no worse. A single-team club has no sibling pool, so its candidate stays consequence-rejected; the sibling count, the substitution, and the before/after club-pool distribution are reported in the option evidence. Cross-age applies only to the placement exchange (participants never move between age groups), candidates that move hosting responsibility are rejected, and the verified option is committed through the ordinary `season apply-repair` boundary rather than a second apply path. The option's consequence evidence evaluates **every** team whose schedule changed -- both moved tournaments' participants plus any identity the bounded roster repair added or removed -- so a candidate is reported `consequence_acceptable: false`, kept off the auto-applicable Pareto front and refused at apply when it materially regresses any of them. A bounded search that finds nothing reports `bounded_search_exhausted`, never `proven_infeasible`.

A `home_representation` finding reports a club x age-group with multiple registered teams where one sibling team represents the club at nearly every one of that club's own home tournaments in the age group (`home_representation.max_material_spread`/`material_skew_pool_count` in the shared quality surface; a spread of 0-1 is balanced). Hosting coverage/balance is unchanged -- the club still hosts the same tournaments. Its `repair-options` enumerate a bounded `home_representation` family of same-club, same-age sibling swaps that never move the host/date/arena or transfer hosting responsibility: a **simple rotation** at one home tournament, a **coupled home + away swap** that pairs the home rotation with the reverse rotation at an away tournament in the same half of the season so both siblings keep their exact half-season participation counts, and a bounded greedy sequence of those units that balances the whole pool. Every option is hard-verified, must strictly reduce the pool's material home skew, and carries the complete changed-team consequence set. The family's material bar is deliberately narrower than a placement/roster repair: a swapped sibling may not gain a `<7`-day double, worsen a participation shortfall or hard maximum, or materially worsen season coverage, and the affected club pool may not deepen or materially increase its total travel. Because changing *which* sibling represents the club necessarily changes opponent identities for everyone in the tournament, extra 7-13-day gaps and opponent-repetition changes stay visible in `quality_vs_current`/the objective vector instead of hard-blocking the repair; a skew with no safe swap is reported as a finding, never as a hard failure. The accounting is season-wide: `season findings` emits one revision-bound finding for every materially skewed multi-team club × age-group pool. A repair of one named club/age group must therefore be followed by a refreshed whole-season findings pass; do not treat the production example that exposed the rule as the scope of the rule itself.

An `intra_club_participation_distribution` finding reports a club × age-group × scope player pool with enough aggregate participation but uneven sibling labels (the `intra_club_distribution` classification from `participation_targets`). It is a soft `quality` finding, never an unresolved participation shortfall: the finding carries the aggregate target/actual, the exact sibling counts/targets, the current spread and the scope. Its `repair-options` enumerate a bounded `intra_club_distribution` family of same-club, same-age sibling **substitutions** that never move date/host/arena/start time and never transfer hosting responsibility: replace an over-represented sibling with an under-represented sibling inside one existing tournament in the finding's scope, regenerate games through the canonical generator, preserve the club-pool aggregate participation count and preserve guest reservations. Away tournaments are enumerated before home tournaments, so a home substitution is returned only when it does not worsen the affected pool's `home_representation` spread. When a direct substitution is blocked by a duplicate-day or a team-schedule consequence, `season search` widens into a bounded coupled sibling-only neighborhood that pairs two substitutions; dates/hosts/arenas/slots are never moved to solve label distribution. Every option must be hard-valid, strictly reduce the selected scope's spread, keep the club-pool aggregate unchanged, not deepen a genuine club-pool shortfall, not worsen home representation, and not materially regress an affected team's spacing, temporal coverage, opponent repetition/travel. The finding is bound to the canonical revision, and the ordinary `season apply-repair` boundary is the only persistence path; a bounded search that finds nothing reports `bounded_search_exhausted`, never `proven_infeasible`.

Both `repair-options` and `search` measure every returned option on the same canonical, "lower is better" objective vector -- hard violations, unresolved hosting obligations, hosting-balance imbalances, manual placements, club-pool-unresolved participation deviations and avoidable deviations, host-confirmation dependencies, and changed-tournament count, merged with the shared Stage-3 quality objectives (`quality_objectives`: club-pool-unresolved participation deviation/avoidability, opponent diversity, turnaround spacing, same-club clustering, hosting spread, intra-club home representation, temporal coverage) and canonical travel (`total`/`max per team`) -- by reproducing the option and re-verifying its committed candidate. The report marks each option's `objectives`/`non_dominated` and carries a bounded `pareto` set (`non_dominated_option_ids` plus a small per-objective-extreme `representative_option_ids`), so a verified option that is strictly worse everywhere than another is not presented as an independent trade-off. Each option also carries `quality_vs_current` (the same `compare_quality_scores` comparison Stage 3's promotion gate uses) and its `travel` metrics; the apply delta returns the same quality/travel before/after evidence. The dominance arithmetic itself is the shared `pareto` module used by Stage 3's multi-objective search, and the quality vector is the shared `quality_objectives` module.

A participation strong-goal deviation may instead be explicitly accepted by the operator: `season accept-deviation` persists an `operator_accepted` decision (provenance, the accepted deviation and its target) in `decisions.json` and injects it into the same independent verifier, without editing the schedule or the configured target. The acceptance is bounded to its scope/target/deviation, so a later target change or a worse deviation deterministically re-surfaces the deviation as a normal finding (`acceptance_stale`), and `season revoke-acceptance` reopens it. Accepting a deviation is a durable operator decision, not a planner outcome. A multi-team club is evaluated primarily on its age-group player pool: an aggregate-complete but label-uneven pool (`intra_club_distribution`) is informational evidence, not an unresolved shortfall, and never blocks publication by itself. It is improved through its own `intra_club_participation_distribution` finding/repair family rather than through the participation-shortfall path, and only ever by redistributing existing same-club participation between sibling labels.

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

### Reserved guest places

A tournament may reserve one or more places for a team from another league/region. A reservation is first-class canonical state (`guest_slots` records with an `open`/`filled`/`released` lifecycle plus a derived `reserved_guest_slots` count): it counts toward capacity/ice-time shape, but never as an RVV season participant, so participation targets, hosting, fairness, travel and team counts are unaffected. An open reservation is therefore an intentional place, not an underfilled tournament, and participant optimization/repair cannot consume it.

- `season guest-candidates` returns deterministic per-tournament facts (free places, existing reservations, half, approval/lock state, displaceable participants) and a spread-aware ranking -- the operator/harness chooses which legal tournaments receive reservations, and the choice is recorded in the controller/decision evidence.
- `season guest-reserve` reserves spare capacity without removing a real team. On a full tournament no participant is dropped arbitrarily: the caller must name displaced team(s) from the candidate facts, and host representation must survive independent verification. A resulting participation shortfall stays explicit.
- `season guest-fill` accepts an external team into an open reservation. The team is stored as a `guest` participant, never a registered RVV season team; games and derived state are regenerated and independently verified.
- `season guest-release` withdraws a reservation, optionally filling it with a real RVV team (`--replacement-label`). A release that would leave an unverifiable underfilled tournament is refused.
- `season guest-report` shows every reservation and its open/filled/released status.

Reservations respect approval/participant locks: a locked tournament must be explicitly unapproved first. They survive later replanning/repair -- `season apply` refuses a candidate that would silently change or drop a reservation -- until deliberately filled or released. `season_plan.html` shows an open place as "N ledige gjesteplasser" and a filled place as a guest participant, and the semantic-audit evidence carries a `guest_reservations` category.

For broader quality/placement repair, use `season replan`, inspect `season diff`, then `season apply`. Search starts from canonical state, honors locks and includes weighted change cost so published-but-unapproved tournaments are not churned gratuitously.

Hosting responsibility is recomputed after candidate-changing operations as a club/shared-registration × age-group ledger: proportional target, assigned responsibility, automatic placements, manual/unplaced responsibility and actual physical hosting. Missing trustworthy ice first triggers bounded responsibility-preserving repair (for example another legal date/slot for the same responsible host, or a roster alternative that still represents that host). If the bounded repair budget finds no verified placement, the obligation remains with the intended club as an unresolved placement finding -- kept out of `plan.tournaments` and every season export, but visible in `manual_schedule.html`/findings/audit -- rather than as a fake scheduled tournament; another club's convenient slot is reported as physical excess rather than silently absorbing the responsibility.

`season normalize-placements` upgrades an already-generated canonical plan to the same placed/provisional/unplaced state model without rerunning Stage 1-3: a tournament whose concrete placement provably overlaps a trusted/fixed external booking, or a legacy exhausted-search placeholder, is moved out of `plan.tournaments` into a stable `unresolved_tournament_placements` obligation, while approved/locked placements, movable-ice host-confirmation candidates and unavailable-calendar provisional placements are preserved. It re-verifies the full plan and reconciles the derived projections before committing. Add `--dry-run` to report the classification without writing. Stage 4 export applies the same normalization automatically before verification and serialization, so an older pre-fix plan is corrected on the next `season export` even if canonical state has not been normalized yet.

`season normalize-arenas` re-emits the canonical schedulable arena across a promoted season. The single owner of arena identity is `club_registry.canonical_arena_name` / `ClubCalendarSource.arena`: a tournament already placed at its club's canonical arena is left byte-identical, while a tournament carrying a former name for that same venue (a `legacy_arena_aliases` entry such as `Ringerikshallen` -> `Schjongshallen`) is rewritten in place. It is an identity correction, not a scheduling change: dates, start times, hosts, participants, games, approvals, change guards, request constraints and guest reservations are preserved (an active `arena` placement guard follows the venue to its new label), the stored verification problem's club->arena mapping and canonical-baseline snapshot are re-emitted so a later repair/replan cannot reintroduce the legacy label, and the full canonical hard verifier runs before the atomic commit. Add `--dry-run` to see the exact before/after delta without writing.

Canonical writes are transactional: rejected verification or write failure must not leave mixed `schedule.json` / `decisions.json` state.

## Export lifecycle

Timestamped exports start as `draft`. `export_manifest.json` records export/candidate fingerprint, source run and (when applicable) canonical season/revision. Draft retention may prune old drafts; published exports and unclassified legacy exports are protected from the draft rolling window.

`season export` regenerates review output from canonical state and records the current canonical revision in the Stage 4 checkpoint/manifest and generated HTML metadata. It verifies with the durable verification context stored in canonical `schedule.json` and does not silently reload mutable Stage 1/2 checkpoints or scrape cache from `.pipeline`; deleting or replacing `.pipeline` must not change what the canonical revision means. Source/public presentation metadata (source/event counts, scraped-calendar and registered-team companion pages, activity calendar) is rebuilt from the promoted `export_context.json`, and the navbar status combines those source facts with the plan-local tournament/game/team counts on `season_plan.html`, `season_plan_report.html` and `manual_schedule.html`.

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

The harness then submits PASS / REVIEW_REQUIRED / FAIL through `operator audit-submit`. The submitted payload also carries a concise structured `operator_assessment` (overall summary, accepted trade-offs, remaining operator/club actions, limitations). Detailed evidence is fingerprint-bound to the same export/run as the bounded overview. The interactive harness must not create another nested model/audit implementation. Headless CI may use the documented `operator audit-run --backend <name>` path instead.

The audit is an independent semantic safety net, not a second Python rules engine. It looks for suspicious operational patterns, missing rules, export inconsistency and defects the deterministic verifier may share with the scheduler.

One committed export directory is self-describing: `evidence_bundle.json` holds the reconciled run/selected-plan evidence, `audit_context.json` is the exact sanitized context the auditor saw (with a deterministic `context_fingerprint`), and `semantic_audit.json` is the verdict bound to both the export fingerprint and that context fingerprint (plus the submitted `operator_assessment`). A regenerated export therefore invalidates its predecessor's audit rather than silently reusing it, and retrospective analysis can reconstruct evidence -> audit input -> audit judgment without the original `.pipeline` workspace. The terminal/chat summary is generated from that same structured assessment. The default `season_plan.html` is deliberately different: it is a current deterministic season/operational view and must not project revision-scoped Harness/LLM assessment prose. If an export still contains the old "Vurdering fra planleggingsassistent" section, that is the remaining #330 projection regression, not the intended audit contract.

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
