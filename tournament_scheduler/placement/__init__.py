"""Tournament placement: placed/provisional/unplaced state, unresolved findings and infeasibility.

``normalization`` classifies every tournament's placement state across all planning
paths, ``findings`` gives unresolved placement findings a stable identity, and
``infeasibility`` records a durable, revision-bound proof that an obligation cannot be
placed. ``participant_relocation`` moves same-date parallel slots that a date cannot
host. Layer: domain helpers; CLI and infrastructure must not be imported from here.
"""
