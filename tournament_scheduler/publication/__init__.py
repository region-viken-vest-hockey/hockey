"""Publication domain: sealed-season publication identity, baselines and export guards.

The published-season baseline and its sealed-lifecycle state (``baseline``), the
reconciliation of a published baseline with later canonical mutations
(``mutation_history``), the schedule-preservation guard that compares an export
with canonical state (``projection_guard``), tournament-scoped incremental
republish eligibility (``scope``), immutable publication evidence (``evidence``)
and export lifecycle manifests (``export_lifecycle``). Layer: domain helpers;
CLI, infrastructure and the pipeline orchestration must not be imported from here
except where the existing dependency is recorded in the architecture doc.
"""
