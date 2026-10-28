"""Release verifier invariants."""

from __future__ import annotations

import sys

import pytest

import scripts.kry_release_verify as release_verify


def test_release_verifier_pins_test_and_lint_tools():
    # A1-4: the privileged release verifier's dev pins MUST match pyproject (they had drifted to
    # 9.1.0/0.15.17 while pyproject moved to 9.1.1/0.15.18 — a stale duplicate is worse than none).
    # The pins are now READ from pyproject, so assert the shape, not the versions: hardcoding a
    # version here would just recreate the duplicate this test exists to catch.
    tools = {pin.split("==")[0] for pin in release_verify.DEV_REQUIREMENTS}
    assert tools == {"pytest", "ruff"}
    assert all("==" in pin for pin in release_verify.DEV_REQUIREMENTS)   # exact pins, never ranges


def test_release_verifier_scrubs_python_path_overrides(monkeypatch):
    monkeypatch.setenv("PYTHONPATH", "src")
    monkeypatch.setenv("PYTHONHOME", "/tmp/not-real")

    env = release_verify._clean_python_env({"KRY_DATA_DIR": "/tmp/kry-data"})

    assert "PYTHONPATH" not in env
    assert "PYTHONHOME" not in env
    assert env["KRY_DATA_DIR"] == "/tmp/kry-data"


@pytest.mark.skipif(sys.platform == "win32",
                    reason="release gate runs on the maintainer's POSIX machine; dev-venv layout is "
                           "bin/python, not Windows Scripts/python.exe")
def test_release_verifier_uses_pinned_tool_venv_for_pytest_and_ruff(tmp_path, monkeypatch):
    bin_dir = tmp_path / "venv-tools" / "bin"
    bin_dir.mkdir(parents=True)
    (bin_dir / "ruff").write_text("#!/bin/sh\n", encoding="utf-8")

    def fake_run(name, cmd, **kwargs):
        return {"name": name, "cmd": " ".join(cmd), "ok": True, "returncode": 0, "output": ""}

    monkeypatch.setattr(release_verify, "_run", fake_run)

    tooling, ruff_cmd, pytest_cmd = release_verify._prepare_dev_tools(tmp_path)

    assert tooling["name"] == "dev-tooling"
    assert tooling["requirements"] == list(release_verify.DEV_REQUIREMENTS)
    assert ruff_cmd[0].endswith("ruff")
    assert pytest_cmd[:4] == [str(tmp_path / "venv-tools" / "bin" / "python"), "-B", "-m", "pytest"]


def test_release_gate_runs_reproduce_with_tool_python(tmp_path, monkeypatch):
    tool_python = str(tmp_path / "venv-tools" / "bin" / "python")
    captured: dict[str, dict[str, str] | None] = {}

    monkeypatch.setattr(release_verify.tempfile, "mkdtemp", lambda prefix: str(tmp_path))
    monkeypatch.setattr(
        release_verify,
        "_verify_install",
        lambda mode, temp_root: {"name": f"install:{mode}", "ok": True},
    )
    monkeypatch.setattr(
        release_verify,
        "_prepare_dev_tools",
        lambda temp_root: (
            {"name": "dev-tooling", "ok": True},
            ["ruff", "check"],
            [tool_python, "-B", "-m", "pytest", "tests/", "-q"],
        ),
    )
    monkeypatch.setattr(release_verify, "_packet_gate", lambda temp_root: {"name": "packet_gate", "ok": True})

    def fake_run(name, cmd, *, env=None, **kwargs):
        if name == "reproducibility-smoke":
            captured["env"] = env
        return {"name": name, "cmd": " ".join(cmd), "ok": True, "returncode": 0, "output": ""}

    monkeypatch.setattr(release_verify, "_run", fake_run)

    report = release_verify.run_release_gate(full=False)

    assert report["ok"] is True
    assert captured["env"]["PYTHON"] == tool_python


@pytest.mark.parametrize("layout", ["cmd", "bin", "mingw64/bin"])
def test_release_gate_finds_git_for_windows_bash_from_every_git_layout(tmp_path, layout):
    # WINDOWS-BASH: Git for Windows' own bash.exe is derived from git, whichever directory git.exe
    # was found in; Git Bash puts mingw64\bin first, the installer puts cmd\ on PATH.
    bash = tmp_path / "Git" / "bin" / "bash.exe"
    bash.parent.mkdir(parents=True)
    bash.write_bytes(b"")
    git = tmp_path / "Git" / layout / "git.exe"
    git.parent.mkdir(parents=True, exist_ok=True)
    git.write_bytes(b"")

    assert release_verify._git_for_windows_bash(str(git)) == str(bash.resolve())


def test_release_gate_fails_closed_without_a_host_bash(tmp_path, monkeypatch):
    # WINDOWS-BASH: with no host bash the proof must not fall back to a bare "bash" (the WSL
    # launcher on such a host) — the check fails instead of running somewhere else.
    ran: list[str] = []

    monkeypatch.setattr(release_verify.tempfile, "mkdtemp", lambda prefix: str(tmp_path))
    monkeypatch.setattr(
        release_verify,
        "_verify_install",
        lambda mode, temp_root: {"name": f"install:{mode}", "ok": True},
    )
    monkeypatch.setattr(
        release_verify,
        "_prepare_dev_tools",
        lambda temp_root: (
            {"name": "dev-tooling", "ok": True},
            ["ruff", "check"],
            ["python", "-B", "-m", "pytest", "tests/", "-q"],
        ),
    )
    monkeypatch.setattr(release_verify, "_packet_gate", lambda temp_root: {"name": "packet_gate", "ok": True})
    monkeypatch.setattr(release_verify, "_bash", lambda: None)

    def fake_run(name, cmd, *, env=None, **kwargs):
        ran.append(name)
        return {"name": name, "cmd": " ".join(cmd), "ok": True, "returncode": 0, "output": ""}

    monkeypatch.setattr(release_verify, "_run", fake_run)

    report = release_verify.run_release_gate(full=False)

    smoke = next(check for check in report["checks"] if check["name"] == "reproducibility-smoke")
    assert smoke["ok"] is False
    assert "reproducibility-smoke" not in ran
    assert report["ok"] is False


def test_untracked_files_check_fails_on_git_clean_output(monkeypatch):
    monkeypatch.setattr(
        release_verify,
        "_run",
        lambda name, cmd: {
            "name": name,
            "cmd": " ".join(cmd),
            "ok": True,
            "returncode": 0,
            "output": "Would remove scratch.txt\n",
        },
    )

    result = release_verify._untracked_files_check()

    assert result["ok"] is False
    assert result["name"] == "untracked-files"
    assert "untracked files" in result["detail"]


def test_untracked_files_check_passes_on_empty_git_clean_output(monkeypatch):
    monkeypatch.setattr(
        release_verify,
        "_run",
        lambda name, cmd: {
            "name": name,
            "cmd": " ".join(cmd),
            "ok": True,
            "returncode": 0,
            "output": "",
        },
    )

    result = release_verify._untracked_files_check()

    assert result["ok"] is True
    assert result["detail"] == "no untracked files reported by git clean -nd"
