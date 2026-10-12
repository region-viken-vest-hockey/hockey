"""Shared scoring: planner-independent quality objectives and multi-objective dominance.

``quality_objectives`` derives the one quality objective vector and comparison from
``score_candidate``, ``pareto`` owns the single dominance relation and bounded
down-select used by every multi-objective surface, and ``game_metrics`` holds plan
metric helpers. Scoring measures and compares; it never verifies legality and never
redefines a rule (the rule catalog in ``rules.catalog`` names the owners). Layer:
domain helpers; CLI and infrastructure must not be imported from here.
"""
