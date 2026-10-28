#!/usr/bin/env python3
"""One-command release gate for the kry repository."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
_FULL_CHECKS = {
    "install:editable", "install:wheel", "dev-tooling", "compileall", "ruff", "pytest",
    "attribution", "diff-check", "cached-diff-check", "untracked-files", "packet_gate",
    "reproducibility-full", "source-stable",
}


def _source_identity() -> dict[str, object]:
    """Record tracked source, including dirty bytes; never label them as the HEAD tree."""
    def git(*args: str) -> bytes:
        return subprocess.check_output(["git", *args], cwd=ROOT, stderr=subprocess.PIPE)

    try:
        diff = git("diff", "--no-ext-diff", "--no-textconv", "--binary", "HEAD", "--")
        return {
            "commit": git("rev-parse", "HEAD").decode().strip(),
            "tree": git("rev-parse", "HEAD^{tree}").decode().strip(),
            "tags": sorted(git("tag", "--points-at", "HEAD").decode().splitlines()),
            "tracked_dirty": bool(diff),
            "tracked_diff_sha256": hashlib.sha256(diff).hexdigest(),
        }
    except (OSError, subprocess.CalledProcessError) as exc:
        return {"error": str(exc)}


def _untracked_package_inputs() -> list[str]:
    """Include ignored source files; mirror build_backend._collect's exclusions."""
    try:
        raw = subprocess.check_output(
            ["git", "ls-files", "-z", "--", "src/"], cwd=ROOT, stderr=subprocess.PIPE)
    except subprocess.CalledProcessError as exc:
        raise ValueError("cannot inventory untracked package inputs") from exc
    tracked = {os.fsdecode(name) for name in raw.split(b"\0") if name}
    extra = []
    # Walk what the backend walks: git's untracked listing can stop at a nested repo.
    for candidate in sorted((ROOT / "src").rglob("*")):
        path = candidate.relative_to(ROOT)
        if (path.suffix != ".pyc"
                and not any(part.startswith(".") or part == "__pycache__" for part in path.parts)
                and candidate.is_file() and path.as_posix() not in tracked):
            extra.append(path.as_posix())
    return extra


def verify_proof(path: Path, expected_tag: str) -> None:
    """Check a full proof against this checkout; provenance authenticates its bytes separately."""
    report = _json_load(path)
    if (not isinstance(report, dict) or report.get("schema") != "kry_release_verify/v2"
            or report.get("full") is not True or report.get("ok") is not True
            or not isinstance(report.get("python"), str) or not report["python"]):
        raise ValueError("expected a successful full v2 release proof with Python identity")
    checks = report.get("checks")
    if (not isinstance(checks, list) or len(checks) != len(_FULL_CHECKS)
            or any(not isinstance(c, dict) or c.get("ok") is not True
                   or not isinstance(c.get("name"), str) for c in checks)
            or {c["name"] for c in checks} != _FULL_CHECKS):
        raise ValueError("proof must include every successful full-gate check exactly once")
    before = report.get("source_before")
    current = _source_identity()
    if (not isinstance(before, dict) or "error" in current
            or before != report.get("source_after")
            or before.get("tracked_dirty") is not False
            or current.get("tracked_dirty") is not False
            or any(before.get(k) != current.get(k)
                   for k in ("commit", "tree", "tracked_diff_sha256"))
            or not isinstance(before.get("tags"), list)
            or expected_tag not in before["tags"] or expected_tag not in current["tags"]):
        raise ValueError("proof must bind unchanged clean source and the expected tag to this checkout")
    extra = _untracked_package_inputs()
    if extra:
        raise ValueError(f"untracked or ignored package inputs: {extra}")


def _dev_requirements() -> tuple[str, ...]:
    """A1-4: read the dev pins from pyproject instead of duplicating them.

    A hand-copied duplicate drifts (it had already drifted once, to 9.1.0/0.15.17) and it breaks
    every dependabot bump, which edits pyproject only. pyproject is the single source of truth.
    """
    with (ROOT / "pyproject.toml").open("rb") as handle:
        pins = tomllib.load(handle)["project"]["optional-dependencies"]["dev"]
    return tuple(pins)


DEV_REQUIREMENTS = _dev_requirements()


def _reject_json_constant(value: str):
    raise ValueError(f"non-standard JSON constant rejected: {value}")


