"""Static/bootstrap contract tests for one-command developer setup."""

from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BOOTSTRAP = ROOT / "scripts" / "bootstrap"
CHECK = ROOT / "scripts" / "check"
README = ROOT / "README.md"


def test_bootstrap_script_is_valid_shell_and_pins_ci_python():
    result = subprocess.run(
        ["sh", "-n", str(BOOTSTRAP)],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert result.returncode == 0, result.stderr

    text = BOOTSTRAP.read_text(encoding="utf-8")
    assert "PYTHON_VERSION=${PYTHON_VERSION:-3.12}" in text
    assert "UV_VERSION=${UV_VERSION:-0.12.17}" in text
    assert 'uv/$UV_VERSION/install.sh' in text
    assert 'venv --seed --python "$PYTHON_VERSION"' in text
    assert 'INSTALL_PLAYWRIGHT=${INSTALL_PLAYWRIGHT:-1}' in text
    assert '"$ROOT_DIR/scripts/check" dependency-lock lint quick' in text


def test_dependency_lock_uses_selected_repository_python():
    text = CHECK.read_text(encoding="utf-8")
    assert 'RVV_CHECK_PYTHON="$PYTHON_CMD"' in text
    assert '"$RVV_CHECK_PYTHON" -m venv "$tmp_dir"' in text
    assert "python3 -m venv" not in text


def test_readme_documents_single_bootstrap_command():
    text = README.read_text(encoding="utf-8")
    assert "make bootstrap" in text
    assert "Python 3.12" in text
    assert "scripts/bootstrap" in text
