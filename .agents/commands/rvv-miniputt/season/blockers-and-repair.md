# RVV Miniputt: season — Publication blockers and localized repair

Load from `../season.md` when routed here. Read `AGENTS.md` and `.agents/skills/rvv/SKILL.md` first; the lifecycle boundary and inspection path in `../season.md` still apply.

## Report what genuinely blocks publication

Use the repository-owned aggregate query instead of combining findings, audit and publication JSON with ad-hoc shell/Python filters:

```bash
scripts/rvv-miniputt season blockers --season <season>
scripts/rvv-miniputt season blockers --season <season> --json
```

The report composes the current canonical findings/resolution classifications, sealed-baseline reconciliation, export-freshness requirement, deterministic publication scope and catalog-driven full-season audit. It separates affected-tournament holds (`genuine_blockers`), global safety blockers, missing prerequisites, accepted/non-blocking authoritative facts and unchanged diagnostic planning debt. A full-season audit `FAIL` remains visible under `diagnostic_audit`; it is not rewritten to `PASS` and does not by itself override an `ELIGIBLE` deterministic publication scope.

Exit codes are stable: `0` means the current deterministic scope is `ELIGIBLE` and global gates are clear; `1` means a tournament hold or global safety blocker exists; `2` means required evidence is unavailable/`NOT_CHECKABLE`. This is a read-only status query, not publication authorization, export parity for a future artifact, or permission to mutate the season.

## Repair a localized finding against the promoted season

Do not re-run Stage 1–4 merely to reach the repair capabilities when the canonical season already has a valid factual baseline. Ask the repository what is actually wrong on the current revision:

```bash
scripts/rvv-miniputt season findings --season <season>
```

Findings are fresh, revision-bound facts (recomputed from the canonical plan, never the snapshot promoted with the season). They include unresolved hosting obligations, hosting-balance deficits, manual placements, hard violations and participation strong-goal deviations with their `avoidability`. A tournament the planner marked as an unresolved manual placement is reported even when its provisional slot is calendar-free (the marker is a plan-level fact verification cannot rediscover); when the responsible host has trusted calendar evidence with host-controlled movable ice, that opportunity is additionally exposed as its own `movable_capacity` finding that carries `requires_host_confirmation: true`, so the ice can be selected directly. A genuine unplaced obligation (a tournament the slot search could not place, so it is planning work with no `Tournament`) is reported as an `unplaced_tournament_placement` finding whose `search_coverage` states which supported repair dimensions were attempted and which remain untried. `bounded_repair_exhausted` on the obligation describes only the planner search that ran; it is not by itself a claim that all meaningful repair dimensions are exhausted, and it stays actionable until a durable infeasibility proof (see **Prove a placement obligation infeasible**) marks it `proven_infeasible_with_current_capacity`. Findings are independent: address whichever one the operator cares about next; there is no repository-imposed queue.

For one selected finding, enumerate the repository's verified alternatives (direct/cheap options first, then coupled options):

```bash
scripts/rvv-miniputt season repair-options --season <season> --finding <finding-id>
```

When the cheap options are insufficient, request a bounded, finding-directed search that preserves unrelated tournaments and approvals:

```bash
scripts/rvv-miniputt season search --season <season> --finding <finding-id>
```

Apply exactly one verified option from the current canonical revision:

```bash
scripts/rvv-miniputt season apply-repair --season <season> \
  --option-id <id> \
  --expected-revision <revision> \
  [--finding <finding-id>]
```

Every option (including the `coupled_placement` and `movable_capacity` families) is checked against the repository-owned operational-acceptability predicate in addition to hard verification: relative to the current canonical baseline it may not *newly* introduce a `fixed_busy`/manual external-calendar placement, a host with an untrusted calendar, unresolved/manual placement work, or a host-confirmation (`movable_busy`) dependency. Such options carry `operational_acceptable: false` and `requires_operational_opt_in`, are listed under `pareto.operational_rejected_option_ids`, and are kept out of the auto-applicable `pareto.non_dominated_option_ids`. Pareto scoring alone is not protection -- an option that fixes one defect while introducing a manual placement can remain non-dominated.

The apply boundary re-runs the predicate independently of the provider: a provider self-report never bypasses it. A refused option returns `reason: operational_acceptability_regression` with `required_opt_in_flags` and leaves canonical state byte-unchanged. Only when the operator explicitly accepts the provisional/manual placement, repeat the apply with `--allow-manual-placement` and/or `--allow-host-confirmation` (also accepted by `season repair-options`/`search` to keep such options on the reported Pareto front). Do not use the opt-in to make an ordinary automatic improvement look acceptable.

