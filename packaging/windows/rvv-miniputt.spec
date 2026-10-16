# PyInstaller spec for the self-contained RVV Miniputt Windows distribution.
#
# Onedir build: the application folder is replaceable (install/update/uninstall
# touch only it). Mutable operator data lives outside it; see
# docs/windows-distribution.md. Build from the repository root with a
# dedicated virtualenv (see the document for the exact commands).
#
# Excluded on purpose: test tooling and the optional CP-SAT engine (ortools),
# which the default planner does not need. Playwright's Python package is
# bundled because the calendar scraper imports it at module load; browser
# binaries are not bundled and remain an optional, advanced recovery step.

import glob
import os
import sys

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = SPECPATH + "/../.."
# PyInstaller's collect_* helpers import the package while analysing, and the
# spec runner's sys.path does not include the repository root by default.
sys.path.insert(0, ROOT)

hiddenimports = collect_submodules("tournament_scheduler") + collect_submodules("holidays")
datas = collect_data_files("tournament_scheduler")

# Shared agent skill files read at runtime through
# tournament_scheduler.repository_resources. They sit outside the package, so
# each one is bundled explicitly at the same repository-relative location.
# Every such file must be a SKILL.md under .agents/skills/rvv.
for skill_file in sorted(glob.glob(os.path.join(ROOT, ".agents", "skills", "rvv", "**", "SKILL.md"), recursive=True)):
    relative_dir = os.path.relpath(os.path.dirname(skill_file), ROOT)
    datas.append((os.path.normpath(skill_file), relative_dir))

a = Analysis(
    [SPECPATH + "/rvv_miniputt_launcher.py"],
    pathex=[ROOT],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "pytest",
        "_pytest",
        "xdist",
        "ortools",
        "tkinter",
    ],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="rvv-miniputt",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    # Console app: the operator-facing CLI prints results and exit-status text.
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="rvv-miniputt",
)
