"""Reproducible shadow-comparison evidence for a candidate engine (issue #276).

A shadow engine run (``cp_sat`` today) is only trustworthy evidence if a
reviewer can later confirm exactly which baseline/problem it ran against and
what it produced -- without re-running the solver. :func:`build_shadow_report`
wraps :func:`stage3_ab.build_ab_report` with content-hash fingerprints of the
baseline candidate, the planning problem, and the shadow candidate, plus the
shadow engine's own ``source`` metadata (engine/version/status/runtime/seed).

Deliberately separate from :mod:`stage3_ab`: an A/B report answers "is the
new candidate better", this module answers "can this specific comparison be
reproduced and audited later". Shadow mode never applies or publishes a
candidate on its own -- this is evidence for a human/controller decision,
not a promotion mechanism.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional

from .pipeline.fingerprints import stable_payload_sha256
from .stage3_ab import build_ab_report

SHADOW_REPORT_SCHEMA_VERSION = 1


def build_shadow_report(
    baseline_candidate: Dict[str, Any],
    shadow_candidate: Dict[str, Any],
    problem: Optional[Dict[str, Any]] = None,
    *,
    engine: str,
) -> Dict[str, Any]:
    """Build a fingerprinted, reproducible shadow-engine evidence artifact.

    *baseline_candidate* is the production candidate the shadow engine ran
    against; *shadow_candidate* is what the shadow engine (*engine*, e.g.
    ``"cp_sat"``) produced from it. The fingerprints let a reviewer confirm,
    from the artifact alone, that a given ``ab_report`` was computed from
    exactly this baseline/problem/candidate triple -- without re-running the
    solver or trusting a prose description of "what was compared".
    """
    ab_report = build_ab_report(baseline_candidate, shadow_candidate, problem)
    return {
        "schema_version": SHADOW_REPORT_SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "engine": engine,
        "fingerprints": {
            "baseline_candidate": stable_payload_sha256(baseline_candidate.get("tournaments", [])),
            "shadow_candidate": stable_payload_sha256(shadow_candidate.get("tournaments", [])),
            "problem": stable_payload_sha256(problem) if problem is not None else None,
        },
        "shadow_candidate_source": shadow_candidate.get("source"),
        "ab_report": ab_report,
    }
