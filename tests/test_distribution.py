"""Per-user data/application separation and version metadata for the packaged distribution."""

from __future__ import annotations

import tomllib
from pathlib import Path, PurePosixPath, PureWindowsPath

import pytest

from tournament_scheduler import __version__, distribution

ROOT = Path(__file__).resolve().parents[1]


def test_windows_default_data_root_is_under_local_app_data_not_install_dir():
    root = distribution.default_data_root(
        {"LOCALAPPDATA": r"C:\Users\ola\AppData\Local"},
        windows=True,
        home=PureWindowsPath(r"C:\Users\ola"),
    )
    assert root == PureWindowsPath(r"C:\Users\ola\AppData\Local\RVV Miniputt\data")


def test_windows_falls_back_to_profile_app_data_without_local_app_data():
    root = distribution.default_data_root({}, windows=True, home=PureWindowsPath(r"C:\Users\ola"))
    assert root == PureWindowsPath(r"C:\Users\ola\AppData\Local\RVV Miniputt\data")


def test_explicit_data_dir_override_wins_on_every_platform():
    env = {distribution.DATA_ROOT_ENV_VAR: r"D:\rvv-data"}
    assert distribution.default_data_root(env, windows=True, home=PureWindowsPath("C:/u")) == PureWindowsPath(
        r"D:\rvv-data"
    )
    env = {distribution.DATA_ROOT_ENV_VAR: "/srv/rvv"}
    assert distribution.default_data_root(env, windows=False, home=PurePosixPath("/home/u")) == PurePosixPath(
        "/srv/rvv"
    )


def test_posix_default_data_root_is_xdg_style_share_directory():
    root = distribution.default_data_root({}, windows=False, home=PurePosixPath("/home/ola"))
    assert root == PurePosixPath("/home/ola/.local/share/rvv-miniputt")


def test_data_root_inside_application_dir_is_rejected(tmp_path):
    app = tmp_path / "Programs" / "RVV Miniputt"
    with pytest.raises(ValueError, match="must not be inside the application directory"):
        distribution.ensure_data_outside_application(app, app / "data")
    with pytest.raises(ValueError):
        distribution.ensure_data_outside_application(app, app)


def test_sibling_data_root_is_accepted_even_with_shared_name_prefix(tmp_path):
    app = tmp_path / "RVV Miniputt"
    # A name-prefix lookalike is not a child of the app directory.
    distribution.ensure_data_outside_application(app, tmp_path / "RVV Miniputt data")
    distribution.ensure_data_outside_application(app, tmp_path / "RVV Miniputt" / ".." / "elsewhere")


def test_prepare_working_root_creates_data_root_sets_season_root_and_changes_directory(tmp_path, monkeypatch):
    data = tmp_path / "data"
    env: dict[str, str] = {}
    original_cwd = Path.cwd()
    try:
        distribution.prepare_working_root(data, env)
        assert data.is_dir()
        assert Path.cwd() == data.resolve()
        assert env[distribution.SEASON_ROOT_ENV_VAR] == str(data / "season")
    finally:
        monkeypatch.chdir(original_cwd)


def test_prepare_working_root_respects_explicit_season_root(tmp_path, monkeypatch):
    original_cwd = Path.cwd()
    env = {distribution.SEASON_ROOT_ENV_VAR: "/custom/season"}
    try:
        distribution.prepare_working_root(tmp_path / "data", env)
        assert env[distribution.SEASON_ROOT_ENV_VAR] == "/custom/season"
    finally:
        monkeypatch.chdir(original_cwd)


def test_season_root_env_name_matches_application_contract():
    from tournament_scheduler.season.baseline_overlay import SEASON_ROOT_ENV_VAR

    assert distribution.SEASON_ROOT_ENV_VAR == SEASON_ROOT_ENV_VAR


def test_version_metadata_is_deterministic_and_matches_pyproject():
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert pyproject["project"]["version"] == __version__

    first = distribution.version_metadata(frozen=True)
    second = distribution.version_metadata(frozen=True)
    assert first == second
    assert first == {"name": "rvv-miniputt", "version": __version__, "distribution": "packaged"}
    assert distribution.version_metadata(frozen=False)["distribution"] == "source"


def test_launcher_targets_existing_cli_entry_point():
    launcher = (ROOT / "packaging" / "windows" / "rvv_miniputt_launcher.py").read_text(encoding="utf-8")
    assert "from tournament_scheduler.cli.rvv_cli import main" in launcher
    spec = (ROOT / "packaging" / "windows" / "rvv-miniputt.spec").read_text(encoding="utf-8")
    assert "rvv_miniputt_launcher.py" in spec
