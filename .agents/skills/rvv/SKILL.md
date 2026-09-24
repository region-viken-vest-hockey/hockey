---
name: rvv
description: Canonical shared runbook for RVV Miniputt season planning, canonical season maintenance, calendar-source recovery, review, export, and publication. Use for work on the hockey repo's planning pipeline and promoted season state.
---

# RVV Miniputt shared runbook

This is the canonical agent-facing operating policy for RVV Miniputt. It is harness-neutral: Claude, Codex, ChatGPT, Pi and future agent harnesses should all consume this same policy and the shared procedures under `.agents/commands/rvv-miniputt/`.

Use repository code for facts, hard constraints, validation, search/solver mechanics, persistence, export and publication safeguards. Use the active agent only for contextual soft judgment among actions the repository exposes. Do not create a second harness-local scheduler, decision controller, audit judge, or scraper.

Read `AGENTS.md` first for repository-wide precedence and hygiene rules.

## Canonical-season implementation navigation

`CanonicalSeasonService` is the stable public application boundary for promoted-season mutations; it is **not** a requirement that every canonical-season use case live in one source file. Preserve one public mutation boundary, one persistence owner (`CanonicalSeasonStore`), and the shared load -> mutate -> verify -> reconcile -> history -> revision -> atomic-write invariants, while preferring cohesive internal modules when implementation size/coupling warrants extraction.

When investigating or changing canonical-season behavior, keep agent context local:
- start from the exact CLI/application entry point and affected `CanonicalSeasonService` method, then follow only the helpers/domain modules it actually calls;
- use symbol/code search and targeted line/range reads rather than loading a large canonical-season implementation wholesale;
- for a localized concern (for example roster swaps, guest slots, approvals, calendar evidence, constraints or baselines), read that concern plus the shared lifecycle/invariants; do not pull unrelated command families into context by default;
- keep `season_state.py` a thin compatibility facade and do not create a second mutation implementation there or in an adapter;
- when extracting implementation, prefer explicit composition/delegation over mixin inheritance, and keep existing public service signatures stable unless a deliberate compatibility change is part of the task.

This locality rule is about agent comprehension as well as code structure: a single architectural owner may delegate to several focused implementation modules without weakening the canonical boundary.

## Operating lifecycle

There are two operating phases.

### 1. Initial season creation

Create and review the season through the canonical Stage 1–4 pipeline. For checkpoint-reviewed agent operation:

```bash
scripts/rvv-miniputt run --interactive --input input.xlsx
```

Inspect the repository-owned `DecisionContext` after each pause and continue only through declared actions. Stage checkpoints under `.pipeline/` are transient run state.

When a verified schedule is deliberately accepted as the operational baseline for club review/ice booking, promote it explicitly through the shared `season` procedure. Promotion is not an automatic side effect of an ordinary planner run.

### Ice-duration contract

For every planning, repair, review and export path, treat `ice_time_minutes` as the **authoritative total booked/occupied ice duration** for that age group. Never add `5 * rounds`, setup time or resurfacing time on top of it when checking a slot or computing an end time.

Round timing is validation evidence only:

```text
minimum_format_minutes =
    actual_round_count * (round_length_minutes + 5)
```

The configured `ice_time_minutes` must be at least that minimum and must also satisfy any applicable governing/local minimum booking allocation. If those checks fail, fix/configure the source value or the canonical duration rule; do not silently enlarge one consumer's interval. A configured value above the minimum is not automatically waste: the 2026–2027 RVV values deliberately include operational headroom for ice preparation/resurfacing (including Zamboni time), setup/clearance, team turnover and small delays, and are intended to preserve the effective windows already used by the published plan/club bookings. Do not automatically reduce them to the format minimum. Slot search, external-calendar availability, arena conflicts, optimizer/repair feasibility, Excel/HTML/iCal end times and audit evidence must all describe the same occupancy interval. See `docs/rvv-miniputt-input-formats.md` for the approved per-age-group values/workbook contract and `docs/system-architecture.md` for ownership.

### 2. Promoted-season maintenance

After promotion, `season/<season>/schedule.json` plus `season/<season>/decisions.json` are the durable operational truth. Use canonical `season` operations for approvals, targeted moves, bounded replanning and re-export rather than regenerating the season from scratch:

```bash
scripts/rvv-miniputt season status --season 2026-2027
scripts/rvv-miniputt season refresh-calendars --season 2026-2027 --dry-run
scripts/rvv-miniputt season refresh-calendars --season 2026-2027
scripts/rvv-miniputt season reconcile-config --season 2026-2027 --dry-run
scripts/rvv-miniputt season reconcile-config --season 2026-2027
scripts/rvv-miniputt season approvals --season 2026-2027
scripts/rvv-miniputt season calendar-booking-candidates --season 2026-2027 --club Jar
scripts/rvv-miniputt season reconcile-calendar-bookings --season 2026-2027 --club Jar --note "Reviewed complete host calendar"
scripts/rvv-miniputt season booking-status --season 2026-2027
scripts/rvv-miniputt season confirm-calendar-booking --season 2026-2027 --event-fingerprint <fingerprint> --tournament-id <id> --note "Matched to host calendar booking"
scripts/rvv-miniputt season release-calendar-booking --season 2026-2027 --event-fingerprint <fingerprint> --tournament-id <id> --note "superseded match"
scripts/rvv-miniputt season calendar-booking-findings --season 2026-2027
scripts/rvv-miniputt season findings --season 2026-2027
scripts/rvv-miniputt season findings --season 2026-2027 --all
scripts/rvv-miniputt season baseline create --season 2026-2027 --note "Accepted season baseline after initial planning"
scripts/rvv-miniputt season baseline show --season 2026-2027
scripts/rvv-miniputt season baseline advance --season 2026-2027
scripts/rvv-miniputt season repair-options --season 2026-2027 --finding <finding-id>
scripts/rvv-miniputt season search --season 2026-2027 --finding <finding-id>
scripts/rvv-miniputt season apply-repair --season 2026-2027 --option-id <id> --expected-revision <rev>
scripts/rvv-miniputt season accept-deviation --season 2026-2027 --finding <finding-id> --note "ice unavailable"
scripts/rvv-miniputt season revoke-acceptance --season 2026-2027 --finding <finding-id>
scripts/rvv-miniputt season approve --season 2026-2027 --tournament-id <id> --note "ice booked"
scripts/rvv-miniputt season unapprove --season 2026-2027 --tournament-id <id> --note "booking changed"
scripts/rvv-miniputt season move --season 2026-2027 --tournament-id <id> --date 2026-10-18 --request-id <request-id>
scripts/rvv-miniputt season replace-participant --season 2026-2027 --tournament-id <id> --remove-team "<team>" --add-team "<team>" --request-id <request-id> --dry-run
scripts/rvv-miniputt season swap-participants --season 2026-2027 --tournament-a <id> --team-a "<team>" --tournament-b <id> --team-b "<team>" --request-id <request-id> --dry-run
scripts/rvv-miniputt season rename-team --season 2026-2027 --club <club> --age-group <age> --from "<old label>" --to "<new label>" --request-id <request-id> --dry-run
scripts/rvv-miniputt season protections --season 2026-2027
scripts/rvv-miniputt season release-protection --season 2026-2027 --request-id <superseded-request-id> --note "superseded by <new-request-id>"
scripts/rvv-miniputt season constraints --season 2026-2027
scripts/rvv-miniputt season add-constraint --season 2026-2027 --type <type> --request-id <request-id> [team/date/min-days flags]
scripts/rvv-miniputt season release-constraint --season 2026-2027 --request-id <superseded-request-id> --note "superseded by <new-request-id>"
scripts/rvv-miniputt season ban-date --season 2026-2027 --date <YYYY-MM-DD> --request-id <operator-request-id> --note "<reason>"
scripts/rvv-miniputt season unban-date --season 2026-2027 --date <YYYY-MM-DD> --note "<reason>"
scripts/rvv-miniputt season banned-dates --season 2026-2027 --json
scripts/rvv-miniputt season allow-holiday-date --season 2026-2027 --date <YYYY-MM-DD> --reason "<why the derived holiday exclusion should not apply>"
scripts/rvv-miniputt season holiday-date-exceptions --season 2026-2027 --json
scripts/rvv-miniputt season disallow-holiday-date --season 2026-2027 --date <YYYY-MM-DD> --note "<reason>"
scripts/rvv-miniputt season guest-report --season 2026-2027
scripts/rvv-miniputt season guest-candidates --season 2026-2027 --age-groups JU10,JU12
scripts/rvv-miniputt season guest-reserve --season 2026-2027 --tournament-id <id> --note "external league team may apply"
scripts/rvv-miniputt season guest-fill --season 2026-2027 --tournament-id <id> --external-club "<club>" --external-label "<team>"
scripts/rvv-miniputt season guest-release --season 2026-2027 --tournament-id <id> --replacement-label "<rvv team>"
scripts/rvv-miniputt season normalize-placements --season 2026-2027
scripts/rvv-miniputt season normalize-arenas --season 2026-2027
scripts/rvv-miniputt season batch --season 2026-2027 --operations <batch.json> --scope <id> --request-id <request-id> --dry-run
scripts/rvv-miniputt season replan --season 2026-2027 --iterations 4000
scripts/rvv-miniputt season diff --season 2026-2027 --candidate <candidate.json>
scripts/rvv-miniputt season apply --season 2026-2027 --candidate <candidate.json>
scripts/rvv-miniputt season export --season 2026-2027
scripts/rvv-miniputt season lifecycle --season 2026-2027 --json
scripts/rvv-miniputt season seal-published --season 2026-2027
scripts/rvv-miniputt season reopen-planning --season 2026-2027 --reason "<operator reason>" --confirm-break-published-baseline
```

