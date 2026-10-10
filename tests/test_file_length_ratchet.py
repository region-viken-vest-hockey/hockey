from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "check_file_length.py"


def _load_guard():
    spec = importlib.util.spec_from_file_location("check_file_length", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_lines(path: Path, count: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x = 1\n" * count, encoding="utf-8")


@pytest.fixture
def guard(tmp_path, monkeypatch):
    module = _load_guard()
    target = tmp_path / "tournament_scheduler"
    baseline = tmp_path / "scripts" / "file-length-baseline.txt"
    baseline.parent.mkdir(parents=True)
    monkeypatch.setattr(module, "TARGET_DIR", target)
    monkeypatch.setattr(module, "BASELINE_PATH", baseline)
    monkeypatch.setattr(module, "ROOT", tmp_path)
    return module, target, baseline


def test_repository_file_length_ratchet_passes() -> None:
    assert _load_guard().main() == 0


def test_new_file_over_guideline_fails(guard) -> None:
    module, target, baseline = guard
    baseline.write_text("", encoding="utf-8")
    _write_lines(target / "new_module.py", module.LIMIT + 1)

    assert module.main() == 1


def test_grandfathered_file_may_not_grow_past_recorded_size(guard) -> None:
    module, target, baseline = guard
    baseline.write_text("tournament_scheduler/big.py\t400\n", encoding="utf-8")
    _write_lines(target / "big.py", 401)

    assert module.main() == 1


def test_grandfathered_file_may_shrink(guard) -> None:
    module, target, baseline = guard
    baseline.write_text("tournament_scheduler/big.py\t400\n", encoding="utf-8")
    _write_lines(target / "big.py", 350)

    assert module.main() == 0
