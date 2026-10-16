"""Thin ``shell`` command handlers. They call the operator shell services and print the result.

No scheduling, verification or publication logic lives here. Exit status: 0 for
a read-only view, a performed refresh, or a repository that is already current;
2 when a requested refresh was refused.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from tournament_scheduler import distribution, operator_shell
from tournament_scheduler.infrastructure.claude_runtime import ClaudeRunner, run_probe
from tournament_scheduler.infrastructure.repository_sync import (
    CredentialStore,
    RepoState,
    RepoSnapshot,
    WindowsCredentialManagerStore,
    run_git,
)


def _repo(args: argparse.Namespace) -> Path:
    repo = getattr(args, "repo", None)
    return Path(repo) if repo else Path.cwd()


def _refresh_exit(ok: bool, snapshot: RepoSnapshot | None) -> int:
    if ok or (snapshot is not None and snapshot.state is RepoState.READY):
        return 0
    return 2


def _cmd_shell_readiness(args: argparse.Namespace, store: CredentialStore, claude_runner: ClaudeRunner) -> int:
    view = operator_shell.gather_readiness(
        _repo(args),
        cache_dir=distribution.default_update_cache_dir(),
        store=store,
        claude_runner=claude_runner,
        installed=distribution.version_metadata()["version"],
        runner=run_git,
    )
    if getattr(args, "json", False):
        print(json.dumps(view.to_dict(), sort_keys=True, ensure_ascii=False))
    else:
        print(operator_shell.render_readiness(view))
    return 0


def _cmd_shell_refresh(args: argparse.Namespace) -> int:
    if args.confirm is None:
        preview = operator_shell.preview_refresh(_repo(args), runner=run_git)
        print(operator_shell.render_refresh_preview(preview))
        return _refresh_exit(preview.can_refresh, preview.snapshot)
    outcome = operator_shell.apply_refresh(_repo(args), confirm_head=args.confirm, runner=run_git)
    print(operator_shell.render_refresh_outcome(outcome))
    return _refresh_exit(outcome.performed, outcome.snapshot)


def _cmd_shell(
    args: argparse.Namespace,
    *,
    store: CredentialStore | None = None,
    claude_runner: ClaudeRunner = run_probe,
) -> int:
    """Dispatch ``rvv-miniputt shell <subcommand>``. With no subcommand, show readiness."""
    subcommand = getattr(args, "shell_command", None) or "readiness"
    if subcommand == "readiness":
        credential_store = store if store is not None else WindowsCredentialManagerStore()
        return _cmd_shell_readiness(args, credential_store, claude_runner)
    if subcommand == "refresh":
        return _cmd_shell_refresh(args)
    if subcommand == "publish":
        print(operator_shell.render_publication_handoff())
        return 0
    print("Unknown shell command. Run 'rvv-miniputt shell --help'.", file=sys.stderr)
    return 2
