"""Unit tests for tournament_scheduler.stage3_shadow (issue #276)."""

from __future__ import annotations

from tournament_scheduler.pipeline.fingerprints import stable_payload_sha256
from tournament_scheduler.stage3_shadow import build_shadow_report


def _team(club: str, label: str, age_group: str) -> dict:
    return {"club": club, "label": label, "age_group": age_group}


def _tournament(t_id: str, date_str: str, arena: str, age_group: str, teams: list[dict]) -> dict:
    game_pairs = [(a["label"], b["label"]) for i, a in enumerate(teams) for b in teams[i + 1 :]]
    return {
        "id": t_id,
        "date": date_str,
        "arena": arena,
        "age_group": age_group,
        "host_club": teams[0]["club"] if teams else None,
        "teams": teams,
        "games": [
            {"home": home, "away": away, "parallel_slot": 0, "round_number": 1}
            for home, away in game_pairs
        ],
    }


def _candidate(source: dict | None = None) -> dict:
    teams = [_team("Club1", "T1", "U10"), _team("Club2", "T2", "U10")]
    payload = {
        "schema_version": 1,
        "tournaments": [_tournament("t1", "2026-01-05", "Arena1", "U10", teams)],
    }
    if source is not None:
        payload["source"] = source
    return payload


class TestBuildShadowReport:
    def test_includes_reproducible_fingerprints(self):
        baseline = _candidate()
        shadow = _candidate(source={"planner": "cp_sat", "status": "OPTIMAL"})
        problem = {"teams": [_team("Club1", "T1", "U10")]}

        report = build_shadow_report(baseline, shadow, problem, engine="cp_sat")

        fingerprints = report["fingerprints"]
        assert fingerprints["baseline_candidate"] == stable_payload_sha256(baseline["tournaments"])
        assert fingerprints["shadow_candidate"] == stable_payload_sha256(shadow["tournaments"])
        assert fingerprints["problem"] == stable_payload_sha256(problem)

    def test_fingerprints_are_deterministic_across_calls(self):
        baseline = _candidate()
        shadow = _candidate()

        first = build_shadow_report(baseline, shadow, None, engine="cp_sat")
        second = build_shadow_report(baseline, shadow, None, engine="cp_sat")

        assert first["fingerprints"] == second["fingerprints"]

    def test_baseline_and_shadow_fingerprints_differ_for_different_candidates(self):
        baseline = _candidate()
        shadow = _candidate()
        shadow["tournaments"][0]["teams"] = list(reversed(shadow["tournaments"][0]["teams"]))
        # Reordering teams changes content, so the fingerprint must move too.

        report = build_shadow_report(baseline, shadow, None, engine="cp_sat")

        assert report["fingerprints"]["baseline_candidate"] != report["fingerprints"]["shadow_candidate"]

    def test_no_problem_yields_null_problem_fingerprint(self):
        baseline = _candidate()
        shadow = _candidate()

        report = build_shadow_report(baseline, shadow, None, engine="cp_sat")

        assert report["fingerprints"]["problem"] is None

    def test_carries_shadow_candidate_source_and_engine(self):
        baseline = _candidate()
        source = {"planner": "cp_sat", "status": "OPTIMAL", "runtime_seconds": 1.5}
        shadow = _candidate(source=source)

        report = build_shadow_report(baseline, shadow, None, engine="cp_sat")

        assert report["engine"] == "cp_sat"
        assert report["shadow_candidate_source"] == source

    def test_embeds_the_underlying_ab_report(self):
        baseline = _candidate()
        shadow = _candidate()

        report = build_shadow_report(baseline, shadow, None, engine="cp_sat")

        assert "ab_report" in report
        assert "dominates_baseline" in report["ab_report"]
        assert "production_ready" in report["ab_report"]
