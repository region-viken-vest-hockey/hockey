# Scheduling rule catalog

> **Generated** from `tournament_scheduler/rule_catalog.py` by
> `python3 scripts/render-rule-catalog.py`. Do not edit by hand.

This is the one compact, searchable answer to *what scheduling semantics exist, who
owns each one, and which semantics take precedence when they interact*. It is metadata
and navigation only: the deterministic implementations named by each entry remain the
executable truth.

Search this page for a rule ID (for example `host_representation`,
`hosting_age_group_coverage`, `hosting_proportional_balance` or
`arena_interval_non_overlap`) to find the owning facade, its verifier and its tests
before editing code.

## Stable identity in deterministic outputs

The catalog is the one source of rule/objective identity, and the semantic/application
boundaries reuse it instead of re-deriving prose:

* verifier violations carry `rule_id` (attached by `annotate_violations`);
* `season findings` carry `rule_id` per finding plus a `counts_by_rule_id` summary
  (attached by `annotate_findings`);
* quality/score metrics in `compare_quality_scores` carry `objective_id` for every
  registered score path;
* the Regler/rules model carries `catalog_id` per per-run row.

`rule_id_for_verifier_code`, `rule_id_for_finding_code` and `rule_id_for_score_path`
own the lookups; callers must not hard-code a second mapping.

## How to use this catalog

Before introducing a new rule because of a production failure, determine which case it is:

1. **Does a catalog entry already describe the intended semantic?** Fix the owning
   implementation/conformance; do not add another rule.
2. **Is the semantic correct but no legal repair is exposed?** Add an action/search
   capability under the existing `local_repair_options` boundary, not another hard rule.
3. **Is the candidate correct but state/resume is wrong?** Route to the stage-3
   session/lifecycle owner (not a hockey rule).
4. **Is the candidate correct but evidence/reporting is wrong?** Fix the evidence/report
   projection.
5. **Is this genuinely new operator policy?** Add/catalog it deliberately with
   classification, source and generic tests.

Production clubs/dates remain regression fixtures, never rule identities.

For hosting anomalies specifically, check in this order:

```text
Is club x age-group coverage satisfied or structurally impossible?
  -> Is assigned responsibility consistent with coverage + proportional target?
     -> Was responsibility preserved through placement/repair/search?
        -> Only then inspect lower-priority balance/placement quality.
```

## Classification vocabulary

| Classification | Meaning |
|---|---|
| `hard_constraint` | Legality boundary; a violation blocks validity (waivable only where declared). |
| `operational_obligation` | Must be satisfied or surfaced as unresolved manual work. |
| `soft_objective` | Optimized, but never justifies regressing a higher-priority obligation. |
| `operator_decision` | A deliberate operator/agent choice among valid alternatives. |
| `fact_evidence_semantic` | A classification/evidence fact feeding rules, not a rule itself. |

## Precedence

The catalog declares only the precedence that actually matters. For hosting:

```text
hard legality / host_representation
        -> hosting_age_group_coverage
           -> hosting_proportional_balance
              -> placement convenience / calendar quality
```

and responsibility preservation applies once a target is assigned:

```text
hosting target -> hosting_responsibility -> placement / repair / search
```

An agent must not compare two candidates solely by a lower-priority metric when one
candidate regresses a higher-priority operational obligation.

| Higher priority | Must precede |
|---|---|
| `host_representation` | `hosting_age_group_coverage` |
| `host_representation` | `hosting_proportional_balance` |
| `hosting_age_group_coverage` | `hosting_proportional_balance` |

## Hard constraints

| Rule ID | Meaning | Canonical owner | Verifier / measurement | Tests |
|---|---|---|---|---|
| `team_age_group_exact` | Every team participating in a tournament belongs to that tournament's age group. | `tournament_scheduler.planning_contract` | `tournament_scheduler.planning_contract.verify_candidate` | `tests/test_planning_contract.py` |
| `team_unique_in_tournament` | A team may appear at most once in a single tournament. | `tournament_scheduler.planning_contract` | `tournament_scheduler.planning_contract.verify_candidate` | `tests/test_planning_contract.py` |
| `team_unique_per_date` | A team may not be scheduled in two different tournaments on the same date. | `tournament_scheduler.planning_contract` | `tournament_scheduler.planning_contract.verify_candidate` | `tests/test_planning_contract.py` |
| `tournament_roster_shape` | A tournament has an admissible, avoidable-by-free participant shape: even teams and no pause/bye rounds (or the exact age-group team count where configured). An input-constrained scarce shape is surfaced separately, never as a soft preference. | `tournament_scheduler.effective_tournament_shape` | `tournament_scheduler.planning_contract.verify_candidate` | `tests/test_effective_tournament_shape.py`, `tests/test_planning_contract.py`, `tests/test_participant_removal.py` |
| `club_hard_max` | At most three teams from one club may participate in one tournament. | `tournament_scheduler.planning_contract` | `tournament_scheduler.planning_contract.verify_candidate` | `tests/test_planning_contract.py` |
| `tournament_ice_booking_duration` | A tournament's configured ice_time_minutes is the complete hall occupancy window. It must be at least the actual rounds times round length plus the per-round changeover buffer, and any governing per-series-round booking floor. | `tournament_scheduler.occupancy` | `tournament_scheduler.planning_contract.verify_candidate` | `tests/test_occupancy.py`, `tests/test_planning_contract.py`, `tests/test_stage1_config.py` |
| `arena_interval_non_overlap` | Two tournaments must never require the same arena in overlapping datetime intervals. A failure to evaluate the interval data is itself a blocking verification result, not a silent pass. | `tournament_scheduler.arena_conflicts` | `tournament_scheduler.planning_contract.verify_candidate` | `tests/test_arena_conflicts.py`, `tests/test_planning_contract.py` |
| `tournament_capacity` | A tournament must not exceed its configured participant capacity. | `tournament_scheduler.planning_contract` | `tournament_scheduler.planning_contract.verify_candidate` | `tests/test_planning_contract.py` |
| `tournament_min_size` | A tournament with enough registered teams in its age group must have at least three participants. | `tournament_scheduler.final_verification` | `tournament_scheduler.final_verification.verify_final_candidate` | `tests/test_final_verification.py` |
| `parallel_capacity` | A tournament must not schedule more concurrent games than the configured parallel capacity. | `tournament_scheduler.final_verification` | `tournament_scheduler.final_verification.verify_final_candidate` | `tests/test_final_verification.py` |
| `registered_teams_only` | Every RVV participant of a tournament must exist in the registered roster (guests are explicit, separate places). | `tournament_scheduler.planning_contract` | `tournament_scheduler.planning_contract.verify_candidate` | `tests/test_planning_contract.py` |
| `valid_tournament_date` | A tournament must carry a parseable date; a missing/invalid date is structural corruption. | `tournament_scheduler.planning_contract` | `tournament_scheduler.planning_contract.verify_candidate` | `tests/test_planning_contract.py` |
| `date_within_window` | Every tournament date lies inside the configured planning/season window. | `tournament_scheduler.planning_contract` | `tournament_scheduler.planning_contract.verify_candidate` | `tests/test_planning_contract.py` |
| `holiday_date_admissible` | Tournaments must not be scheduled on canonically excluded holiday dates for the planning window. | `tournament_scheduler.date_policy` | `tournament_scheduler.planning_contract.verify_candidate` | `tests/test_holiday_date_policy.py` |
| `banned_dates_not_used` | Tournaments must not be scheduled on an operator-banned global date. | `tournament_scheduler.canonical_banned_dates` | `tournament_scheduler.planning_contract.verify_candidate` | `tests/test_canonical_banned_dates.py` |
| `excluded_host_club_not_used` | A club explicitly excluded as host for a scope must not host there. | `tournament_scheduler.planning_contract` | `tournament_scheduler.planning_contract.verify_candidate` | `tests/test_planning_contract.py` |
| `host_representation` | A tournament's host club must be represented by its own participating team (shared/joint registrations count for either constituent). This is the hard representation invariant; proportional hosting balance is a separate soft objective. | `tournament_scheduler.host_representation` | `tournament_scheduler.planning_contract.verify_candidate` | `tests/test_stage3_optimizer_host_representation.py`, `tests/test_host_team_missing_repair.py` |
| `locked_date_preserved` | A canonical locked date must still have a scheduled tournament. | `tournament_scheduler.canonical_baseline` | `tournament_scheduler.planning_contract.verify_candidate` | `tests/test_canonical_baseline.py` |
| `pinned_tournament_preserved` | A pinned tournament id must remain present in the candidate. | `tournament_scheduler.canonical_baseline` | `tournament_scheduler.planning_contract.verify_candidate` | `tests/test_canonical_baseline.py` |
| `canonical_locked_tournament_preserved` | An approved/placement-locked canonical tournament must not be dropped by replanning. | `tournament_scheduler.canonical_baseline` | `tournament_scheduler.canonical_baseline.verify_canonical_locks` | `tests/test_canonical_baseline.py` |
| `canonical_placement_preserved` | A placement-locked canonical tournament keeps its protected placement fields. | `tournament_scheduler.canonical_baseline` | `tournament_scheduler.canonical_baseline.verify_canonical_locks` | `tests/test_canonical_baseline.py`, `tests/test_approval_lifecycle.py` |
| `canonical_participants_preserved` | A participant-locked canonical tournament keeps its protected participant list. | `tournament_scheduler.canonical_baseline` | `tournament_scheduler.canonical_baseline.verify_canonical_locks` | `tests/test_canonical_baseline.py`, `tests/test_approval_lifecycle.py` |
| `participation_hard_max` | An explicitly configured participation hard maximum is a legality boundary and is operator-waivable. It is separate from the ordinary participation target, which is a soft objective and never a hard cap. | `tournament_scheduler.participation_targets` | `tournament_scheduler.planning_contract.verify_candidate` | `tests/test_participation_targets.py`, `tests/test_operator_waivers.py` |
| `game_team_membership` | Every exported game is between two declared participants of its tournament. | `tournament_scheduler.final_verification` | `tournament_scheduler.final_verification.verify_final_candidate` | `tests/test_final_verification.py` |
| `valid_game_record` | Every exported game record is a well-formed object with distinct participants. | `tournament_scheduler.final_verification` | `tournament_scheduler.final_verification.verify_final_candidate` | `tests/test_final_verification.py` |
| `game_round_integrity` | Every exported game has a valid positive round number. | `tournament_scheduler.final_verification` | `tournament_scheduler.final_verification.verify_final_candidate` | `tests/test_final_verification.py` |
| `game_integrity_ambiguous_participants` | A tournament's participant labels must be present and unique before game integrity can be judged. | `tournament_scheduler.final_verification` | `tournament_scheduler.final_verification.verify_final_candidate` | `tests/test_final_verification.py` |
| `round_robin_pairing` | A round-robin tournament plays every required pairing exactly once. | `tournament_scheduler.final_verification` | `tournament_scheduler.final_verification.verify_final_candidate` | `tests/test_final_verification.py` |
| `team_unique_per_round` | A team plays at most one game per round. | `tournament_scheduler.final_verification` | `tournament_scheduler.final_verification.verify_final_candidate` | `tests/test_final_verification.py` |
| `tournament_round_count` | A limited-rounds tournament produces exactly the effective configured round count. | `tournament_scheduler.final_verification` | `tournament_scheduler.final_verification.verify_final_candidate` | `tests/test_final_verification.py` |
| `intra_club_game_minimum` | Same-club matchups are limited to the minimum required by the limited-rounds format. | `tournament_scheduler.final_verification` | `tournament_scheduler.final_verification.verify_final_candidate` | `tests/test_final_verification.py` |
| `request_team_unavailable` | A durable operator request constraint: the team must not play inside its declared unavailable date range. | `tournament_scheduler.request_constraints` | `tournament_scheduler.request_constraints.request_constraint_violations` | `tests/test_request_constraints.py` |
| `request_minimum_gap` | A durable operator request constraint: the team must keep at least the requested days between tournaments. | `tournament_scheduler.request_constraints` | `tournament_scheduler.request_constraints.request_constraint_violations` | `tests/test_request_constraints.py` |
| `request_opponent_avoidance` | A durable operator request constraint: two teams must not meet inside the declared date range. | `tournament_scheduler.request_constraints` | `tournament_scheduler.request_constraints.request_constraint_violations` | `tests/test_request_constraints.py` |

