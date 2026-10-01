#!/usr/bin/env python3
"""Guarded one-command republish flow for a published/sealed season.

This script only orchestrates existing repository-owned commands. It does not
replan, refresh calendars, mutate canonical season state, or bypass any
publication gate.
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
            "Regenerate, verify, audit and preview a published season; "
            "optionally publish and verify the deployed result."
        )
    )
    parser.add_argument("--season", required=True, help="Season id, e.g. 2026-2027")
    parser.add_argument(
        "--backend",
        required=True,
        choices=("claude", "openai", "llm_bridge"),
        help="Semantic audit backend",
    )
    parser.add_argument(
        "--confirm-public",
        action="store_true",
        help="Actually publish after every preview/gate succeeds",
    )
    args = parser.parse_args(argv)

    rvv = os.environ.get("RVV", str(DEFAULT_RVV))

    try:
        # Establish published/sealed lifecycle and authoritative baseline.
        _run(rvv, "season", "lifecycle", "--season", args.season, "--json")

        # Schedule-preserving regeneration from current canonical state.
        _run(rvv, "season", "export", "--season", args.season)

        # Show the complete published-to-canonical replacement delta.
        _run(rvv, "season", "publication-evidence", "--season", args.season, "--json")

        # Semantic safety-net audit bound to the fresh export.
        _run(rvv, "operator", "audit-run", "--backend", args.backend)

        # Repository-owned publication preflight: freshness, parity,
        # sanitization, publication scope and other hard gates.
        _run(rvv, "operator", "publish", "--dry-run")

        if not args.confirm_public:
            print(
                "Preview complete. Nothing was published. "
                "Re-run with --confirm-public to publish this verified export."
            )
            return 0

        _run(rvv, "operator", "publish", "--confirm-public")
        _run(rvv, "operator", "verify")
        return 0
    except subprocess.CalledProcessError as exc:
        return exc.returncode or 1


if __name__ == "__main__":
    sys.exit(main())
