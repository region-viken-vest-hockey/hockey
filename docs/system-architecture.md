# System architecture

This document describes the current high-level RVV Miniputt system. Detailed workbook fields belong in `rvv-miniputt-input-formats.md`; detailed operation belongs in `rvv-miniputt-pipeline.md`.

## System shape

RVV Miniputt is a repository-operated Python system, not a continuously hosted application.

It has three related workflows:

1. **Season planning and canonical-season maintenance** — registrations/configuration + calendar evidence → verified schedule → promoted operational state → review/export bundle.
2. **Påmeldte lag** — reviewed registration export → public registered-team snapshot.
3. **Aktivitetskalender** — regional activity workbook → public activity-calendar snapshot.

All three may feed the same sanitized GitHub Pages publication snapshot.

## Sources of truth

- **SharePoint List** is the reviewed source for registration-workflow data.
- **Root `input.xlsx`** is the canonical controlled input to initial season planning.
- **`Årshjul for aktiviteter.xlsx`** is the activity-calendar source workbook.
- **External calendar sources** are authoritative for their own availability evidence, subject to source-health/provenance checks.
- **Repository code and tests** define deterministic parsing, hard constraints, verification, metrics, persistence, export and publication safety.
- **`season/<season>/schedule.json`, `season/<season>/decisions.json` and (when present) `season/<season>/export_context.json`** are the Git-backed canonical current season state after deliberate promotion. The schedule file owns schedule facts and provenance; the decisions file owns approval/lock workflow state; `export_context.json` owns the immutable public/source presentation snapshot (scrape counts, calendar viewer payload, registered-team and activity snapshots) promoted with the reviewed Stage 4 handoff. Verification stays bound to the promoted verification problem, while canonical export rebuilds source/public companion pages and the navbar source/event status from this frozen snapshot instead of mutable `.pipeline` scrape state.
- **`.agents/skills/rvv/SKILL.md`** and `.agents/commands/rvv-miniputt/` are the shared harness-neutral operating policy/procedures.
- **GitHub issues** are the implementation backlog; ADRs preserve durable rationale.

Generated HTML, CSV, Excel, iCal, caches, checkpoints and Pages bundles are derived data. After promotion, GitHub Pages remains the official published view, while the canonical season state in Git is the machine-readable operational truth.

## Runtime state and storage

```text
controlled inputs
      ↓
shared harness instructions
      ↓
scripts/rvv-miniputt / Python CLI
      ↓
.pipeline/        transient checkpoints/cache/logs
season/<season>/  canonical schedule + decisions
export/<time>/    generated review/export bundle
      ↓
public-bundle preparation + privacy gate
      ↓
gh-pages branch   published static snapshots
```

No database, queue, long-running web service or object store is required for normal operation.

## Decision ownership

### Deterministic repository code owns

- workbook/config parsing and normalization;
- team, club, source, arena and calendar facts;
- hard scheduling constraints and candidate validation;
- reproducible metrics and scorecards;
- solver/search mechanics;
- checkpoints, manifests, fingerprints and provenance;
- canonical schedule/decision persistence;
- action validation/application;
- export, privacy and publication safety gates.

### Active agent owns contextual soft judgment

- which warning/quality dimension to address first;
- which exposed recovery/search/refinement action to request;
- soft trade-offs when no hard rule decides the result;
- focused recommendations and escalation;
- semantic safety-net review using bounded repository evidence.

The agent acts through validated repository capabilities/decision contracts. It cannot override a hard violation through prose.

### Human operator owns

- credentials/MFA;
- explicit policy changes/exceptions requiring authority;
- acceptance/promotion of the operational baseline;
- public publication/rollback approval;
- questions deliberately escalated by the system.

### Interactive Stage 3 is one persisted session

Interactive Stage 3 is a resumable workflow -- build a candidate, pause for a decision, persist, resume in a new process, repair/search/adopt, finalize, continue to Stage 4 -- not a one-shot batch stage. It is represented by one typed, versioned application object, `Stage3Session`, persisted through `Stage3SessionStore`, instead of being inferred by reconciling several ad-hoc side files.

```text
Stage3Session (one authoritative object per run_id)
  status, candidate_revision, candidate_fingerprint, candidate_source
  adopted baseline candidate (what keep_baseline restores)
  run-scoped decisions + candidate-scoped pending decision
  transition history + search/attempt metadata
  finalized revision/fingerprint
```

Every candidate-changing action is an explicit transition applied by `Stage3Controller`:

```text
create_baseline / assign_shared_host / resolve_placement_conflict
apply_repair / run_search / select_candidate / keep_baseline
request_operator / finalize_stage3
```

Each transition validates the submitted action against the session's exact pending scope (run-scoped decisions stay valid across later revisions where their facts remain valid; candidate-scoped decisions are revision/fingerprint-bound), invokes a deterministic domain capability, records `revision N -> N+1` (or rejects a stale action before recording provenance), and returns the next decision context or terminal result. The CLI/harness adapter parses actions and renders contexts; it does not own fall-through lifecycle semantics.