## Operational obligations

| Rule ID | Meaning | Canonical owner | Verifier / measurement | Tests |
|---|---|---|---|---|
| `hosting_age_group_coverage` | For every (club, age_group) with at least one registered team, the club receives at least one hosting responsibility in that age group during the season whenever the number of tournaments makes it mathematically possible. This is club x age-group coverage, not aggregate club hosting. A structural shortfall (fewer tournaments than clubs needing coverage) is surfaced explicitly, never hidden as a soft imbalance. | `tournament_scheduler.hosting_coverage.hosting_targets_with_coverage_floor` | `tournament_scheduler.hosting_coverage.hosting_coverage_matrix` | `tests/test_hosting_coverage.py`, `tests/test_hosting_same_age_repair.py` |
| `hosting_responsibility` | Once a hosting responsibility is assigned, calendar convenience must not silently transfer it to another club. If the responsible host has no verified automatic slot, the responsibility is preserved and the tournament becomes MANUAL PLACEMENT REQUIRED rather than moving the burden. This is a cross-path invariant over placement/repair/search. | `tournament_scheduler.hosting_responsibility` | `tournament_scheduler.hosting_responsibility.hosting_responsibility_facts` | `tests/test_hosting_responsibility.py` |
| `tournament_placement_obligation` | A scheduled tournament's host is chosen among its participants; when no legal and trustworthy slot exists for the responsible host, the obligation stays unresolved for manual placement instead of being given to an unrelated club. | `tournament_scheduler.placement_normalization` | `tournament_scheduler.planning_contract.verify_candidate` | `tests/test_placement_normalization.py`, `tests/test_unplaced_placement_repair.py` |
| `guest_reservation_integrity` | A reserved guest place is a deliberate capacity reservation that counts toward capacity/ice-time shape but never toward RVV participation, hosting, fairness or travel; participant optimization/repair cannot consume it. | `tournament_scheduler.guest_slots` | `tournament_scheduler.planning_contract.verify_candidate` | `tests/test_guest_slots.py` |

## Soft objectives

| Rule ID | Meaning | Canonical owner | Verifier / measurement | Tests |
|---|---|---|---|---|
| `hosting_proportional_balance` | After the age-group coverage floor is satisfied (or a structural shortfall is explicitly surfaced), remaining hosting responsibility is distributed roughly in proportion to registered team counts. This objective is secondary to coverage: a larger club may legitimately under-host relative to its proportional target so a smaller club gets its first responsibility. | `tournament_scheduler.hosting_coverage.hosting_balance_matrix` | `tournament_scheduler.hosting_coverage.material_hosting_balance_imbalances` | `tests/test_hosting_coverage.py` |
| `participation_target` | The configured per-team/per-half tournament participation target is a desired optimization goal with evidenced relaxation, not an unconditional obligation or hard bound. Attainment is optimized within real season capacity; a capacity-constrained miss is not itself a planning failure. | `tournament_scheduler.participation_targets` | `tournament_scheduler.participation_targets.evaluate_participation` | `tests/test_participation_targets.py` |
| `intra_club_participation_distribution` | Within a multi-team club and age group, participation is rotated evenly across the club's sibling team labels. An aggregate-complete but label-uneven pool is a distribution imbalance, not a missing participation opportunity. | `tournament_scheduler.participation_targets` | `tournament_scheduler.participation_targets` | `tests/test_participation_targets.py`, `tests/test_intra_club_distribution.py` |
| `home_representation` | When a multi-team club hosts a tournament, the club's sibling teams take turns representing it. A spread of 0-1 is balanced; hosting coverage/balance is unchanged by which sibling shows up. | `tournament_scheduler.home_representation` | `tournament_scheduler.home_representation` | `tests/test_home_representation.py` |
| `opponent_repetition` | Teams should meet diverse opponents; repeated pairings beyond the configured expectation are minimized. | `tournament_scheduler.quality_objectives` | `tournament_scheduler.planning_contract.score_candidate` | `tests/test_quality_objectives.py` |
| `inter_club_diversity` | Tournaments should mix teams across clubs rather than concentrating same-club matchups. | `tournament_scheduler.quality_objectives` | `tournament_scheduler.planning_contract.score_candidate` | `tests/test_quality_objectives.py` |
| `temporal_spacing` | A team's tournaments should be spaced sensibly; very short turnaround gaps are minimized. | `tournament_scheduler.team_schedule_quality` | `tournament_scheduler.planning_contract.score_candidate` | `tests/test_team_schedule_quality.py`, `tests/test_fairness_temporal.py` |
| `temporal_coverage` | A team's tournaments should cover the season rather than cluster in one stretch. | `tournament_scheduler.temporal_coverage` | `tournament_scheduler.planning_contract.score_candidate` | `tests/test_temporal_coverage.py` |
| `travel_distance` | Estimated team travel between home and away arenas should be kept reasonable. | `tournament_scheduler.club_distances` | `tournament_scheduler.planning_contract.score_candidate` | `tests/test_club_distances.py` |

## Operator decisions

| Rule ID | Meaning | Canonical owner | Verifier / measurement | Tests |
|---|---|---|---|---|
| `shared_host_choice` | A joint registration such as Kongsberg/Tønsberg can genuinely require a contextual choice of which constituent physically carries the shared hosting responsibility. This is the limited case where agent/operator judgment over deterministic registration facts is legitimate. | `tournament_scheduler.shared_host_decision` | `tournament_scheduler.hosting_coverage.shared_registration_facts` | `tests/test_shared_host_decision.py` |
| `operator_waiver` | An authorized operator may explicitly waive a classified hard planning rule for a precise scope. The planner/agent may suggest but never create or broaden a waiver; waived violations stay visible and downgrade publication readiness. | `tournament_scheduler.operator_waivers` | `tournament_scheduler.planning_contract.verify_candidate` | `tests/test_operator_waivers.py` |
| `operator_accepted_participation_deviation` | An operator may deliberately live with a bounded participation deviation. The acceptance never changes the target or the schedule, is bound to the deviation scope/magnitude and becomes stale when the target changes or the deviation worsens. | `tournament_scheduler.participation_targets` | `tournament_scheduler.participation_targets.evidence_covers_deviation` | `tests/test_participation_targets.py`, `tests/test_season_maintenance.py` |
| `manual_placement_opt_in` | An automatic canonical mutation may not newly introduce a manual/unt trusted-calendar placement or host-confirmation dependency. A deliberate provisional placement requires the exact explicit opt-in and is audited. | `tournament_scheduler.operational_acceptability` | `tournament_scheduler.operational_acceptability` | `tests/test_operational_acceptability.py` |
| `guest_slot_reservation` | The operator reserves guest places as intent; the selection among legal alternatives is recorded as a decision. | `tournament_scheduler.guest_slots` | `tournament_scheduler.planning_contract.verify_candidate` | `tests/test_guest_slots.py` |
| `season_quality_baseline` | An operator may accept the current set and severity of non-hard findings as the season's regression reference. The baseline records stable finding ids and measurements (never aggregate counts alone), so later maintenance classifies fresh findings as known/improved/resolved/regressed/new. It never suppresses hard verification failures and never changes the schedule. | `tournament_scheduler.season_baseline` | `tournament_scheduler.planning_contract.verify_candidate` | `tests/test_season_baseline.py` |
| `operator_banned_date` | The operator may ban a global date as unusable for every tournament; the ban is durable policy, not a one-off. | `tournament_scheduler.canonical_banned_dates` | — | `tests/test_canonical_banned_dates.py` |
| `operator_holiday_date_exception` | The operator may allow one date that is excluded only by the derived holiday/date policy; the exception does not override explicit banned dates. | `tournament_scheduler.canonical_holiday_exceptions` | — | `tests/test_holiday_date_policy.py` |