### Published-season lifecycle (maintenance-only)

A season that has been published is an operational schedule, not a planning problem. The canonical application layer owns an explicit lifecycle state: `planning` -> `promoted` -> `published_sealed`. The **first successful publication seals the season automatically**, and a season published before this feature existed is backfilled with `season seal-published`, which locates the real published baseline through authoritative publication history (never through a supplied export directory), derives publication omissions from the canonical plan at the publication revision, requires explicit provenance for any post-publication materialization, and **fails closed** if `published baseline + attested additions + recorded canonical mutations` does not exactly equal current canonical state.

Once `published_sealed`:

- never regenerate or globally replan the season. `season replan`, planner-generated `season apply`, mutating `season normalize-placements`, `season promote --force`, and a full/new pipeline `run` whose window matches the sealed season are all refused by the service/application layer (not only by CLI text), so a direct Python caller gets the same refusal. A sealed-season scoped mutation is authorized only when the application layer re-runs the exact typed operation (move, swap/replace participant, bounded repair option) from current canonical state at the apply boundary and the submitted candidate equals the reproduced result as a whole plan, excluding only reconciliation-owned derived/reporting projections; a caller-supplied mode string, contract, affected-id list or capability object is never authorization, and unrelated plan facts (unresolved placements, games, placement/host-confirmation metadata) may not drift;
- keep evolving it only through explicit, audited canonical maintenance operations (`move`, `swap-participants`, `replace-participant`, `batch`, `rename-team`, guest reserve/fill/release, approve/unapprove, booking-evidence reconciliation, `refresh-calendars`, safe `reconcile-config`, banned/holiday dates, ...). Each such operation still starts from the current revision, verifies the exact change, records durable evidence and advances the revision atomically;
- `season normalize-placements --dry-run` stays available as a diagnostic, but mutating normalization is refused. When newer calendar evidence shows a published tournament conflicts or is not booked, surface it as an operational finding/booking state and resolve it with an explicit move/cancel/booking decision;
- run `season lifecycle --json` (or the pre-publication reconciliation check) before publishing a new revision; a new publication refuses unexplained schedule drift.

Decision-only evidence changes (approval, booking status, calendar refresh, audit metadata, season baseline) are never treated as schedule mutations. The emergency escape hatch `season reopen-planning --reason ... --confirm-break-published-baseline` is operator-only, prominent, permanently audited and must never be inferred or auto-selected by an agent; normal maintenance should never need it.

Classify participant changes by intent. For a pure identity/name correction of the same underlying registered team across the promoted season, use `season rename-team`; do not reconcile the registration set, use `replace-participant` tournament-by-tournament, or replan. For "replace team A with team B in this tournament", use `season replace-participant`; do not manufacture a second tournament to fit the swap API. The command changes exactly one tournament, keeps its placement/host fixed, adds only a registered same-age RVV participant, regenerates games, respects locks/protections/constraints/guest reservations/hosting responsibility, reports before/after consequences and participation counts for both outgoing and incoming teams, and persists team-specific protections when applied. For a genuine two-tournament exchange, use `season swap-participants` rather than hand-editing canonical JSON or refusing because no repair finding exists. The swap command only swaps two participants between same-age tournaments, keeps both placements/hosts fixed, regenerates both game schedules, respects participant locks and guest reservations, and runs the full canonical hard-verification/hosting-responsibility boundary. It also computes per-team before/after consequences for **both** swapped teams (spacing, full-season temporal coverage, opponent repetition/diversity and travel). `--dry-run` returns these facts even for a poor candidate; applying a participant replacement or swap is refused when an affected team gets a deterministic material regression (additional <7/<14-day gap, materially worse >60-day season coverage, additional opponent-repeat excess above two meetings, or travel increase of at least 50 km and 25%). Evaluate alternatives with `--dry-run` and choose a candidate that improves the target without materially degrading the affected teams.

When promoted-season maintenance is blocked because canonical findings/repair/search are verifying against stale external arena evidence, use `season refresh-calendars` to run a fresh Stage 2 scrape and advance only the calendar evidence. Start with `--dry-run`; if the source change is expected, commit the refresh, then inspect `season findings`. This operation must not move tournaments, change participants, change approvals/protections/request constraints or absorb new baseline regressions. It deliberately makes existing export/audit evidence stale, so run a fresh export/audit before publication. Do not rerun the full Stage 1-4 planner merely to refresh calendar availability for an already promoted season.

When promoted-season maintenance is blocked because a config-owned fact changed meaning after promotion, use `season reconcile-config` rather than baselining hard violations away or hand-editing canonical JSON. Start with `--dry-run`; apply only when the command reports a safe recognized semantic migration and full verification passes. For the ice-time semantic migration, reconciliation preserves the legacy effective occupancy (`stored ice_time_minutes + 5 * actual round count`) rather than copying a lower current minimum or shortening the booked window.