A freshly produced candidate is bound as the session's current candidate (`Stage3SessionStore.bind_candidate`) **before** any candidate-scoped decision for it is emitted. The pending decision's revision/fingerprint therefore identify the exact candidate a capability will mutate, and the hosting-responsibility guard compares that candidate to the mutation (`B -> B'`) rather than a previous attempt's adopted baseline (`A -> B'`). Binding retains the candidate it replaces as the session's baseline, so `keep_baseline` still restores the best/adopted plan while arena/repair decisions operate on the current attempt.

No-action resume (`run --interactive --resume-from 3` with no `--decision-action`) renders the session's one `pending_decision` context and exits paused. It does not enumerate capability types; an ordinary attempt-comparison decision resumes exactly like a shared-host or arena decision, and repeated resume returns the same persisted context without invoking the planner, advancing a revision or creating a new attempt.

The session also owns resume ownership: `Stage3Session.pending_resume_stage()` (exposed as `pending_decision.resume_from` by `stage3 session`) reports the stage number a transport must answer the pending decision with -- `3` for in-Stage-3 sub-decisions (`shared_host_assignment`, `arena_conflict_resolution`) and `4` for post-plan candidate adoption/repair/search decisions. The CLI rejects an action submitted with the wrong `--resume-from` with a precise lifecycle error before building any stage context or invoking a domain capability.

A candidate-scoped `request_operator` persists an explicit awaiting-operator pause carrying the question/rationale and the exact current revision/fingerprint. It never selects or finalizes a candidate, and the operator answer continues from that same revision. A hard-valid current attempt therefore stays adoptable through `select_candidate`; when a local/manual repair context is pending and `keep_baseline` would restore the previous attempt, the context also exposes `apply_candidate` for the exact current attempt.

Within one session the loop also retains a bounded portfolio of independently verified attempts (`candidate_attempts`) with stable `candidate_ref`s, so a later, worse attempt never makes an earlier good attempt unselectable. Each record carries the candidate body, revision/fingerprint, source/search arguments, hard-verification result, shared quality/objective vector, hosting/manual-placement counts, concise A/B evidence and the Stage 1/2 facts identity it was verified against. The `DecisionContext` exposes the retained refs (`facts.retained_candidates` and the `apply_candidate` enum). Adoption is still the explicit `select_candidate` transition: `InteractiveStage3Capabilities` resolves the ref from the portfolio and re-validates the retained attempt against the current Stage 1/2 facts identity and the current hard verifier (locks/decisions included), rejecting it as stale instead of trusting its original verification. The bounded retention window lives in the session, not in a side file.

The deterministic domain operations behind those transitions live in one adapter, `cli/pipeline_orchestrator/stage3_capabilities.InteractiveStage3Capabilities` (shared-host recording, arena-conflict application and next-collision enumeration, local repair application, candidate selection, baseline retention). `run_command_interactive.py` only picks the persisted session, submits the typed action to `Stage3Controller` with that adapter, and renders the returned context/exit code; it no longer contains the repair/arena/shared-host transition bodies or the fall-through branches that used to decide whether an answer replans, repairs or advances. Executing a search is still a Stage 3 entry point (it builds a new candidate and then binds the resulting revision), so the CLI maps the `run_search` transition to that entry point rather than duplicating the optimizer.

`rvv-miniputt stage3 session` exposes one compact, machine-readable view (run/session id, status, revision/fingerprint, pending decision with its resume owner, resolved decisions, finalized revision, legal next transitions) so a resume problem does not require reconciling several JSON files.

A finalized but unpromoted candidate can be refined in place without promotion, a Stage 3 reset or a Stage 1/2 rerun. `rvv-miniputt stage3 refine` reopens the exact reviewed candidate through the explicit `refine_candidate` transition (the reviewed candidate becomes the retained baseline), enumerates finding-directed repair options with the same repository-owned providers and verifier every other boundary uses, applies one verified option as a new candidate revision, re-finalizes and re-runs Stage 4. Stage 1/2 checkpoints and fingerprints are read-only inputs -- refinement never rescrapes or rebuilds the planning baseline. The replacement export's lifecycle manifest records the reviewed export it `supersedes`; the superseded export itself is marked `superseded`, kept as immutable history, protected from draft retention and refused when it is the published public projection (that transition belongs to the publication/rollback boundary). Semantic audit is not re-run inside the command: the repository owns audit-context assembly and the verdict belongs to the active harness, so the result reports `audit_required` and the fresh export fingerprint for that re-audit.

A `REVIEW_REQUIRED` result is refinement feedback rather than an automatic human escalation. `rvv-miniputt stage3 converge` composes the same single-step refinement boundary into a bounded outer loop: one controller epoch addresses an actionable finding direction, measures the provider's options on the shared objective vector, folds each independently verified non-dominated candidate into a bounded frontier and commits one accepted mutation as an internal verified revision. Internal epochs do not materialize review bundles: exactly one timestamped Stage 4 export is produced at the batch/audit boundary, and only that handoff marks `audit_required` (an unmaterialized owed handoff reports `export_required` and keeps the prior audit valid). The repository-owned pieces of that loop are split deliberately: `application.pareto_convergence` owns the dominance-pruned archive, the exploration order, the plateau counter and the terminal vocabulary; `application.convergence_refinement` only composes those mechanics with the existing providers/verifier/refinement boundary; `stage3_converge_command` is transport. No hockey legality, mutation, metric or audit judge is re-implemented in the controller. The bounded frontier and the epoch/explored-direction/remaining-finding/terminal state live on `Stage3Session` (`pareto_archive`/`convergence`, schema 6), and the owed-review-handoff flag (`export_pending`, schema 8) lives there too, so they resume with the revision/fingerprint they describe rather than in a parallel side file, and retained frontier candidate bodies stay in the existing verified-attempt portfolio. `search_incomplete` keeps exploration open; convergence stops only on `pass`, `operator_required`, `bounded_search_exhausted` or a bounded plateau/budget, and the terminal report is explicitly relative to the explored neighborhoods and configured budgets (`globally_optimal: false`). Every finding family carries the same per-finding `search_coverage`, so a bounded search that actually ran and found nothing is recorded as `bounded_search_exhausted` for that candidate (cleared on a new candidate revision) rather than staying `search_incomplete` until the epoch budget expires. Each coverage record also carries the search capability/version/fingerprint that produced it: a `bounded_search_exhausted` recorded under a superseded capability (a widened cap, a new dimension) is stale evidence, so the finding becomes retryable under the current search instead of being suppressed. The convergence report ranks the retained frontier by material audit priority (unresolved placements first) as `review_frontier`, exposes `recommended_review_candidate_ref` and records the chosen `review_selection`, so the review handoff is an explicit frontier comparison rather than the last mutation; `stage3 converge --review-candidate <ref>` adopts a specific retained candidate deliberately. `application.audit_convergence` bridges the harness-owned semantic audit into the loop: it classifies material checklist findings against the repository's current directions, marks mapped+actionable ones covered, and turns open-ended/no-capability findings into explicit operator questions instead of a blind search. The `operator audit-submit`/`audit-run` transports enter this loop automatically on `REVIEW_REQUIRED` (`--no-refine` opts out; headless rounds are bounded). Any retained non-dominated frontier candidate stays selectable through `stage3 adopt --candidate-ref <ref>` (`application.convergence_refinement.select_frontier_candidate`), which re-validates facts identity + hard verification and re-exports as a new revision; the archive entry keeps named consequence metrics (manual/unresolved counts, participation/hosting deltas, change cost, quality comparison) in addition to the objective vector.

A plain full-pipeline run must not silently invalidate that reviewed handoff. `reviewed_unpromoted_candidate` is the repository-owned lifecycle predicate (finalized session + current, non-superseded Stage 4 export + no matching `promoted_from` provenance in canonical season state); the CLI transport refuses a Stage 1 restarting run before the session is cleared and names both `stage3 refine` and the explicit `run --new-full-run` opt-in. Promotion clears the predicate.

During the migration away from the older side files, the store migrates them on first load and keeps writing them only as non-authoritative compatibility mirrors. The session -- not the mirrors -- is the read/write authority: the CLI's interactive-state helpers are a thin facade over `Stage3SessionStore`, run-scoped shared-host/fixes and candidate-scoped arena/fixes are held in separate session buckets, and the store removes every mirror on finalization. New Stage 3 capabilities must not add a feature-specific `*_state.json` authority; architecture tests block a new authoritative side-state file and block any module outside the store/facade from naming the legacy files.

## Scheduling-rule implementation map

Scheduling rules must have one authoritative implementation and remain valid across initial generation, optimization, repair, promoted-season maintenance and export. Do not fix a persistent invariant only in whichever planner path first exposed the bug.

| Concern | Owning layer | Examples |
|---|---|---|
| Input/configured policy | controlled workbook/config parsing | participation targets, season window, source configuration |
| Domain facts and reusable rule math | small planner-independent deterministic modules | hosting targets, coverage and responsibility facts (`hosting_coverage`, `hosting_responsibility`), placement state classification/normalization across every path and stable unresolved-placement finding identity (`placement_normalization`, `placement_findings`: placed/provisional vs unplaced), participation target/hard-max resolution, deviation metrics and avoidability classification (`participation_targets`), canonical holiday/date admissibility (`date_policy`), participant eligibility, availability facts |
| Hard/required verification | canonical verifier | host representation, explicit participation hard maxima, collisions, required obligations, unexplained responsibility transfer, canonical excluded/holiday dates |
| Operational acceptability of an automatic repair (distinct from hard validity) | planner-independent `operational_acceptability` predicate + canonical mutation boundary | reject/classify a candidate that *newly* introduces a `fixed_busy`/manual external-calendar placement, a host with an untrusted calendar, unresolved/manual placement work, or a host-confirmation (`movable_busy`) dependency relative to the canonical baseline; explicit `--allow-manual-placement` / `--allow-host-confirmation` opt-in; re-checked at `season move` / `season apply-repair` / `season apply`/`replan` and used to filter the auto-applicable Pareto set |
| Durable semantic request constraints | planner-independent `request_constraints` model/verifier + canonical-season application boundary | typed identity/validation and derived satisfaction for `team_unavailable`/`minimum_gap`/`opponent_avoidance`, enforced at every schedule-changing canonical commit (`move`/`swap`/`apply`/guest lifecycle) until an operator explicitly releases them |
| Global operator banned dates | canonical `canonical_banned_dates` record + projection into the existing `manual_adjustments.banned_dates` read path | one durable record per date with request/actor/note provenance and add/list/remove lifecycle; recording is a policy-only write allowed while the date is still in use; the existing `banned_date_used` hard rule and every date-changing consumer (move/batch/repair/search/candidate-weekend/optimizer/replan) read the projected set, so the same source of truth is never duplicated |
| Soft deterministic measurements | scorecard/fairness measurement | hosting deviation, bounded participation target deviation/avoidability, temporal spacing, intra-club home representation (`home_representation.py`); the shared `quality_objectives` module owns which soft quality metrics are compared and in which direction |
| Feasible repair/action enumeration | canonical application/decision capability | common `local_repair_options` boundary over small planner-neutral providers: `rehost_tournament`/`remove_tournament`/participant repair for `host_team_missing`, `fill_participant`/`swap_participant` for an underfilled roster, `move_same_host_date`/`move_same_host_start_time`/`swap_compatible_tournament_placement`/`interpret_calendar_event_as_movable` for a manual placement, same-age rehost for a hosting-balance/coverage deficit (`hosting_balance_repair`), `replace_participants` roster substitution that keeps an already-valid placement for a double-booked/duplicated participant (`placement_preserving_roster_repair`), bounded participant/host search for a participation strong-goal deviation (`participation_deviation_repair`), `move_to_movable_capacity`/`move_to_movable_capacity_reselect_participants` for verified host-controlled ice, ranked conflict-aware manual candidate weekends (`candidate_weekends`) as read-only evidence, materialization of a genuine unplaced obligation into a verified tournament (`unplaced_placement_repair`) with its coupled capacity-release/cross-age dimensions and per-finding search coverage, bounded cross-age coupled placement + same-age roster repair for a `temporal_clustering` finding (`coupled_placement_repair`, including a same-club/same-age sibling substitution when a hard-valid exchange would otherwise materially regress a multi-team club's individual team), bounded same-club sibling rotation / coupled home + away swaps that rebalance a multi-team club's intra-club home representation without moving host/date/arena (`home_representation_repair`), bounded neighborhood search (`search_neighborhood_repair`) for a locally searchable hard finding no direct option repairs, batch relocation of tournaments off canonical excluded dates (`date_policy_relocation`), manual-placement fallback |
| Multi-objective comparison | canonical `pareto` module and shared `quality_objectives` | the one dominance relation and bounded per-objective-extreme down-select shared by Stage 3's multi-objective search and promoted-season maintenance, plus the one planner-independent quality objective vector/comparison derived from `score_candidate`; callers own only how they combine their own defect dimensions with the shared quality vector |
| State mutation and persistence | canonical application/season operation | atomically apply validated changes while respecting locks/approvals; compose several explicitly scoped mutations against one in-memory candidate and commit once (`CanonicalSeasonService.batch_maintenance`, CLI `season batch`) so simultaneous pre-existing request-constraint violations can be repaired together without weakening the single-mutation full-season constraint gate |
| Baseline generation/search | planner/optimizer implementations | propose candidates using the shared domain facts; never redefine the rule |
| Rules/report/export | rules model and renderers | display serialized/recomputed facts; never become the business-policy engine |
| Agent judgment | shared RVV decision protocol | choose among repository-exposed valid actions for soft trade-offs |
| Harness adapters | transport/UI only | parse/display/invoke; no independent scheduling policy |

The dependency direction for a scheduling rule is therefore:

```text
controlled policy / current evidence
        ↓