## Fact / evidence semantics

| Rule ID | Meaning | Canonical owner | Verifier / measurement | Tests |
|---|---|---|---|---|
| `calendar_source_trust` | A per-club calendar is only trustworthy when the configured source produced usable evidence this run. A 'known' status does not prove every candidate time is free; an untrusted calendar yields manual placement, never a silent pass. | `tournament_scheduler.pipeline.source_health` | `tournament_scheduler.calendar_availability` | `tests/test_source_health.py`, `tests/test_calendar_availability.py` |
| `calendar_interval_classification` | A host's calendar interval is classified fixed_busy (hard external conflict), movable_busy (host-controlled, requires host confirmation) or unclassified. The classification is policy evidence, not a scheduling rule in itself. A promoted season may also carry an explicit event-to-tournament booking association overlay that makes one fixed event non-conflicting only for the associated tournament while that event still covers the tournament's current canonical occupied interval. | `tournament_scheduler.calendar_availability / tournament_scheduler.calendar_bookings` | `tournament_scheduler.planning_contract.external_calendar_conflict` | `tests/test_calendar_availability.py`, `tests/test_movable_capacity_repair.py`, `tests/test_approval_lifecycle.py` |
| `club_pool_classification` | For each club x age group x scope, the registered teams' aggregate target/actual is classified as complete / intra_club_distribution / minor\|material_club_pool_shortfall / single_team_deviation / over_target. The classification is the single source of truth for whether a residual is a genuine player-pool shortage. | `tournament_scheduler.participation_targets` | — | `tests/test_participation_targets.py` |
| `search_coverage_evidence` | A bounded repair/search result records what was actually tried: option_available, search_incomplete, bounded_search_exhausted, proven_infeasible or inapplicable, bound to a search capability/version. Zero options means only that this search found none, never proof of global infeasibility. | `tournament_scheduler.search_capability` | — | `tests/test_search_neighborhood_repair.py` |
| `approval_lifecycle` | Operator approvals/locks live in decisions.json separately from schedule facts. A protected placement whose fingerprint changed becomes a stale approval whose lock is dropped; an approval whose tournament is gone becomes orphaned. Both require re-review. | `tournament_scheduler.canonical_baseline` | — | `tests/test_approval_lifecycle.py`, `tests/test_canonical_baseline.py` |
| `verification_completeness` | Verification reports which checks were skipped because their required input was unavailable. An incomplete verification is never a pass. | `tournament_scheduler.planning_contract` | — | `tests/test_planning_contract.py` |

## Stable ID map

| Emitted code / score path | Rule / objective ID |
|---|---|
| `age_group_mismatch` (verifier) | `team_age_group_exact` |
| `duplicate_team_in_tournament` (verifier) | `team_unique_in_tournament` |
| `duplicate_participation_same_date` (verifier) | `team_unique_per_date` |
| `bye_team_not_allowed` (verifier) | `tournament_roster_shape` |
| `club_hard_max_exceeded` (verifier) | `club_hard_max` |
| `ice_time_playing_minimum` (verifier) | `tournament_ice_booking_duration` |
| `ice_time_governing_minimum` (verifier) | `tournament_ice_booking_duration` |
| `arena_interval_conflict` (verifier) | `arena_interval_non_overlap` |
| `arena_interval_check_failed` (verifier) | `arena_interval_non_overlap` |
| `tournament_over_capacity` (verifier) | `tournament_capacity` |
| `tournament_under_minimum` (verifier) | `tournament_min_size` |
| `parallel_capacity_exceeded` (verifier) | `parallel_capacity` |
| `unregistered_team` (verifier) | `registered_teams_only` |
| `invalid_date` (verifier) | `valid_tournament_date` |
| `date_outside_window` (verifier) | `date_within_window` |
| `holiday_date_used` (verifier) | `holiday_date_admissible` |
| `banned_date_used` (verifier) | `banned_dates_not_used` |
| `excluded_host_club_used` (verifier) | `excluded_host_club_not_used` |
| `host_team_missing` (verifier) | `host_representation` |
| `locked_date_missing` (verifier) | `locked_date_preserved` |
| `pinned_tournament_missing` (verifier) | `pinned_tournament_preserved` |
| `canonical_locked_tournament_missing` (verifier) | `canonical_locked_tournament_preserved` |
| `canonical_placement_locked` (verifier) | `canonical_placement_preserved` |
| `canonical_participants_locked` (verifier) | `canonical_participants_preserved` |
| `participation_hard_max_exceeded` (verifier) | `participation_hard_max` |
| `game_team_not_participant` (verifier) | `game_team_membership` |
| `invalid_game_record` (verifier) | `valid_game_record` |
| `invalid_game_round` (verifier) | `game_round_integrity` |
| `game_integrity_ambiguous_participants` (verifier) | `game_integrity_ambiguous_participants` |
| `round_robin_missing_pair` (verifier) | `round_robin_pairing` |
| `round_robin_duplicate_pair` (verifier) | `round_robin_pairing` |
| `team_double_booked_in_round` (verifier) | `team_unique_per_round` |
| `configured_round_count_mismatch` (verifier) | `tournament_round_count` |
| `avoidable_same_club_matchup` (verifier) | `intra_club_game_minimum` |
| `team_unavailable` (verifier) | `request_team_unavailable` |
| `minimum_gap` (verifier) | `request_minimum_gap` |
| `opponent_avoidance` (verifier) | `request_opponent_avoidance` |
| `stale_approval` (verifier) | `approval_lifecycle` |
| `orphaned_approval` (verifier) | `approval_lifecycle` |
| `input_constrained_shape` (finding) | `tournament_roster_shape` |
| `unresolved_hosting_obligations` (finding) | `hosting_age_group_coverage` |
| `unresolved_hosting` (finding) | `hosting_age_group_coverage` |
| `unresolved_hosting_obligation` (finding) | `hosting_age_group_coverage` |
| `unexplained_hosting_responsibility_transfer` (finding) | `hosting_responsibility` |
| `unplaced_placement` (finding) | `tournament_placement_obligation` |
| `unplaced_tournament_placement` (finding) | `tournament_placement_obligation` |
| `hosting_balance_imbalances` (finding) | `hosting_proportional_balance` |
| `hosting_balance_imbalance` (finding) | `hosting_proportional_balance` |
| `participation_target_deviation` (finding) | `participation_target` |
| `participation_shortfalls` (finding) | `participation_target` |
| `participation_deviation` (finding) | `participation_target` |
| `intra_club_participation_distribution` (finding) | `intra_club_participation_distribution` |
| `home_representation` (finding) | `home_representation` |
| `home_representation_skew` (finding) | `home_representation` |
| `temporal_clustering` (finding) | `temporal_spacing` |
| `operator_waivers` (finding) | `operator_waiver` |
| `manual_placement` (finding) | `manual_placement_opt_in` |
| `manual_calendar_placements` (finding) | `calendar_source_trust` |
| `external_calendar_conflicts` (finding) | `calendar_interval_classification` |
| `movable_host_confirmation_required` (finding) | `calendar_interval_classification` |
| `movable_capacity_opportunity` (finding) | `calendar_interval_classification` |
| `stale_calendar_booking_association` (finding) | `calendar_interval_classification` |
| `bounded_search_exhausted` (finding) | `search_coverage_evidence` |
| `proven_infeasible` (finding) | `search_coverage_evidence` |
| `search_incomplete` (finding) | `search_coverage_evidence` |
| `stale_approvals` (finding) | `approval_lifecycle` |
| `orphaned_approvals` (finding) | `approval_lifecycle` |
| `incomplete_verification` (finding) | `verification_completeness` |
| `hosting.unresolved_obligations_count` (score) | `hosting_age_group_coverage` |
| `hosting.spread` (score) | `hosting_proportional_balance` |
| `participation.spread` (score) | `participation_target` |
| `participation.club_pool_unresolved_avoidable_deviation_count` (score) | `participation_target` |
| `participation.club_pool_unresolved_shortfall_count` (score) | `participation_target` |
| `participation.club_pool_unresolved_season_total_absolute_deviation` (score) | `participation_target` |
| `participation.club_pool_unresolved_max_team_season_deviation` (score) | `participation_target` |
| `participation.club_pool_unresolved_half_total_absolute_deviation` (score) | `participation_target` |
| `participation.club_pool_unresolved_max_team_half_deviation` (score) | `participation_target` |
| `home_representation.max_material_spread` (score) | `home_representation` |
| `home_representation.material_skew_pool_count` (score) | `home_representation` |
| `opponent_diversity.unique_pairs` (score) | `opponent_repetition` |
| `opponent_diversity.pairwise_novelty` (score) | `opponent_repetition` |
| `opponent_diversity.max_pair_repeat` (score) | `opponent_repetition` |
| `opponent_diversity.pairs_meeting_3_plus` (score) | `opponent_repetition` |
| `opponent_diversity.inter_club_diversity` (score) | `inter_club_diversity` |
| `opponent_diversity.same_club_pairing_count` (score) | `inter_club_diversity` |
| `opponent_diversity.max_same_club_teams_per_tournament` (score) | `inter_club_diversity` |
| `opponent_diversity.club_count_excess_over_2` (score) | `inter_club_diversity` |
| `opponent_diversity.tournaments_with_3plus_same_club` (score) | `inter_club_diversity` |
| `turnaround.min_turnaround_days` (score) | `temporal_spacing` |
| `turnaround.gaps_under_days.7` (score) | `temporal_spacing` |
| `turnaround.gaps_under_days.14` (score) | `temporal_spacing` |
| `temporal.max_gap_days` (score) | `temporal_coverage` |
| `temporal.offenders_count` (score) | `temporal_coverage` |
| `travel` (score) | `travel_distance` |

## Entry details

### `team_age_group_exact`

