#!/usr/bin/env python3
"""Guarded republish preparation/resume flow for a published/sealed season.

The interactive semantic audit belongs to the active harness. This script
materializes the exact canonical export and evidence, exposes the audit context,
and can resume the repository-owned publish preflight after the harness has
submitted a verdict for that export.

It never replans, refreshes calendars, mutates canonical season state, invokes a
nested/headless LLM judge, or bypasses publication gates.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RVV = ROOT / "scripts" / "rvv-miniputt"


def _run(rvv: str, *args: str) -> None:
    cmd = [rvv, *args]
    print("+ " + " ".join(cmd), flush=True)
    subprocess.run(cmd, cwd=ROOT, check=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Prepare a published-season republish for harness semantic audit, "
            "or resume publish preflight after that audit was submitted."
        )
    )
    parser.add_argument("--season", required=True, help="Season id, e.g. 2026-2027")
    parser.add_argument(
        "--resume-after-audit",
        action="store_true",
        help="Skip regeneration and resume at publication preflight using the current audited export",
    )
    parser.add_argument(
        "--confirm-public",
        action="store_true",
        help="On resume, actually publish after the repository preflight succeeds",
    )
    args = parser.parse_args(argv)

    rvv = os.environ.get("RVV", str(DEFAULT_RVV))

    try:
        if not args.resume_after_audit:
            # Establish the authoritative published/sealed lifecycle.
            _run(rvv, "season", "lifecycle", "--season", args.season, "--json")

            # Schedule-preserving regeneration from current canonical state.
            _run(rvv, "season", "export", "--season", args.season)

            # Show the complete published-to-canonical replacement delta.
            _run(rvv, "season", "publication-evidence", "--season", args.season, "--json")

            # Materialize/read the bounded semantic-audit context for the active
            # harness. Do not invoke operator audit-run here: that is the
            # headless cron/CI path and would create a nested model judgment.
            _run(rvv, "operator", "audit-context")

            print(
                "\nRepublish prepared. Nothing was published.\n"
                "The active harness must now review this exact export with "
                "operator audit-context / audit-evidence and submit its verdict "
                "with operator audit-submit. Then resume with:\n"
                f"  make season-republish SEASON={args.season} RESUME_AFTER_AUDIT=1"
            )
            return 0

        # Resume only: do not regenerate, because the submitted semantic audit
        # is fingerprint-bound to the existing export.
        _run(rvv, "season", "lifecycle", "--season", args.season, "--json")
        _run(rvv, "season", "publication-evidence", "--season", args.season, "--json")

        # Repository-owned publication preflight validates audit freshness,
        # canonical reconciliation, parity/freshness, sanitization and exact
        # publication scope. Any missing/stale audit fails closed here.
        _run(rvv, "operator", "publish", "--dry-run")

        if not args.confirm_public:
            print(
                "Publish preview passed. Nothing was published. "
                "Re-run with RESUME_AFTER_AUDIT=1 CONFIRM_PUBLIC=1 to publish."
            )
            return 0

        _run(rvv, "operator", "publish", "--confirm-public")
        _run(rvv, "operator", "verify")
        return 0
    except subprocess.CalledProcessError as exc:
        return exc.returncode or 1


if __name__ == "__main__":
    sys.exit(main())