`--expected-revision` is the `revision` reported by `season findings`/`repair-options`. An apply against a changed revision is rejected as stale and leaves canonical state byte-unchanged. A successful apply is atomic, full-season verified, returns the new revision and a deterministic before/after delta, and re-derives findings from the new revision. For an `unplaced_tournament_placement` finding, `repair-options`/`search` return materialization options that build a real verified `Tournament` (the responsible host's arena, a verified date/start time, the final roster and regenerated games) and remove the obligation; the escalating dimensions are same-date start time, same-host date, bounded participant reselection, capacity release (move a scheduled tournament off the shared arena/time, including across age groups) and the full bounded neighborhood. Applying one commits the placement (and any paired blocker move) atomically. The finding's `search_coverage` distinguishes `option_available`/`search_incomplete`/`bounded_search_exhausted`; it reports `proven_infeasible` only when a current durable, capacity-bound proof was recorded (see **Prove a placement obligation infeasible**). Without such a proof, when dimensions remain untried, request `season search` rather than treating the obligation as infeasible. A supported dimension whose hard precondition is unmet for this obligation (participant reselection when the roster collides on no tried date) is reported under `inapplicable`, not `untried`, so it never keeps the finding `search_incomplete` and requestable forever; reselection applicability is checked against the source date and every alternate date it would run on. A bounded `search` option carries its own dimension tag, so applying it does not require repeating the `--dimensions` it was produced with. Hosting responsibility stays authoritative: a repair may draw down a deficit only from a surplus, never by relocating the shortfall or transferring burden to a club that does not owe it. A `bounded_search_exhausted` participation deviation is never proof of infeasibility. Respect the repair-cost order: when a tournament's host/date/arena/start time are already valid and only the selected roster conflicts, the first options are verified placement-preserving participant substitutions -- do not move the slot or request a broader search while such a substitution exists.

When a club's own tournaments are clustered (a `temporal_clustering` finding), `repair-options`/`search` enumerate a bounded `coupled_placement` family: a cross-age placement exchange plus, where the exchange double-books a displaced tournament's team, a bounded **same-age** participant reselection for that tournament with regenerated games. The candidate is the fully verified coupled state, so a placement-only intermediate conflict does not reject a valid repair. Cross-age applies only to the *placement exchange*; participants never cross age groups, and a candidate that moves hosting responsibility is rejected. The option's consequence evidence covers every team whose schedule changed -- both moved tournaments' participants and any team the roster repair added or removed -- and an option that materially regresses any of them (new <7/<14-day gap, new participation shortfall, materially worse coverage/opponent repetition/travel) reports `consequence_acceptable: false`, is listed under `pareto.consequence_rejected_option_ids`, kept off `pareto.non_dominated_option_ids` and refused at apply with `reason: team_schedule_regression`. `season apply-repair` is still the only apply path. A `search` that finds nothing reports `bounded_search_exhausted`, not `proven_infeasible`. Apply the selected option through the ordinary `season apply-repair` boundary -- the coupled provider introduces no separate apply/persistence path.

`repair-options`/`search` measure every returned option on one canonical objective vector (hard violations, unresolved hosting obligations, hosting-balance imbalances, manual placements, participation deviations and avoidable deviations, host-confirmation dependencies, and changed-tournament count, merged with the shared Stage-3 soft-quality objectives and canonical travel) and report a bounded non-dominated `pareto` set (`non_dominated_option_ids` plus a small `representative_option_ids` down-select). Each option also carries `quality_vs_current` and `travel`, and the apply delta returns the same quality/travel before/after evidence. Prefer a non-dominated option; a verified option that is strictly worse everywhere than another is not an independent trade-off and must not be presented as one.

### Prove a placement obligation infeasible

When every supported repair dimension has been walked for a genuine unplaced obligation and no legal placement exists under the current authoritative capacity, retain that as durable, obligation-specific evidence instead of an eternal actionable blocker:

```bash
scripts/rvv-miniputt season record-infeasibility --season <season> \
  [--finding <finding-id> ...] \
  [--expected-revision <revision>] \
  [--actor <id>] [--note <why>]
scripts/rvv-miniputt season infeasibility-report --season <season>
```

`record-infeasibility` reruns the repository-owned bounded placement search for the selected obligations (all unresolved obligations by default), and persists a proof only for an obligation whose full declared scope was covered with zero feasible candidates. The proof records the obligation identity, the authoritative capacity/calendar fingerprint, the search capability/version/scope, candidate/rejection counts and the deterministic rejection reasons; it is a decision-only, revision-bound canonical write (it never moves or removes a tournament). `season findings` then attaches the proof: a *current* proof classifies the finding as `proven_infeasible_with_current_capacity` (visible but non-blocking), while a proof whose capacity/calendar fingerprint or search capability changed is stale and the obligation reopens automatically as actionable. `bounded_search_exhausted` without a recorded proof stays actionable, and the provenance is the evidence, not the absence of options. `--dry-run` runs the search and reports what would be recorded without writing. `release-infeasibility` supersedes the active proofs explicitly so the obligations reopen.

This is capacity-scarcity evidence, never a waiver: do not move the obligation to an alternate host, shorten accepted bookings, fabricate capacity, add a tournament-id allowlist, or suppress the finding merely to clear audit. `hosting_responsibility` derives its proportional targets from the planned season shape (a cancelled tournament and an unresolved obligation still count toward the responsible host's target), so placing or proving an obligation never silently re-proportions hosting burden onto another club.

### Accept a participation deviation explicitly

When the operator deliberately decides to live with a remaining participation strong-goal deviation, persist that decision instead of leaving it as an unresolved finding or editing the target:

```bash
scripts/rvv-miniputt season accept-deviation --season <season> \
  --finding <participation-finding-id> \
  --note "<why this deviation is accepted>"
```

The acceptance is stored as an `operator_accepted` decision in `decisions.json` and injected into the same independent verifier, so the deviation stays visible with `avoidability: operator_accepted` but is no longer treated as an open finding. It never changes the schedule or the configured target. It is bounded to its scope, target and accepted deviation: if the target later changes or the deviation gets worse, the acceptance stops applying and the finding re-appears with `acceptance_stale: true`. Reopen it explicitly with:

```bash
scripts/rvv-miniputt season revoke-acceptance --season <season> \
  --finding <participation-finding-id> \
  --note "<reason>"
```

Do not use acceptance to hide an `avoidable` deviation that a bounded search can still improve; prefer `repair-options`/`search` first.

