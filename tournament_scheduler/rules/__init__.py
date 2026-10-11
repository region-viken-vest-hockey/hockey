"""Rules model and report: the planner-independent rules/audit model and its renderers.

``model`` builds the per-run rules model from the canonical rule semantics,
``obligations`` holds the operational-obligation entries, ``report`` renders the
rules report, and ``capacity_rules``/``operational_rules`` hold the report's
capacity and operational entries. The rules model displays serialized or
recomputed facts; it must not become the business-policy engine (see
``rule_catalog`` for the semantic owners). Layer: domain/presentation helpers;
CLI and infrastructure must not be imported from here.
"""