planner-independent domain facts + rule math
        ↓
verification + deterministic measurements
        ↓
validated feasible actions
        ↓
canonical mutation/persistence
        ↓
rules/report/export rendering
```

Generators and optimizers may consume these rules to construct candidates, but they do not own them. A rule that must remain true after later mutations must be independently measurable/verifiable outside the generator that produced the original candidate.

### Participation targets are strong goals, not hard caps

Participation targets (`deltakelser_per_lag_før_jul` / `_etter_jul`) are owned by the planner-independent `participation_targets` module. The module resolves the configured per-half/season target and any optional explicit `participation_hard_max`, computes bounded deviation metrics and classifies every remaining deviation as `avoidable`, `proven_infeasible`, `bounded_search_exhausted` or `operator_accepted`. It also owns the **club x age-group x scope player-pool view**: for every club's registered teams it sums the aggregate target and actual participations, keeps the exact per-team distribution and classifies the pool (`complete`, `intra_club_distribution`, `minor_club_pool_shortfall`, `material_club_pool_shortfall`, `single_team_deviation`, `over_target`). That classification is the single source of truth for whether a residual is a genuine club/player-pool shortage or only an imbalance between nominal team labels, so no caller has to infer "players can simply be shuffled" from team names. `verify_candidate` reclassifies ordinary target deviation as non-blocking strong-goal evidence; only an explicit hard maximum is a hard (and operator-waivable) rule. Candidate comparison (`stage3_ab`), the Pareto archive, the Stage 3 `DecisionContext`, `season_maintenance` findings/objectives and `publication_readiness` consume the club-pool-aware evidence, so a worse-but-avoidable participation regression cannot be promoted over a lower-priority quality gain and an aggregate-complete but label-uneven pool is not treated as an equal-weight unresolved shortfall (nor does it trigger repair search or block publication by itself). `bounded_search_exhausted` is never presented as proof of unavoidability. `operator_accepted` is an explicit, durable operator decision rather than a verifier finding: canonical season maintenance persists it in `decisions.json`, injects it as the verifier's per-team `search_evidence`, and the acceptance is honoured only while it still covers the current deviation. Its `scope`/`direction`/`target`/`accepted_deviation` bound is owned by `participation_targets.evidence_covers_deviation`, so a target change or a worse deviation deterministically re-surfaces the deviation as a finding instead of masking an avoidable regression. The schedule and the configured target are never edited by an acceptance.

Likewise, renderers and generated HTML must consume authoritative serialized/recomputed state; they must not infer or repair scheduling policy themselves. Presentation order is one such renderer concern with a single canonical definition: `models.tournament_chronological_key`/`chronological_tournaments` order by full ISO date (so a season crossing New Year stays chronological) and then by deterministic same-day tie-breakers (start time, age group, arena, host club, stable tournament id). The HTML exporter serializes that order and the browser schedule renderer re-sorts defensively, so no view depends on the incidental order of `SeasonPlan.tournaments`, which candidate refinement, repair or adoption may permute without changing any scheduling fact.

### Holiday/date admissibility is one canonical hard rule

Date admissibility is owned by the planner-independent `date_policy` module: it derives the excluded dates (the Monday-Sunday week containing a Norwegian public holiday and the weekend immediately before a holiday) for the planning window, and it is the single implementation the initial scheduler's `HolidayConflictChecker`, the normalized `planning_problem` (`date_exclusions`), every repair/optimizer date move and `verify_candidate` read. The verifier re-derives the exclusions from the planning window itself instead of trusting the planner that produced the candidate, so a candidate a later optimizer/repair moved onto an excluded date fails final verification (`holiday_date_used`) no matter which path created it, and the active excluded dates are surfaced as `holiday_dates_not_used` rules evidence.

Because consecutive hard violations of the same rule block every single-tournament repair (each commits only a fully verified candidate), `date_policy_relocation` owns the batch capability: it relocates every excluded-date tournament to an admissible date for the same responsible host, or -- when the bounded search finds none -- demotes the obligation to an explicit `unresolved_tournament_placements` item with its per-date rejection evidence rather than leaving it scheduled on a forbidden date. It is exposed through the same `local_repair_options` findings/repair/apply boundary as every other provider.

### Simultaneous request-constraint violations need one atomic boundary

A typed request constraint is a hard maintenance requirement checked on every schedule-changing canonical commit (see the durable-request-constraints row in the implementation map). That is correct for an isolated request, but one operator request can legitimately record several independent constraints at once and leave several tournaments invalid. Because each ordinary `move`/`swap`/`apply` still has to satisfy the *whole* active set, no single-mutation sequence can make progress: the first repair is refused while the other pre-existing violations remain. Releasing the other valid constraints to unlock an intermediate mutation, hand-editing canonical JSON, and fencing a broad `season replan` with approvals are all prohibited.

`CanonicalSeasonService.batch_maintenance` (CLI `season batch`) is the generic owner of this case. It takes an explicit declared scope of affected tournament ids plus a list of `move`/`swap_participants`/`cancel` operations, applies them to one in-memory copy of the current canonical plan using the same in-memory mutation helpers (`_apply_move_to_plan`, `_apply_swap_to_plan`) the ordinary commands use, freezes every out-of-scope tournament, and then runs the complete authoritative gate set exactly once on the final candidate: all active request constraints, the independent hard verifier, operational acceptability, approval/placement/participant locks, existing change protections, guest-reservation integrity, hosting-responsibility transfer, and per-team swap consequences. It refuses the entire batch (naming the out-of-scope operation or changed id) if any gate fails or if a tournament outside the declared scope changed, and otherwise commits exactly once with a `batch_maintenance` history record keyed by the operator's stable request-id. The ordinary single-mutation commands keep their existing full-season constraint gate; the batch boundary does not replace it -- it composes several mutations before applying that gate to the final state.

### Global operator banned dates are one canonical policy record

A date that is unusable for **every** tournament (a closed hall, a whole holiday weekend) is not a per-team request constraint. It is a canonical **banned date**, owned by `canonical_banned_dates`: one durable record per date with request/actor/note provenance and an add/list/remove lifecycle. The record is stored only in `decisions.json`; the existing `planning_problem.manual_adjustments.banned_dates` is a derived projection (`project_banned_dates_into_problem`) applied at every canonical problem boundary (`CanonicalSeasonService._resolve_plan_problem`, `season_maintenance.load_context`, `canonical_replan.replan_around_baseline`), so there is no duplicate policy in `plan.manual_adjustments` and no second verifier. The existing `banned_date_used` hard rule and `date_policy.problem_forbidden_dates` (used by the optimizer, candidate-weekend enumeration and unplaced-obligation materialization) are the single read paths. Recording a ban is a policy-only write allowed while the schedule is still in use, mirroring `season add-constraint`: it reports the affected tournament ids, advances the canonical revision and lets the caller repair the scope with one `season batch`.

### Local repair-option families

A localized hard defect is repaired through the same validated action boundary as every other decision, not through another procedural pass inside the planner. `local_repair_options` is the single application entry point: each planner-neutral provider owns one finding family, enumerates hard-feasible options plus explicit per-candidate rejection reasons, and applies a selected option id atomically before the independent verifier runs. A bounded-search option's stable id names the deterministic request that reconstructs it (finding scope, seed and enabled move dimensions); it never embeds the produced candidate's fingerprint, because that candidate carries non-reproducible wall-clock instrumentation and would give the same option a new id on every enumeration. An apply recovers the option's own dimensions from that id, so a caller does not have to replay the search flags.

```text
finding (host_team_missing, bye_team_not_allowed, ...)
        ↓