`Hard constraint` · status `active` · operator-waivable: yes  
**Meaning:** Every team participating in a tournament belongs to that tournament's age group.  
**Canonical owner:** `tournament_scheduler.planning_contract`  
**Input / fact source:** planning_problem.teams[].age_group + candidate tournament age_group  
**Verifier / measurement:** `tournament_scheduler.planning_contract.verify_candidate`  
**Codes:** verifier `age_group_mismatch` · finding — · score —  
**Providers:** mutation `tournament_scheduler.participant_selection` · search `tournament_scheduler.search_neighborhood_repair`  
**Evidence / report:** `verify_candidate.violations`, `rules_model age_group_exact_match`  
**Tests:** `tests/test_planning_contract.py`  
**Precedence:** precedes — · depends on —

### `team_unique_in_tournament`

`Hard constraint` · status `active` · operator-waivable: yes  
**Meaning:** A team may appear at most once in a single tournament.  
**Canonical owner:** `tournament_scheduler.planning_contract`  
**Input / fact source:** candidate tournament roster  
**Verifier / measurement:** `tournament_scheduler.planning_contract.verify_candidate`  
**Codes:** verifier `duplicate_team_in_tournament` · finding — · score —  
**Providers:** mutation `tournament_scheduler.participant_selection` · search `tournament_scheduler.search_neighborhood_repair`  
**Evidence / report:** `verify_candidate.violations`  
**Tests:** `tests/test_planning_contract.py`  
**Precedence:** precedes — · depends on —

### `team_unique_per_date`

`Hard constraint` · status `active` · operator-waivable: yes  
**Meaning:** A team may not be scheduled in two different tournaments on the same date.  
**Canonical owner:** `tournament_scheduler.planning_contract`  
**Input / fact source:** candidate tournament dates + roster  
**Verifier / measurement:** `tournament_scheduler.planning_contract.verify_candidate`  
**Codes:** verifier `duplicate_participation_same_date` · finding — · score —  
**Providers:** mutation `tournament_scheduler.participant_selection` · search `tournament_scheduler.search_neighborhood_repair`  
**Evidence / report:** `verify_candidate.violations`, `rules_model no_same_date_double_participation`  
**Tests:** `tests/test_planning_contract.py`  
**Precedence:** precedes — · depends on —

### `tournament_roster_shape`

`Hard constraint` · status `active` · operator-waivable: yes  
**Meaning:** A tournament has an admissible, avoidable-by-free participant shape: even teams and no pause/bye rounds (or the exact age-group team count where configured). An input-constrained scarce shape is surfaced separately, never as a soft preference.  
**Canonical owner:** `tournament_scheduler.effective_tournament_shape`  
**Input / fact source:** planning_problem registered pool + rounds_per_tournament, reduced per tournament by scoped revision-bound participation_withdrawals records (tournament_scheduler.participation_withdrawals); the registered roster is never rewritten  
**Verifier / measurement:** `tournament_scheduler.planning_contract.verify_candidate`  
**Codes:** verifier `bye_team_not_allowed` · finding `input_constrained_shape` · score —  
**Providers:** mutation `tournament_scheduler.participant_roster_sizing`, `tournament_scheduler.application.canonical_season.withdrawal` · search `tournament_scheduler.participant_roster_repair`  
**Evidence / report:** `verify_candidate.violations`, `verify_candidate.input_constrained_shapes`  
**Tests:** `tests/test_effective_tournament_shape.py`, `tests/test_planning_contract.py`, `tests/test_participant_removal.py`  
**Precedence:** precedes — · depends on —

### `club_hard_max`

`Hard constraint` · status `active` · operator-waivable: yes  
**Meaning:** At most three teams from one club may participate in one tournament.  
**Canonical owner:** `tournament_scheduler.planning_contract`  
**Input / fact source:** candidate tournament roster club labels  
**Verifier / measurement:** `tournament_scheduler.planning_contract.verify_candidate`  
**Codes:** verifier `club_hard_max_exceeded` · finding — · score —  
**Providers:** mutation `tournament_scheduler.participant_selection` · search `tournament_scheduler.search_neighborhood_repair`, `tournament_scheduler.stage3_cpsat_club_cap`  
**Evidence / report:** `verify_candidate.violations`  
**Tests:** `tests/test_planning_contract.py`  
**Precedence:** precedes — · depends on —

### `tournament_ice_booking_duration`

`Hard constraint` · status `active` · operator-waivable: no (structural)  
**Meaning:** A tournament's configured ice_time_minutes is the complete hall occupancy window. It must be at least the actual rounds times round length plus the per-round changeover buffer, and any governing per-series-round booking floor.  
**Canonical owner:** `tournament_scheduler.occupancy`  
**Input / fact source:** planning_problem ice_time_minutes + round_length_minutes + generated round count  
**Verifier / measurement:** `tournament_scheduler.planning_contract.verify_candidate`  
**Codes:** verifier `ice_time_playing_minimum`, `ice_time_governing_minimum` · finding — · score —  
**Providers:** mutation — · search —  
**Evidence / report:** `verify_candidate.violations`, `rules_model tournament_duration`  
**Tests:** `tests/test_occupancy.py`, `tests/test_planning_contract.py`, `tests/test_stage1_config.py`  
**Precedence:** precedes — · depends on —

### `arena_interval_non_overlap`

`Hard constraint` · status `active` · operator-waivable: yes  
**Meaning:** Two tournaments must never require the same arena in overlapping datetime intervals. A failure to evaluate the interval data is itself a blocking verification result, not a silent pass.  
**Canonical owner:** `tournament_scheduler.arena_conflicts`  
**Input / fact source:** candidate start_time/duration + arena identity  
**Verifier / measurement:** `tournament_scheduler.planning_contract.verify_candidate`  
**Codes:** verifier `arena_interval_conflict`, `arena_interval_check_failed` · finding — · score —  
**Providers:** mutation `arena_conflict_decision`, `date_policy_relocation` · search `tournament_scheduler.search_neighborhood_repair`  
**Evidence / report:** `verify_candidate.violations`, `plan.arena_day_collisions`, `rules_model arena_day_collisions`  
**Tests:** `tests/test_arena_conflicts.py`, `tests/test_planning_contract.py`  
**Precedence:** precedes — · depends on —

### `tournament_capacity`

`Hard constraint` · status `active` · operator-waivable: yes  
**Meaning:** A tournament must not exceed its configured participant capacity.  
**Canonical owner:** `tournament_scheduler.planning_contract`  
**Input / fact source:** planning_problem tournament sizing config  
**Verifier / measurement:** `tournament_scheduler.planning_contract.verify_candidate`  
**Codes:** verifier `tournament_over_capacity` · finding — · score —  
**Providers:** mutation `tournament_scheduler.participant_roster_sizing` · search —  
**Evidence / report:** `verify_candidate.violations`, `rules_model tournament_capacity`  
**Tests:** `tests/test_planning_contract.py`  
**Precedence:** precedes — · depends on —

### `tournament_min_size`

`Hard constraint` · status `active` · operator-waivable: yes  
**Meaning:** A tournament with enough registered teams in its age group must have at least three participants.  
**Canonical owner:** `tournament_scheduler.final_verification`  
**Input / fact source:** planning_problem registered pool + candidate capacity  
**Verifier / measurement:** `tournament_scheduler.final_verification.verify_final_candidate`  
**Codes:** verifier `tournament_under_minimum` · finding — · score —  
**Providers:** mutation `tournament_scheduler.underfilled_roster_repair` · search —  
**Evidence / report:** `verify_final_candidate.violations`  
**Tests:** `tests/test_final_verification.py`  
**Precedence:** precedes — · depends on —

### `parallel_capacity`

`Hard constraint` · status `active` · operator-waivable: yes  
**Meaning:** A tournament must not schedule more concurrent games than the configured parallel capacity.  
**Canonical owner:** `tournament_scheduler.final_verification`  
**Input / fact source:** planning_problem parallel_games + generated games  
**Verifier / measurement:** `tournament_scheduler.final_verification.verify_final_candidate`  
**Codes:** verifier `parallel_capacity_exceeded` · finding — · score —  
**Providers:** mutation `tournament_scheduler.game_generation` · search —  
**Evidence / report:** `verify_final_candidate.violations`  
**Tests:** `tests/test_final_verification.py`  
**Precedence:** precedes — · depends on —

### `registered_teams_only`

`Hard constraint` · status `active` · operator-waivable: yes  
**Meaning:** Every RVV participant of a tournament must exist in the registered roster (guests are explicit, separate places).  
**Canonical owner:** `tournament_scheduler.planning_contract`  
**Input / fact source:** planning_problem.teams  
**Verifier / measurement:** `tournament_scheduler.planning_contract.verify_candidate`  
**Codes:** verifier `unregistered_team` · finding — · score —  
**Providers:** mutation `tournament_scheduler.participant_selection` · search —  
**Evidence / report:** `verify_candidate.violations`, `rules_model registered_teams_only`  
**Tests:** `tests/test_planning_contract.py`  
**Precedence:** precedes — · depends on —

### `valid_tournament_date`

`Hard constraint` · status `active` · operator-waivable: no (structural)  
**Meaning:** A tournament must carry a parseable date; a missing/invalid date is structural corruption.  
**Canonical owner:** `tournament_scheduler.planning_contract`  
**Input / fact source:** candidate tournament date  
**Verifier / measurement:** `tournament_scheduler.planning_contract.verify_candidate`  
**Codes:** verifier `invalid_date` · finding — · score —  
**Providers:** mutation — · search —  
**Evidence / report:** `verify_candidate.violations`  
**Tests:** `tests/test_planning_contract.py`  
**Precedence:** precedes — · depends on —

### `date_within_window`

