"""Write-gated canonical commit and fast-forward push against temporary local repositories (offline)."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.distribution_signing_support import trust
from tests.repository_sync_support import (
    INSTALLED,
    advance_remote,
    git,
    head,
    write_cache,
    write_schedule,
)
from tournament_scheduler.distribution_update import WriteBlockedError
from tournament_scheduler.infrastructure.repository_sync import (
    RepositoryStateError,
    commit_canonical_season,
    run_git,
)

SEASON_ROOT = Path("season") / "2026"


@pytest.fixture(autouse=True)
def _cache_is_signed_by_test_signer(monkeypatch):
    trust(monkeypatch)


def _commit(checkout: Path, cache_dir: Path, *, runner=run_git, push: bool = True, token=None):
    return commit_canonical_season(
        checkout,
        SEASON_ROOT,
        "Update canonical season state",
        installed_version=INSTALLED,
        cache_dir=cache_dir,
        token=token,
        push=push,
        runner=runner,
    )


def test_commit_and_push_season_change_fast_forwards_remote(remote_pair, tmp_path) -> None:
    bare, checkout = remote_pair
    cache = tmp_path / "cache"
    write_cache(cache)
    write_schedule(checkout, "rev-2")
    result = _commit(checkout, cache)
    assert result.committed and result.pushed
    assert result.commit == head(checkout)
    assert git(bare, "rev-parse", "main") == head(checkout)


def test_write_is_refused_when_gate_denies_and_nothing_changes(remote_pair, tmp_path) -> None:
    bare, checkout = remote_pair
    cache = tmp_path / "cache"
    write_cache(cache, minimum_write="2.2.0", latest="2.2.0")
    write_schedule(checkout, "rev-2")
    before_local, before_remote = head(checkout), git(bare, "rev-parse", "main")
    with pytest.raises(WriteBlockedError, match="below the minimum write version"):
        _commit(checkout, cache)
    assert head(checkout) == before_local
    assert git(bare, "rev-parse", "main") == before_remote


def test_corrupt_cached_manifest_blocks_writes(remote_pair, tmp_path) -> None:
    _, checkout = remote_pair
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "release-manifest.json").write_text("{not json", encoding="utf-8")
    write_schedule(checkout, "rev-2")
    before = head(checkout)
    with pytest.raises(WriteBlockedError, match="unusable"):
        _commit(checkout, cache)
    assert head(checkout) == before


def test_missing_manifest_allows_writes_as_documented(remote_pair, tmp_path) -> None:
    _, checkout = remote_pair
    write_schedule(checkout, "rev-2")
    result = _commit(checkout, tmp_path / "no-cache")
    assert result.pushed is True


def test_commit_refused_when_behind_origin(remote_pair, tmp_path) -> None:
    bare, checkout = remote_pair
    cache = tmp_path / "cache"
    write_cache(cache)
    advance_remote(bare, tmp_path, "colleague", "rev-remote")
    write_schedule(checkout, "rev-local")
    before = head(checkout)
    with pytest.raises(RepositoryStateError, match="local-changes"):
        _commit(checkout, cache)  # dirty and behind: local changes are reported first
    assert head(checkout) == before


def test_commit_refused_when_clean_checkout_is_behind(remote_pair, tmp_path) -> None:
    bare, checkout = remote_pair
    cache = tmp_path / "cache"
    write_cache(cache)
    advance_remote(bare, tmp_path, "colleague", "rev-remote")
    before = head(checkout)
    with pytest.raises(RepositoryStateError, match="behind"):
        _commit(checkout, cache)
    assert head(checkout) == before


def test_commit_refused_when_tracked_change_is_outside_season_root(remote_pair, tmp_path) -> None:
    _, checkout = remote_pair
    cache = tmp_path / "cache"
    write_cache(cache)
    write_schedule(checkout, "rev-2")
    (checkout / "tracked.txt").write_text("v1\n", encoding="utf-8")
    git(checkout, "add", "tracked.txt")
    git(checkout, "commit", "--quiet", "-m", "track other file")
    git(checkout, "push", "--quiet", "origin", "main")
    (checkout / "tracked.txt").write_text("v2\n", encoding="utf-8")
    before = head(checkout)
    with pytest.raises(RepositoryStateError, match="outside the season root"):
        _commit(checkout, cache)
    assert head(checkout) == before


def test_commit_refused_when_unrelated_file_is_staged(remote_pair, tmp_path) -> None:
    _, checkout = remote_pair
    cache = tmp_path / "cache"
    write_cache(cache)
    write_schedule(checkout, "rev-2")
    (checkout / "unrelated.txt").write_text("staged elsewhere\n", encoding="utf-8")
    git(checkout, "add", "unrelated.txt")
    before = head(checkout)
    with pytest.raises(RepositoryStateError, match="outside the season root"):
        _commit(checkout, cache)
    assert head(checkout) == before


def test_no_canonical_change_is_a_no_op(remote_pair, tmp_path) -> None:
    _, checkout = remote_pair
    cache = tmp_path / "cache"
    write_cache(cache)
    before = head(checkout)
    result = _commit(checkout, cache)
    assert result.committed is False and result.pushed is False
    assert head(checkout) == before


def test_refused_push_keeps_local_commit_and_never_forces(remote_pair, tmp_path) -> None:
    bare, checkout = remote_pair
    cache = tmp_path / "cache"
    write_cache(cache)
    write_schedule(checkout, "rev-local")

    raced = {"done": False}

    def racing_runner(args, cwd, env):
        # Simulate a colleague pushing between our state check and our push.
        if args[0] == "push" and not raced["done"]:
            raced["done"] = True
            advance_remote(bare, tmp_path, "colleague", "rev-remote")
        return run_git(args, cwd, env)

    result = _commit(checkout, cache, runner=racing_runner)
    assert result.committed is True and result.pushed is False
    assert "refused" in result.reason and "nothing was force-pushed" in result.reason
    assert head(checkout) == result.commit
    assert git(bare, "show", "main:season/2026/schedule.json").find("rev-remote") >= 0


def test_push_false_keeps_commit_local(remote_pair, tmp_path) -> None:
    bare, checkout = remote_pair
    cache = tmp_path / "cache"
    write_cache(cache)
    before_remote = git(bare, "rev-parse", "main")
    write_schedule(checkout, "rev-2")
    result = _commit(checkout, cache, push=False)
    assert result.committed and not result.pushed
    assert git(bare, "rev-parse", "main") == before_remote


def test_token_is_not_persisted_to_repository_config(remote_pair, tmp_path) -> None:
    _, checkout = remote_pair
    cache = tmp_path / "cache"
    write_cache(cache)
    write_schedule(checkout, "rev-2")
    token = "ghp_TOKEN_MUST_NOT_PERSIST"
    result = _commit(checkout, cache, token=token)
    assert result.pushed is True
    config_text = (checkout / ".git" / "config").read_text(encoding="utf-8")
    assert token not in config_text
