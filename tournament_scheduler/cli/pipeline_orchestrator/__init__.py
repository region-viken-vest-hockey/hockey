"""Pipeline-oriented RVV CLI command handlers.

Split into topical submodules (manifest persistence, judging/verdict logic,
refinement, stage1-4 runners, stage3 optimization, interactive/shared-host
state, plan adoption, operator subcommands, calendars/scrape) so no single
file exceeds the repo's file-length guideline. This package re-exports only
the names imported directly by ``rvv_cli.py``.
"""

from __future__ import annotations

from .calendars_scrape import _cmd_calendars, _cmd_scrape
from .operator_publish import (
    _cmd_operator_publish,
    _cmd_operator_publish_history,
    _cmd_operator_rollback,
    _cmd_operator_verify,
    _execute_operator_publish,
    _print_pages_result,
)
from .operator_run import _cmd_operator_run
from .run_command import _cmd_run

__all__ = [
    "_cmd_calendars",
    "_cmd_operator_publish",
    "_cmd_operator_publish_history",
    "_cmd_operator_rollback",
    "_cmd_operator_run",
    "_cmd_operator_verify",
    "_execute_operator_publish",
    "_print_pages_result",
    "_cmd_run",
    "_cmd_scrape",
]