providers enumerate legal options + rejected alternatives
        ↓
DecisionContext exposes stable option ids to the controller
        ↓
controller selects one option id
        ↓
atomic mutation -> games regenerated -> full independent verification
```

A provider never weakens a hard rule, never transfers an obligation implicitly and never hides a legal option behind an ad-hoc ranking. When no local option verifies, the controller may request a bounded broader search or escalate with the recorded rejection evidence.

When none of the direct options is legal, the `search_neighborhood_repair` provider runs the generic Stage 3 local search over an explicit neighborhood -- every tournament in the age group(s) touched by a *locally searchable* hard finding (`host_team_missing`, `excluded_host_club_used`, `club_hard_max_exceeded`, `duplicate_participation_same_date`, `duplicate_team_in_tournament`, `arena_interval_conflict`) -- and freezes every tournament outside it. Inside the neighborhood it may only re-pair participants and reassign the host to a club already represented by that tournament's own teams, and every seed's result must pass the full independent verifier, strictly reduce hard violations, **and not increase any club's hosting excess over its canonical target** before it is exposed as an option id. A plan whose remaining hard findings are not locally searchable (for example `unregistered_team`/`date_outside_window`), or a soft manual placement, keeps the ordinary controller context rather than being replaced by a solver pass. An optional finding scope restricts the neighborhood to one selected finding's age group/tournament, and the enabled move dimensions (participants/host/date/slot) are explicit, so an unrelated finding never forces a season-wide search.

The strong-goal families are owned the same way. `hosting_balance_repair` turns a club x age-group hosting deficit (coverage or proportional balance) into a same-age rehost option where the deficit club already participates, reassigning the physical host and arena to the deficit club and keeping every other tournament frozen. A rehost is exposed only when it is hard-valid, keeps hosting responsibility with a club that owes it, and **reduces the selected deficit without increasing any other club's deficit** -- relocating the shortfall is rejected with explicit evidence. When no direct rehost verifies, the provider runs a bounded `optimize_candidate` neighborhood over the finding's age group with the requested dimensions and exposes only verified deficit-reducing results. `participation_deviation_repair` does the same for one selected participation strong-goal deviation, preserving its `avoidability` classification: a `bounded_search_exhausted` deviation is reported as a bounded-search outcome, never as `proven_infeasible`, and an `avoidable` deviation is never made worse. Deviations classified `intra_club_distribution` (an aggregate-complete multi-team pool whose labels are uneven) are not surfaced as repair findings at all -- the club already received its player-pool capacity, so no search is spent rebalancing labels.

`season_maintenance` is the harness-neutral application surface that makes this same boundary available over a promoted canonical season. `season findings` recomputes findings from the canonical plan with the independent verifier -- never the snapshot promoted with the season -- and binds each to the canonical revision. A genuine unplaced obligation is surfaced from `unresolved_tournament_placements` with its own stable finding id (it has no tournament id), while a tournament the planner marked as an unresolved manual placement is surfaced even when its provisional slot is calendar-free (the marker is a plan-level fact). A manual placement whose responsible host has trusted host-controlled movable ice is additionally exposed as a dedicated `movable_capacity` finding carrying `requires_host_confirmation` -- so the harness selects the ice opportunity directly instead of reconstructing it from provider options. `repair-options`/`search` enumerate the owning provider's verified alternatives for one selected finding; and `apply-repair` re-derives the selected option against the current revision and persists it through the same atomically-verified `apply_candidate` boundary (locks/approvals, full verification and the responsibility guard all apply). A stale revision is rejected without touching canonical state, and a successful apply returns the new revision plus a deterministic, repository-computed before/after delta. Findings are independent facts, so the harness chooses which one to address next rather than processing a repository-defined queue. Both `repair-options` and `search` measure every returned option on one canonical "lower is better" objective vector -- hard violations, unresolved hosting obligations, hosting-balance imbalances, manual placements, club-pool-unresolved participation deviations and avoidable deviations, host-confirmation dependencies, and changed-tournament count -- merged with the shared planner-independent Stage-3 quality objectives (`quality_objectives`: club-pool-unresolved participation deviation/avoidability, opponent diversity, turnaround spacing, same-club clustering, hosting spread, temporal coverage) and canonical travel (`total`/`max per team`) -- by reproducing the option and re-verifying its committed candidate, and report a bounded non-dominated `pareto` set (`non_dominated_option_ids` plus a small per-objective-extreme `representative_option_ids`). Each option also carries `quality_vs_current` (the same `compare_quality_scores` comparison Stage 3's promotion gate uses, measured against the current canonical plan) and its `travel` metrics, and the apply delta returns the same quality/travel before/after evidence so no consumer reconstructs quality arithmetic. A verified option strictly worse everywhere than another is therefore never presented as an independent trade-off. The dominance relation and down-select are the shared `pareto` module Stage 3's multi-objective search also uses, and the quality vector is the shared `quality_objectives` module; only the maintenance defect/cost dimensions are maintenance-specific. For a participation finding the harness may instead persist an explicit `operator_accepted` decision (`season accept-deviation`/`revoke-acceptance`), which is a durable operator record in `decisions.json` -- not a schedule mutation -- and is injected into the same verifier so an accepted deviation stays visible but stops being treated as unresolved. Accepting never changes the target or the schedule, and the acceptance stops applying once the target changes or the deviation gets worse.

A manual placement can also be blocked by an *ambiguous* scraped event nothing configured has classified. The `host_placement_repair` provider then offers an `interpret_calendar_event_as_movable` option alongside the same-host moves: the controller may request an inferred `movable_busy` interpretation, which is recorded as a `calendar_interpretations` overlay on the candidate and re-applied by the independent verifier. Only an `unclassified` interval is eligible, the source calendar is never edited, and the resulting placement is host-confirmation-gated exactly like a configured movable interval.

When configured host-controlled capacity already exists, `movable_capacity_repair` turns that fact into bounded responsibility-preserving alternatives. For an unresolved same-host placement it may move the tournament into a configured `movable_busy` window and, when the current roster conflicts on that date, rotate a small number of same-age participants while keeping the responsible host represented. Each alternative is exposed through `local_repair_options`, retains `requires_host_confirmation`, and must pass the full independent verifier before the controller can select it. The provider does not transfer hosting responsibility or add harness-local scheduling policy.

When the placement itself is already legal and the blocker is only *which* teams are selected, the repair-cost order starts with `placement_preserving_roster_repair`: a `duplicate_participation_same_date`/`duplicate_team_in_tournament` finding is first offered as a verified same-age participant substitution that keeps the host, date, arena and start time byte-identical (games are regenerated and the whole candidate re-verified). Replacements are ranked by canonical participation need so the repair tends to improve balance, and the runtime-host-representation rule is enforced on the resulting roster. A valid placement is therefore never discarded -- by a date move or a broader neighborhood search -- merely because the initially selected roster conflicts, as long as a hard-valid lower-cost substitution exists. Only when no substitution verifies does control escalate to the date/host or bounded-search providers.

When an obligation is still unresolved, the same provider also attaches a ranked, **conflict-aware manual candidate-weekend shortlist** (owned by the planner-independent `candidate_weekends` module). For each bounded same-host weekend it reuses the normalized availability facts (`free` / `movable_busy` / `unknown`) and checks the proposed roster against the candidate's existing team/date occupancy, so a weekend whose current roster already plays that date is never suggested as usable -- it is recorded as a `team_already_plays` rejection. Suggestions carry the concrete interval, availability classification, host-confirmation requirement and the calendar event, and the bundle states whether the bounded date set or the search budget ended. This is evidence only: the module never mutates the candidate and a real placement still goes through a validated, independently verified repair option. The same read-only evidence is projected into the operator manual view through `host_placement_repair.collect_candidate_weekend_evidence`: `manual_schedule.html` groups every finding for one underlying tournament/obligation into a single work item, renders the ranked suggestions plus deterministic rejection reasons, and stops treating an unresolved placement as a confirmed arena/time booking (in both the manual view and `season_plan.html`).

An unresolved obligation is *planning work*, not a scheduled tournament, so the placement providers above cannot repair it in place. `unplaced_placement_repair` owns the materialization step: for one obligation it walks the repository-owned ladder (same-date start time, same-host date, bounded host-representing participant reselection, capacity release, coupled cross-age exchange, then the full bounded neighborhood), builds a real `Tournament` (verified date, the responsible host's arena, start time, final roster, freshly generated games), removes the obligation and runs the full independent verifier. A coupled capacity-release option moves a scheduled tournament that consumes the responsible host's shared arena/time -- including another age group, because arena/time rather than age group is the constrained resource -- while keeping that blocker's own host/arena, so hosting responsibility never transfers. Each obligation reports which supported dimensions were attempted and which remain untried: `option_available`, `search_incomplete` (untried supported dimensions remain) and `bounded_search_exhausted` are distinct, and `proven_infeasible` is never claimed without a deterministic exhaustive capability. A dimension whose hard precondition is unmet for the obligation (participant reselection when the current roster collides on no tried date) is reported separately as `inapplicable` and excluded from `untried`, so coverage can reach `bounded_search_exhausted` instead of remaining `search_incomplete` forever and inviting an unbounded retry of a search that can never run; reselection applicability is evaluated against every date it would be attempted on (source and alternate), not only the source date. A persisted planner marker (`bounded_repair_exhausted`) is bound to the search capability that produced it (`search_capability.py`), so widening the ladder makes the marker stale/retryable (`search_coverage.capability_stale`) instead of inheriting a superseded result; the capped alternate-date/roster/capacity search samples the whole allowed start-time day rather than only its first morning entries. `season findings`, `repair-options`, `search` and `apply-repair` all consume the same provider, so the harness chooses among returned hard-valid options instead of reconstructing placement logic.

### Responsibility is separate from automatic placement

For hosting and similar obligations, distinguish the domain responsibility from whether a verified automatic slot currently exists. Calendar convenience must not silently transfer responsibility to another club.

Canonical behavior is:

```text
assign fair/intended hosting responsibility
        ↓
