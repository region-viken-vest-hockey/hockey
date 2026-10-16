"""Claude runtime readiness, compatibility contract and operate dispatch (epic #364, #620).

Owners:

* ``contract``: supported Claude version range and agent protocol version
* ``probe``: readiness states (pure classification behind an injectable runner)
* ``dispatch``: pre-mutation fail-closed check, then one ``/rvv-miniputt:operate`` run
* ``login``: launches the supported ``claude auth login`` command as a subprocess
* ``runner``: the only module that starts the Claude executable

Credentials are owned by the Claude CLI. This package never reads or stores them.
"""

from .contract import SUPPORTED_CONTRACT, RuntimeContract, Version
from .dispatch import (
    DispatchOutcome,
    RuntimeBlockedError,
    check_dispatch_allowed,
    dispatch_operate,
    operate_argv,
)
from .login import launch_login
from .probe import (
    ClaudeRunner,
    CommandResult,
    RuntimeNotFoundError,
    RuntimeReadiness,
    RuntimeState,
    probe_runtime,
)
from .runner import run_operate, run_probe

__all__ = [
    "SUPPORTED_CONTRACT", "ClaudeRunner", "CommandResult", "DispatchOutcome", "RuntimeBlockedError",
    "RuntimeContract", "RuntimeNotFoundError", "RuntimeReadiness", "RuntimeState", "Version",
    "check_dispatch_allowed", "dispatch_operate", "launch_login", "operate_argv", "probe_runtime",
    "run_operate", "run_probe",
]