`Hard constraint` · status `active` · operator-waivable: yes  
**Meaning:** Every tournament date lies inside the configured planning/season window.  
**Canonical owner:** `tournament_scheduler.planning_contract`  
**Input / fact source:** planning_problem start/end date  
**Verifier / measurement:** `tournament_scheduler.planning_contract.verify_candidate`  
**Codes:** verifier `date_outside_window` · finding — · score —  
**Providers:** mutation `date_policy_relocation` · search —  
**Evidence / report:** `verify_candidate.violations`, `rules_model date_within_planning_window`  
**Tests:** `tests/test_planning_contract.py`  
**Precedence:** precedes — · depends on —

### `holiday_date_admissible`

`Hard constraint` · status `active` · operator-waivable: yes  
**Meaning:** Tournaments must not be scheduled on canonically excluded holiday dates for the planning window.  
**Canonical owner:** `tournament_scheduler.date_policy`  
**Input / fact source:** planning_problem window -> derived date exclusions  
**Verifier / measurement:** `tournament_scheduler.planning_contract.verify_candidate`  
**Codes:** verifier `holiday_date_used` · finding — · score —  
**Providers:** mutation `date_policy_relocation` · search —  
**Evidence / report:** `verify_candidate.violations`, `rules_model holiday_dates_not_used`  
**Tests:** `tests/test_holiday_date_policy.py`  
**Precedence:** precedes — · depends on —

### `banned_dates_not_used`

`Hard constraint` · status `active` · operator-waivable: yes  
**Meaning:** Tournaments must not be scheduled on an operator-banned global date.  
**Canonical owner:** `tournament_scheduler.canonical_banned_dates`  
**Input / fact source:** decisions.json banned dates projected into the planning problem  
**Verifier / measurement:** `tournament_scheduler.planning_contract.verify_candidate`  
**Codes:** verifier `banned_date_used` · finding — · score —  
**Providers:** mutation `canonical season batch`, `date_policy_relocation` · search —  
**Evidence / report:** `verify_candidate.violations`, `season banned-dates --json`  
**Tests:** `tests/test_canonical_banned_dates.py`  
**Precedence:** precedes — · depends on —

### `excluded_host_club_not_used`

`Hard constraint` · status `active` · operator-waivable: yes  
**Meaning:** A club explicitly excluded as host for a scope must not host there.  
**Canonical owner:** `tournament_scheduler.planning_contract`  
**Input / fact source:** planning_problem manual_adjustments excluded_host_clubs  
**Verifier / measurement:** `tournament_scheduler.planning_contract.verify_candidate`  
**Codes:** verifier `excluded_host_club_used` · finding — · score —  
**Providers:** mutation `host_placement_repair` · search `tournament_scheduler.search_neighborhood_repair`  
**Evidence / report:** `verify_candidate.violations`, `rules_model excluded_host_clubs_not_used`  
**Tests:** `tests/test_planning_contract.py`  
**Precedence:** precedes — · depends on —

### `host_representation`

`Hard constraint` · status `active` · operator-waivable: yes  
**Meaning:** A tournament's host club must be represented by its own participating team (shared/joint registrations count for either constituent). This is the hard representation invariant; proportional hosting balance is a separate soft objective.  
**Canonical owner:** `tournament_scheduler.host_representation`  
**Input / fact source:** candidate host_club + tournament roster  
**Verifier / measurement:** `tournament_scheduler.planning_contract.verify_candidate`  
**Codes:** verifier `host_team_missing` · finding — · score —  
**Providers:** mutation `host_team_missing_repair`, `placement_preserving_roster_repair` · search `tournament_scheduler.search_neighborhood_repair`  
**Evidence / report:** `verify_candidate.violations`  
**Tests:** `tests/test_stage3_optimizer_host_representation.py`, `tests/test_host_team_missing_repair.py`  
**Precedence:** precedes `hosting_age_group_coverage`, `hosting_proportional_balance` · depends on —

### `locked_date_preserved`

`Hard constraint` · status `active` · operator-waivable: yes  
**Meaning:** A canonical locked date must still have a scheduled tournament.  
**Canonical owner:** `tournament_scheduler.canonical_baseline`  
**Input / fact source:** canonical decisions.json locked dates  
**Verifier / measurement:** `tournament_scheduler.planning_contract.verify_candidate`  
**Codes:** verifier `locked_date_missing` · finding — · score —  
**Providers:** mutation — · search —  
**Evidence / report:** `verify_candidate.violations`, `rules_model locked_dates_preserved`  
**Tests:** `tests/test_canonical_baseline.py`  
**Precedence:** precedes — · depends on —

### `pinned_tournament_preserved`

`Hard constraint` · status `active` · operator-waivable: yes  
**Meaning:** A pinned tournament id must remain present in the candidate.  
**Canonical owner:** `tournament_scheduler.canonical_baseline`  
**Input / fact source:** canonical decisions.json pinned tournament ids  
**Verifier / measurement:** `tournament_scheduler.planning_contract.verify_candidate`  
**Codes:** verifier `pinned_tournament_missing` · finding — · score —  
**Providers:** mutation — · search —  
**Evidence / report:** `verify_candidate.violations`, `rules_model pinned_tournaments_preserved`  
**Tests:** `tests/test_canonical_baseline.py`  
**Precedence:** precedes — · depends on —

### `canonical_locked_tournament_preserved`

`Hard constraint` · status `active` · operator-waivable: no (structural)  
**Meaning:** An approved/placement-locked canonical tournament must not be dropped by replanning.  
**Canonical owner:** `tournament_scheduler.canonical_baseline`  
**Input / fact source:** canonical decisions.json locks  
**Verifier / measurement:** `tournament_scheduler.canonical_baseline.verify_canonical_locks`  
**Codes:** verifier `canonical_locked_tournament_missing` · finding — · score —  
**Providers:** mutation — · search —  
**Evidence / report:** `verify_candidate.violations`  
**Tests:** `tests/test_canonical_baseline.py`  
**Precedence:** precedes — · depends on —

### `canonical_placement_preserved`

`Hard constraint` · status `active` · operator-waivable: no (structural)  
**Meaning:** A placement-locked canonical tournament keeps its protected placement fields.  
**Canonical owner:** `tournament_scheduler.canonical_baseline`  
**Input / fact source:** canonical decisions.json locks  
**Verifier / measurement:** `tournament_scheduler.canonical_baseline.verify_canonical_locks`  
**Codes:** verifier `canonical_placement_locked` · finding — · score —  
**Providers:** mutation — · search —  
**Evidence / report:** `verify_candidate.violations`, `season protections`  
**Tests:** `tests/test_canonical_baseline.py`, `tests/test_approval_lifecycle.py`  
**Precedence:** precedes — · depends on —

### `canonical_participants_preserved`

`Hard constraint` · status `active` · operator-waivable: no (structural)  
**Meaning:** A participant-locked canonical tournament keeps its protected participant list.  
**Canonical owner:** `tournament_scheduler.canonical_baseline`  
**Input / fact source:** canonical decisions.json locks  
**Verifier / measurement:** `tournament_scheduler.canonical_baseline.verify_canonical_locks`  
**Codes:** verifier `canonical_participants_locked` · finding — · score —  
**Providers:** mutation — · search —  
**Evidence / report:** `verify_candidate.violations`, `season protections`  
**Tests:** `tests/test_canonical_baseline.py`, `tests/test_approval_lifecycle.py`  
**Precedence:** precedes — · depends on —

### `participation_hard_max`

`Hard constraint` · status `active` · operator-waivable: yes  
**Meaning:** An explicitly configured participation hard maximum is a legality boundary and is operator-waivable. It is separate from the ordinary participation target, which is a soft objective and never a hard cap.  
**Canonical owner:** `tournament_scheduler.participation_targets`  
**Input / fact source:** planning_problem participation_hard_max / participation_hard_max_by_age_group  
**Verifier / measurement:** `tournament_scheduler.planning_contract.verify_candidate`  
**Codes:** verifier `participation_hard_max_exceeded` · finding — · score —  
**Providers:** mutation `tournament_scheduler.participation_deviation_repair` · search —  
**Evidence / report:** `verify_candidate.violations`, `verify_candidate.waived_violations`, `waiver list`  
**Tests:** `tests/test_participation_targets.py`, `tests/test_operator_waivers.py`  
**Precedence:** precedes — · depends on —

### `game_team_membership`

`Hard constraint` · status `active` · operator-waivable: yes  
**Meaning:** Every exported game is between two declared participants of its tournament.  
**Canonical owner:** `tournament_scheduler.final_verification`  
**Input / fact source:** candidate tournament roster + games  
**Verifier / measurement:** `tournament_scheduler.final_verification.verify_final_candidate`  
**Codes:** verifier `game_team_not_participant` · finding — · score —  
**Providers:** mutation — · search —  
**Evidence / report:** `verify_final_candidate.violations`  
**Tests:** `tests/test_final_verification.py`  
**Precedence:** precedes — · depends on —

### `valid_game_record`

`Hard constraint` · status `active` · operator-waivable: no (structural)  
**Meaning:** Every exported game record is a well-formed object with distinct participants.  
**Canonical owner:** `tournament_scheduler.final_verification`  
**Input / fact source:** candidate tournament games  
**Verifier / measurement:** `tournament_scheduler.final_verification.verify_final_candidate`  
**Codes:** verifier `invalid_game_record` · finding — · score —  
**Providers:** mutation — · search —  
**Evidence / report:** `verify_final_candidate.violations`  
**Tests:** `tests/test_final_verification.py`  
**Precedence:** precedes — · depends on —

### `game_round_integrity`

`Hard constraint` · status `active` · operator-waivable: no (structural)  
**Meaning:** Every exported game has a valid positive round number.  
**Canonical owner:** `tournament_scheduler.final_verification`  
**Input / fact source:** candidate tournament games  
**Verifier / measurement:** `tournament_scheduler.final_verification.verify_final_candidate`  
**Codes:** verifier `invalid_game_round` · finding — · score —  
**Providers:** mutation — · search —  
**Evidence / report:** `verify_final_candidate.violations`  
**Tests:** `tests/test_final_verification.py`  
**Precedence:** precedes — · depends on —

### `game_integrity_ambiguous_participants`

