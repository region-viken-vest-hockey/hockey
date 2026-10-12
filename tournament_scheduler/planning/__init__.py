"""Planning domain: planning problem, candidate verification and scoring, and the Stage 1/2 planner.

The canonical candidate contract and its independent hard verifier and scorer
(``contract``), the scoring blocks it delegates to (``contract_distribution``), the
shared before/after-Christmas split (``half``), final export-time verification
(``verification``), the initial season planner (``season_planner``) with its
participant-selection, roster-sizing, warning, start-date and derived-state helpers,
and the canonical date-admissibility policy (``date_policy``). Layer: domain helpers;
CLI and infrastructure must not be imported from here.
"""