def _json_load(path: Path):
    with path.open() as handle:
        return json.load(handle, parse_constant=_reject_json_constant)


def _json_dumps(value: object, **kwargs) -> str:
    kwargs.setdefault("allow_nan", False)
    return json.dumps(value, **kwargs)


def _cmd_text(cmd: list[str]) -> str:
    return " ".join(cmd)


def _python() -> str:
    return sys.executable or "python3"


def _git_for_windows_bash(git: str) -> str | None:
    # WINDOWS-BASH: git.exe sits one level below the Git for Windows root (cmd\, bin\) or two
    # (mingw64\bin\); bash.exe is in the root's bin\ in every layout.
    executable = Path(git).resolve()
    for root in executable.parents[1:3]:
        bash = root / "bin" / "bash.exe"
        if bash.is_file():
            return str(bash)
    return None


def _bash() -> str | None:
    # WINDOWS-BASH: CreateProcess searches System32 before PATH, so a bare "bash" on a Windows host
    # with WSL starts the WSL launcher. The reproducibility proof then ran in a Linux guest that
    # never received PYTHON, under the guest's python3, while the report named the host
    # interpreter. None fails the check closed rather than falling back to a bare "bash".
    if os.name != "nt":
        return "bash"
    git = shutil.which("git")
    return _git_for_windows_bash(git) if git else None


def _clean_python_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONHOME", None)
    # An inherited selection or collection-only flag cannot narrow the release suite.
    env.pop("PYTEST_ADDOPTS", None)
    if extra:
        env.update(extra)
    return env


def _run(
    name: str,
    cmd: list[str],
    *,
    env: dict[str, str] | None = None,
    cwd: Path = ROOT,
    capture: bool = True,
) -> dict[str, object]:
    start = time.time()
    try:
        proc = subprocess.run(
            cmd,
            cwd=cwd,
            env=env,
            text=True,
            stdout=subprocess.PIPE if capture else None,
            stderr=subprocess.STDOUT if capture else None,
        )
    except FileNotFoundError as exc:
        elapsed = round(time.time() - start, 3)
        return {
            "name": name,
            "cmd": _cmd_text(cmd),
            "elapsed_seconds": elapsed,
            "ok": False,
            "returncode": 127,
            "output": str(exc),
        }
    elapsed = round(time.time() - start, 3)
    return {
        "name": name,
        "cmd": _cmd_text(cmd),
        "elapsed_seconds": elapsed,
        "ok": proc.returncode == 0,
        "returncode": proc.returncode,
        "output": (proc.stdout or "")[-8000:] if capture else "",
    }


