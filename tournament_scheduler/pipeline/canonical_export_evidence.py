"""Canonical ``season export`` immutable evidence bundle.

Split out of :mod:`.evidence_bundle` so the promoted-season maintenance path
can build the same self-describing artifact without growing the interactive
run bundle's module past the repository's file-length guideline.

The canonical path has no live run manifest or Stage 3 controller log, so this
records the provenance the promoted season *does* own — verification-context
run id, the promoted normalized problem, the final selected plan, its
independently re-verified/scored result, the export fingerprint, and the
reconciled operator evidence — instead of pretending transient ``.pipeline``
state still exists.
"""

from __future__ import annotations

from typing import Any

from .evidence_bundle import build_final_operator_evidence, build_run_evidence_bundle


def build_canonical_export_evidence(
    *,
    schedule: dict[str, Any],
    export_checkpoint: dict[str, Any],
) -> dict[str, Any]:
    """Build the immutable evidence bundle for a canonical ``season export``.

    This is what lets a committed export directory reconstruct evidence ->
    audit input -> audit judgment without the original working directory.
    """
    from ..planning_contract import extract_candidate, score_candidate
    from .fingerprints import stable_payload_sha256

    plan_dict = schedule.get("plan") if isinstance(schedule.get("plan"), dict) else None
    verification_context = schedule.get("verification_context")
    verification_context = verification_context if isinstance(verification_context, dict) else {}
    problem = verification_context.get("problem")

    candidate: dict[str, Any] | None = None
    if plan_dict is not None:
        try:
            candidate = extract_candidate({"plan": plan_dict})
        except ValueError:
            candidate = None
    verify_result = export_checkpoint.get("verify_result") or {}
    score_result = score_candidate(candidate, problem=problem) if candidate is not None else None
    run_id = str(verification_context.get("run_id") or "")
    final_fingerprint = (
        stable_payload_sha256(candidate.get("tournaments", [])) if candidate is not None else None
    )
    approval_status = export_checkpoint.get("approval_status")
    final_operator_evidence = build_final_operator_evidence(
        run_id=run_id,
        plan_dict=plan_dict,
        final_candidate_fingerprint=final_fingerprint,
        export_fingerprint=export_checkpoint.get("export_fingerprint"),
        final_verify_result=verify_result,
        approval_status=approval_status if isinstance(approval_status, dict) else None,
    )

    public_context = export_checkpoint.get("public_export_context")
    scrape = (
        public_context.get("scrape")
        if isinstance(public_context, dict) and isinstance(public_context.get("scrape"), dict)
        else {}
    )
    bundle = build_run_evidence_bundle(
        run_id=run_id,
        input_fingerprint={},
        decision_log=[],
        scraping_checkpoint={
            "sources": [],
            "blocked": list(scrape.get("blocked") or []),
            "club_calendar_status": {},
        },
        stage3_attempt_log=[],
        final_candidate=candidate,
        final_verify_result=verify_result,
        final_score_result=score_result,
        export_dir=export_checkpoint.get("export_dir"),
        export_output_files=export_checkpoint.get("output_files"),
        export_fingerprint=export_checkpoint.get("export_fingerprint"),
        export_verify_result=verify_result,
        final_operator_evidence=final_operator_evidence,
        controller_trace=None,
    )
    bundle["evidence_source"] = "canonical_season_export"
    bundle["canonical_season"] = schedule.get("season")
    bundle["canonical_revision"] = schedule.get("revision") or schedule.get("fingerprint")
    if export_checkpoint.get("season_baseline"):
        bundle["season_baseline"] = export_checkpoint.get("season_baseline")
    return bundle
