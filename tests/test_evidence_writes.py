"""Research scripts leave committed evidence alone unless asked, and refuse a run that measured nothing.

Run offline, keyless or with --help, these scripts used to replace the committed results under
docs/evidence/ with zeros or placeholders and exit 0: the 0.7096 behind the README's 71%
prefix-cacheable figure became 0.0, and a WildChat dry run replaced the 467-line usage log with one
blank line. Each case here runs a real script from a copy of the repository with no API keys and
every HTTP(S) request sent to a closed local port, then checks the exit status and every byte under
docs/evidence/. examples/demo.sh is held to the same rule: it may not report success on INVALID.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "scripts"))
import kry_evidence  # noqa: E402

_SECRET_MARKERS = ("KEY", "TOKEN", "SECRET", "PASSWORD", "CREDENTIAL")
_CLOSED_PORT = "http://127.0.0.1:9"
# Three tiny MBPP-shaped tasks whose reference solutions pass their tests (the --dry path).
_MBPP = [
    {"task_id": 1, "text": "Add two numbers.", "code": "def add(a, b):\n    return a + b",
     "test_list": ["assert add(1, 2) == 3"], "test_setup_code": ""},
    {"task_id": 2, "text": "Double a number.", "code": "def double(x):\n    return 2 * x",
     "test_list": ["assert double(4) == 8"], "test_setup_code": ""},
    {"task_id": 3, "text": "Negate a number.", "code": "def neg(x):\n    return -x",
     "test_list": ["assert neg(5) == -5"], "test_setup_code": ""},
]


def _evidence(repo: Path) -> dict[str, str]:
    root = repo / "docs" / "evidence"
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.is_file()}


@pytest.fixture(scope="module")
def sandbox(tmp_path_factory):
    base = tmp_path_factory.mktemp("evidence")
    repo = base / "repo"
    shutil.copytree(_ROOT, repo, ignore=shutil.ignore_patterns(
        ".git", "__pycache__", ".venv", "venv", "kry_data", "kry_runs", "media", "node_modules",
        "*.egg-info", ".pytest_cache", "dist"))
    home = base / "home"
    (home / ".cache").mkdir(parents=True)
    (home / ".cache" / "kry_mbpp.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in _MBPP), encoding="utf-8")
    return repo, home


def _env(home: Path, tmp_path: Path) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items()
           if not any(m in k.upper() for m in _SECRET_MARKERS) and k.upper() != "NO_PROXY"}
    env.update({
        "HOME": str(home), "USERPROFILE": str(home),
        "TMPDIR": str(tmp_path), "TEMP": str(tmp_path), "TMP": str(tmp_path),
        "KRY_DATA_DIR": str(tmp_path / "kry_data"),
        "HTTP_PROXY": _CLOSED_PORT, "HTTPS_PROXY": _CLOSED_PORT,
        "http_proxy": _CLOSED_PORT, "https_proxy": _CLOSED_PORT,
    })
    return env


def _run(sandbox, tmp_path, script: str, *args: str) -> subprocess.CompletedProcess:
    repo, home = sandbox
    return subprocess.run([sys.executable, str(repo / script), *args], cwd=str(repo),
                          env=_env(home, tmp_path), capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=300)


@pytest.mark.parametrize(("script", "args", "reason"), [
    # The findings' reproductions: each exited 0 after overwriting committed evidence.
    ("scripts/kry_wildchat_corpus_proof.py", ("--rows", "100", "--dry-run"),
     "the WildChat pull stopped at offset 0"),
    ("scripts/kry_multiturn_prefix_savings.py", ("100", "--write-evidence"),
     "the WildChat pull gave up at offset 0"),
    # Keyless: every task is skipped, so the run measures nothing, even when asked to write.
    ("scripts/kry_cod_experiment.py", ("3", "--write-evidence"), "the run measured nothing"),
    ("scripts/kry_shadow_sim.py", ("3", "--write-evidence"), "the run measured nothing"),
])
def test_a_run_that_measured_nothing_fails_and_writes_no_evidence(sandbox, tmp_path, script, args, reason):
    before = _evidence(sandbox[0])
    proc = _run(sandbox, tmp_path, script, *args)
    assert proc.returncode == 1, proc.stdout[-1500:] + proc.stderr[-1500:]
    assert reason in proc.stderr
    assert _evidence(sandbox[0]) == before


def test_placeholder_holdout_costs_can_never_replace_the_committed_log(sandbox, tmp_path):
    before = _evidence(sandbox[0])
    proc = _run(sandbox, tmp_path, "scripts/kry_wildchat_corpus_proof.py",
                "--rows", "100", "--dry-run", "--write-evidence")
    assert proc.returncode == 2
    assert "--write-evidence needs real holdout calls" in proc.stderr
    assert _evidence(sandbox[0]) == before


def test_a_measured_run_writes_evidence_only_when_asked(sandbox, tmp_path):
    repo = sandbox[0]
    before = _evidence(repo)
    proc = _run(sandbox, tmp_path, "scripts/kry_shadow_sim.py", "--dry", "3")
    assert proc.returncode == 0, proc.stdout[-1500:] + proc.stderr[-1500:]
    assert "--write-evidence replaces it" in proc.stdout
    assert _evidence(repo) == before

    proc = _run(sandbox, tmp_path, "scripts/kry_shadow_sim.py", "--dry", "3", "--write-evidence")
    assert proc.returncode == 0, proc.stdout[-1500:] + proc.stderr[-1500:]
    after = _evidence(repo)
    changed = sorted(k for k in before.keys() | after.keys() if before.get(k) != after.get(k))
    assert changed == ["level1_sim/level1_sim_rows_dry.jsonl", "level1_sim/level1_sim_summary_dry.json"]
    summary = json.loads((repo / "docs/evidence/level1_sim/level1_sim_summary_dry.json").read_text(encoding="utf-8"))
    assert summary["tasks_measured"] == 3


def test_publish_copies_only_when_asked_and_only_what_was_measured(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(kry_evidence, "EVIDENCE", tmp_path / "evidence")
    committed = tmp_path / "evidence" / "demo" / "result.json"
    committed.parent.mkdir(parents=True)
    committed.write_text("committed", encoding="utf-8")
    fresh = tmp_path / "run" / "result.json"
    fresh.parent.mkdir()
    fresh.write_text("fresh", encoding="utf-8")
    files = {"result.json": fresh}

    assert kry_evidence.publish(files, "demo", rows=0, write=True) == 1
    assert kry_evidence.publish(files, "demo", rows=5, write=True, failure="the pull stopped") == 1
    assert kry_evidence.publish(files, "demo", rows=5, write=False) == 0
    assert committed.read_text(encoding="utf-8") == "committed"
    assert kry_evidence.publish(files, "demo", rows=5, write=True) == 0
    assert committed.read_text(encoding="utf-8") == "fresh"
    err = capsys.readouterr().err
    assert "the run measured nothing" in err and "the pull stopped" in err


_TAMPER = '''\
import json, sys
path = sys.argv[1]
with open(path, encoding="utf-8") as f:
    att = json.load(f)
att["links"][0]["tokens_saved"] += 1
with open(path, "w", encoding="utf-8") as f:
    json.dump(att, f)
'''


@pytest.mark.skipif(sys.platform == "win32" or shutil.which("bash") is None,
                    reason="examples/demo.sh is a bash script; the PYTHON stand-in below is POSIX sh")
@pytest.mark.parametrize("tamper", [False, True])
def test_demo_exits_nonzero_when_the_stranger_check_fails(sandbox, tmp_path, tamper):
    repo, home = sandbox
    python = tmp_path / "python"
    if tamper:
        (tmp_path / "tamper.py").write_text(_TAMPER, encoding="utf-8")
        python.write_text(
            "#!/bin/sh\n"
            f'if [ "$1" = "scripts/kry_verify.py" ]; then "{sys.executable}" "{tmp_path / "tamper.py"}" "$2"; fi\n'
            f'exec "{sys.executable}" "$@"\n', encoding="utf-8")
    else:
        python.write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n', encoding="utf-8")
    python.chmod(0o755)
    env = _env(home, tmp_path)
    env.update({"PYTHON": str(python), "KRY_DEMO_PACE": "0"})
    proc = subprocess.run(["bash", str(repo / "examples" / "demo.sh")], cwd=str(repo), env=env,
                          capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300)
    confirmed = "confirmed by code that does NOT trust the producer" in proc.stdout
    if tamper:
        assert proc.returncode != 0
        assert "VERDICT: INVALID" in proc.stdout
        assert not confirmed
    else:
        assert proc.returncode == 0, proc.stdout[-1500:] + proc.stderr[-1500:]
        assert confirmed
