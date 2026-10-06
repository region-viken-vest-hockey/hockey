"""Shared RVV policy and command procedures must not drift across harness adapters."""

from __future__ import annotations

from pathlib import Path

from tournament_scheduler.pipeline.audit_context import AUDIT_CHECKLIST

REPO_ROOT = Path(__file__).resolve().parents[1]
SKILL_MD = REPO_ROOT / ".agents" / "skills" / "rvv" / "SKILL.md"
SHARED_COMMAND_DIR = REPO_ROOT / ".agents" / "commands" / "rvv-miniputt"
SHARED_OPERATE = SHARED_COMMAND_DIR / "operate.md"
PI_PROMPTS_DIR = REPO_ROOT / ".pi" / "prompts"
# Pi derives a prompt command's name from its filename (without .md), so the
# operator-facing spelling lives in the filename itself.
PI_OPERATE_PROMPT = PI_PROMPTS_DIR / "rvv-miniputt:operate.md"
# Harness command directories that expose RVV operations as one file per command.
ADAPTER_DIRS = (
    REPO_ROOT / ".claude" / "commands" / "rvv-miniputt",
    REPO_ROOT / ".codex" / "commands" / "rvv-miniputt",
    REPO_ROOT / ".chatgpt" / "commands" / "rvv-miniputt",
)
OPERATOR_ENTRYPOINT = "operate.md"
# Every harness adapter file -> the single shared procedure it must delegate to.
ADAPTERS = tuple(
    (adapter_dir / OPERATOR_ENTRYPOINT, SHARED_OPERATE) for adapter_dir in ADAPTER_DIRS
) + ((PI_OPERATE_PROMPT, SHARED_OPERATE),)


def _adapter_files():
    for path, _shared in ADAPTERS:
        if path.exists():
            yield path


def test_audit_checklist_stays_in_code_not_always_loaded_router():
    text = SKILL_MD.read_text(encoding="utf-8")
    assert len(AUDIT_CHECKLIST) == 9
    for item in AUDIT_CHECKLIST:
        assert item["question"] not in text


def test_operate_procedure_routes_to_audit_execution_model():
    text = SHARED_OPERATE.read_text(encoding="utf-8")
    assert "semantic audit contract" in text
    assert "audit-publication.md" in text
    assert "operator audit" not in SKILL_MD.read_text(encoding="utf-8")


def test_harnesses_expose_only_the_single_operator_entrypoint():
    for adapter_dir in ADAPTER_DIRS:
        if not adapter_dir.exists():
            continue
        assert [path.name for path in sorted(adapter_dir.glob("*.md"))] == [
            OPERATOR_ENTRYPOINT
        ]


def test_no_adapter_file_duplicates_the_checklist_or_defines_its_own_policy():
    offenders: list[str] = []
    for path in _adapter_files():
        text = path.read_text(encoding="utf-8")
        for item in AUDIT_CHECKLIST:
            if item["question"] in text:
                offenders.append(f"{path}: duplicates checklist item {item['item_id']!r}")
        if "PASS" in text and "REVIEW_REQUIRED" in text and "FAIL" in text:
            offenders.append(f"{path}: appears to redefine the audit status vocabulary")
    assert offenders == []


def test_pi_operate_prompt_name_is_the_shared_operator_entrypoint() -> None:
    assert PI_OPERATE_PROMPT.exists()
    # Pi names prompt-template commands from the filename stem.
    assert PI_OPERATE_PROMPT.stem == "rvv-miniputt:operate"


def test_pi_operate_prompt_forwards_the_complete_operator_request() -> None:
    """Pi 0.87.1 substitutes bare ``$ARGUMENTS`` with the full trailing text.

    Braced ``${@}`` is not a supported substitution, so the adapter must not
    rely on it or the raw placeholder would reach the shared procedure.
    """

    text = PI_OPERATE_PROMPT.read_text(encoding="utf-8")
    assert "$ARGUMENTS" in text
    assert "${@}" not in text


def test_pi_project_instructions_are_not_duplicated() -> None:
    """Pi loads AGENTS.md as a context file; a Pi-only bootstrap must not fork it."""

    for name in ("SYSTEM.md", "APPEND_SYSTEM.md", "AGENTS.md", "CLAUDE.md"):
        candidate = REPO_ROOT / ".pi" / name
        assert not candidate.exists(), f"{candidate} would duplicate shared project instructions"


