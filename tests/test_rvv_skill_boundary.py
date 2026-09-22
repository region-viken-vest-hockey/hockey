from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RVV_SKILL_FILE = ROOT / ".agents" / "skills" / "rvv" / "SKILL.md"
REVIEW_BASELINE_SKILL_FILE = ROOT / ".agents" / "skills" / "rvv-review-baseline" / "SKILL.md"
CONFIRM_TOURNAMENT_SKILL_FILE = ROOT / ".agents" / "skills" / "rvv-confirm-tournament" / "SKILL.md"
MOVE_TOURNAMENT_SKILL_FILE = ROOT / ".agents" / "skills" / "rvv-move-tournament" / "SKILL.md"
GUIDE_FILE = ROOT / ".agents" / "commands" / "rvv-miniputt" / "guide.md"
SCRAPE_LLM_FILE = ROOT / ".agents" / "commands" / "rvv-miniputt" / "scrape-llm.md"


def test_rvv_skill_is_harness_neutral_and_documents_two_phase_lifecycle() -> None:
    text = RVV_SKILL_FILE.read_text(encoding="utf-8")

    assert "Initial season creation" in text
    assert "Promoted-season maintenance" in text
    assert "scripts/rvv-miniputt run --interactive" in text
    assert "scripts/rvv-miniputt season" in text
    assert "operator audit-context" in text
    assert "operator audit-evidence" in text
    assert "operator audit-submit" in text
    assert "rvv_miniputt_scrape" not in text
    assert "rvv_miniputt_scrape_llm" not in text


def test_review_baseline_skill_promotes_verified_state_and_isolates_experiments() -> None:
    text = REVIEW_BASELINE_SKILL_FILE.read_text(encoding="utf-8")

    assert "Read `AGENTS.md`, `.agents/skills/rvv/SKILL.md`" in text
    assert "scripts/rvv-miniputt season promote --work-dir <work-dir>" in text
    assert "season/<season>/schedule.json" in text
    assert "season/<season>/decisions.json" in text
    assert "git status --short" in text
    assert "--work-dir .pipeline-test" in text
    assert "--export-dir export-test" in text
    assert "Never use `--force`" in text
    assert "Never mark an export `published` merely to protect it" in text


def test_confirm_tournament_skill_defaults_to_placement_lock_only() -> None:
    text = CONFIRM_TOURNAMENT_SKILL_FILE.read_text(encoding="utf-8")

    assert "season approve" in text
    assert "placement only" in text
    assert "--participants-lock" in text
    assert "only when the operator explicitly says" in text
    assert "Do **not** add `--no-placement-lock`" in text
    assert "season approvals --season <season> --json" in text
    assert "Confirm <tournament-id> placement" in text
    assert "does **not** authorize public GitHub Pages publication" in text


def test_move_tournament_skill_unlocks_safely_and_does_not_silently_reapprove() -> None:
    text = MOVE_TOURNAMENT_SKILL_FILE.read_text(encoding="utf-8")

    assert "season unapprove" in text
    assert "season move" in text
    assert "restore the previous approval/lock scopes" in text
    assert "Do not silently reapprove the new placement" in text
    assert "durable id unchanged" in text
    assert "unrelated tournaments are unchanged" in text
    assert "Move tournament <tournament-id>" in text
    assert "does **not** authorize public GitHub Pages publication" in text
    assert "--request-id <request-id>" in text
    assert "season protections" in text
    assert "release-protection" in text


def test_shared_guide_routes_promoted_season_without_pi_special_case() -> None:
    text = GUIDE_FILE.read_text(encoding="utf-8")

    assert "shared conversational guide procedure for every agent harness" in text
    assert "season promote" in text
    assert "season approve" in text
    assert "season replan" in text
    assert "season protections" in text
    assert "swap-participants" in text
    assert "release-protection" in text
    assert "Pi may provide its own" not in text


def test_browser_recovery_is_shared_harness_capability_not_pi_extension() -> None:
    text = SCRAPE_LLM_FILE.read_text(encoding="utf-8")

    assert "Browser-enabled harness" in text
    assert "recovery-inject" in text
    assert "scrape-merge" in text
    assert "rvv_miniputt_scrape_llm" not in text
    assert "Pi owns" not in text


def test_repo_does_not_require_rvv_specific_pi_extension() -> None:
    assert not (ROOT / ".pi" / "extensions" / "rvv-miniputt.ts").exists()
    assert not (ROOT / ".pi" / "extensions" / "rvv-miniputt-season.ts").exists()
    assert not (ROOT / ".pi" / "lib" / "pipeline-runner.ts").exists()
    assert not (ROOT / ".pi" / "lib" / "operator-audit.ts").exists()
    assert not (ROOT / ".pi" / "lib" / "browser-recovery.ts").exists()

    # The removed Pi extension left no TypeScript tooling behind: no tsconfig,
    # no npm typecheck script, and no CI job that needs Node.
    assert not (ROOT / "tsconfig.pi.json").exists()
    package_json = ROOT / "package.json"
    if package_json.exists():
        assert "check:pi" not in package_json.read_text(encoding="utf-8")
    ci_workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "pi-typecheck" not in ci_workflow
    assert "check:pi" not in ci_workflow
