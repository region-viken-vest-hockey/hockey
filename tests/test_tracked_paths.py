from __future__ import annotations

import importlib.util
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "check_tracked_paths.py"
CI_WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
PORTABILITY_WORKFLOW = ROOT / ".github" / "workflows" / "tracked-path-portability.yml"


def _load_guard():
    spec = importlib.util.spec_from_file_location("check_tracked_paths", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_unicode_normalization_equivalent_paths_collide() -> None:
    guard = _load_guard()

    collisions = guard.find_collisions(
        ["A\u030arshjul for aktiviteter.xlsx", "Årshjul for aktiviteter.xlsx"]
    )

    assert collisions == [("A\u030arshjul for aktiviteter.xlsx", "Årshjul for aktiviteter.xlsx")]


def test_case_only_paths_collide() -> None:
    guard = _load_guard()

    collisions = guard.find_collisions(["docs/Guide.md", "docs/guide.md"])

    assert collisions == [("docs/Guide.md", "docs/guide.md")]


def test_distinct_paths_do_not_collide() -> None:
    guard = _load_guard()

    assert guard.find_collisions(["docs/guide.md", "docs/reference.md"]) == []


def test_failure_lists_each_colliding_path_clearly(capsys) -> None:
    guard = _load_guard()

    assert guard.main(["A\u030a.xlsx", "Å.xlsx", "README.md", "readme.md"]) == 1

    stderr = capsys.readouterr().err
    assert "NFC normalization and case folding" in stderr
    assert "'A\\u030a.xlsx'" in stderr
    assert "'\\xc5.xlsx'" in stderr
    assert "'README.md'" in stderr
    assert "'readme.md'" in stderr


def _workflow_triggers(path: Path):
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data.get("on", data.get(True, {})), data


def test_portability_workflow_is_always_on_without_expanding_full_ci() -> None:
    portability_triggers, portability = _workflow_triggers(PORTABILITY_WORKFLOW)
    full_ci_triggers, _ = _workflow_triggers(CI_WORKFLOW)

    assert "paths" in full_ci_triggers["pull_request"]
    assert "paths" in full_ci_triggers["push"]
    assert set(portability_triggers) == {"pull_request", "push", "workflow_dispatch"}
    assert "paths" not in (portability_triggers["pull_request"] or {})
    assert "paths" not in portability_triggers["push"]
    assert set(portability["jobs"]) == {"tracked-paths"}
    assert "scripts/check tracked-paths" in PORTABILITY_WORKFLOW.read_text(encoding="utf-8")


def test_repository_tracked_paths_have_no_collisions() -> None:
    assert _load_guard().main() == 0
