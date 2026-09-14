"""The semantic safety-net audit checklist/policy must live only in the
canonical runbook (.agents/skills/rvv/SKILL.md), not be duplicated or
redefined by the thin `.claude/commands/rvv-miniputt/*` adapters
(issue #325 / AGENTS.md command-boundary rule)."""

from __future__ import annotations

from pathlib import Path

from tournament_scheduler.pipeline.audit_context import AUDIT_CHECKLIST

REPO_ROOT = Path(__file__).resolve().parents[1]
SKILL_MD = REPO_ROOT / ".agents" / "skills" / "rvv" / "SKILL.md"
ADAPTER_DIR = REPO_ROOT / ".claude" / "commands" / "rvv-miniputt"


def test_skill_md_contains_all_nine_checklist_items_verbatim():
    text = SKILL_MD.read_text(encoding="utf-8")
    for item in AUDIT_CHECKLIST:
        assert item["question"] in text


def test_skill_md_documents_the_audit_execution_model():
    text = SKILL_MD.read_text(encoding="utf-8")
    assert "## Semantic safety-net audit" in text
    assert "audit-context" in text
    assert "audit-submit" in text
    assert "audit-run" in text


def test_no_adapter_file_duplicates_the_checklist_or_defines_its_own_policy():
    offenders: list[str] = []
    for path in sorted(ADAPTER_DIR.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        for item in AUDIT_CHECKLIST:
            if item["question"] in text:
                offenders.append(f"{path.name}: duplicates checklist item {item['item_id']!r}")
        if "PASS" in text and "REVIEW_REQUIRED" in text and "FAIL" in text:
            offenders.append(f"{path.name}: appears to redefine the audit status vocabulary")
    assert offenders == []