try verified automatic placement on the selected date
        ↓
try bounded responsibility-preserving repair
(same responsible host on another legal date/slot; roster alternatives that keep it represented)
        ↓
slot exists              no trustworthy/legal slot after repair budget
    ↓                              ↓
automatic placement      keep responsibility with intended host
                         as an unresolved placement finding
                         (no tournament materialized)
```

An unresolved placement is planning work, not schedule state. The planner must not materialize a fake `Tournament` on the originally desired date/arena/start time: that object would be indistinguishable from a real placement to every season-plan consumer (participation, opponent/game counts, hosting counts, chronological schedule and every CSV/XLSX/Spond/ICS/club export). It is instead recorded in `SeasonPlan.unresolved_tournament_placements` with a stable finding id (owned by `placement_findings`, independent of any tournament id) and the attempted-search evidence (responsible host, hosts searched, same-host dates checked, bounded-repair exhaustion). It stays visible in `manual_schedule.html`, the finding surfaces and the audit evidence, and keeps the responsible club's hosting obligation unresolved -- an unplaced obligation never counts as successful hosting or as a played game.

The three placement states are therefore distinct:

```text
placed                    concrete date/arena/start time, verified against the planning contract
                          -> belongs in plan.tournaments and every season export
provisional/confirmation  concrete but unconfirmed placement (host-controlled movable ice, or an
                          unavailable/untrusted calendar the operator intentionally keeps)
                          -> a real Tournament carrying requires_host_confirmation/manual_booking_reason,
                             rendered visibly as provisional, never as verified free ice
