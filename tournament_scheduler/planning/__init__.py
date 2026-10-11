"""Planning domain: planning problem, candidate verification and scoring.

The canonical candidate contract and its independent hard verifier and scorer
(``contract``), the scoring blocks it delegates to (``contract_distribution``),
the shared before/after-Christmas split (``half``) and final export-time
verification and readiness (``verification``). Layer: domain/application
helpers; CLI and infrastructure must not be imported from here.
"""