When a club's own tournaments are clustered and no single move, same-age participant swap or placement-only exchange is legal, do not conclude the cluster is unavoidable: `season findings` reports a `temporal_clustering` finding, and `repair-options`/`search` for it enumerate a bounded **coupled placement + roster repair** family (`coupled_placement`). The provider pairs each clustered tournament with other scheduled tournaments (including other age groups) and exchanges only placement; when the placement exchange double-books a displaced tournament's team, it searches bounded **same-age** participant reselection for that tournament, regenerates its games, and re-verifies the whole coupled state. Cross-age applies to the *placement exchange only*; participants never move across age groups, and candidates that transfer hosting responsibility are rejected. A coupled option also carries the complete per-team consequence set for **every** team whose schedule changed -- both moved tournaments' participants plus any team the roster repair added or removed -- and an option that materially regresses any of them (a new <7/<14-day gap, a new participation shortfall, materially worse coverage/opponent repetition/travel) is reported `consequence_acceptable: false`, kept off the auto-applicable Pareto front and refused at apply. A hard-valid exchange that would otherwise regress a retained participant of a **multi-team** club triggers a bounded **same-club, same-age sibling substitution** in the affected moved tournament rather than relaxing that individual team's gap rule: the candidate is accepted only when the complete changed-team consequence set, operational acceptability and the affected club-pool participation are all no worse, and the option evidence reports the sibling count, the substitution and the before/after club-pool distribution. A single-team club has no sibling pool, so its candidate stays rejected. Because the provider truncates its returned options to a bounded display set, automatic acceptability also participates in that ranking: a consequence-rejected candidate can stay visible as evidence but never consumes a returned slot ahead of an automatically acceptable one (the operational opt-in class is still ordered after plain automatic acceptance and before rejection). `search` that finds nothing reports `bounded_search_exhausted`, never `proven_infeasible`. Apply a verified option through the ordinary revision-bound `season apply-repair`; there is no separate placement-swap apply command.

When one of a multi-team club's sibling teams represents it at nearly every one of that club's home tournaments in an age group, `season findings` reports a `home_representation` finding (a spread of 0-1 is balanced). Hosting coverage/balance is unchanged -- the club keeps hosting the same tournaments. `repair-options` enumerate a bounded `home_representation` family of same-club, same-age **sibling swaps** that never move host/date/arena or transfer hosting responsibility: a simple rotation at one home tournament, a coupled home + away swap that keeps both siblings' half-season participation counts unchanged, and a bounded greedy sequence that balances the whole pool. A swapped sibling may not gain a `<7`-day double, worsen a participation shortfall/hard maximum or materially worsen season coverage, and the affected club pool may not deepen or materially increase its total travel; opponent-repetition and extra 7-13-day-gap changes stay visible in `quality_vs_current`/the objective vector instead of hard-blocking the repair, because changing which sibling represents the club necessarily changes opponent identities. A skew with no safe swap stays a finding, never a hard failure. The accounting/finding is generic across **every** multi-team club × age-group pool, not specific to the club that exposed the defect. After repairing one named pool, refresh `season findings` and inspect the remaining `home_representation:*` findings across the whole canonical season; only apply revision-bound, hard-valid, consequence-acceptable/non-dominated repairs. Fixing one club never proves sibling home representation is balanced elsewhere.

When a multi-team club × age-group player pool has enough aggregate participation but its sibling labels are uneven, `participation_targets` classifies it `intra_club_distribution` and `season findings` reports a soft `intra_club_participation_distribution:<club>:<age_group>:<scope>` quality finding (scope is `season`, `before_christmas` or `after_christmas`). It exposes the aggregate target/actual, the exact sibling counts/targets and the current spread, and is never an unresolved participation shortfall or a publication blocker. `repair-options` enumerate a bounded `intra_club_distribution` family of same-club, same-age **sibling substitutions**: replace an over-represented sibling with an under-represented sibling inside one existing tournament in the finding's scope, keep the tournament id/date/arena/start time/host/age group, regenerate games, preserve the club-pool aggregate and preserve guest reservations. Away tournaments are preferred; a home substitution is returned only when it does not worsen the affected pool's `home_representation`. When a direct substitution is blocked by a duplicate-day or a team-schedule consequence, `season search` widens into a bounded coupled sibling-only neighborhood instead of moving a date/host/arena/slot. Every option must be hard-valid, strictly reduce the selected scope's spread, not deepen a genuine club-pool shortfall, not worsen home representation, and not materially regress an affected team's spacing, coverage, opponent repetition or travel. Apply through the ordinary revision-bound `season apply-repair`; a bounded search that finds nothing reports `bounded_search_exhausted`, never `proven_infeasible`.

Every accepted participant swap and explicit `season move` also writes granular **change protections** into canonical `decisions.json`: swapped teams must remain in the tournament they were moved to and remain out of the tournament they were deliberately removed from, while moves protect only the placement fields the request explicitly changed. These guards are part of the canonical-state revision and are enforced by later swaps and generic `season apply`, so a later request/optimizer cannot unknowingly undo an earlier accepted change. Pass a stable source identifier with `--request-id` whenever available. Inspect guards with `season protections`. A later club request that intentionally supersedes an earlier one must first use `season release-protection` with the earlier protection/request id and an audit note naming the superseding request; never release a protection merely to make an optimizer candidate fit. The protection is team-specific rather than a whole-roster lock, so unrelated teams in the same tournaments remain editable.

Club feedback often states **intent**, not an exact replacement schedule ("we cannot play 2027-02-21; another date is fine", "keep at least 21 days between our tournaments", "do not place us with opponent X that weekend"). Do not reduce such a request to an exact placement lock. Translate it into a canonical typed **request constraint** and record it before searching or mutating:

```bash
scripts/rvv-miniputt season constraints --season 2026-2027 --json
scripts/rvv-miniputt season add-constraint --season 2026-2027 \
  --type team_unavailable \
  --team-club Kongsberg --team-label "K9" --team-age-group U9 \
  --date-from 2027-02-21 \
  --request-id <request-id> \
  --note "club cannot play 2027-02-21"
```

Supported types are `team_unavailable` (one team, inclusive `--date-from`/`--date-to`), `minimum_gap` (one team, positive `--min-days`) and `opponent_avoidance` (two teams, inclusive date range). Scopes use stable team identity (club + label + age group). Constraint ids are deterministic from the semantic payload plus `--request-id`, so a retried identical request is idempotent rather than a duplicate.

