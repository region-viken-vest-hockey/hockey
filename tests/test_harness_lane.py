"""Harness verification lane contract.

The harness lane is intentionally optional, but it must not disappear as a
zero-collected pytest selection. This sentinel is collected by
``scripts/check harness`` and reports the missing external runtime as an
explicit skip unless the caller opts into a concrete smoke command.
"""

from __future__ import annotations

import os
import shlex
import subprocess

import pytest

pytestmark = pytest.mark.harness


def test_harness_runtime_smoke_prerequisite_is_explicit() -> None:
    command = os.environ.get("RVV_HARNESS_SMOKE_COMMAND")
    if not command:
        pytest.skip(
            "harness runtime unavailable: set RVV_HARNESS_SMOKE_COMMAND to a bounded smoke command"
        )

    result = subprocess.run(shlex.split(command), text=True, capture_output=True, timeout=30, check=False)

    assert result.returncode == 0, result.stderr or result.stdout
