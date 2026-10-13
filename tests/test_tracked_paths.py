from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "check_tracked_paths.py"


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


def test_repository_tracked_paths_have_no_collisions() -> None:
    assert _load_guard().main() == 0