`season add-constraint` is a **decision-only write**: it is deliberately allowed even when the current schedule violates the new constraint. It persists the validated constraint, immediately reports the derived violation(s) (`season constraints` shows each active constraint's `satisfied`/`violations`), and advances the canonical-state revision so previously generated repair/search options become stale. This is how a new real-world fact is recorded before the plan is made to satisfy it.

Persist a constraint from a concrete durable semantic requirement ("Kongsberg U9 must have at least 7 days between tournaments" -> `minimum_gap`; "Kongsberg cannot play 21 February" -> `team_unavailable`). Do **not** invent a numeric threshold from vague language such as "three tournaments in eight days is a problem" -- preserve that as the operator's intent and use findings/search, unless the operator states the actual policy threshold.

Keep evaluating the operator's original outcome across intermediate mutations. A finding disappearing after a temporary or partial change does **not** prove the real problem is resolved (for example, moving a clustered team to an unusable slot can remove a `temporal_clustering` finding while failing "produce a usable, better-spaced schedule"). Continue until the requested outcome is satisfied or explicitly abandoned. Prefer `current canonical state -> repository-owned search -> final coupled candidate -> one atomic apply` over chains of temporary canonical moves used only to unlock later search; if a temporary mutation is genuinely necessary, treat it as temporary and release only the protections that operation created, never unrelated accepted protections.

Active constraints are hard maintenance requirements enforced by the repository at the canonical application boundary -- `season move`, `season swap-participants`, generic `season apply`/replans and other schedule-changing commits are refused while a candidate still violates one. `repair-options`/`search` pass the active set into option evaluation and mark constraint-violating options as rejected, but the final apply boundary remains authoritative. Search for a legal result satisfying **all** active constraints; never auto-release a prior constraint because it blocks an easy candidate. Release only when a newer request explicitly supersedes/revokes it:

When one request records several independent violations, no single canonical mutation can satisfy the whole active set first (each commit still has to clear every active constraint). Do not release valid constraints to unlock an intermediate mutation, do not hand-edit canonical JSON, and do not fence a broad `season replan` with approvals. Use atomic scoped batch maintenance instead: `scripts/rvv-miniputt season batch` applies a declared list of `move`/`swap_participants`/`cancel` operations to one in-memory candidate, requires an explicit `--scope` of affected tournament ids, freezes every out-of-scope tournament, runs the complete authoritative gate set once on the final candidate (all active request constraints, hard verification, operational acceptability, locks/protections, guest reservations, hosting responsibility, and per-team swap consequences) and commits exactly once. A batch that leaves any violation, changes an out-of-scope tournament, or fails any gate is refused without a partial write; a `--dry-run` reports scope, changed ids, remaining violations and every gate verdict. One isolated violation still goes through ordinary `season move` / `season swap-participants`, whose full-season constraint gate is unchanged.

Distinguish a **global date restriction** from a **team restriction**. When a whole date is unusable for every tournament (hall closed, holiday weekend nobody can host), record a canonical **banned date** with `season ban-date`, not a per-team request constraint. A banned date reuses the existing `banned_dates` rule read by the planner, verifier, candidate-weekend enumeration, repair/search, optimizer and the `season batch` boundary -- there is no separate date-policy engine. Like `season add-constraint`, recording a ban is a decision-only write that is allowed even while tournaments are still scheduled on that date: the ban is persisted, `season banned-dates --json` reports the exact `affected_tournament_ids`, and the canonical revision advances so stale options are invalidated. Repair is separate: derive the scope from `season banned-dates --json` and repair it with one scoped atomic `season batch`. Remove a ban only with `season unban-date` when the underlying restriction no longer applies; never hand-edit JSON. A banned date blocks `season move`, `season batch` moves, generated repair/search options, candidate-weekend/optimizer/replan date moves and final canonical verification until repaired or explicitly unbanned.

A holiday-date exception is the opposite kind of global date policy: it allows one exact date that the derived Norwegian holiday/weekend policy would otherwise exclude. Use `season allow-holiday-date` only when RVV explicitly confirms the derived exclusion should not apply for that season/date. It is stored in canonical `decisions.json` with reason/provenance and advances the canonical revision, but it does not move tournaments. It removes only the derived holiday-policy exclusion; if the same date is also an explicit `ban-date`, the ban still wins and must be unbanned separately.

```bash
scripts/rvv-miniputt season release-constraint --season 2026-2027 \
  --request-id <old-request-id> \
  --note "superseded by <new-request-id>"
```

If a semantic request cannot be represented by a supported type, surface that capability gap instead of silently reducing it to an exact placement lock. A request constraint stays authoritative even after a successful repair: change protections may still guard the exact accepted result, but they never replace the higher-level request.

For a localized defect (an unresolved hosting obligation, a manual placement, a host-controlled movable-ice opportunity, a participation strong-goal deviation), prefer the finding-directed loop over whole-season replanning:

```text
season findings            -> fresh, revision-bound facts (never the promoted snapshot)
season repair-options       -> direct/coupled options for ONE selected finding
season search               -> bounded neighborhood search when cheaper options are insufficient
season apply-repair         -> atomic, full-season-verified, revision-bound apply + delta
```

`season_plan` means the actual schedule: `season normalize-placements` upgrades an already-generated canonical plan to the placed/provisional/unplaced model in place (removing genuine slot-search failures and fixed-external-conflict placements into stable unresolved obligations while preserving approved/provisional placements) without rerunning Stage 1-3; on a `published_sealed` season only `--dry-run` is allowed (it diagnoses what newer evidence would classify differently) because mutating normalization could silently move or demote a published tournament. After a season has been published, export is schedule-preserving: refreshed calendar evidence may flag conflicts for explicit operator action, but `season export` itself must not move, demote or remove canonical tournaments. `season normalize-arenas` re-emits the canonical schedulable arena identity owned by `club_registry.canonical_arena_name`: a tournament already at its club's canonical arena is byte-identical, while a legacy venue label (for example `Ringerikshallen` for Ringerike's `Schjongshallen`) is rewritten in place with dates, hosts, participants, games, approvals, guards and reservations preserved and the stored club->arena problem mapping re-emitted so a later repair/replan cannot reintroduce the legacy label.

Findings are independent facts, not a mandatory queue: select whichever finding matters next. Options and findings are bound to the canonical revision they came from; applying against a changed revision is rejected as stale and leaves canonical state unchanged. A `bounded_search_exhausted` participation deviation is not proof of infeasibility -- request another bounded search rather than recording it as `proven_infeasible`. The same epistemic rule applies to any bounded repair/search family: **zero options from the configured bounded search means only that this search found none**. Do not summarize it as "genuinely exhausted", "no solution exists" or equivalent unless a deterministic capability explicitly returns `proven_infeasible`/an exhaustive proof. A remaining deviation an operator deliberately decides to live with may be persisted with `season accept-deviation` as an explicit `operator_accepted` decision: it never changes the target or the schedule, becomes stale (and re-surfaces the finding) if the target changes or the deviation gets worse, and is reopened with `season revoke-acceptance`. A recorded exhaustion carries the search capability that produced it, so when the repository widens a neighborhood, an obligation marked `bounded_repair_exhausted` by the superseded search is reported as stale/retryable (`search_coverage.capability_stale`) and canonical maintenance can re-open it against the current search without rebuilding Stage 1/2 facts or discarding the promoted baseline. Repairs must never transfer hosting responsibility to a club that does not owe it, and must never make another club's hosting deficit worse. A genuine unplaced obligation (no `Tournament` exists yet) is repaired through the same loop: `unplaced_placement_repair` materializes it into a real verified tournament along the escalating ladder (same-date start time, same-host date, bounded participant reselection, capacity release, coupled cross-age exchange), and its finding reports `search_coverage` so `search_incomplete` (untried supported dimensions remain), `option_available` and `bounded_search_exhausted` are never conflated. A supported dimension whose hard precondition is unmet for the obligation is reported as `inapplicable` (not `untried`) so coverage can terminate instead of inviting an unbounded retry of a search that can never run; participant-reselection applicability is evaluated against the source date and every alternate date it would run on, not only the source date. Respect the repository's repair-cost order: when a tournament's host/date/arena/time are already legal and only the selected roster conflicts, the first options are placement-preserving roster substitutions, so do not move the slot or escalate to a broader search while a verified substitution exists. Every returned option is measured on one canonical objective vector (verifier defect counts plus change cost and host-confirmation dependencies, merged with the shared Stage-3 soft-quality objectives and canonical travel) and the report marks the non-dominated `pareto` set; each option also carries `quality_vs_current`/`travel` and the apply delta returns the same quality/travel before/after evidence. Prefer a non-dominated option over a dominated one when its trade-off is acceptable.

**Hard-valid is not the same as an acceptable automatic repair.** `verify_candidate.ok` deliberately represents some external-calendar collisions and unresolved obligations as non-blocking *manual work* so a schedule that still needs human placement can be represented and reviewed. The repository owns a separate non-regression acceptance predicate: an automatic canonical mutation (`season move`, `season apply-repair`, `season apply`/`replan`, and every provider option) may not *newly* introduce, relative to the current canonical baseline, a placement inside a host's `fixed_busy`/external-calendar interval, a placement at a host with no trustworthy calendar, unresolved/manual tournament-placement work, or a host-confirmation dependency (a `movable_busy` allocation). Existing manual work stays visible; only *new* work is gated. Options that would introduce such work are classified (`operational_acceptable: false`, `requires_operational_opt_in`) and kept off the auto-applicable Pareto front, and the apply boundary re-checks the same predicate rather than trusting a provider's self-report. `pareto` alone is never sufficient protection, because an option that fixes one defect while introducing a manual placement can remain non-dominated.

