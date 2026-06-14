#!/usr/bin/env python3
"""One-command release gate for the kry repository."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEV_REQUIREMENTS = ("pytest==9.0.3", "ruff==0.15.16")


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


def _clean_python_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONHOME", None)
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
    install_cmd = [py, "-m", "pip", "install", "-e", str(ROOT)] if mode == "editable" else [
        py,
        "-m",
        "pip",
        "install",
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
    steps.append(_run("venv:tools", [_python(), "-m", "venv", str(venv)], env=env))
    if steps[-1]["ok"]:
        py = _venv_python(venv)
        steps.append(_run("pip-install:tools", [py, "-m", "pip", "install", *DEV_REQUIREMENTS], env=env))

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
    if result["ok"] and str(result["output"]).strip():
        result["ok"] = False
        result["detail"] = "untracked files would be removed by git clean -nd"
    elif result["ok"]:
        result["detail"] = "no untracked files reported by git clean -nd"
    return result


def run_release_gate(*, full: bool) -> dict[str, object]:
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

    reproduce_cmd = ["bash", "lab/reproduce.sh", "10" if full else "1"]
    checks.append(_run("reproducibility-full" if full else "reproducibility-smoke", reproduce_cmd, env=clean_env))

    return {
        "schema": "kry_release_verify/v1",
        "full": full,
        "ok": all(check["ok"] for check in checks),
        "root": str(ROOT),
        "temp_root": str(temp_root),
        "checks": checks,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the KRY release-candidate gate.")
    parser.add_argument("--full", action="store_true", help="run the 10-round reproducibility proof")
    parser.add_argument("--json-out", help="write the full machine-readable report to this path")
    args = parser.parse_args(argv)

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
