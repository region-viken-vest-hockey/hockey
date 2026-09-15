from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / "scripts" / "rvv-miniputt"

LEGACY_ENTRYPOINTS = (
    ROOT / "tournament_scheduler.py",
    ROOT / "tournament_scheduler_interactive.py",
    ROOT / "tournament-scheduler.sh",
    ROOT / "scripts" / "rvv-miniputt-checkpoint",
)

LEGACY_COMMAND_MODULES = (
    ROOT / "tournament_scheduler" / "cli" / "season_command.py",
    ROOT / "tournament_scheduler" / "cli" / "scheduling_command.py",
    ROOT / "tournament_scheduler" / "cli" / "reschedule_command.py",
    ROOT / "tournament_scheduler" / "cli" / "update_command.py",
)


def test_legacy_scheduler_entrypoints_do_not_return() -> None:
    assert not [str(path.relative_to(ROOT)) for path in LEGACY_ENTRYPOINTS if path.exists()]
    assert not [str(path.relative_to(ROOT)) for path in LEGACY_COMMAND_MODULES if path.exists()]


def test_repository_launcher_is_transport_only() -> None:
    text = LAUNCHER.read_text(encoding="utf-8")

    assert "-m tournament_scheduler.cli.rvv_cli" in text
    for forbidden in (
        "BOOKUP_",
        "manual-bookup-login",
        "dotenvx",
        "stage1_config",
        "stage2_scraping",
        "stage3_planning",
        "stage4_export",
    ):
        assert forbidden not in text
