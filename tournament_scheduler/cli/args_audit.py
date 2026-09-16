"""Argparse subparsers for the harness-led semantic safety-net audit
(issue #325) — split out of ``args.py`` to stay within the repo's
300-line-per-file guideline (``scripts/check_file_length.py``).

"audit-context" and "audit-submit" mirror the existing read-context/
submit-decision shape (SKILL.md's "Structured decision protocol"): an
interactive harness reads context, reasons in-session, then submits its
verdict. "audit-run" is the headless-only path that does all three in one
call.
"""

from __future__ import annotations

import argparse
from pathlib import Path


def add_operator_audit_subparsers(operator_sub: "argparse._SubParsersAction") -> None:
    op_audit_context = operator_sub.add_parser(
        "audit-context",
        help="Print the assembled evidence inventory for the semantic safety-net audit (issue #325)",
    )
    op_audit_context.add_argument(
        "--work-dir",
        default=".pipeline",
        help="Pipeline work directory (default: .pipeline)",
    )

    op_audit_evidence = operator_sub.add_parser(
        "audit-evidence",
        help="Return detailed semantic-audit evidence for one bounded selector "
        "(item/tournament/club/age-group/category) instead of the whole evidence bundle",
    )
    op_audit_evidence.add_argument(
        "--work-dir",
        default=".pipeline",
        help="Pipeline work directory (default: .pipeline)",
    )
    op_audit_evidence.add_argument(
        "--item",
        type=int,
        default=None,
        help="Operator-checklist item number (1-9)",
    )
    op_audit_evidence.add_argument(
        "--tournament",
        default=None,
        help="Durable tournament id to retrieve evidence for",
    )
    op_audit_evidence.add_argument("--club", default=None, help="Club name (shared-registration aware)")
    op_audit_evidence.add_argument(
        "--age-group",
        dest="age_group",
        default=None,
        help="Age group (for example U10, JU12)",
    )
    op_audit_evidence.add_argument(
        "--category",
        default=None,
        help="Evidence category or finding type (for example participation_shortfalls)",
    )
    op_audit_evidence.add_argument(
        "--unresolved",
        action="store_true",
        help="Only return unresolved/manual findings",
    )
    op_audit_evidence.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Maximum records to return (default: repository evidence-query limit)",
    )

    op_audit_submit = operator_sub.add_parser(
        "audit-submit",
        help="Persist a structured PASS/REVIEW_REQUIRED/FAIL semantic audit verdict (issue #325)",
    )
    op_audit_submit.add_argument(
        "--work-dir",
        default=".pipeline",
        help="Pipeline work directory (default: .pipeline)",
    )
    op_audit_submit_group = op_audit_submit.add_mutually_exclusive_group(required=True)
    op_audit_submit_group.add_argument(
        "--result-file", type=Path, default=None, help="Path to a JSON file containing the structured audit result"
    )
    op_audit_submit_group.add_argument(
        "--result-json", default=None, help="The structured audit result as a JSON string"
    )

    op_audit_run = operator_sub.add_parser(
        "audit-run",
        help="Headless semantic audit: build context, call an LLM judge backend, submit the "
        "result (issue #325) — for cron/CI where no interactive harness is orchestrating",
    )
    op_audit_run.add_argument(
        "--work-dir",
        default=".pipeline",
        help="Pipeline work directory (default: .pipeline)",
    )
    op_audit_run.add_argument(
        "--backend",
        required=True,
        choices=["claude", "openai", "llm_bridge"],
        help="LLM judge backend to call for this audit — deliberately not inherited from "
        "RVV_JUDGE_BACKEND, since this is a higher-stakes safety-net check than routine "
        "inter-stage judgments and should not silently pick up whatever backend happens to be configured elsewhere",
    )
    op_audit_run.add_argument(
        "--force",
        action="store_true",
        help="Run the headless audit even while an interactive harness session is detected "
        "(normally the harness should perform the audit itself in-session instead)",
    )