When the operator explicitly wants a deliberate provisional/manual placement, opt in for that exact action with `--allow-manual-placement` and/or `--allow-host-confirmation`; the opt-in is audited in the result/`decisions.json` evidence. Never treat the absence of a hard verification failure, or the disappearance of one finding, as implicit consent. Release only the protections the operator's request actually supersedes; never release unrelated accepted protections to make a candidate fit.

Never hand-edit canonical season JSON to work around a lock or verifier.

When refreshed host-calendar evidence contains an ambiguous busy event that the host has confirmed is the actual booked RVV tournament window (for example a generic title such as `Serieturneringer U10`), do not add broad title regexes or mutate the raw scraped event. Use `season calendar-booking-candidates` to get bounded deterministic candidates by host/date/interval, let the harness/operator make the semantic match, then persist it with `season confirm-calendar-booking`. The persisted overlay binds one calendar-event fingerprint to one canonical tournament id, records note/provenance plus the canonical occupied interval, makes that event non-conflicting only for that tournament, and reuses ordinary approval/placement-lock semantics while leaving participants editable. The same event stays busy for every other tournament. Projection fails closed: if the scraped event, the associated tournament's host/arena/date/start facts, or the current canonical occupied interval are no longer compatible with the confirmed event, the association is reported as stale and no longer suppresses the calendar conflict. Use `calendar-booking-findings` or `season findings` to surface stale associations, and `season release-calendar-booking` before deliberately rebinding the same event to another tournament.

One definition of "confirmed booked": only an explicit event-to-tournament association (or an already-valid one) is `confirmed_booked`. Bulk `season reconcile-calendar-bookings` records only evidence: a trustworthy host calendar with no relevant event becomes `confirmed_not_booked`, a lone overlapping event stays `ambiguous` candidate evidence, and multiple overlaps stay `ambiguous`. It never promotes mere overlap to a confirmed booking. Confirm the semantic match for an ambiguous candidate with `season confirm-calendar-booking`; `confirm-calendar-booking` is the only path that creates `confirmed_booked`. The booking-status projection enforces the same invariant: a persisted positive evidence record with no currently-valid explicit association (a legacy overlap match, or one whose association was released without rebinding) is surfaced as an ambiguous item requiring review, not as `confirmed_booked`, while the historical evidence record itself is preserved.

### Season quality baseline

A promoted season can carry a number of accepted, non-hard deviations that cannot realistically be eliminated (season capacity, arena availability, excluded weekends, club constraints). Those accepted deviations must stay visible without being re-litigated on every maintenance change, but they must never be confused with hard validity.

The **season quality baseline** is a reviewed snapshot of the current set and severity of non-hard findings, used only as a regression reference for later maintenance. It is deliberately distinct from every adjacent concept:

- **canonical schedule truth** (`season/<season>/schedule.json`) is the actual placement/roster state; the baseline never edits it;
- **operator waiver** (`waiver ...`) is a narrow, authorized exception to one classified hard planning rule;
- **participation acceptance** (`season accept-deviation`) is a decision about one bounded participation strong-goal deviation, bound to its scope/magnitude;
- **season baseline** (`season baseline ...`) is a full-season regression reference over *all* non-hard findings, recording stable finding identities and their measured severities, never aggregate counts alone.

Hard structural verification stays authoritative. Deleting one finding while introducing a different one is a regression even when counts are unchanged, and a participation shortfall changing from `-1` to `-2` is a regression even though the finding id is unchanged. The baseline therefore records, per finding, the stable finding id, category, rule id, relevant measured values and search-coverage state (e.g. `bounded_search_exhausted`), plus provenance (season, source canonical-state revision, schedule fingerprint, creation time, actor, note, and an optional export/audit fingerprint).

```bash
scripts/rvv-miniputt season baseline create --season 2026-2027 --note "Accepted after initial planning"
scripts/rvv-miniputt season baseline show --season 2026-2027
scripts/rvv-miniputt season baseline advance --season 2026-2027
```

Once a baseline exists, `season findings` reports a comparison that classifies every current non-hard finding as `KNOWN`, `IMPROVED`, `RESOLVED`, `REGRESSED` or `NEW`, and the default operator view emphasizes `NEW`/`REGRESSED` while `--all` still lists the accepted known debt. Hard findings are never baselineable: they stay blocking and remain visible in the default view. `season baseline create`/`advance` refuse to run while hard verification fails, so the baseline can never make an invalid plan valid.

`season baseline advance` tightens the accepted state to the current equal-or-better state: it is allowed only when there are no `NEW` or `REGRESSED` findings, and it records the prior baseline and comparison summary in `decisions.json` history. A genuinely worse/new state is never silently accepted by `advance`; it requires the explicit `season baseline replace --note "why the worse state is deliberately accepted"`, which rebaselines and records the prior baseline as audit history. Baseline mutations are decision-only writes: they change the canonical-state revision and provenance but never the schedule fingerprint when no tournament changed. Export evidence and the audit context expose the active baseline for traceability.

### Reserved guest places

The operator expresses guest reservations as intent, not command syntax, for example "Reserve 3 JU10 and 2 JU12 guest places, max one per tournament, spread them sensibly", "Reserve a guest place in the JU12 tournament on 14 February", "Give that guest place to <external team>", or "Release the unused guest places". Resolve the intent to canonical state and repository operations; the operator never needs to know action ids or flags.

For a policy-level request, call `season guest-candidates` and choose among the returned legal alternatives using the ordinary deterministic facts/score/evidence surface, recording the selection and trade-offs in controller/audit evidence. Do not construct a harness-local scheduler or reduce the global JU10/JU12 tournament size to emulate this. A reservation is a per-tournament `guest_slot`: it counts toward capacity/ice-time shape but never toward RVV participation, hosting, fairness, travel or team counts, and it is never an accidental underfilled tournament. Participant optimization/repair cannot consume it, and the host-representation rule still requires a real participating team from the host club. When a selected tournament is already full, do not drop an arbitrary participant: use the candidate facts to name a legal displaced team (a resulting participation shortfall stays explicit). `guest-fill` accepts an external team without adding it to the Stage-1 RVV roster and regenerates/independently verifies games; `guest-release` withdraws a reservation, optionally refilling it with a real RVV team where legal. Reservations respect approval/participant locks and survive later replanning/repair until deliberately filled or released (`season apply` rejects a candidate that would silently change them). `season_plan.html` shows open/filled guest places and the semantic audit carries `guest_reservations` evidence.

## Command boundary

Shared procedures live under `.agents/commands/rvv-miniputt/` and are used by every harness. The repository-local transport is:

```bash
scripts/rvv-miniputt ...
```

Harness adapters may add genuinely necessary UI/transport integration, but must not redefine shared pipeline policy or create an independent reasoning loop. The active harness itself should read the shared procedure, inspect repository output, choose a declared action and invoke the next canonical command.

Session startup/evidence gathering uses the read-only `make handover` adapter described in `.agents/commands/rvv-miniputt/handover.md`. It is diagnostic evidence only, never an operation or publication authority, and does not broaden the operation command surface.

When an agent changes repository code, use `scripts/check` for the fast/default feedback loop. Before treating a substantive change to scheduling semantics, verification, canonical-season mutation/persistence, audit/export/publication contracts or multi-process lifecycle as complete, run the comprehensive hermetic lane `scripts/check full`. Harness-dependent and live external-source checks remain explicit separate lanes (`scripts/check harness`, `scripts/check live`) and are never substitutes for deterministic full verification.

## Inputs

The four-stage planner uses:

- root `input.xlsx` as the controlled planner workbook;
- reviewed registration exports only through the controlled import path when `Lag` needs rebuilding;
- external calendar/source evidence collected in Stage 2;
- browser/session access only when a configured source genuinely requires interactive recovery.

