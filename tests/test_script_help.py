"""Every command-line script answers --help with its usage and does nothing else.

Scripts that read sys.argv by hand let --help fall through to main(): paid experiments started (one
announced a $15 budget), a network request went out, several crashed on int('--help'), and two
rewrote committed files under docs/evidence/. Each script here runs with --help from a copy of the
repository, with no API keys, the network blocked, HOME, the temp dir and KRY_DATA_DIR inside the
copy, and a cp1252 console. It must exit 0 with usage text, attempt no connection and change no file.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
# Import-only helper modules: they have no command line of their own.
_LIBRARIES = {"scripts/kry_action_mcp.py", "scripts/kry_artifact_io.py", "scripts/kry_artifact_privacy.py",
              "scripts/kry_evidence.py"}
_SECRET_MARKERS = ("KEY", "TOKEN", "SECRET", "PASSWORD", "CREDENTIAL")
_NETGUARD = '''\
import os
import socket


def _blocked(*args, **kwargs):
    with open(os.environ["KRY_TEST_NETLOG"], "a", encoding="utf-8") as f:
        f.write(repr(args[1:2] or args[:1]) + "\\n")
    raise OSError("network blocked by tests/test_script_help.py")


socket.socket.connect = _blocked
socket.socket.connect_ex = _blocked
socket.create_connection = _blocked
socket.getaddrinfo = _blocked
'''


def _cli_scripts() -> list[str]:
    found = []
    for folder in ("scripts", "examples", "lab"):
        for path in sorted((_ROOT / folder).glob("*.py")):
            rel = path.relative_to(_ROOT).as_posix()
            if rel not in _LIBRARIES:
                found.append(rel)
    return found


def _snapshot(root: Path) -> dict[str, tuple[int, int]]:
    out = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for name in filenames:
            p = Path(dirpath) / name
            st = p.stat()
            out[p.relative_to(root).as_posix()] = (st.st_size, st.st_mtime_ns)
    return out


@pytest.fixture(scope="module")
def sandbox(tmp_path_factory):
    base = tmp_path_factory.mktemp("help")
    repo = base / "repo"
    shutil.copytree(_ROOT, repo, ignore=shutil.ignore_patterns(
        ".git", "__pycache__", ".venv", "venv", "kry_data", "kry_runs", "media", "node_modules",
        "*.egg-info", ".pytest_cache", "dist"))
    for name in ("home", "tmp"):
        (repo / name).mkdir()
    guard = base / "netguard"
    guard.mkdir()
    (guard / "sitecustomize.py").write_text(_NETGUARD, encoding="utf-8")
    return repo, guard


def test_the_sweep_covers_the_known_entry_points():
    scripts = _cli_scripts()
    for expected in ("scripts/kry_shadow_sim.py", "scripts/kry_cod_experiment.py", "scripts/kry_verify.py",
                     "examples/try_kry.py", "lab/run_local.py"):
        assert expected in scripts
    assert not _LIBRARIES & set(scripts)


@pytest.mark.parametrize("script", _cli_scripts())
def test_help_prints_usage_with_no_side_effects(sandbox, script, tmp_path):
    repo, guard = sandbox
    netlog = tmp_path / "net.log"
    env = {k: v for k, v in os.environ.items() if not any(m in k.upper() for m in _SECRET_MARKERS)}
    env.update({
        "HOME": str(repo / "home"), "USERPROFILE": str(repo / "home"),
        "TMPDIR": str(repo / "tmp"), "TEMP": str(repo / "tmp"), "TMP": str(repo / "tmp"),
        "KRY_DATA_DIR": str(repo / "kry_data"),
        "PYTHONPATH": os.pathsep.join([str(guard), str(repo / "src")]),
        "PYTHONIOENCODING": "cp1252",
        "KRY_TEST_NETLOG": str(netlog),
    })
    before = _snapshot(repo)
    proc = subprocess.run([sys.executable, str(repo / script), "--help"], cwd=str(repo), env=env,
                          capture_output=True, encoding="cp1252", errors="replace", timeout=120)
    after = _snapshot(repo)
    output = f"stdout:\n{proc.stdout[-1500:]}\nstderr:\n{proc.stderr[-1500:]}"

    assert proc.returncode == 0, f"{script} --help exited {proc.returncode}\n{output}"
    assert "usage:" in proc.stdout.lower(), f"{script} --help printed no usage\n{output}"
    assert not netlog.exists(), f"{script} --help tried to connect: {netlog.read_text(encoding='utf-8')}"
    changed = sorted(k for k in before.keys() | after.keys() if before.get(k) != after.get(k))
    assert not changed, f"{script} --help changed files: {changed}"
