from __future__ import annotations

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / ".agents" / "instructions.yaml"


def _manifest() -> dict:
    return yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))


def _assert_budget(path: str, max_bytes: int) -> None:
    target = ROOT / path
    assert target.is_file(), f"instruction route does not exist: {path}"
    size = len(target.read_bytes())
    assert size <= max_bytes, f"{path} is {size} bytes; budget is {max_bytes}"


def test_instruction_manifest_routes_exist_and_fit_context_budgets() -> None:
    manifest = _manifest()

    _assert_budget(manifest["always"]["path"], manifest["always"]["max_bytes"])
    for route in manifest["routers"].values():
        _assert_budget(route["path"], route["max_bytes"])
    for route in manifest["capabilities"].values():
        _assert_budget(route["path"], route["max_bytes"])
    for path, config in manifest["harness_adapters"].items():
        _assert_budget(path, config["max_bytes"])

    assert (ROOT / manifest["adr"]).is_file()
    assert (ROOT / manifest["procedure_root"]).is_dir()


def test_rvv_router_declares_every_capability_route() -> None:
    manifest = _manifest()
    router = (ROOT / manifest["routers"]["rvv"]["path"]).read_text(encoding="utf-8")

    for capability, route in manifest["capabilities"].items():
        relative = Path(route["path"]).relative_to(".agents/skills/rvv").as_posix()
        assert relative in router, f"RVV router does not route capability {capability!r} to {relative}"


def test_harness_adapters_bootstrap_shared_instructions_without_rvv_policy() -> None:
    manifest = _manifest()
    forbidden = (
        "Stage 3",
        "booking reconciliation",
        "published_sealed",
        "publication scope",
        "request constraint",
        "season planner",
    )

    for path in manifest["harness_adapters"]:
        text = (ROOT / path).read_text(encoding="utf-8")
        assert "AGENTS.md" in text, f"{path} must bootstrap shared AGENTS.md"
        lowered = text.lower()
        for phrase in forbidden:
            assert phrase.lower() not in lowered, f"{path} duplicates shared RVV policy: {phrase}"


def test_always_loaded_instructions_do_not_hardcode_mutable_season_lifecycle() -> None:
    manifest = _manifest()
    texts = [
        (ROOT / manifest["always"]["path"]).read_text(encoding="utf-8"),
        *((ROOT / route["path"]).read_text(encoding="utf-8") for route in manifest["routers"].values()),
    ]
    joined = "\n".join(texts).lower()

    forbidden = (
        "current 2026",
        "2026–2027 season is",
        "2026-2027 season is",
        "current season is published",
        "current season is sealed",
    )
    for phrase in forbidden:
        assert phrase.lower() not in joined


def test_instruction_ownership_contract_is_visible_at_entry_points() -> None:
    manifest = _manifest()
    agents = (ROOT / manifest["always"]["path"]).read_text(encoding="utf-8")
    router = (ROOT / manifest["routers"]["rvv"]["path"]).read_text(encoding="utf-8")

    assert "harness-neutral" in agents.lower()
    assert "progressive context loading" in agents.lower()
    assert "harness-neutral" in router.lower()
    assert "load only the capability guidance needed" in router.lower()