`Årshjul for aktiviteter.xlsx` and the public registered-team workflow are related repository workflows but are not Stage 1–4 planner policy inputs.

See `docs/rvv-miniputt-input-formats.md` for the workbook contract.

## Four-stage pipeline

### Stage 1 — configuration

Repository code validates and normalizes the controlled workbook.

Agent policy:

- do not invent missing teams, clubs, age groups or settings;
- treat invalid/reversed season windows and invalid identities as input problems, not soft preferences;
- choose only from actions declared by the returned context.

### Stage 2 — source/calendar evidence

Repository code owns extraction results, cache/provenance, source status and validation.

Agent policy:

- inspect blocked, empty and suspiciously sparse sources before trusting the plan;
- prefer bounded recovery/retry actions exposed by the repository;
- do not declare a source healthy merely because a request technically succeeded;
- browser-assisted recovery is optional and harness-neutral. If needed, perform only browser/navigation extraction and return recovered data through the canonical `recovery-inject` + `scrape-merge` path described in `.agents/commands/rvv-miniputt/scrape-llm.md`;
- do not maintain a dedicated browser scraper inside a harness adapter.

Useful commands:

```bash
make sources-status
make calendars
scripts/rvv-miniputt scrape --club <name>
scripts/rvv-miniputt recovery-targets
```

### Stage 3 — planning

Repository code owns the normalized planning problem, hard constraints, candidate schema, solver/search primitives, deterministic verification and reproducible metrics.

Before adding or changing any recurring scheduling semantic, start from the canonical metadata catalog in `tournament_scheduler/rule_catalog.py` (generated view: `docs/architecture/rule-catalog.md`). If the intended semantic already has a stable ID, change its named canonical owner/conformance rather than creating a second rule in planner/search/repair/report/harness code. If it is genuinely new operator policy, add the generic semantic to the catalog with classification, owner, evidence and tests. The catalog is navigation/identity metadata, not a rules engine: executable truth remains in the deterministic owner named by the entry.

Agent policy:

- never accept a candidate with hard verification failures;
- use exposed optimize/refine/apply/keep/request actions rather than editing the season plan in prose;
- compare candidates using returned metrics/findings rather than intuition alone;
- prefer Pareto/multi-objective evidence when several valid trade-offs exist;
- do not turn a one-run preference into a new hard rule. If RVV wants a preference to become mandatory, implement/test it in deterministic code/configuration.

Typical soft dimensions include participation balance, hosting distribution, temporal spacing, opponent diversity/repetition, travel and source uncertainty.

Participation targets are a **strong operational goal with bounded, evidenced relaxation**, not an ordinary low-priority soft preference and not a hard legality boundary:

- Python resolves the configured per-half/season targets, minimizes deviation, and classifies every material deviation as `avoidable`, `proven_infeasible`, `bounded_search_exhausted` or `operator_accepted` (see `participation_targets.py`);
- a known-avoidable participation regression must not be selected merely to improve a lower-priority quality metric (travel, opponent diversity, start time, ...);
- `bounded_search_exhausted` is **not** proof of unavoidability: request another bounded search or escalate before accepting it, and never call it `proven_infeasible`;
- `operator_accepted` is an explicit, durable operator decision (persisted during promoted-season maintenance), never a planner outcome; it is scope/target/magnitude-bound and never edits the configured target;
- cross-half compensation (`2 + 4` for a `3 + 3` target) is legal and is recognized as season-total-complete with an explicit half-distribution deviation;
- an explicit `participation_hard_max` is a separate, genuinely hard rule and is never inferred from the target. Ordinary bounded target deviation never requires an operator waiver.

Before switching between pipeline work and promoted-season maintenance, keep their state scopes explicit. An unpromoted Stage 3/Stage 4 candidate in `.pipeline` may differ from `season/<season>/schedule.json`. `season findings` / `season repair-options` / `season search` always describe the promoted canonical season, not the just-exported candidate. Until promotion, analyze the candidate through its DecisionContext/Stage3Session, verifier/evidence bundle, audit context and export artifacts; never blend counts/findings from the two fingerprints.

When the semantic audit finds a localized defect in a finalized but unpromoted candidate, refine that exact candidate instead of promoting merely to unlock repair, resetting Stage 3 (a recovery operation) or rerunning Stage 1-4. `scripts/rvv-miniputt stage3 refine` reopens the reviewed candidate through the explicit `refine_candidate` session transition, exposes finding-directed repair options from the same repository-owned providers, applies one verified option as a new candidate revision, re-finalizes and re-runs Stage 4 with provenance to the superseded export. Inspect findings/options with `stage3 refine --finding <id>` (omit `--option-id`) and apply with `stage3 refine --finding <id> --option-id <id>`. Stage 1/2 evidence and fingerprints are untouched; the reviewed export is kept as immutable history and marked superseded (a published export is refused -- that is the publication/rollback boundary). The command does not perform the semantic audit itself: re-run `operator audit-context`/`audit-submit` over the new export before publication (`audit_required` is reported with the fresh fingerprint).

`REVIEW_REQUIRED` is feedback, not automatically an operator escalation. While repository-owned findings still map to a supported repair/search direction, do not ask the operator merely because another safe automatic refinement step exists: run the bounded Pareto-convergence outer loop, which continues refining the exact reviewed candidate instead of stopping at the first finding.

```bash
scripts/rvv-miniputt stage3 converge --work-dir .pipeline --json
scripts/rvv-miniputt stage3 converge --work-dir .pipeline --finding <id> --max-epochs 2 --json
scripts/rvv-miniputt stage3 converge --work-dir .pipeline --dry-run --json
```

Each epoch addresses one actionable finding direction, measures the provider options on the shared objective vector, retains every independently verified non-dominated candidate in a bounded frontier and commits one accepted mutation as a new candidate revision (independently verified and persisted, **without** a Stage 4 bundle). Internal epochs are planning state, not review handoffs: `stage3 converge` materializes exactly one timestamped Stage 4 export at the batch/audit boundary, and only then does the workflow return to `audit_required` for the new fingerprint. A dominated option is never committed; `search_incomplete` keeps exploring supported untried dimensions instead of declaring convergence; a later worse attempt cannot make an earlier non-dominated candidate unselectable. Every finding family (hosting, participation, manual placement, movable capacity, hard violation) now carries the same `search_coverage` view, so a direction whose configured bounded search was actually run and found nothing reports `bounded_search_exhausted` (never `proven_infeasible`) instead of masquerading as untried until the epoch budget runs out. The coverage record also carries a search capability/version/fingerprint: `bounded_search_exhausted` describes only the search that produced it, so when the repair neighborhood changes (a larger date/time cap, a new dimension, a changed roster heuristic) the recorded exhaustion becomes stale and the finding is eligible again for the new search instead of being trusted as current. The convergence report exposes the retained frontier ranked by material audit priority (unresolved placements first) as `review_frontier`/`recommended_review_candidate_ref` and records the chosen `review_selection`, so the review handoff is an explicit comparison rather than the last mutation; `stage3 converge --review-candidate <ref>` (or `stage3 adopt --candidate-ref <ref>`) adopts a retained candidate deliberately. Any retained non-dominated frontier candidate stays selectable with `stage3 adopt --candidate-ref <ref>`, which re-validates it against the current Stage 1/2 facts and the current hard verifier and re-exports it as a new revision (rejected as stale if either changed). Stop only on an explicit terminal reason: `pass`, `operator_required` (a genuine policy/waiver/input question), `bounded_search_exhausted`, or a bounded plateau/budget (`pareto_stable`/`bounded_budget_exhausted`). A fixed epoch/attempt budget is bounded convergence, never proof that human input is required, and no result is described as globally Pareto-optimal unless the search space is actually exhaustive. Inspect the retained frontier and terminal state with `stage3 session --json` (`pareto_archive`, `pareto_frontier_refs`, `convergence`); use `--dry-run` to preview the next epoch without mutating. The convergence state lives on the same Stage3Session, so do not keep a harness-local frontier or attempt counter.

