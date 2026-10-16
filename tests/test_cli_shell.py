"""``rvv-miniputt shell`` wiring: parser, thin handlers, exit status and JSON output (offline)."""

from __future__ import annotations

import json

import pytest

from tests.claude_runtime_support import auth_output, runner_for, version_output
from tests.distribution_signing_support import trust
from tests.operator_shell_support import make_dirty
from tests.repository_sync_support import advance_remote, head, write_cache
from tournament_scheduler.cli.args import build_parser
from tournament_scheduler.cli.shell_command import _cmd_shell
from tournament_scheduler.infrastructure.repository_sync import InMemoryCredentialStore


@pytest.fixture(autouse=True)
def _cache_is_signed_by_test_signer(monkeypatch):
    trust(monkeypatch)


@pytest.fixture
def data_root(tmp_path, monkeypatch):
    root = tmp_path / "data"
    monkeypatch.setenv("RVV_MINIPUTT_DATA_DIR", str(root))
    write_cache(root / "update-cache")
    return root


def _run(argv, capsys, store=None):
    args = build_parser().parse_args(argv)
    code = _cmd_shell(
        args,
        store=store if store is not None else InMemoryCredentialStore(),
        claude_runner=runner_for(version_output("2.1.296"), auth_output(True)),
    )
    return code, capsys.readouterr().out


def test_readiness_json_is_credential_free_and_blocks_without_sign_in(remote_pair, data_root, capsys):
    _, checkout = remote_pair
    code, out = _run(["shell", "readiness", "--repo", str(checkout), "--json"], capsys)
    assert code == 0
    payload = json.loads(out)
    assert payload["repository"]["state"] == "ready"
    assert payload["canonical_writes_allowed"] is False
    assert payload["claude"]["state"] == "ready"


def test_readiness_text_is_the_default_shell_view(remote_pair, data_root, capsys):
    _, checkout = remote_pair
    code, out = _run(["shell", "--repo", str(checkout)], capsys)
    assert code == 0
    assert "Canonical season writes: BLOCKED" in out
    assert "Claude is optional" in out


def test_refresh_preview_then_confirm_fast_forwards(remote_pair, tmp_path, data_root, capsys):
    bare, checkout = remote_pair
    remote_sha = advance_remote(bare, tmp_path, "colleague", "rev-2")
    local = head(checkout)

    code, out = _run(["shell", "refresh", "--repo", str(checkout)], capsys)
    assert code == 0
    assert f"--confirm {local}" in out
    assert head(checkout) == local

    code, _ = _run(["shell", "refresh", "--repo", str(checkout), "--confirm", "f" * 40], capsys)
    assert code == 2
    assert head(checkout) == local

    code, out = _run(["shell", "refresh", "--repo", str(checkout), "--confirm", local], capsys)
    assert code == 0
    assert head(checkout) == remote_sha


def test_refresh_refused_on_local_changes_exits_nonzero(remote_pair, tmp_path, data_root, capsys):
    bare, checkout = remote_pair
    advance_remote(bare, tmp_path, "colleague", "rev-2")
    make_dirty(checkout)
    local = head(checkout)
    code, out = _run(["shell", "refresh", "--repo", str(checkout), "--confirm", local], capsys)
    assert code == 2
    assert "uncommitted" in out
    assert head(checkout) == local


def test_publish_prints_handoff_only(data_root, capsys, tmp_path):
    code, out = _run(["shell", "publish"], capsys)
    assert code == 0
    assert "separate" in out
