"""Read-only readiness view and the blocked-write explanations, against shared services (offline)."""

from __future__ import annotations

import json

import pytest

from tests.claude_runtime_support import auth_output, not_installed_runner, runner_for, version_output
from tests.operator_shell_support import INSTALLED, make_diverged, make_dirty
from tests.repository_sync_support import advance_remote, write_cache
from tournament_scheduler.distribution_update import CACHE_FILE_NAME
from tournament_scheduler.infrastructure.repository_sync import (
    Credential,
    InMemoryCredentialStore,
    RepoState,
    WindowsCredentialManagerStore,
)
from tournament_scheduler.operator_shell import gather_readiness, render_readiness


def _ready_runner():
    return runner_for(version_output("2.1.296"), auth_output(True))


def _view(checkout, cache_dir, *, store=None, claude=None, installed=INSTALLED):
    return gather_readiness(
        checkout,
        cache_dir=cache_dir,
        store=store if store is not None else InMemoryCredentialStore(),
        claude_runner=claude if claude is not None else _ready_runner(),
        installed=installed,
    )


def _blockers_text(view) -> str:
    return " | ".join(view.blockers)


def test_ready_checkout_is_blocked_only_by_missing_sign_in(remote_pair, tmp_path):
    _, checkout = remote_pair
    cache = tmp_path / "cache"
    write_cache(cache)
    view = _view(checkout, cache)
    assert view.repository.state is RepoState.READY
    assert view.version.writes_allowed is True
    assert view.canonical_writes_allowed is False
    assert len(view.blockers) == 1
    assert "sign-in" in view.blockers[0].lower()


def test_stored_but_unverified_sign_in_still_blocks_and_never_leaks_token(remote_pair, tmp_path):
    _, checkout = remote_pair
    cache = tmp_path / "cache"
    write_cache(cache)
    secret = "ghp_example_secret_token_value"
    view = _view(checkout, cache, store=InMemoryCredentialStore(Credential(token=secret, login="operator")))
    assert view.sign_in.state == "stored-unverified"
    assert view.canonical_writes_allowed is False
    rendered = render_readiness(view) + json.dumps(view.to_dict())
    assert secret not in rendered
    assert "operator" not in rendered


def test_sign_in_store_seam_fails_closed(remote_pair, tmp_path):
    _, checkout = remote_pair
    cache = tmp_path / "cache"
    write_cache(cache)
    view = _view(checkout, cache, store=WindowsCredentialManagerStore())
    assert view.sign_in.state == "unavailable"
    assert view.canonical_writes_allowed is False


def test_missing_claude_is_reported_but_does_not_block_canonical_writes(remote_pair, tmp_path):
    _, checkout = remote_pair
    cache = tmp_path / "cache"
    write_cache(cache)
    view = _view(checkout, cache, claude=not_installed_runner())
    assert view.claude.state.value == "not-installed"
    assert all("claude" not in blocker.lower() for blocker in view.blockers)


def test_behind_checkout_is_blocked_with_refresh_guidance(remote_pair, tmp_path):
    bare, checkout = remote_pair
    advance_remote(bare, tmp_path, "colleague", "rev-2")
    cache = tmp_path / "cache"
    write_cache(cache)
    view = _view(checkout, cache)
    assert view.repository.state is RepoState.BEHIND
    assert "refresh" in _blockers_text(view).lower()


def test_diverged_checkout_is_blocked(remote_pair, tmp_path):
    bare, checkout = remote_pair
    make_diverged(checkout, bare, tmp_path, advance_remote)
    cache = tmp_path / "cache"
    write_cache(cache)
    view = _view(checkout, cache)
    assert view.repository.state is RepoState.DIVERGED
    assert "diverged" in _blockers_text(view).lower()


def test_local_changes_block_writes(remote_pair, tmp_path):
    _, checkout = remote_pair
    make_dirty(checkout)
    cache = tmp_path / "cache"
    write_cache(cache)
    view = _view(checkout, cache)
    assert view.repository.state is RepoState.LOCAL_CHANGES
    assert "uncommitted" in _blockers_text(view).lower()


def test_missing_checkout_is_reported_as_not_cloned(tmp_path):
    empty = tmp_path / "nowhere"
    empty.mkdir()
    cache = tmp_path / "cache"
    write_cache(cache)
    view = _view(empty, cache)
    assert view.repository.state is RepoState.NOT_CLONED
    assert view.canonical_writes_allowed is False


@pytest.mark.parametrize(
    ("installed", "minimum_write", "expected"),
    [
        ("2.0.0", "2.1.0", "update-required"),
        ("1.5.0", "2.0.0", "unsupported"),
    ],
)
def test_version_floor_blocks_writes_with_operator_reason(remote_pair, tmp_path, installed, minimum_write, expected):
    _, checkout = remote_pair
    cache = tmp_path / "cache"
    write_cache(cache, minimum_write=minimum_write, latest="2.1.0")
    view = _view(checkout, cache, installed=installed)
    assert view.version.update_status == expected
    assert view.version.writes_allowed is False
    assert any(blocker.startswith("Software version:") for blocker in view.blockers)


def test_corrupt_cached_manifest_blocks_writes(remote_pair, tmp_path):
    _, checkout = remote_pair
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / CACHE_FILE_NAME).write_text("{not json", encoding="utf-8")
    view = _view(checkout, cache)
    assert view.version.update_status == "unreadable-cache"
    assert view.version.writes_allowed is False
    assert view.canonical_writes_allowed is False


def test_no_cached_manifest_reports_unknown_minimum(remote_pair, tmp_path):
    _, checkout = remote_pair
    view = _view(checkout, tmp_path / "empty-cache")
    assert view.version.update_status == "unknown"
    assert view.version.writes_allowed is True
