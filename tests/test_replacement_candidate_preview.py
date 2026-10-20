"""Replacement candidates carry the same canonical preview as a direct replacement dry-run.

Discovery runs one dry-run per (removed team, incoming team) pair. Each candidate
must report the contract envelope a direct ``replace`` preview would report for
that pair on the same revision, so no consumer has to re-derive applicability.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from tests.test_season_inspection import _promote_replacement_ranking_fixture
from tournament_scheduler.application.canonical_season.consequence_preview import (
    PREVIEW_CONTRACT,
    preview_canonical_operation,
)
from tournament_scheduler.application.canonical_season.replacement_search import (
    search_participant_replacements,
)
from tournament_scheduler.application.canonical_season.store_facade import _service, load_schedule

SEASON = "2026-2027"


def _direct_preview(root: Path, result: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    return preview_canonical_operation(
        _service(root),
        operation="replace",
        season=SEASON,
        expected_revision=result["canonical_state_revision"],
        tournament_id=result["tournament_id"],
        remove_team_label=candidate["remove_team"]["label"],
        add_team_label=candidate["add_team"]["label"],
    )


def test_candidate_preview_matches_direct_replacement_dry_run(tmp_path: Path) -> None:
    root = _promote_replacement_ranking_fixture(tmp_path)
    result = search_participant_replacements(_service(root), season=SEASON, tournament_id="t1")

    statuses: set[str] = set()
    for candidate in result["candidates"]:
        direct = _direct_preview(root, result, candidate)
        embedded = candidate["canonical_preview"]

        # Details are the raw dry-run payload; the candidate keeps its own fields.
        direct.pop("details")
        assert embedded == direct
        assert candidate["verdict"] == direct["verdict"]
        assert candidate["safe_to_apply"] is direct["verdict"]["applicable"]
        assert candidate["blockers"] == direct["verdict"]["blockers"]
        statuses.add(direct["verdict"]["status"])

    # Both branches must be exercised, or the parity above proves little.
    assert statuses == {"safe_to_apply", "blocked"}


def test_candidate_preview_is_bound_to_the_discovery_revision_and_writes_nothing(tmp_path: Path) -> None:
    root = _promote_replacement_ranking_fixture(tmp_path)
    revision = load_schedule(SEASON, root=root)["revision"]
    result = search_participant_replacements(_service(root), season=SEASON, tournament_id="t1")

    assert load_schedule(SEASON, root=root)["revision"] == revision
    assert result["candidates"]
    for candidate in result["candidates"]:
        preview = candidate["canonical_preview"]
        assert preview["contract"] == PREVIEW_CONTRACT
        assert preview["writes_canonical_state"] is False
        assert preview["expected_revision"] == result["canonical_state_revision"]
        assert preview["changed_tournament_ids"] == ["t1"]
        assert "details" not in preview
        assert preview["candidate_fingerprint"] is not None or preview["verdict"]["status"] == "blocked"