The semantic audit is bridged into the same loop rather than treated as a terminal verdict. `operator audit-submit` on a `REVIEW_REQUIRED` result automatically enters bounded convergence over the reviewed candidate (use `--no-refine` to opt out), and `operator audit-run` loops audit → bounded refinement → re-audit within its bounded rounds. `stage3 converge` auto-reads a *fresh* persisted audit result for the current export (`--ignore-audit` to opt out): a material audit finding whose checklist item maps to a direction the repository currently has an actionable finding for is covered by the normal loop, while an open-ended item (8/9) or a mapped direction the repository cannot act on becomes an explicit operator question. Do not search blindly on behalf of an audit finding the repository cannot act on, and do not silently drop it either.

The audit → convergence → re-audit outer loop is an explicit **persisted workflow phase**, not a harness convention. A finalized Stage 4 export is `audit_required` for its exact export fingerprint; a `REVIEW_REQUIRED` verdict becomes `convergence_required`; internal convergence revisions stay `convergence_required` (the prior audit is not invalidated), and the batch boundary materializes one Stage 4 handoff that returns the workflow to `audit_required` for the new fingerprint. A batch that committed revisions but could not materialize a handoff (`--no-export`, or a failed export that leaves the verified candidate persisted) reports `export_required` and stays pending rather than binding an audit to the stale export. Only `PASS` or an explicit terminal (`pareto_stable`, `bounded_search_exhausted`, `operator_required`) with no owed handoff reaches `complete`. The phase lives on the same Stage3Session and is reported (with its canonical `next_command`) by `operator audit-context`, `stage3 converge`, `operator audit-submit`, `stage3 session` and the Stage 4 `DecisionContext`. A run must not be summarized as complete while `audit_required` or `convergence_required` is pending, and a stale audit for a superseded export can never satisfy the newer one. Do not ask the operator whether to refine while a safe automatic continuation (`next_command`) remains; human interaction is only appropriate at a genuine `operator_required` boundary.

A plain `run` must not silently invalidate that reviewed candidate. While a finalized, exported, unpromoted candidate exists, `run --interactive --resume-from 1` (and a non-interactive full run) is refused before Stage 1 restarts and before the Stage 3 session is cleared; the refusal points to `stage3 refine` for ordinary improvement and requires an explicit `run --new-full-run` for a deliberately new full run. Use `--new-full-run` only when a genuinely fresh pipeline run is intended, and never as a way to work around an audit finding.

Within one Stage3Session the interactive Stage 3 loop retains a bounded portfolio of verified attempts with stable refs (`stage3_interactive:attempt_N`, `pareto:N:i`, `stage3_cp_sat:*`); a later worse attempt does not make an earlier good attempt unselectable. The DecisionContext exposes the retained refs (`facts.retained_candidates`, the `apply_candidate` enum) and `apply_candidate(candidate_ref=...)` adopts any retained attempt. Adoption re-validates the retained attempt against the current Stage 1/2 facts identity and the current hard verifier (locks/decisions included), and rejects it as stale if either changed; do not reset/re-solve merely to recover an already verified attempt.

Once a season has been promoted, Stage 3 is baseline-aware by default. A normal planning run whose window matches canonical state adopts that schedule as its baseline instead of regenerating it:

- approved/placement-locked tournaments are hard-preserve constraints;
- unapproved tournaments remain optimizable **while the season is still in the `promoted` state**, but weighted change cost is part of the search objective so prefer the smallest justified change;
- use `season replan` around the canonical baseline, inspect `season diff`, and persist only through verified `season apply`;
- a rejected candidate must leave `schedule.json` and `decisions.json` unchanged.

Once the season is `published_sealed`, that baseline-aware optimization is no longer part of normal operation: work only through explicit, audited canonical maintenance operations. A full/new pipeline run whose window matches a sealed season is refused, and `season replan`/planner `apply`/mutating normalization/`promote --force` are refused at the service boundary. Use `season reopen-planning` only for a deliberate, operator-authorised fundamental restructuring.

Interactive Stage 3 is one explicit persisted session with candidate revisions. Inspect it with:

```bash
scripts/rvv-miniputt stage3 session --work-dir .pipeline --json
```

The view is authoritative for the active run's status, current candidate revision/fingerprint, pending decision, resolved run-scoped decisions, finalized revision/fingerprint, `search_history` and legal next transitions. Use it to debug a resume problem instead of reconciling several JSON files.

A pending candidate-scoped decision (arena/time conflict, local repair, attempt comparison) refers to the current attempt revision, so answer it against that exact fingerprint; the adopted plan is retained separately as the session baseline that `keep_baseline` restores.

Escalating a candidate-scoped decision with `request_operator` (which requires a `question`) pauses the session on that exact current revision/fingerprint: it never restores the baseline, rewrites the checkpoint or finalizes Stage 3. The pause and question are inspectable from `stage3 session`. After the operator answers, continue from the same revision. When a hard-valid optimized attempt is the current candidate and `keep_baseline` would restore the previous attempt, retain the current attempt explicitly with `apply_candidate` against the context's `candidate_ref`; do not use `keep_baseline` to mean "accept the work I just saw".

Stage 3 continuation is evidence- and strategy-driven, not attempt-count-driven. A raw number of attempts never removes `optimize_plan`, and the returned `facts.search_history` summarizes what has already been tried (actions used, unique action signatures, candidate revisions, hard violations before/now, repeated no-progress actions, last actions) so you can choose a genuinely different repair family, search scope, roster/date hypothesis or evidence investigation, or accept/escalate/abort when continuing has no useful direction. Deterministic loop safety still applies: a stale candidate action is rejected, and repeating the exact same action against the exact same candidate after it made no progress is rejected as `repeated_no_progress_action`. The only attempt-count bound is a generous emergency circuit breaker surfaced as a technical safety failure (runaway/broken orchestration), never as proof that the season is unsolvable.

Approval/lock state lives in `decisions.json`, separate from schedule facts. `season approve` re-verifies the current placement; approval is never a waiver of hard rules. `season unapprove` restores editability. `season approvals` lists status. If protected scheduling facts change, the stored fingerprint becomes a deterministic `stale_approval`, its lock is dropped, and explicit reapproval is required.

### Stage 4 — export/review

Stage 4 re-verifies the selected candidate before serialization.

Agent policy:

- hard verification failure blocks export/publication;
- review manual arena/hosting/calendar follow-up separately from plan-quality warnings;
- generated output is derived data: correct source/config/code/canonical state and regenerate rather than permanently patching HTML/CSV/Excel/iCal;
- use the Stage 4 `output_files` map to know what the run actually produced;
- after canonical season state changes through move/apply/approve/unapprove, regenerate the exact current projection with `season export` before audit/publication.

## Stage gating policy

The repository's `DecisionContext` is authoritative. A hard violation always blocks `proceed`.

### Stage 1

- `proceed` when at least one calendar source is configured and the date range is a realistic hockey season window;
- `abort` when no sources are configured or the date range is clearly wrong.

### Stage 2

- `proceed` when enough configured sources have usable evidence for meaningful planning;
- prefer repository-exposed retry/recovery actions for suspicious blocked sources;
- `abort` when missing/blocked source evidence makes planning meaningless.

### Stage 3

- `proceed` when the plan is structurally plausible and hard-valid;
- `abort` when the plan is empty/obviously wrong because of upstream/configuration failure;
- planning-quality tradeoffs remain contextual soft judgment once hard validity is satisfied.