`Hard constraint` · status `active` · operator-waivable: no (structural)  
**Meaning:** A tournament's participant labels must be present and unique before game integrity can be judged.  
**Canonical owner:** `tournament_scheduler.final_verification`  
**Input / fact source:** candidate tournament roster labels  
**Verifier / measurement:** `tournament_scheduler.final_verification.verify_final_candidate`  
**Codes:** verifier `game_integrity_ambiguous_participants` · finding — · score —  
**Providers:** mutation — · search —  
**Evidence / report:** `verify_final_candidate.violations`  
**Tests:** `tests/test_final_verification.py`  
**Precedence:** precedes — · depends on —

### `round_robin_pairing`

`Hard constraint` · status `active` · operator-waivable: yes  
**Meaning:** A round-robin tournament plays every required pairing exactly once.  
**Canonical owner:** `tournament_scheduler.final_verification`  
**Input / fact source:** candidate tournament roster + games  
**Verifier / measurement:** `tournament_scheduler.final_verification.verify_final_candidate`  
**Codes:** verifier `round_robin_missing_pair`, `round_robin_duplicate_pair` · finding — · score —  
**Providers:** mutation `tournament_scheduler.game_generation` · search —  
**Evidence / report:** `verify_final_candidate.violations`  
**Tests:** `tests/test_final_verification.py`  
**Precedence:** precedes — · depends on —

### `team_unique_per_round`

`Hard constraint` · status `active` · operator-waivable: yes  
**Meaning:** A team plays at most one game per round.  
**Canonical owner:** `tournament_scheduler.final_verification`  
**Input / fact source:** candidate tournament games  
**Verifier / measurement:** `tournament_scheduler.final_verification.verify_final_candidate`  
**Codes:** verifier `team_double_booked_in_round` · finding — · score —  
**Providers:** mutation `tournament_scheduler.game_generation` · search —  
**Evidence / report:** `verify_final_candidate.violations`  
**Tests:** `tests/test_final_verification.py`  
**Precedence:** precedes — · depends on —

### `tournament_round_count`

`Hard constraint` · status `active` · operator-waivable: yes  
**Meaning:** A limited-rounds tournament produces exactly the effective configured round count.  
**Canonical owner:** `tournament_scheduler.final_verification`  
**Input / fact source:** planning_problem rounds_per_tournament + effective shape  
**Verifier / measurement:** `tournament_scheduler.final_verification.verify_final_candidate`  
**Codes:** verifier `configured_round_count_mismatch` · finding — · score —  
**Providers:** mutation `tournament_scheduler.game_generation` · search —  
**Evidence / report:** `verify_final_candidate.violations`  
**Tests:** `tests/test_final_verification.py`  
**Precedence:** precedes — · depends on —

### `intra_club_game_minimum`

`Hard constraint` · status `active` · operator-waivable: yes  
**Meaning:** Same-club matchups are limited to the minimum required by the limited-rounds format.  
**Canonical owner:** `tournament_scheduler.final_verification`  
**Input / fact source:** planning_problem parallel_games + roster club labels  
**Verifier / measurement:** `tournament_scheduler.final_verification.verify_final_candidate`  
**Codes:** verifier `avoidable_same_club_matchup` · finding — · score —  
**Providers:** mutation `tournament_scheduler.game_generation` · search —  
**Evidence / report:** `verify_final_candidate.violations`  
**Tests:** `tests/test_final_verification.py`  
**Precedence:** precedes — · depends on —

### `request_team_unavailable`

`Hard constraint` · status `active` · operator-waivable: yes  
**Meaning:** A durable operator request constraint: the team must not play inside its declared unavailable date range.  
**Canonical owner:** `tournament_scheduler.request_constraints`  
**Input / fact source:** canonical decisions.json request constraints  
**Verifier / measurement:** `tournament_scheduler.request_constraints.request_constraint_violations`  
**Codes:** verifier `team_unavailable` · finding — · score —  
**Providers:** mutation — · search —  
**Evidence / report:** `season constraints --json`, `canonical apply boundary refusals`  
**Tests:** `tests/test_request_constraints.py`  
**Precedence:** precedes — · depends on —

### `request_minimum_gap`

`Hard constraint` · status `active` · operator-waivable: yes  
**Meaning:** A durable operator request constraint: the team must keep at least the requested days between tournaments.  
**Canonical owner:** `tournament_scheduler.request_constraints`  
**Input / fact source:** canonical decisions.json request constraints  
**Verifier / measurement:** `tournament_scheduler.request_constraints.request_constraint_violations`  
**Codes:** verifier `minimum_gap` · finding — · score —  
**Providers:** mutation — · search —  
**Evidence / report:** `season constraints --json`, `canonical apply boundary refusals`  
**Tests:** `tests/test_request_constraints.py`  
**Precedence:** precedes — · depends on —

### `request_opponent_avoidance`

`Hard constraint` · status `active` · operator-waivable: yes  
**Meaning:** A durable operator request constraint: two teams must not meet inside the declared date range.  
**Canonical owner:** `tournament_scheduler.request_constraints`  
**Input / fact source:** canonical decisions.json request constraints  
**Verifier / measurement:** `tournament_scheduler.request_constraints.request_constraint_violations`  
**Codes:** verifier `opponent_avoidance` · finding — · score —  
**Providers:** mutation — · search —  
**Evidence / report:** `season constraints --json`, `canonical apply boundary refusals`  
**Tests:** `tests/test_request_constraints.py`  
**Precedence:** precedes — · depends on —

### `hosting_age_group_coverage`

`Operational obligation` · status `active` · operator-waivable: yes  
**Meaning:** For every (club, age_group) with at least one registered team, the club receives at least one hosting responsibility in that age group during the season whenever the number of tournaments makes it mathematically possible. This is club x age-group coverage, not aggregate club hosting. A structural shortfall (fewer tournaments than clubs needing coverage) is surfaced explicitly, never hidden as a soft imbalance.  
**Canonical owner:** `tournament_scheduler.hosting_coverage.hosting_targets_with_coverage_floor`  
**Input / fact source:** planning_problem.teams + candidate tournaments (hosting_coverage_matrix)  
**Verifier / measurement:** `tournament_scheduler.hosting_coverage.hosting_coverage_matrix`  
**Codes:** verifier — · finding `unresolved_hosting_obligations`, `unresolved_hosting`, `unresolved_hosting_obligation` · score `hosting.unresolved_obligations_count`  
**Providers:** mutation `hosting_balance_repair`, `host_placement_repair`, `unplaced_placement_repair`, `hosting_same_age_repair`, `hosting_cross_age_repair` · search `tournament_scheduler.search_neighborhood_repair`  
**Evidence / report:** `plan.unresolved_hosting_obligations`, `verify_candidate.unresolved_hosting_obligations`, `publication_readiness unresolved_hosting`, `rules_model hosting_obligation_coverage`  
**Tests:** `tests/test_hosting_coverage.py`, `tests/test_hosting_same_age_repair.py`  
**Precedence:** precedes `hosting_proportional_balance` · depends on `host_representation`

### `hosting_responsibility`

`Operational obligation` · status `active` · operator-waivable: yes  
**Meaning:** Once a hosting responsibility is assigned, calendar convenience must not silently transfer it to another club. If the responsible host has no verified automatic slot, the responsibility is preserved and the tournament becomes MANUAL PLACEMENT REQUIRED rather than moving the burden. This is a cross-path invariant over placement/repair/search.  
**Canonical owner:** `tournament_scheduler.hosting_responsibility`  
**Input / fact source:** hosting_coverage target/actual ledger + candidate physical hosting  
**Verifier / measurement:** `tournament_scheduler.hosting_responsibility.hosting_responsibility_facts`  
**Codes:** verifier — · finding `unexplained_hosting_responsibility_transfer` · score —  
**Providers:** mutation `responsibility_preserving_repair`, `unplaced_placement_repair` · search —  
**Evidence / report:** `hosting_responsibility finding code`, `repair/search option rejection evidence`, `review/audit evidence`  
**Tests:** `tests/test_hosting_responsibility.py`  
**Precedence:** precedes — · depends on `hosting_age_group_coverage`

### `tournament_placement_obligation`

`Operational obligation` · status `active` · operator-waivable: yes  
**Meaning:** A scheduled tournament's host is chosen among its participants; when no legal and trustworthy slot exists for the responsible host, the obligation stays unresolved for manual placement instead of being given to an unrelated club.  
**Canonical owner:** `tournament_scheduler.placement_normalization`  
**Input / fact source:** candidate placements + host calendar availability  
**Verifier / measurement:** `tournament_scheduler.planning_contract.verify_candidate`  
**Codes:** verifier — · finding `unplaced_placement`, `unplaced_tournament_placement` · score —  
**Providers:** mutation `unplaced_placement_repair`, `host_placement_repair` · search —  
**Evidence / report:** `plan.unresolved_tournament_placements`, `manual work items`  
**Tests:** `tests/test_placement_normalization.py`, `tests/test_unplaced_placement_repair.py`  
**Precedence:** precedes — · depends on —

### `guest_reservation_integrity`

`Operational obligation` · status `active` · operator-waivable: yes  
**Meaning:** A reserved guest place is a deliberate capacity reservation that counts toward capacity/ice-time shape but never toward RVV participation, hosting, fairness or travel; participant optimization/repair cannot consume it.  
**Canonical owner:** `tournament_scheduler.guest_slots`  
**Input / fact source:** canonical tournament guest_slot records  
**Verifier / measurement:** `tournament_scheduler.planning_contract.verify_candidate`  
**Codes:** verifier — · finding — · score —  
**Providers:** mutation `guest-fill`, `guest-release` · search —  
**Evidence / report:** `season_plan.html guest places`, `semantic audit guest_reservations`  
**Tests:** `tests/test_guest_slots.py`  
**Precedence:** precedes — · depends on —

### `hosting_proportional_balance`

