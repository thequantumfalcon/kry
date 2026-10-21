"""Dependency updates must preserve hash enforcement before CI installs tools."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from scripts import check_dependency_locks as locks
from scripts import kry_release_verify as release_verify


@pytest.fixture
def locked_project(tmp_path):
    (tmp_path / ".github").mkdir()
    shutil.copy(locks.ROOT / "pyproject.toml", tmp_path)
    for name in ("dev-requirements.txt", "litellm-requirements.in", "litellm-requirements.txt"):
        shutil.copy(locks.ROOT / ".github" / name, tmp_path / ".github")
    return tmp_path


def test_checked_in_inputs_match_locks():
    assert locks.check_locks() == []


@pytest.mark.parametrize("source", ["pyproject.toml", ".github/litellm-requirements.in"])
def test_dependency_bump_requires_lock_refresh(locked_project, source):
    path = locked_project / source
    original = path.read_text(encoding="utf-8")
    old = (release_verify.DEV_REQUIREMENTS[0] if source == "pyproject.toml"
           else next(line for line in original.splitlines() if line.startswith("litellm==")))
    path.write_text(original.replace(old, old.split("==")[0] + "==999.0.0"), encoding="utf-8")
    assert any("regenerate the lock" in error for error in locks.check_locks(locked_project))


@pytest.mark.parametrize("replacement", ["", "; sys_platform == 'linux'"])
def test_direct_pin_cannot_be_missing_or_platform_limited(locked_project, replacement):
    path = locked_project / ".github/dev-requirements.txt"
    pin = release_verify.DEV_REQUIREMENTS[0]
    text = path.read_text(encoding="utf-8")
    path.write_text(text.replace(pin, pin + " " + replacement if replacement else "other==1.0"), encoding="utf-8")
    assert locks.check_locks(locked_project)


def test_unhashed_transitive_is_rejected(locked_project):
    path = locked_project / ".github/dev-requirements.txt"
    path.write_text(path.read_text(encoding="utf-8") + "missing-hash==1.0\n", encoding="utf-8")
    assert any("missing SHA-256" in error for error in locks.check_locks(locked_project))


def test_stale_lock_stops_release_tool_install(tmp_path, monkeypatch):
    commands = []

    def fail_lock(name, cmd, **kwargs):
        commands.append(cmd)
        return {"name": name, "ok": False, "returncode": 1, "output": "stale lock"}

    monkeypatch.setattr(release_verify, "_run", fail_lock)
    result, _, _ = release_verify._prepare_dev_tools(tmp_path)
    assert result["ok"] is False
    assert len(commands) == 1
    assert commands[0][-1] == "scripts/check_dependency_locks.py"


def test_release_tool_install_requires_hashes_and_wheels(tmp_path, monkeypatch):
    commands = []

    def record(name, cmd, **kwargs):
        commands.append(cmd)
        return {"name": name, "ok": True, "returncode": 0, "output": ""}

    monkeypatch.setattr(release_verify, "_run", record)
    release_verify._prepare_dev_tools(tmp_path)
    install = next(cmd for cmd in commands if "install" in cmd)
    assert "--require-hashes" in install
    assert "--only-binary=:all:" in install
    assert Path(install[-1]).name == "dev-requirements.txt"


@pytest.mark.parametrize("mode", ["editable", "wheel"])
def test_local_package_install_cannot_resolve_dependencies(tmp_path, monkeypatch, mode):
    commands = []

    def record(name, cmd, **kwargs):
        commands.append(cmd)
        return {"name": name, "ok": True, "returncode": 0, "output": ""}

    monkeypatch.setattr(release_verify, "_run", record)
    assert release_verify._verify_install(mode, tmp_path)["ok"]
    install = next(cmd for cmd in commands if "install" in cmd)
    assert "--no-deps" in install
    assert "--no-build-isolation" in install
