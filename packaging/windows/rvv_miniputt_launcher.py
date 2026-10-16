"""PyInstaller entry point for the packaged RVV Miniputt distribution.

Thin wrapper only: it places mutable operator data in the per-user data root,
then delegates to the same ``tournament_scheduler.cli.rvv_cli.main`` that the
``rvv-miniputt`` console script and ``scripts/rvv-miniputt`` use.
"""

from __future__ import annotations

import multiprocessing
import os
import sys
from pathlib import Path


def _application_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def _configure_console_encoding() -> None:
    # Norwegian characters and status glyphs must not crash a cp1252 console.
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


def run() -> int:
    from tournament_scheduler import distribution
    from tournament_scheduler.cli.rvv_cli import main

    _configure_console_encoding()
    app_dir = _application_dir()
    home = Path(os.path.expanduser("~"))
    data_root = Path(
        distribution.default_data_root(os.environ, windows=os.name == "nt", home=home)
    )
    distribution.ensure_data_outside_application(app_dir, data_root)
    distribution.prepare_working_root(data_root, os.environ)
    return main(sys.argv[1:])


if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.exit(run())