unplaced                  the deterministic search ran and found no legal/free placement
                          -> an unresolved_tournament_placements finding only; no Tournament object
```

A manual-placement tournament still counts toward the intended club's assigned hosting burden; an unplaced obligation likewise keeps that responsibility visible. Another club must not absorb that tournament merely because it has easier or more abundant ice, unless an explicit validated operator/decision action intentionally changes the hosting responsibility.

The state model has one semantic owner, `placement_normalization`. `classify_tournament` decides placed/provisional/unplaced from the tournament, the normalized calendar evidence and the operator approval state (an explicit approval or placement lock is confirmation, so an approved placement is never demoted); `normalize_unplaced_placements` removes the unplaced tournaments and hands them to `unresolved_tournament_placements`, reusing any richer obligation the plan already carries for that slot. Every path uses it, not just the initial slot search: the arena-conflict decision demotes through `demote_tournament_to_unplaced` instead of leaving a `start_time=None` placeholder in `plan.tournaments`, and the Stage 4 export chokepoint normalizes the plan immediately before verification and serialization, so an already-generated pre-fix plan is corrected without a Stage 1-3 regeneration. A canonical season can also be upgraded in place with `season normalize-placements`, which re-verifies and reconciles the derived projections before committing a new revision.

A tournament whose concrete interval overlaps a trusted/fixed external booking with no accepted alternative is *unplaced*, not provisional: it must not be exported as scheduled hockey while a verifier finding says that same object is unusable. The movable/confirmation-required and unavailable/untrusted-calendar cases stay provisional and visibly marked. Normalization does not paper over a malformed candidate: a bare missing `start_time` with no manual/unplaced marker is left for the hard verifier to reject rather than silently converted into work that hides the defect.

The rule has one planner-independent semantic owner, `hosting_responsibility`: it derives the canonical target / actual / assigned / manual-placement facts from the registration model and `hosting_coverage`, and compares two candidates to detect an *unexplained responsibility transfer* -- a club whose physical hosting excess over its canonical target grew without the canonical target itself changing. Every candidate-changing boundary re-checks it and rejects or surfaces the change instead of committing it:

- repair/search providers reject a candidate that introduces a transfer (the bounded search never offers such a seed);
- the interactive Stage 3 capability guard refuses to commit a candidate revision that introduces a transfer, so the lifecycle controller only accepts capabilities whose result passes the canonical check;
- canonical `season replan --apply` refuses a candidate that transfers a club's obligation relative to the promoted schedule, and explicit operator host/date/arena moves remain the deliberate, audited mutation path;
- review/export evidence reports the responsibility ledger (target / assigned / automatic / manual) so a human sees any absorbed burden.

Registration or tournament-volume changes are a canonical fairness recompute, not an unexplained placement transfer: the target math changed, so the guard skips that age group rather than mislabeling the legal redistribution.

### Calendar availability is a classification, not a boolean

Calendar evidence is normalized into explicit availability classes before planning, so occupied time is not automatically unavailable time:

```text
fixed_busy        cannot be displaced automatically (a genuine external booking)
movable_busy      host-controlled ice the club may move/replace (e.g. open ice)
free              verified free interval
unknown/untrusted cannot be assumed free
```

The planner-independent owner is `calendar_availability`: it defines the classes, applies per-club/source event-title rules and resolves legacy interval tags. Event titles are classified only through explicit `ClubCalendarSource.event_classification_rules` (for example Kongsberg's `Åpen ishall` is `movable_busy` for Kongsberg only) -- there is deliberately no global phrase table, because the same words can mean different things at another hall.

`pipeline.stage3_helpers._build_club_busy_intervals` carries the classification into the planner-neutral `planning_problem` contract, and `planning_contract` reads it: `external_calendar_conflict` only rejects `fixed_busy`; `movable_calendar_opportunity` exposes a `movable_busy` interval as a non-blocking candidate for its own host. The slot search and responsibility-preserving repair providers may use that candidate for the responsible host, but the resulting placement is never reported as unconditionally free: `verify_candidate` records it in `movable_allocations_used` with `requires_host_confirmation` and the event that must move, `publication_readiness` returns `REVIEW_REQUIRED`, and the audit evidence/context surface the normalized fact (host, date, interval, `availability: movable_busy`, calendar event, host action).

Event classification provenance is derived from the event title (`configured` when an explicit rule matched, `club_default` when the club declared its whole calendar club-controlled, otherwise `unclassified`). An unclassified title is an *ambiguous fact*, not a verdict: hard verification still treats it as occupied (it is never assumed free), but `planning_problem.unclassified_calendar_events` exposes it to the controller separately from a configured booking (the serialized busy-interval contract itself is unchanged). The controller may then explore a single inferred `movable_busy` interpretation through the ordinary validated repair flow -- `host_placement_repair` offers an `interpret_calendar_event_as_movable` option, and applying it records the interpretation as a `calendar_interpretations` overlay on the *candidate*. The Stage 2 source calendar is never mutated, only a title no rule classifies can be reinterpreted (a configured `fixed_busy` booking never can), and `verify_candidate` re-applies the overlay so the placement is still a host-confirmation-gated `movable_busy` allocation. The evidence bundle records the inferred interpretation (`calendar_interpretations_used`, raw event title plus concise reason) so the audit can tell it apart from a configured or confirmed-free fact. Deterministic known-fixed allocations (for example Sandefjord's weekend block) are configured `fixed_busy` so they can never be reinterpreted.

### Change checklist for agents

When implementing or modifying a scheduling rule:

1. identify its authoritative deterministic owner before editing code;
2. implement reusable facts/math below planner/CLI/rendering layers;
3. make every candidate/mutation path consume or re-check the same rule;
4. expose legal repairs through validated repository actions instead of ad-hoc planner or harness logic;
5. preserve canonical IDs, provenance, approvals and locks when applying changes;
6. derive rules/report/export output from the final authoritative state rather than cached/stale intermediate evidence;
7. add regression coverage at the domain/verifier boundary and, when relevant, the mutation path that previously allowed the invariant to drift.

If a proposed fix only changes a baseline generator, one optimizer, one renderer, or one harness prompt for a rule that must survive later changes, the fix is incomplete.

## Harness boundary

Claude, ChatGPT, Codex, Pi and future interactive agents all consume the same shared repository instructions and command procedures:

```text
AGENTS.md
  ↓
