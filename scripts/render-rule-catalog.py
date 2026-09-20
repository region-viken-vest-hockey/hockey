#!/usr/bin/env python3
"""Regenerate the agent-facing scheduling rule catalog.

Writes ``docs/architecture/rule-catalog.md`` from the single canonical source
``tournament_scheduler/rule_catalog.py``. Pass ``--check`` to fail when the
committed document is stale (used by tests/CI).
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tournament_scheduler.rule_catalog import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
