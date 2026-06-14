"""Packaging smoke tests for the stdlib-only build backend."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _copy_minimal_checkout(tmp_path: Path) -> Path:
    src = tmp_path / "checkout"
    (src / "src").mkdir(parents=True)
    shutil.copy2(ROOT / "pyproject.toml", src / "pyproject.toml")
    shutil.copy2(ROOT / "build_backend.py", src / "build_backend.py")
    shutil.copytree(ROOT / "src" / "kry", src / "src" / "kry")
    return src


def _venv_python(venv: Path) -> Path:
    return venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def _clean_python_env() -> dict[str, str]:
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONHOME", None)
    return env


def _run(cmd: list[str], *, cwd: Path | None = None):
    subprocess.run(
        cmd,
        cwd=cwd,
        check=True,
        env=_clean_python_env(),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )


def _install_and_import(tmp_path: Path, *, editable: bool):
    checkout = _copy_minimal_checkout(tmp_path)
    venv = tmp_path / ("venv-editable" if editable else "venv-wheel")
    _run([sys.executable, "-m", "venv", str(venv)])
    py = _venv_python(venv)
    install_cmd = [str(py), "-m", "pip", "install", "--no-index"]
    if editable:
        install_cmd.append("-e")
    install_cmd.append(str(checkout))
    _run(install_cmd)
    out = subprocess.check_output(
        [str(py), "-c", "import kry; print(kry.__file__)"],
        env=_clean_python_env(),
        text=True,
    ).strip()
    return checkout, Path(out)


def test_editable_install_uses_source_tree_without_index(tmp_path):
    checkout, imported = _install_and_import(tmp_path, editable=True)
    assert str(imported).startswith(str(checkout / "src" / "kry"))


def test_wheel_install_copies_package_without_index(tmp_path):
    checkout, imported = _install_and_import(tmp_path, editable=False)
    assert "site-packages" in str(imported)
    assert not str(imported).startswith(str(checkout))
