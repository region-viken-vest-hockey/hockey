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


def test_routed_procedures_fit_progressive_context_budget() -> None:
    manifest = _manifest()
    procedure_root = ROOT / manifest["procedure_root"]
    budget = manifest["procedure_max_bytes"]

    procedures = sorted(procedure_root.rglob("*.md"))
    assert procedures, f"no procedures found under {manifest['procedure_root']}"
    for procedure in procedures:
        _assert_budget(procedure.relative_to(ROOT).as_posix(), budget)


# Each operation heading must live in the sub-procedure the router names for it.
SEASON_OPERATION_ROUTES = {
    "## Process incoming club/operator change requests": "season/change-requests.md",
    "## Record semantic request constraints": "season/request-constraints.md",
    "## Approve / lock booked ice": "season/approvals-and-booking.md",
    "## Change an approved tournament": "season/tournament-changes.md",
    "## Replace one tournament participant safely": "season/tournament-changes.md",
    "## Retire a team (cancel hosted tournaments + withdraw from away tournaments)": "season/tournament-changes.md",
    "## Remove a participant with no replacement": "season/participant-changes.md",
    "## Swap tournament participants safely": "season/participant-changes.md",
    "## Report what genuinely blocks publication": "season/blockers-and-repair.md",
    "## Repair a localized finding against the promoted season": "season/blockers-and-repair.md",
    "## Replan around the published baseline": "season/replan-and-export.md",
    "## Export after canonical changes": "season/replan-and-export.md",
}


# Router bullet phrase that must route each operation family to its owner file.
SEASON_ROUTER_PHRASES = {
    "## Process incoming club/operator change requests": "change requests",
    "## Record semantic request constraints": "semantic request constraints",
    "## Approve / lock booked ice": "Approval, locks",
    "## Change an approved tournament": "Changing an approved tournament",
    "## Replace one tournament participant safely": "replacing one participant",
    "## Retire a team (cancel hosted tournaments + withdraw from away tournaments)": "retiring a team",
    "## Remove a participant with no replacement": "Removing a participant",
    "## Swap tournament participants safely": "swapping participants",
    "## Report what genuinely blocks publication": "Publication blockers",
    "## Repair a localized finding against the promoted season": "Publication blockers",
    "## Replan around the published baseline": "Replanning around",
    "## Export after canonical changes": "Replanning around",
}


def _router_bullet_for(router: str, phrase: str) -> str:
    matches = [line for line in router.splitlines() if line.startswith("- ") and phrase.lower() in line.lower()]
    assert len(matches) == 1, f"expected exactly one router bullet mentioning {phrase!r}, found {len(matches)}"
    return matches[0]


def test_season_router_routes_every_season_sub_procedure() -> None:
    manifest = _manifest()
    procedure_root = ROOT / manifest["procedure_root"]
    router = (procedure_root / "season.md").read_text(encoding="utf-8")

    for sub_procedure in sorted((procedure_root / "season").glob("*.md")):
        relative = sub_procedure.relative_to(procedure_root).as_posix()
        assert relative in router, f"season router does not route {relative}"


def test_season_operation_headings_live_in_the_routed_procedure() -> None:
    procedure_root = ROOT / _manifest()["procedure_root"]
    router = (procedure_root / "season.md").read_text(encoding="utf-8")
    sub_texts = {
        path.relative_to(procedure_root).as_posix(): path.read_text(encoding="utf-8")
        for path in (procedure_root / "season").glob("*.md")
    }
    all_texts = {"season.md": router, **sub_texts}

    for heading, expected in SEASON_OPERATION_ROUTES.items():
        owners = [name for name, text in all_texts.items() if f"\n{heading}\n" in f"\n{text}\n"]
        assert owners == [expected], f"{heading!r} lives in {owners}, expected only {expected}"
        bullet = _router_bullet_for(router, SEASON_ROUTER_PHRASES[heading])
        assert f"`{expected}`" in bullet, f"season router routes {heading!r} elsewhere: {bullet!r}"


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
