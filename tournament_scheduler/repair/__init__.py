"""Repair: deterministic, verified repair options for scheduling findings.

The provider modules each enumerate bounded repair options for one family of findings
(placement, host representation, roster fill, home/participation spread, movable ice,
unplaced obligations, relocation off excluded dates, and bounded neighborhood search).
A provider proposes candidates only; the canonical verifier, the operational
acceptability predicate and the canonical mutation boundary decide what is applied.
``dispatch`` discovers and routes options to providers, ``local_options`` is the common
application boundary over them, ``evaluation`` measures consequences, ``adoption_guard``
checks cross-rule before/after adoption, ``apply`` performs atomic application, and
``capability``/``coverage`` identify and record search capability. Layer: domain and
application helpers; CLI and infrastructure must not be imported from here.
"""
