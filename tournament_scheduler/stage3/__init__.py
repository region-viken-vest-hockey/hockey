"""Stage 3 search domain: local search, CP-SAT, engine dispatch and decision gating.

Planner-driven candidate improvement after the baseline plan exists. The optimizer
and CP-SAT engines propose candidates; ``ab``/``shadow`` compare them and
``decision`` turns that comparison into a reviewable decision. Layer: domain and
application helpers; the CLI and infrastructure layers must not be imported from here.
"""