def _venv_python(venv: Path) -> str:
    return str(venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python"))


def _verify_install(mode: str, temp_root: Path) -> dict[str, object]:
    venv = temp_root / f"venv-{mode}"
    env = _clean_python_env()
    steps: list[dict[str, object]] = []
    steps.append(_run(f"venv:{mode}", [_python(), "-m", "venv", str(venv)], env=env))
    if not steps[-1]["ok"]:
        return {"name": f"install:{mode}", "ok": False, "steps": steps}

    py = _venv_python(venv)
    install_cmd = [py, "-m", "pip", "install", "--no-deps", "--no-build-isolation", "-e", str(ROOT)] if mode == "editable" else [
        py,
        "-m",
        "pip",
        "install",
        "--no-deps",
        "--no-build-isolation",
        str(ROOT),
    ]
    steps.append(_run(f"pip-install:{mode}", install_cmd, env=env))
    if steps[-1]["ok"]:
        steps.append(_run(f"import:{mode}", [py, "-c", "import kry; print(kry.__file__)"], env=env))
    return {"name": f"install:{mode}", "ok": all(step["ok"] for step in steps), "steps": steps}


def _packet_gate(temp_root: Path) -> dict[str, object]:
    packet_root = temp_root / "packet-gate"
    packet_root.mkdir(parents=True, exist_ok=True)
    env = _clean_python_env({"KRY_DATA_DIR": str(packet_root / "kry_data")})

    outputs = {
        "doctor_before": packet_root / "doctor-before.json",
        "savings": packet_root / "savings.txt",
        "savings_mint": packet_root / "savings-mint.txt",
        "verify": packet_root / "verify.txt",
        "artifact_build": packet_root / "artifact-build.json",
        "artifact_verify": packet_root / "artifact-verify.json",
        "finops": packet_root / "finops.md",
        "doctor_after": packet_root / "doctor-after.json",
    }

    commands = [
        (
            "doctor-before",
            [_python(), "scripts/kry_doctor.py", "--json"],
            outputs["doctor_before"],
        ),
        (
            "savings-report",
            [_python(), "scripts/kry_savings_report.py", "examples/sample_usage_log.jsonl"],
            outputs["savings"],
        ),
        (
            "savings-mint-attest",
            [
                _python(),
                "scripts/kry_savings_report.py",
                "examples/sample_usage_log.jsonl",
                "--mint",
                "--attest",
                str(packet_root / "att.json"),
            ],
            outputs["savings_mint"],
        ),
        (
            "verify-attestation",
            [_python(), "scripts/kry_verify.py", str(packet_root / "att.json")],
            outputs["verify"],
        ),
        (
            "build-artifact",
            [
                _python(),
                "scripts/kry_verified_artifact.py",
                "examples/sample_usage_log.jsonl",
                "--attestation",
                str(packet_root / "att.json"),
                "--mint-log",
                str(packet_root / "kry_data" / "kry_mint_log.jsonl"),
                "--bundle-dir",
                str(packet_root / "packet"),
            ],
            outputs["artifact_build"],
        ),
        (
            "verify-artifact",
            [
                _python(),
                "scripts/kry_verified_artifact.py",
                "--verify-artifact",
                str(packet_root / "packet" / "artifact.json"),
            ],
            outputs["artifact_verify"],
        ),
        (
            "finops-report",
            [
                _python(),
                "scripts/kry_finops_report.py",
                str(packet_root / "packet" / "artifact.json"),
            ],
            outputs["finops"],
        ),
        (
            "doctor-after",
            [
                _python(),
                "scripts/kry_doctor.py",
                "--artifact",
                str(packet_root / "packet" / "artifact.json"),
                "--json",
            ],
            outputs["doctor_after"],
        ),
    ]

    steps: list[dict[str, object]] = []
    for name, cmd, output in commands:
        result = _run(name, cmd, env=env)
        output.write_text(str(result["output"]), encoding="utf-8")
        result["artifact"] = str(output)
        steps.append(result)
        if not result["ok"]:
            return {"name": "packet_gate", "ok": False, "steps": steps}

    artifact_verify = _json_load(outputs["artifact_verify"])
    doctor_after = _json_load(outputs["doctor_after"])
    assertions = {
        "artifact_ok": artifact_verify.get("ok") is True,
        "ship_scope_internal": artifact_verify.get("ship_scope") == "internal_or_demo_only",
        "external_claim_blocked": artifact_verify.get("claim_allowed", {}).get("external_verified_savings") is False,
        "doctor_no_failures": doctor_after.get("summary", {}).get("fail") == 0,
    }
    return {
        "name": "packet_gate",
        "ok": all(assertions.values()) and all(step["ok"] for step in steps),
        "steps": steps,
        "assertions": assertions,
        "packet_dir": str(packet_root / "packet"),
    }


def _prepare_dev_tools(temp_root: Path) -> tuple[dict[str, object], list[str], list[str]]:
    venv = temp_root / "venv-tools"
    env = _clean_python_env()
    steps: list[dict[str, object]] = []
    steps.append(_run("dependency-locks", [_python(), "scripts/check_dependency_locks.py"], env=env))
    if steps[-1]["ok"]:
        steps.append(_run("venv:tools", [_python(), "-m", "venv", str(venv)], env=env))
    if steps[-1]["ok"]:
        py = _venv_python(venv)
        steps.append(_run("pip-install:tools", [
            py, "-m", "pip", "install", "--require-hashes", "--only-binary=:all:",
            "-r", str(ROOT / ".github" / "dev-requirements.txt"),
        ], env=env))

    ruff_bin = str(venv / ("Scripts/ruff.exe" if os.name == "nt" else "bin/ruff"))
    pytest_cmd = [_venv_python(venv), "-B", "-m", "pytest", "tests/", "-q"]
    return {
        "name": "dev-tooling",
        "ok": all(step["ok"] for step in steps) and Path(ruff_bin).exists(),
        "steps": steps,
        "requirements": list(DEV_REQUIREMENTS),
    }, [ruff_bin, "check", "src/", "scripts/", "tests/", "examples/", "lab/"], pytest_cmd


def _untracked_files_check() -> dict[str, object]:
    result = _run("untracked-files", ["git", "clean", "-nd"])
    try:
        extra = _untracked_package_inputs()
    except (OSError, ValueError) as exc:
        result.update(ok=False, detail=str(exc))
        return result
    if extra:
        result.update(ok=False, detail=f"untracked or ignored package inputs: {extra}")
        return result
    if result["ok"] and str(result["output"]).strip():
        result["ok"] = False
        result["detail"] = "untracked files would be removed by git clean -nd"
    elif result["ok"]:
        result["detail"] = "no untracked files reported by git clean -nd"
    return result


def run_release_gate(*, full: bool) -> dict[str, object]:
    source_before = _source_identity()
    temp_root = Path(tempfile.mkdtemp(prefix="kry-release-verify."))
    checks: list[dict[str, object]] = []

    checks.append(_verify_install("editable", temp_root))
    checks.append(_verify_install("wheel", temp_root))
    dev_tooling, ruff_cmd, pytest_cmd = _prepare_dev_tools(temp_root)
    checks.append(dev_tooling)
    clean_env = _clean_python_env({"PYTHON": pytest_cmd[0]})
    simple_checks = [
        (
            "compileall",
            [_python(), "-B", "-m", "compileall", "-q", "build_backend.py", "scripts", "src", "tests", "lab", "examples"],
            clean_env,
        ),
        ("ruff", ruff_cmd, clean_env),
        ("pytest", pytest_cmd, clean_env),
        ("attribution", [_python(), "scripts/check_attribution.py"], clean_env),
        ("diff-check", ["git", "diff", "--check"], None),
        ("cached-diff-check", ["git", "diff", "--cached", "--check"], None),
    ]
    for name, cmd, env in simple_checks:
        checks.append(_run(name, cmd, env=env))
    checks.append(_untracked_files_check())

    checks.append(_packet_gate(temp_root))

    reproduce_name = "reproducibility-full" if full else "reproducibility-smoke"
    bash = _bash()
    if bash is None:
        checks.append({
            "name": reproduce_name,
            "ok": False,
            "detail": "no Git for Windows bash found next to git; a bare 'bash' would start the WSL launcher",
        })
    else:
        reproduce_cmd = [bash, "lab/reproduce.sh", "10" if full else "1"]
        checks.append(_run(reproduce_name, reproduce_cmd, env=clean_env))
    source_after = _source_identity()
    checks.append({
        "name": "source-stable",
        "ok": "error" not in source_before and source_before == source_after,
        "detail": "commit, tree, tags and tracked diff must remain unchanged during verification",
    })

    return {
        "schema": "kry_release_verify/v2",
        "full": full,
        "ok": all(check["ok"] for check in checks),
        "root": str(ROOT),
        "temp_root": str(temp_root),
        "checks": checks,
        "source_before": source_before,
        "source_after": source_after,
        "python": sys.version,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the KRY release-candidate gate.")
    parser.add_argument("--full", action="store_true", help="run the 10-round reproducibility proof")
    parser.add_argument("--json-out", help="write the full machine-readable report to this path")
    parser.add_argument("--verify-proof", help="validate an existing full proof before publication")
    parser.add_argument("--expected-tag", help="release tag required by --verify-proof")
    args = parser.parse_args(argv)
    if args.verify_proof:
        if not args.expected_tag or args.full or args.json_out:
            parser.error("--verify-proof requires --expected-tag and excludes --full/--json-out")
        try:
            verify_proof(Path(args.verify_proof), args.expected_tag)
        except (OSError, ValueError) as exc:
            print(f"[FAIL] release proof: {exc}", file=sys.stderr)
            return 1
        print("[PASS] release proof matches this checkout and tag")
        return 0
    if args.expected_tag:
        parser.error("--expected-tag requires --verify-proof")

    report = run_release_gate(full=args.full)
    if args.json_out:
        Path(args.json_out).write_text(_json_dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    for check in report["checks"]:
        status = "PASS" if check["ok"] else "FAIL"
        print(f"[{status}] {check['name']}")
        if not check["ok"]:
            print(_json_dumps(check, indent=2, sort_keys=True))

    print("RESULT:", "PASS" if report["ok"] else "FAIL")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