Do not introduce a fixed threshold or magic weight in the runbook to solve a one-off tradeoff.

## Structured decision protocol

`run --interactive` returns a `DecisionContext` containing facts, hard violations, warnings, metrics, available actions and argument/template information.

For each pause:

1. read the current context;
2. if a hard violation exists, do not bypass it;
3. choose exactly one returned available action;
4. use only the action's declared argument shape;
5. submit a concise operational rationale;
6. invoke the next canonical command and reassess the new context.

Transport rule: fill the returned `decision_action_template` and pass the **entire resulting JSON object** to `--decision-action` (or `--decision-action-file`). `rationale` is inside that JSON object, and every action-specific parameter is nested under `arguments`; never invent separate flags such as `--rationale` or action-specific CLI options.

Use `.agents/commands/rvv-miniputt/run.md` for the explicit resume contract. Do not infer `--resume-from` from intuition. Do not persist or request hidden/private reasoning; durable records need only the action, relevant facts/outcome and concise rationale.

## Semantic safety-net audit

After every Stage 4/canonical export and before publication, an interactive harness must perform the semantic safety-net audit itself using the active conversation/model. Do not implement a second harness-local model call or audit engine.

Start with the bounded overview:

```bash
scripts/rvv-miniputt operator audit-context
```

The default context contains counts, distributions, worst/top-N examples, fingerprints and an evidence index, not the whole evidence universe. Pull exact detail only where needed through the canonical query capability:

```bash
scripts/rvv-miniputt operator audit-evidence --item 2
scripts/rvv-miniputt operator audit-evidence --tournament <durable-id>
scripts/rvv-miniputt operator audit-evidence --club Kongsberg
scripts/rvv-miniputt operator audit-evidence --category participation_shortfalls
scripts/rvv-miniputt operator audit-evidence --unresolved
```

Query results are bound to the same run/export fingerprint as the overview. Never combine stale evidence from another export.

Audit checklist:

1. Antall cuper pr lag?
2. Antall hjemmeturneringer pr lag?
3. Lengde på turneringer? (Bekreft at eksportert/sluttid bruker nøyaktig konfigurert `ice_time_minutes`, og at denne samtidig dekker rundene + 5 min overgang pr runde samt eventuelle minimumskrav til booket istid.)
4. Er det faktisk ledig tid på is? (For hele denne konfigurerte bookingperioden.)
5. Deltar vertsklubben i samme turnering?
6. Deltar hvert lag maksimalt én gang per dag?
7. Er det normalt maks 2 lag fra samme klubb, med 3 kun som synlig unntak?
8. Er eksportformatene konsistente?
9. Ser harnesset andre materielle problemer eller manglende regler vi ikke allerede har tenkt på?

Submit the harness verdict through `operator audit-submit`. The submitted payload must also carry a concise structured `operator_assessment`, so the operator-facing conclusion is bound to the same export/context fingerprint as the verdict rather than living only in terminal history:

```json
{
  "status": "PASS|REVIEW_REQUIRED|FAIL|INCOMPLETE",
  "export_fingerprint": "<from audit-context>",
  "operator_assessment": {
    "operator_summary": "2–4 sentences on whether the plan is usable and why.",
    "key_tradeoffs": [{"title": "...", "summary": "what was accepted and why", "severity": "info|minor|major"}],
    "remaining_actions": [{"title": "...", "summary": "what RVV/the club must still do", "category": "manual_placement|participation|hosting|..."}],
    "limitations": ["what the harness could not independently establish"]
  }
}
```

The repository persists the exact submitted assessment in fingerprint-bound `semantic_audit.json`. Generate the final chat/terminal summary from that same structured assessment so verdict, trade-offs, remaining actions and limitations cannot contradict the persisted audit. The default `season_plan.html` is a current deterministic season/operational view and must not present the revision-scoped Harness/LLM narrative as season truth; audit prose stays in the audit artifact and chat/terminal summary. If a generated season page still contains a “Vurdering fra planleggingsassistent” narrative section, treat that as the outstanding #330 projection regression rather than as part of the intended contract. Hard counts and validity still come only from deterministic repository evidence; the assessment is a conclusion, not a second rule engine. When no interactive harness is active, the documented headless `operator audit-run --backend <name>` path may call an LLM backend instead.

A harness `FAIL` or `REVIEW_REQUIRED` may occur even when deterministic checks pass. It can never override a deterministic hard `FAIL`; an incomplete audit is never `PASS`.

## Operator waivers for hard planning rules

Structural invariants such as corrupt serialization, invalid tournament identity or malformed data are never waivable. Explicitly classified hard planning rules may only be waived by an authorized operator for a precise scope.

The planner/optimizer/agent may suggest a waiver, but may never create, broaden or silently infer one. Canonical operator capability:

```bash
scripts/rvv-miniputt waiver list [--all]
scripts/rvv-miniputt waiver create --rule participation_hard_max_exceeded \
  --club "Frisk Asker" --team "Frisk Asker 4" --age-group U11 \
  --tournament <id> \
  --allowed-value 8 --reason "operator-approved exception"
scripts/rvv-miniputt waiver revoke <waiver-id> --reason "withdrawn"
```

A waiver is only relevant to a genuinely hard, explicit ceiling such as `participation_hard_max`. An ordinary bounded participation-target deviation is strong-goal evidence, not a hard rule, and does **not** require a waiver. A matching waiver is narrowly scoped and becomes invalid when relevant tournament/team/date/value facts leave that scope. Waived violations remain visible and downgrade publication readiness; do not use `--non-strict` or global cap changes as substitutes.

## Human escalation

Escalate when the repository actually requires human authority/information, for example:

- a real policy exception/change;
- credentials/MFA or an unavailable interactive access step;
- an impossible hard-constraint situation requiring organizer action;
- public publication or rollback approval.

Do not escalate merely because a safe repository action can be retried/refined automatically.

Human decision queue:

```bash
make questions
make answer ID=<id> ANSWER='<answer>'
make operator-run
```

## Publication

Planning/export does not imply publication. The export being published must represent the current canonical revision. If canonical schedule/decision state changed since the current export, run `season export` first and perform a fresh semantic audit. A successful publication records an immutable published baseline and seals the season (first publication) or appends a new publication revision; a publication of a sealed season is refused when canonical state no longer reconciles to the published baseline plus recorded canonical mutations, or when the export no longer matches the sealed canonical schedule. A legacy export without an embedded `schedule_projection` cannot auto-seal and is reported together with the `season seal-published` migration command.

Useful commands:

```bash
make audit-context
make audit-evidence ARGS='--item 2'
make audit-run BACKEND=<claude|openai|llm_bridge>
make audit-submit RESULT_FILE=<path>
make publish-preview
make publish CONFIRM_PUBLIC=1
make verify-publish
```

Publication creates a separate allowlisted public bundle. Review packets and Spond exports are private/review artifacts by default. Rollback is explicit:

```bash
make publish-history
make rollback RUN_ID=<id> CONFIRM_PUBLIC=1
```

## Related public workflows

The repository also manages:

```bash
make aktivitetskalender
make registered-teams CSV=<reviewed-registration-export.csv>
```

Their publishing variants use the same publication machinery but are not Stage 1–4 planner stages.

## Documentation ownership

Use these rather than creating overlapping notes:

- `README.md` — what the system does, inputs/outputs, normal operation;
- `docs/system-architecture.md` — current end-to-end boundaries;
- `docs/rvv-miniputt-pipeline.md` — Stage 1–4 and canonical-season workflow;
- `docs/rvv-miniputt-input-formats.md` — workbook/input contract;
- `docs/adr/` — durable architectural rationale;
- GitHub issues — unfinished implementation work.
