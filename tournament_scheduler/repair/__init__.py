"""Repair providers: deterministic, verified repair options for scheduling findings.

Each provider module enumerates bounded repair options for one family of findings
(placement, host representation, roster fill, home/participation spread, movable
ice, unplaced obligations, relocation off excluded dates, and bounded neighborhood
search). A provider proposes candidates only; the canonical verifier, the operational
acceptability predicate and the canonical mutation boundary decide what is applied.
The dispatch and adoption boundary lives in ``repair_options``, ``local_repair_options``
and ``repair_application`` at the package root. Layer: domain/application helpers;
CLI and infrastructure must not be imported from here.
"""