`Soft objective` · status `active` · operator-waivable: yes  
**Meaning:** After the age-group coverage floor is satisfied (or a structural shortfall is explicitly surfaced), remaining hosting responsibility is distributed roughly in proportion to registered team counts. This objective is secondary to coverage: a larger club may legitimately under-host relative to its proportional target so a smaller club gets its first responsibility.  
**Canonical owner:** `tournament_scheduler.hosting_coverage.hosting_balance_matrix`  
**Input / fact source:** planning_problem.teams + candidate tournaments  
**Verifier / measurement:** `tournament_scheduler.hosting_coverage.material_hosting_balance_imbalances`  
**Codes:** verifier — · finding `hosting_balance_imbalances`, `hosting_balance_imbalance` · score `hosting.spread`  
**Providers:** mutation `hosting_balance_repair` · search —  
**Evidence / report:** `score_candidate.hosting.spread`, `verify_candidate.hosting_balance_imbalances`, `rules_model hosting_deviation`  
**Tests:** `tests/test_hosting_coverage.py`  
**Precedence:** precedes — · depends on `hosting_age_group_coverage`

### `participation_target`

`Soft objective` · status `active` · operator-waivable: yes  
**Meaning:** The configured per-team/per-half tournament participation target is a desired optimization goal with evidenced relaxation, not an unconditional obligation or hard bound. Attainment is optimized within real season capacity; a capacity-constrained miss is not itself a planning failure.  
**Canonical owner:** `tournament_scheduler.participation_targets`  
**Input / fact source:** controlled workbook participation_targets_by_age_group / explicit team targets  
**Verifier / measurement:** `tournament_scheduler.participation_targets.evaluate_participation`  
**Codes:** verifier — · finding `participation_target_deviation`, `participation_shortfalls`, `participation_deviation` · score `participation.spread`, `participation.club_pool_unresolved_avoidable_deviation_count`, `participation.club_pool_unresolved_shortfall_count`, `participation.club_pool_unresolved_season_total_absolute_deviation`, `participation.club_pool_unresolved_max_team_season_deviation`, `participation.club_pool_unresolved_half_total_absolute_deviation`, `participation.club_pool_unresolved_max_team_half_deviation`  
**Providers:** mutation `tournament_scheduler.participation_deviation_repair` · search `tournament_scheduler.stage3_optimizer`  
**Evidence / report:** `score_candidate.participation.*`, `verify_candidate.participation_deviations`, `rules_model participation_target_deviation`, `rules_model participation_shortfalls`  
**Tests:** `tests/test_participation_targets.py`  
**Precedence:** precedes — · depends on —

### `intra_club_participation_distribution`

`Soft objective` · status `active` · operator-waivable: yes  
**Meaning:** Within a multi-team club and age group, participation is rotated evenly across the club's sibling team labels. An aggregate-complete but label-uneven pool is a distribution imbalance, not a missing participation opportunity.  
**Canonical owner:** `tournament_scheduler.participation_targets`  
**Input / fact source:** planning_problem club pools + candidate participations  
**Verifier / measurement:** `tournament_scheduler.participation_targets`  
**Codes:** verifier — · finding `intra_club_participation_distribution` · score —  
**Providers:** mutation `intra_club_distribution_repair` · search —  
**Evidence / report:** `verify_candidate.participation_club_pools`, `rules_model club_participation_fairness`  
**Tests:** `tests/test_participation_targets.py`, `tests/test_intra_club_distribution.py`  
**Precedence:** precedes — · depends on —

### `home_representation`

`Soft objective` · status `active` · operator-waivable: yes  
**Meaning:** When a multi-team club hosts a tournament, the club's sibling teams take turns representing it. A spread of 0-1 is balanced; hosting coverage/balance is unchanged by which sibling shows up.  
**Canonical owner:** `tournament_scheduler.home_representation`  
**Input / fact source:** candidate host club + tournament roster  
**Verifier / measurement:** `tournament_scheduler.home_representation`  
**Codes:** verifier — · finding `home_representation`, `home_representation_skew` · score `home_representation.max_material_spread`, `home_representation.material_skew_pool_count`  
**Providers:** mutation `home_representation_repair` · search —  
**Evidence / report:** `score_candidate.home_representation.*`, `rules_model`  
**Tests:** `tests/test_home_representation.py`  
**Precedence:** precedes — · depends on —

### `opponent_repetition`

`Soft objective` · status `active` · operator-waivable: yes  
**Meaning:** Teams should meet diverse opponents; repeated pairings beyond the configured expectation are minimized.  
**Canonical owner:** `tournament_scheduler.quality_objectives`  
**Input / fact source:** candidate generated games  
**Verifier / measurement:** `tournament_scheduler.planning_contract.score_candidate`  
**Codes:** verifier — · finding — · score `opponent_diversity.unique_pairs`, `opponent_diversity.pairwise_novelty`, `opponent_diversity.max_pair_repeat`, `opponent_diversity.pairs_meeting_3_plus`  
**Providers:** mutation — · search —  
**Evidence / report:** `score_candidate.opponent_diversity.*`, `rules_model pairwise_matchups`  
**Tests:** `tests/test_quality_objectives.py`  
**Precedence:** precedes — · depends on —

### `inter_club_diversity`

`Soft objective` · status `active` · operator-waivable: yes  
**Meaning:** Tournaments should mix teams across clubs rather than concentrating same-club matchups.  
**Canonical owner:** `tournament_scheduler.quality_objectives`  
**Input / fact source:** candidate generated games  
**Verifier / measurement:** `tournament_scheduler.planning_contract.score_candidate`  
**Codes:** verifier — · finding — · score `opponent_diversity.inter_club_diversity`, `opponent_diversity.same_club_pairing_count`, `opponent_diversity.max_same_club_teams_per_tournament`, `opponent_diversity.club_count_excess_over_2`, `opponent_diversity.tournaments_with_3plus_same_club`  
**Providers:** mutation — · search —  
**Evidence / report:** `score_candidate.opponent_diversity.inter_club_diversity`  
**Tests:** `tests/test_quality_objectives.py`  
**Precedence:** precedes — · depends on —

### `temporal_spacing`

`Soft objective` · status `active` · operator-waivable: yes  
**Meaning:** A team's tournaments should be spaced sensibly; very short turnaround gaps are minimized.  
**Canonical owner:** `tournament_scheduler.team_schedule_quality`  
**Input / fact source:** candidate tournament dates per team  
**Verifier / measurement:** `tournament_scheduler.planning_contract.score_candidate`  
**Codes:** verifier — · finding `temporal_clustering` · score `turnaround.min_turnaround_days`, `turnaround.gaps_under_days.7`, `turnaround.gaps_under_days.14`  
**Providers:** mutation — · search —  
**Evidence / report:** `score_candidate.turnaround.*`, `rules_model`  
**Tests:** `tests/test_team_schedule_quality.py`, `tests/test_fairness_temporal.py`  
**Precedence:** precedes — · depends on —

### `temporal_coverage`

`Soft objective` · status `active` · operator-waivable: yes  
**Meaning:** A team's tournaments should cover the season rather than cluster in one stretch.  
**Canonical owner:** `tournament_scheduler.temporal_coverage`  
**Input / fact source:** candidate tournament dates per team  
**Verifier / measurement:** `tournament_scheduler.planning_contract.score_candidate`  
**Codes:** verifier — · finding — · score `temporal.max_gap_days`, `temporal.offenders_count`  
**Providers:** mutation — · search —  
**Evidence / report:** `score_candidate.temporal.*`  
**Tests:** `tests/test_temporal_coverage.py`  
**Precedence:** precedes — · depends on —

### `travel_distance`

`Soft objective` · status `active` · operator-waivable: yes  
**Meaning:** Estimated team travel between home and away arenas should be kept reasonable.  
**Canonical owner:** `tournament_scheduler.club_distances`  
**Input / fact source:** configured club distances + candidate placements  
**Verifier / measurement:** `tournament_scheduler.planning_contract.score_candidate`  
**Codes:** verifier — · finding — · score `travel`  
**Providers:** mutation — · search —  
**Evidence / report:** `score_candidate travel evidence`, `rules_model travel detail rows`  
**Tests:** `tests/test_club_distances.py`  
**Precedence:** precedes — · depends on —

### `shared_host_choice`

`Operator decision` · status `active` · operator-waivable: yes  
**Meaning:** A joint registration such as Kongsberg/Tønsberg can genuinely require a contextual choice of which constituent physically carries the shared hosting responsibility. This is the limited case where agent/operator judgment over deterministic registration facts is legitimate.  
**Canonical owner:** `tournament_scheduler.shared_host_decision`  
**Input / fact source:** joint-registration club labels + hosting_responsibility facts  
**Verifier / measurement:** `tournament_scheduler.hosting_coverage.shared_registration_facts`  
**Codes:** verifier — · finding — · score —  
**Providers:** mutation `shared host decision action` · search —  
**Evidence / report:** `plan.shared_host_decisions`, `rules_model shared_host_* decisions`  
**Tests:** `tests/test_shared_host_decision.py`  
**Precedence:** precedes — · depends on `hosting_age_group_coverage`, `hosting_responsibility`

### `operator_waiver`

`Operator decision` · status `active` · operator-waivable: yes  
**Meaning:** An authorized operator may explicitly waive a classified hard planning rule for a precise scope. The planner/agent may suggest but never create or broaden a waiver; waived violations stay visible and downgrade publication readiness.  
**Canonical owner:** `tournament_scheduler.operator_waivers`  
**Input / fact source:** canonical operator waiver records  
**Verifier / measurement:** `tournament_scheduler.planning_contract.verify_candidate`  
**Codes:** verifier — · finding `operator_waivers` · score —  
**Providers:** mutation `waiver create/revoke` · search —  
**Evidence / report:** `verify_candidate.waived_violations`, `publication_readiness operator_waivers`  
**Tests:** `tests/test_operator_waivers.py`  
**Precedence:** precedes — · depends on —

### `operator_accepted_participation_deviation`

