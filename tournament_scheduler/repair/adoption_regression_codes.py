"""Regression code vocabulary for the adoption guard.

These codes are the stable contract an operator override names, so they live in one
small module. ``repair.adoption_guard`` re-exports them.
"""

from __future__ import annotations

# Every material regression the guard can raise. The code vocabulary is the
# contract an operator override names; it must stay stable.
REGRESSION_HARD_VERIFICATION = "hard_verification_regression"
REGRESSION_MORE_GAPS_UNDER_7 = "more_gaps_under_7_days"
REGRESSION_MORE_GAPS_UNDER_14 = "more_gaps_under_14_days"
REGRESSION_TEMPORAL_COVERAGE = "temporal_coverage_materially_worse"
REGRESSION_TEMPORAL_OFFENDERS = "temporal_offenders_worse"
REGRESSION_PARTICIPATION_DEVIATION = "participation_target_deviation_worse"
REGRESSION_PARTICIPATION_SHORTFALL = "club_pool_shortfall_worse"
REGRESSION_PARTICIPATION_AVOIDABLE = "avoidable_participation_worse"
REGRESSION_CLUB_EXPOSURE = "more_concentrated_club_exposure"
REGRESSION_CLUB_REPETITION = "club_pair_repetition_worse"
REGRESSION_SAME_CLUB_CLUSTERING = "same_club_clustering_worse"
REGRESSION_UNIQUE_OPPONENTS = "fewer_unique_opponent_clubs"
REGRESSION_INTER_CLUB_COVERAGE = "inter_club_coverage_worse"
REGRESSION_HOSTING_BALANCE = "hosting_balance_worse"
REGRESSION_HOSTING_OBLIGATION = "unresolved_hosting_obligation_worse"
REGRESSION_HOME_REPRESENTATION = "home_representation_worse"
REGRESSION_TRAVEL = "travel_materially_worse"
REGRESSION_CYCLE = "cycle_detected"

# Codes an operator may explicitly accept. A hard-verification regression is
# deliberately not acceptable here: a hard constraint is never a soft
# trade-off. Participation target compliance stays a guardrail the operator
# may accept deliberately (it is a strong goal, not a legality rule).
ACCEPTABLE_ADOPTION_REGRESSION_CODES = frozenset(
    {
        REGRESSION_MORE_GAPS_UNDER_7,
        REGRESSION_MORE_GAPS_UNDER_14,
        REGRESSION_TEMPORAL_COVERAGE,
        REGRESSION_TEMPORAL_OFFENDERS,
        REGRESSION_PARTICIPATION_DEVIATION,
        REGRESSION_PARTICIPATION_SHORTFALL,
        REGRESSION_PARTICIPATION_AVOIDABLE,
        REGRESSION_CLUB_EXPOSURE,
        REGRESSION_CLUB_REPETITION,
        REGRESSION_SAME_CLUB_CLUSTERING,
        REGRESSION_UNIQUE_OPPONENTS,
        REGRESSION_INTER_CLUB_COVERAGE,
        REGRESSION_HOSTING_BALANCE,
        REGRESSION_HOSTING_OBLIGATION,
        REGRESSION_HOME_REPRESENTATION,
        REGRESSION_TRAVEL,
    }
)