.agents/skills/rvv/SKILL.md
  ↓
.agents/commands/rvv-miniputt/<command>.md
  ↓
scripts/rvv-miniputt ...
  ↓
repository DecisionContext/result
  ↓
active harness chooses one declared action
```

There is no RVV-specific Pi scheduler/audit/scraper implementation. Harness-local code may exist only when a transport/UI capability truly cannot be expressed through the shared repository command surface, and it must remain thin.

Browser-assisted source recovery is not tied to a particular harness. A browser-capable session may perform navigation/extraction and then hand recovered data back through repository `recovery-inject` / `scrape-merge`; repository validation determines whether it becomes trusted evidence.

Generic personal agent frameworks/tooling stay outside this repository.

## Canonical-season boundary

All promoted-season writes go through one explicit lifecycle. The
infrastructure-only `CanonicalSeasonStore`
(`tournament_scheduler/infrastructure/canonical_season_store.py`) owns the two
durable files and installs them together as one staged directory swap; the
application `CanonicalSeasonService`
(`tournament_scheduler/application/canonical_season_service.py`) owns the common
load -> mutate -> verify -> reconcile -> history -> write sequence. Schedule
mutations (`season move`, `season replan`/`apply`, promotion) and decision-only
mutations (`approve`/`unapprove`, participation `accept`/`revoke`) share that
boundary, so a rejected mutation leaves both files untouched and a decision-only
change still advances the effective state revision. `season_state` is a thin
compatibility facade over the store/service.

Two identities are kept distinct:

- `schedule_fingerprint` is the tournament-content identity;
- `canonical_state_revision` (persisted in `decisions.json`) additionally covers
decisions, participation acceptances, accepted-change protections, typed
request constraints and the promoted verification/problem context. Findings,
repair options and applies bind to it, so a decision change (including
recording a new request constraint) invalidates options generated before it
even though the schedule did not change. Participation acceptance identity
includes `age_group`
(`participation_acceptance:<club>:<label>:<age_group>:<scope>`); legacy ids are
migrated explicitly.

Verifier-derived plan projections have one owner,
`plan_derived_state.reconcile_plan_derived_state`. Every canonical write and the
Stage 4 renderer call it, so hosting coverage/imbalance and repair logs,
`publication_readiness`, `unresolved_external_conflicts`,
`unresolved_participation_shortfalls`, the `participation_club_pools`
aggregate-classification projection and the operator-waiver audit rows are
re-derived from the same verify result that owns the rule and cannot contradict
it. It only overwrites facts the authoritative source actually carries (a
partial fixture never clears real plan data) and leaves planner-time facts that
cannot be reconstructed from the tournaments (for example
`unresolved_tournament_placements`) untouched.

Once a verified schedule is promoted, normal planning becomes baseline-aware:

- durable tournament IDs survive ordinary moves/rehosting/participant edits;
- approved placement/participant locks are hard-preserve constraints;
- unapproved schedule changes receive weighted change-cost pressure to minimize churn;
- `season move` handles targeted changes;
- `season replan` + `season diff` + `season apply` handles bounded refinement;
- `season constraints` / `season add-constraint` / `season release-constraint`
  owns durable typed **request constraints** (a team unavailable on a date or
  inclusive range, a minimum gap between a team's tournaments, opponent
  avoidance within a date range). They live in `decisions.json`, participate in
  the canonical-state revision, and are enforced by `request_constraints` (the
  planner-independent verifier) at every schedule-changing canonical
  application boundary until explicitly released. Recording one is a
  decision-only write deliberately allowed while the current plan still
  violates it; the derived `satisfied`/`violations` report is recomputed from
  the current plan rather than stored, so a later change can satisfy the same
  active constraint without rewriting its record. Granular change protections
  still guard the exact result of one accepted mutation; the semantic request
  remains authoritative above them;
- `season approve` / `season unapprove` owns the approval lifecycle;
- changed approval fingerprints become `stale_approval` and require explicit reapproval;
- `season export` projects the exact current canonical revision before audit/publication.

## Microsoft 365 boundary

Use Microsoft 365 for intake and lightweight integration: Forms submission, registration-code validation, reviewed SharePoint storage, notifications and controlled exports. Keep planning, verification and publication logic in tested repository code rather than duplicating it in Power Automate.

## Publication boundary

GitHub Pages is a static publication target, not the planning system of record. Publication:

1. starts from an already generated/reviewed export snapshot representing the intended canonical revision;
2. creates a separate allowlisted public bundle;
3. checks/redacts/blocklists sensitive/internal content;
4. requires explicit public-write approval;
5. updates Pages and verifies the result.

Spond exports and per-club review packets remain private/review artifacts unless deliberately distributed separately. WordPress is the editorial/navigation layer and should link/embed generated Pages output rather than copy schedules by hand.

## Generated data

Generated checkpoints, exports, reports, visualizations and evidence are not maintained documentation. Keep them under runtime/export/test/CI locations; promote only durable conclusions into current docs or ADRs.
