"""Release verifier invariants."""

from __future__ import annotations

import sys

import pytest

import scripts.kry_release_verify as release_verify


def test_release_verifier_pins_test_and_lint_tools():
    # A1-4: the privileged release verifier's dev pins MUST match pyproject (they had drifted to
    # 9.1.0/0.15.17 while pyproject moved to 9.1.1/0.15.18 — a stale duplicate is worse than none).
    assert release_verify.DEV_REQUIREMENTS == ("pytest==9.1.1", "ruff==0.15.18")


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
