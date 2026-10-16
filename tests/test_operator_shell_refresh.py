"""Preview-then-confirm refresh from main, and blocked refresh cases, against the shared repository adapter (offline)."""

from __future__ import annotations

from tests.operator_shell_support import make_dirty, make_diverged
from tests.repository_sync_support import advance_remote, head
from tournament_scheduler.infrastructure.repository_sync import RepoState
from tournament_scheduler.operator_shell import PUBLICATION_HANDOFF, apply_refresh, preview_refresh


def test_preview_describes_fast_forward_without_changing_head(remote_pair, tmp_path):
    bare, checkout = remote_pair
    remote_sha = advance_remote(bare, tmp_path, "colleague", "rev-2")
    before = head(checkout)
    preview = preview_refresh(checkout)
    assert preview.can_refresh is True
    assert preview.confirm_token == before
    assert "fast-forward" in preview.message
    assert head(checkout) == before
    assert remote_sha != before


def test_apply_without_confirmation_is_refused_and_changes_nothing(remote_pair, tmp_path):
    bare, checkout = remote_pair
    advance_remote(bare, tmp_path, "colleague", "rev-2")
    before = head(checkout)
    outcome = apply_refresh(checkout, confirm_head=None)
    assert outcome.performed is False
    assert "preview" in outcome.message.lower()
    assert head(checkout) == before


def test_apply_with_stale_confirmation_is_refused(remote_pair, tmp_path):
    bare, checkout = remote_pair
    advance_remote(bare, tmp_path, "colleague", "rev-2")
    before = head(checkout)
    outcome = apply_refresh(checkout, confirm_head="0" * 40)
    assert outcome.performed is False
    assert "changed since the preview" in outcome.message
    assert head(checkout) == before


def test_apply_with_previewed_head_fast_forwards_to_origin_main(remote_pair, tmp_path):
    bare, checkout = remote_pair
    remote_sha = advance_remote(bare, tmp_path, "colleague", "rev-2")
    preview = preview_refresh(checkout)
    outcome = apply_refresh(checkout, confirm_head=preview.confirm_token)
    assert outcome.performed is True
    assert head(checkout) == remote_sha
    assert outcome.snapshot is not None and outcome.snapshot.state is RepoState.READY


def test_diverged_checkout_is_refused_without_change(remote_pair, tmp_path):
    bare, checkout = remote_pair
    make_diverged(checkout, bare, tmp_path, advance_remote)
    before = head(checkout)
    preview = preview_refresh(checkout)
    assert preview.can_refresh is False and preview.confirm_token is None
    outcome = apply_refresh(checkout, confirm_head=before)
    assert outcome.performed is False
    assert "diverged" in outcome.message
    assert head(checkout) == before


def test_dirty_checkout_is_refused_and_edits_are_kept(remote_pair, tmp_path):
    bare, checkout = remote_pair
    advance_remote(bare, tmp_path, "colleague", "rev-2")
    make_dirty(checkout)
    before = head(checkout)
    schedule = checkout / "season" / "2026" / "schedule.json"
    edited = schedule.read_text(encoding="utf-8")
    outcome = apply_refresh(checkout, confirm_head=before)
    assert outcome.performed is False
    assert "uncommitted" in outcome.message
    assert head(checkout) == before
    assert schedule.read_text(encoding="utf-8") == edited


def test_already_current_checkout_has_nothing_to_refresh(remote_pair):
    _, checkout = remote_pair
    preview = preview_refresh(checkout)
    assert preview.can_refresh is False
    assert "Already current" in preview.message


def test_publication_is_a_separate_handoff_text():
    assert "separate" in PUBLICATION_HANDOFF
    assert "never publishes" in PUBLICATION_HANDOFF