`Operator decision` · status `active` · operator-waivable: yes  
**Meaning:** An operator may deliberately live with a bounded participation deviation. The acceptance never changes the target or the schedule, is bound to the deviation scope/magnitude and becomes stale when the target changes or the deviation worsens.  
**Canonical owner:** `tournament_scheduler.participation_targets`  
**Input / fact source:** canonical decisions.json operator acceptances  
**Verifier / measurement:** `tournament_scheduler.participation_targets.evidence_covers_deviation`  
**Codes:** verifier — · finding — · score —  
**Providers:** mutation — · search —  
**Evidence / report:** `season findings`, `participation avoidability classification`  
**Tests:** `tests/test_participation_targets.py`, `tests/test_season_maintenance.py`  
**Precedence:** precedes — · depends on —

### `manual_placement_opt_in`

`Operator decision` · status `active` · operator-waivable: yes  
**Meaning:** An automatic canonical mutation may not newly introduce a manual/unt trusted-calendar placement or host-confirmation dependency. A deliberate provisional placement requires the exact explicit opt-in and is audited.  
**Canonical owner:** `tournament_scheduler.operational_acceptability`  
**Input / fact source:** current canonical baseline + candidate placements  
**Verifier / measurement:** `tournament_scheduler.operational_acceptability`  
**Codes:** verifier — · finding `manual_placement` · score —  
**Providers:** mutation — · search —  
**Evidence / report:** `operational_acceptable / requires_operational_opt_in`, `decisions.json audit`  
**Tests:** `tests/test_operational_acceptability.py`  
**Precedence:** precedes — · depends on —

### `guest_slot_reservation`

`Operator decision` · status `active` · operator-waivable: yes  
**Meaning:** The operator reserves guest places as intent; the selection among legal alternatives is recorded as a decision.  
**Canonical owner:** `tournament_scheduler.guest_slots`  
**Input / fact source:** operator guest intent + season guest-candidates  
**Verifier / measurement:** `tournament_scheduler.planning_contract.verify_candidate`  
**Codes:** verifier — · finding — · score —  
**Providers:** mutation `guest-reserve`, `guest-fill`, `guest-release` · search —  
**Evidence / report:** `season guest-report`, `season_plan.html`  
**Tests:** `tests/test_guest_slots.py`  
**Precedence:** precedes — · depends on —

### `season_quality_baseline`

`Operator decision` · status `active` · operator-waivable: yes  
**Meaning:** An operator may accept the current set and severity of non-hard findings as the season's regression reference. The baseline records stable finding ids and measurements (never aggregate counts alone), so later maintenance classifies fresh findings as known/improved/resolved/regressed/new. It never suppresses hard verification failures and never changes the schedule.  
**Canonical owner:** `tournament_scheduler.season_baseline`  
**Input / fact source:** canonical decisions.json season baseline + fresh season findings  
**Verifier / measurement:** `tournament_scheduler.planning_contract.verify_candidate`  
**Codes:** verifier — · finding — · score —  
**Providers:** mutation `season baseline create`, `season baseline advance`, `season baseline replace` · search —  
**Evidence / report:** `season findings baseline comparison`, `season/<season>/decisions.json season_baseline`, `export/evidence_bundle.json season_baseline`  
**Tests:** `tests/test_season_baseline.py`  
**Precedence:** precedes — · depends on —

### `operator_banned_date`

`Operator decision` · status `active` · operator-waivable: yes  
**Meaning:** The operator may ban a global date as unusable for every tournament; the ban is durable policy, not a one-off.  
**Canonical owner:** `tournament_scheduler.canonical_banned_dates`  
**Input / fact source:** canonical decisions.json banned dates  
**Verifier / measurement:** —  
**Codes:** verifier — · finding — · score —  
**Providers:** mutation `season ban-date`, `season unban-date` · search —  
**Evidence / report:** `season banned-dates --json`  
**Tests:** `tests/test_canonical_banned_dates.py`  
**Precedence:** precedes — · depends on —

### `operator_holiday_date_exception`

`Operator decision` · status `active` · operator-waivable: yes  
**Meaning:** The operator may allow one date that is excluded only by the derived holiday/date policy; the exception does not override explicit banned dates.  
**Canonical owner:** `tournament_scheduler.canonical_holiday_exceptions`  
**Input / fact source:** canonical decisions.json holiday_date_exceptions  
**Verifier / measurement:** —  
**Codes:** verifier — · finding — · score —  
**Providers:** mutation `season allow-holiday-date`, `season disallow-holiday-date` · search —  
**Evidence / report:** `season holiday-date-exceptions --json`  
**Tests:** `tests/test_holiday_date_policy.py`  
**Precedence:** precedes — · depends on —

### `calendar_source_trust`

`Fact / evidence semantic` · status `active` · operator-waivable: yes  
**Meaning:** A per-club calendar is only trustworthy when the configured source produced usable evidence this run. A 'known' status does not prove every candidate time is free; an untrusted calendar yields manual placement, never a silent pass.  
**Canonical owner:** `tournament_scheduler.pipeline.source_health`  
**Input / fact source:** Stage 2 source/calendar evidence + cache provenance  
**Verifier / measurement:** `tournament_scheduler.calendar_availability`  
**Codes:** verifier — · finding `manual_calendar_placements` · score —  
**Providers:** mutation — · search —  
**Evidence / report:** `club_calendar_status`, `manual_calendar_placements`, `rules_model calendar_trust_by_host`  
**Tests:** `tests/test_source_health.py`, `tests/test_calendar_availability.py`  
**Precedence:** precedes — · depends on —

### `calendar_interval_classification`

`Fact / evidence semantic` · status `active` · operator-waivable: yes  
**Meaning:** A host's calendar interval is classified fixed_busy (hard external conflict), movable_busy (host-controlled, requires host confirmation) or unclassified. The classification is policy evidence, not a scheduling rule in itself. A promoted season may also carry an explicit event-to-tournament booking association overlay that makes one fixed event non-conflicting only for the associated tournament while that event still covers the tournament's current canonical occupied interval.  
**Canonical owner:** `tournament_scheduler.calendar_availability / tournament_scheduler.calendar_bookings`  
**Input / fact source:** configured/stage-2 calendar intervals + decisions.json calendar_booking_associations  
**Verifier / measurement:** `tournament_scheduler.planning_contract.external_calendar_conflict`  
**Codes:** verifier — · finding `external_calendar_conflicts`, `movable_host_confirmation_required`, `movable_capacity_opportunity`, `stale_calendar_booking_association` · score —  
**Providers:** mutation `movable_capacity_repair`, `CanonicalSeasonService.confirm_calendar_booking` · search —  
**Evidence / report:** `movable_allocations_used`, `manual_external_conflict_placements`, `calendar_booking_associations`  
**Tests:** `tests/test_calendar_availability.py`, `tests/test_movable_capacity_repair.py`, `tests/test_approval_lifecycle.py`  
**Precedence:** precedes — · depends on —

### `club_pool_classification`

`Fact / evidence semantic` · status `active` · operator-waivable: yes  
**Meaning:** For each club x age group x scope, the registered teams' aggregate target/actual is classified as complete / intra_club_distribution / minor|material_club_pool_shortfall / single_team_deviation / over_target. The classification is the single source of truth for whether a residual is a genuine player-pool shortage.  
**Canonical owner:** `tournament_scheduler.participation_targets`  
**Input / fact source:** planning_problem club pools + candidate participations  
**Verifier / measurement:** —  
**Codes:** verifier — · finding — · score —  
**Providers:** mutation — · search —  
**Evidence / report:** `verify_candidate.participation_club_pools`, `rules_model club_participation_fairness`  
**Tests:** `tests/test_participation_targets.py`  
**Precedence:** precedes — · depends on —

### `search_coverage_evidence`

`Fact / evidence semantic` · status `active` · operator-waivable: yes  
**Meaning:** A bounded repair/search result records what was actually tried: option_available, search_incomplete, bounded_search_exhausted, proven_infeasible or inapplicable, bound to a search capability/version. Zero options means only that this search found none, never proof of global infeasibility.  
**Canonical owner:** `tournament_scheduler.search_capability`  
**Input / fact source:** repair/search provider run results  
**Verifier / measurement:** —  
**Codes:** verifier — · finding `bounded_search_exhausted`, `proven_infeasible`, `search_incomplete` · score —  
**Providers:** mutation — · search —  
**Evidence / report:** `season findings search_coverage`, `stage3 converge coverage`  
**Tests:** `tests/test_search_neighborhood_repair.py`  
**Precedence:** precedes — · depends on —

### `approval_lifecycle`

`Fact / evidence semantic` · status `active` · operator-waivable: yes  
**Meaning:** Operator approvals/locks live in decisions.json separately from schedule facts. A protected placement whose fingerprint changed becomes a stale approval whose lock is dropped; an approval whose tournament is gone becomes orphaned. Both require re-review.  
**Canonical owner:** `tournament_scheduler.canonical_baseline`  
**Input / fact source:** canonical decisions.json approvals  
**Verifier / measurement:** —  
**Codes:** verifier `stale_approval`, `orphaned_approval` · finding `stale_approvals`, `orphaned_approvals` · score —  
**Providers:** mutation — · search —  
**Evidence / report:** `verify_candidate.stale_approvals`, `verify_candidate.orphaned_approvals`  
**Tests:** `tests/test_approval_lifecycle.py`, `tests/test_canonical_baseline.py`  
**Precedence:** precedes — · depends on —

### `verification_completeness`

`Fact / evidence semantic` · status `active` · operator-waivable: yes  
**Meaning:** Verification reports which checks were skipped because their required input was unavailable. An incomplete verification is never a pass.  
**Canonical owner:** `tournament_scheduler.planning_contract`  
**Input / fact source:** available planning problem fields  
**Verifier / measurement:** —  
**Codes:** verifier — · finding `incomplete_verification` · score —  
**Providers:** mutation — · search —  
**Evidence / report:** `verify_candidate.skipped`, `publication_readiness incomplete_verification`  
**Tests:** `tests/test_planning_contract.py`  
**Precedence:** precedes — · depends on —
