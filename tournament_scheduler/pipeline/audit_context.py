"""Assembles the read-only evidence inventory a harness-led semantic audit
needs to review the current Stage 4 export (issue #325 Phase 1).

:func:`build_audit_context` never decides PASS/FAIL/REVIEW_REQUIRED itself —
it only gathers facts already produced by the pipeline (Stage 4's own
verification/fingerprint, the run evidence bundle, source/calendar
evidence) so the harness (or the headless judge in
:mod:`tournament_scheduler.llm_judge.audit`) can reason without re-deriving
policy or doing fresh live scraping, per the issue's explicit non-goals.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .audit_result import current_export_fingerprint, current_run_id
from .fingerprints import stable_payload_sha256
from .state import PipelineState, StageName

AUDIT_PROMPT_VERSION = 1

# The canonical 9-item operator checklist (issue #325). Item 9 is the
# open-ended, most-important item — the audit's entire reason for existing
# is to surface problems/missing rules no existing check already covers.
# This constant is the single source of truth: SKILL.md's prose copy must
# match it verbatim (see tests/test_skill_ownership.py).
AUDIT_CHECKLIST: tuple[dict[str, Any], ...] = (
    {"item_id": 1, "question": "Antall cuper pr lag?"},
    {"item_id": 2, "question": "Antall hjemmeturneringer pr lag?"},
    {"item_id": 3, "question": "Lengde på turneringer?"},
    {"item_id": 4, "question": "Er det faktisk ledig tid på is?"},
    {"item_id": 5, "question": "Deltar vertsklubben i samme turnering?"},
    {"item_id": 6, "question": "Deltar hvert lag maksimalt én gang per dag?"},
    {"item_id": 7, "question": "Er det normalt maks 2 lag fra samme klubb, med 3 kun som synlig unntak?"},
    {"item_id": 8, "question": "Er eksportformatene konsistente?"},
    {
        "item_id": 9,
        "question": (
            "Ser harnesset andre materielle problemer eller manglende regler vi ikke "
            "allerede har tenkt på?"
        ),
    },
)

_SKILL_MD_PATH = Path(__file__).resolve().parents[2] / ".agents" / "skills" / "rvv" / "SKILL.md"
_AUDIT_POLICY_HEADING = "## Semantic safety-net audit (post-export, pre-publication)"


def _runbook_version() -> str:
    """Hash of the canonical SKILL.md audit-policy section, or of the whole
    file if the section can't be located (keeps this functional even if the
    heading text drifts, at the cost of a coarser version signal)."""
    try:
        markdown = _SKILL_MD_PATH.read_text(encoding="utf-8")
    except OSError:
        return "unavailable"
    lines = markdown.splitlines()
    try:
        start = next(i for i, line in enumerate(lines) if line.strip() == _AUDIT_POLICY_HEADING)
    except StopIteration:
        return stable_payload_sha256(markdown)[:16]
    body: list[str] = []
    for line in lines[start + 1 :]:
        stripped = line.strip()
        if stripped.startswith("## "):
            break
        body.append(line)
    return stable_payload_sha256("\n".join(body))[:16]


def _read_evidence_bundle(export_dir: "str | None") -> dict[str, Any] | None:
    if not export_dir:
        return None
    path = Path(export_dir) / "evidence_bundle.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def build_audit_context(*, work_dir: "str | Path") -> dict[str, Any]:
    """Assemble the audit evidence inventory for the current run's export.

    Pure read of already-persisted checkpoints/artifacts: no fresh scraping,
    no re-deriving deterministic policy (the deterministic verify result is
    read as Stage 4 already computed it, not recomputed here).
    """
    from .run_manifest import RunManifest

    state = PipelineState(work_dir)
    export_checkpoint = state.read_stage(StageName.EXPORT)
    planning_checkpoint = state.read_stage(StageName.PLANNING)
    plan_dict = planning_checkpoint.get("plan") if isinstance(planning_checkpoint, dict) else None

    manifest = RunManifest(work_dir).read()
    export_dir = export_checkpoint.get("export_dir")

    deterministic_verify_result = export_checkpoint.get("verify_result") or {}
    publication_readiness: dict[str, Any] | None = None
    if isinstance(plan_dict, dict) and plan_dict.get("publication_readiness"):
        publication_readiness = plan_dict.get("publication_readiness")
    else:
        from ..final_verification import publication_readiness as _compute_readiness

        publication_readiness = _compute_readiness(deterministic_verify_result)

    evidence_bundle = _read_evidence_bundle(export_dir)
    calendar_evidence_summary = (evidence_bundle or {}).get("source_summary") or {}

    return {
        "audit_prompt_version": AUDIT_PROMPT_VERSION,
        "runbook_version": _runbook_version(),
        "checklist": [dict(item) for item in AUDIT_CHECKLIST],
        "run_id": current_run_id(work_dir),
        "export_fingerprint": current_export_fingerprint(work_dir),
        "source_fingerprints": {
            "input_fingerprint": manifest.get("input_fingerprint"),
            "effective_config_fingerprint": manifest.get("effective_config_fingerprint"),
        },
        "export_dir": export_dir,
        "output_files": export_checkpoint.get("output_files") or {},
        "deterministic_verify_result": deterministic_verify_result,
        "publication_readiness": publication_readiness,
        "evidence_bundle": evidence_bundle,
        "calendar_evidence_summary": calendar_evidence_summary,
    }