def test_pi_exposes_only_one_rvv_operational_prompt() -> None:
    rvv_prompts = sorted(path.name for path in PI_PROMPTS_DIR.glob("rvv-miniputt*.md"))
    assert rvv_prompts == [PI_OPERATE_PROMPT.name]
    # The generic coding prompt is not an RVV operation and must not be confused
    # with one, but it must remain available.
    assert (PI_PROMPTS_DIR / "implement-issue.md").exists()


def test_pi_operate_adapter_loads_the_shared_instruction_chain() -> None:
    text = PI_OPERATE_PROMPT.read_text(encoding="utf-8")
    for reference in (
        "AGENTS.md",
        ".agents/skills/rvv/SKILL.md",
        ".agents/commands/rvv-miniputt/handover.md",
    ):
        assert reference in text


def test_pi_prompts_are_transport_only_and_own_no_rvv_behavior() -> None:
    offenders: list[str] = []
    for path in sorted(PI_PROMPTS_DIR.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        for marker in (
            "scripts/rvv-miniputt",
            "python3 -m tournament_scheduler",
            "rvv_miniputt_",
        ):
            if marker in text:
                offenders.append(f"{path}: embeds RVV implementation/policy via {marker!r}")
        for item in AUDIT_CHECKLIST:
            if item["question"] in text:
                offenders.append(f"{path}: duplicates audit checklist item {item['item_id']!r}")
        if "PASS" in text and "REVIEW_REQUIRED" in text and "FAIL" in text:
            offenders.append(f"{path}: redefines the audit status vocabulary")
    assert offenders == []


def test_pi_has_no_rvv_implementation_or_configuration_beyond_prompts() -> None:
    """Only command-transport prompts may exist under ``.pi``.

    A code/config file with a generic name (``.pi/extensions/operator.ts``), a
    Python/shell helper, a project ``settings.json`` wiring in a package, or any
    other top-level Pi resource would be reachable Pi-owned behavior. Runtime
    directories (``.pi/logs``, ``.pi/lib``) are ignored, but committed code
    inside them is not.
    """

    pi_root = REPO_ROOT / ".pi"
    runtime_only = {"logs", "lib", "runtime"}
    allowed_top_level = {"prompts"}
    implementation_suffixes = {
        ".ts",
        ".tsx",
        ".js",
        ".mjs",
        ".cjs",
        ".py",
        ".sh",
        ".bash",
        ".json",
    }

    offenders: list[str] = []
    for entry in sorted(pi_root.iterdir()):
        if entry.name in runtime_only:
            continue
        if entry.name not in allowed_top_level:
            offenders.append(str(entry.relative_to(REPO_ROOT)))

    for base_name in runtime_only:
        base = pi_root / base_name
        if not base.exists():
            continue
        for path in sorted(base.rglob("*")):
            if not path.is_file() or "node_modules" in path.parts:
                continue
            if path.suffix in implementation_suffixes:
                offenders.append(str(path.relative_to(REPO_ROOT)))

    prompt_files = sorted(path.name for path in PI_PROMPTS_DIR.iterdir() if path.is_file())
    if prompt_files != ["implement-issue.md", PI_OPERATE_PROMPT.name]:
        offenders.append(f".pi/prompts contains unexpected files: {prompt_files}")

    assert offenders == []


def test_harness_command_adapters_delegate_to_shared_agent_neutral_procedures():
    offenders: list[str] = []
    for path, shared_path in ADAPTERS:
        if not path.exists():
            offenders.append(f"{path}: missing harness adapter")
            continue

        text = path.read_text(encoding="utf-8")
        shared_ref = shared_path.relative_to(REPO_ROOT).as_posix()
        if shared_ref not in text:
            offenders.append(f"{path}: does not reference {shared_ref}")

        # Repository commands and operational procedure text belong in the shared
        # file; adapters should contain only harness metadata/transport guidance.
        if "scripts/rvv-miniputt" in text or "python3 -m tournament_scheduler" in text:
            offenders.append(f"{path}: duplicates repository command procedure")

    assert offenders == []


def test_shared_command_procedures_are_harness_neutral():
    offenders: list[str] = []
    forbidden = (".claude/commands/", ".codex/commands/", ".chatgpt/commands/", ".pi/prompts/")
    for path in sorted(SHARED_COMMAND_DIR.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        for marker in forbidden:
            if marker in text:
                offenders.append(f"{path}: contains harness-specific path {marker}")
    assert offenders == []
