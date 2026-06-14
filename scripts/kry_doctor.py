#!/usr/bin/env python3
"""Local health checks for the KRY stranger-verification surface.

The doctor does not create evidence and does not certify external savings. It only
checks that the local repo is in a shape where a reviewer can run the public verifier
and verified-savings packet workflow without path/config surprises.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "kry_doctor/v1"
PASS = "PASS"
WARN = "WARN"
FAIL = "FAIL"

REQUIRED_FILES = (
    "pyproject.toml",
    "README.md",
    "scripts/kry_verify.py",
    "scripts/kry_verified_artifact.py",
    "scripts/kry_savings_report.py",
    "scripts/kry_reconcile.py",
    "scripts/kry_finops_report.py",
    "docs/KRY_VERIFIED_SAVINGS_ARTIFACT.md",
    "examples/sample_usage_log.jsonl",
)

PUBLIC_DOCS = (
    "README.md",
    "docs/KRY_READINESS.md",
    "docs/KRY_VERIFIED_SAVINGS_ARTIFACT.md",
)

STALE_PUBLIC_PATTERNS = (
    "PYTHONPATH=src",
    "python scripts/kry_",
    "127 tests",
    "172 stdlib",
    "215 tests",
    "133 passed",
)

REMAINING_EXTERNAL_REQUIREMENTS = (
    "real provider export",
    "provider export provenance manifest",
    "real corpus manifest",
    "outside artifact verification",
    "buyer feedback",
    "legal/claims review",
)
PACKET_SURFACE_FILES = (
    "reviewer_checklist.json",
    "finops_report.md",
)
ARTIFACT_PATH_INPUTS = (
    "usage_log",
    "attestation",
    "mint_log",
    "t1_manifest",
    "provider_export",
    "provider_export_manifest",
    "corpus_manifest",
    "outside_review",
    "buyer_feedback",
    "legal_review",
)
PRIVATE_PACKET_NAME_FRAGMENTS = (
    "mint_log",
    "kry_mint_log",
)
PRIVATE_PACKET_NAMES = (
    "kry_data",
    "mint.jsonl",
    "ledger.json",
    "decay.json",
)


def _check(name: str, status: str, detail: str) -> dict:
    return {"name": name, "status": status, "detail": detail}


def _read(root: Path, rel: str) -> str:
    return (root / rel).read_text(encoding="utf-8")


def _reject_json_constant(value: str):
    raise ValueError(f"non-standard JSON constant rejected: {value}")


def _json_loads(text: str):
    return json.loads(text, parse_constant=_reject_json_constant)


def _json_dumps(data: object, **kwargs) -> str:
    kwargs.setdefault("allow_nan", False)
    return json.dumps(data, **kwargs)


def _json_pretty(data: object) -> str:
    return _json_dumps(data, indent=2, sort_keys=True)


def _required_files(root: Path) -> dict:
    missing = [rel for rel in REQUIRED_FILES if not (root / rel).exists()]
    if missing:
        return _check("required_files", FAIL, "missing: " + ", ".join(missing))
    return _check("required_files", PASS, f"{len(REQUIRED_FILES)} required files present")


def _python_version() -> dict:
    version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    if sys.version_info < (3, 11):
        return _check("python_version", FAIL, f"Python {version}; requires >= 3.11")
    return _check("python_version", PASS, f"Python {version}")


def _pytest_config(root: Path) -> dict:
    try:
        data = tomllib.loads(_read(root, "pyproject.toml"))
    except Exception as exc:
        return _check("pytest_pythonpath", FAIL, f"pyproject.toml unreadable: {exc}")
    opts = ((data.get("tool") or {}).get("pytest") or {}).get("ini_options") or {}
    if opts.get("pythonpath") != ["src"]:
        return _check("pytest_pythonpath", FAIL, "pyproject must set [tool.pytest.ini_options] pythonpath = ['src']")
    return _check("pytest_pythonpath", PASS, "pytest can import src without PYTHONPATH=src")


def _module_available(module: str, *, required: bool) -> dict:
    if importlib.util.find_spec(module) is not None:
        return _check(f"module:{module}", PASS, f"{module} importable")
    status = FAIL if required else WARN
    kind = "required" if required else "optional"
    return _check(f"module:{module}", status, f"{kind} module {module} not importable")


def _runtime_data_ignored(root: Path) -> dict:
    try:
        ignored = _read(root, ".gitignore").splitlines()
    except FileNotFoundError:
        return _check("runtime_data_ignored", FAIL, ".gitignore missing")
    if "kry_data/" not in {line.strip() for line in ignored}:
        return _check("runtime_data_ignored", FAIL, "kry_data/ must be gitignored")
    return _check("runtime_data_ignored", PASS, "kry_data/ is gitignored")


def _stdlib_verifier_independent(root: Path) -> dict:
    text = _read(root, "scripts/kry_verify.py")
    bad_imports = []
    for i, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if re.match(r"^(from\s+kry\b|import\s+kry\b)", stripped):
            bad_imports.append(str(i))
    if bad_imports:
        return _check("stdlib_verifier_independent", FAIL, "kry imports at lines " + ", ".join(bad_imports))
    return _check("stdlib_verifier_independent", PASS, "scripts/kry_verify.py imports no kry package code")


def _public_docs_current(root: Path) -> dict:
    hits = []
    for rel in PUBLIC_DOCS:
        text = _read(root, rel)
        for pattern in STALE_PUBLIC_PATTERNS:
            if pattern in text:
                hits.append(f"{rel}:{pattern}")
    if hits:
        return _check("public_docs_current", FAIL, "stale public wording: " + ", ".join(hits))
    return _check("public_docs_current", PASS, "no stale public command/test-count patterns found")


def _verified_artifact_surface(root: Path) -> dict:
    text = _read(root, "scripts/kry_verified_artifact.py")
    required = (
        "kry_verified_savings_artifact/v1",
        "kry_claim_register/v1",
        "kry_claim_evidence_manifest/v1",
        "kry_reviewer_checklist/v1",
        "kry_validation_plan/v1",
    )
    missing = [marker for marker in required if marker not in text]
    if missing:
        return _check("verified_artifact_surface", FAIL, "missing markers: " + ", ".join(missing))
    return _check("verified_artifact_surface", PASS, "verified artifact schemas present")


def _sample_log_disclosure(root: Path) -> dict:
    readme = _read(root, "README.md")
    doc = _read(root, "docs/KRY_VERIFIED_SAVINGS_ARTIFACT.md")
    if "examples/sample_usage_log.jsonl is synthetic" not in readme:
        return _check("sample_log_disclosure", FAIL, "README must label sample log synthetic")
    if "sample log is synthetic" not in doc and "The sample log is synthetic" not in doc:
        return _check("sample_log_disclosure", FAIL, "artifact doc must label demo packet synthetic")
    return _check("sample_log_disclosure", PASS, "sample/demo data is labeled synthetic")


def _resolve_artifact_path(root: Path, artifact: str | None) -> Path | None:
    if not artifact:
        return None
    path = Path(artifact)
    if not path.is_absolute():
        path = root / path
    return path


def _artifact_verification(root: Path, artifact: str | None) -> dict | None:
    path = _resolve_artifact_path(root, artifact)
    if path is None:
        return None
    if not path.exists():
        return _check("artifact_verification", FAIL, f"artifact not found: {artifact}")
    spec = importlib.util.spec_from_file_location("kry_verified_artifact_for_doctor", root / "scripts" / "kry_verified_artifact.py")
    if spec is None or spec.loader is None:
        return _check("artifact_verification", FAIL, "cannot load scripts/kry_verified_artifact.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    result = mod.verify_artifact_file(str(path))
    if result.get("ok"):
        return _check("artifact_verification", PASS, f"{artifact} verifies; ship_scope={result.get('ship_scope')}")
    return _check("artifact_verification", FAIL, "; ".join(result.get("errors") or ["verification failed"]))


def _packet_report_current(root: Path, artifact: str | None) -> dict | None:
    path = _resolve_artifact_path(root, artifact)
    if path is None:
        return None
    if not path.exists():
        return None
    report_path = path.parent / "finops_report.md"
    if not report_path.exists():
        if _is_packet_like(path):
            return _check(
                "packet_report_current",
                FAIL,
                "finops_report.md missing from packet; bundle mode should generate it",
            )
        return _check(
            "packet_report_current",
            WARN,
            "finops_report.md missing beside artifact; bundle mode should generate it",
        )

    spec = importlib.util.spec_from_file_location("kry_finops_report_for_doctor", root / "scripts" / "kry_finops_report.py")
    if spec is None or spec.loader is None:
        return _check("packet_report_current", FAIL, "cannot load scripts/kry_finops_report.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    report = mod.build_report(
        path,
        display_artifact_path=path.name,
        require_packet_surfaces=False,
    )
    if not report.get("ok"):
        return _check("packet_report_current", FAIL, "cannot render report from artifact: " + "; ".join(report.get("errors") or []))
    expected = mod.render_markdown(report)
    if not expected.endswith("\n"):
        expected += "\n"
    actual = report_path.read_text(encoding="utf-8")
    if actual != expected:
        return _check("packet_report_current", FAIL, "finops_report.md does not match a fresh render from artifact.json")
    return _check("packet_report_current", PASS, "finops_report.md matches artifact.json")


def _packet_checklist_current(root: Path, artifact: str | None) -> dict | None:
    path = _resolve_artifact_path(root, artifact)
    if path is None:
        return None
    if not path.exists():
        return None
    checklist_path = path.parent / "reviewer_checklist.json"
    if not checklist_path.exists():
        if _is_packet_like(path):
            return _check(
                "packet_checklist_current",
                FAIL,
                "reviewer_checklist.json missing from packet; bundle mode should generate it",
            )
        return _check(
            "packet_checklist_current",
            WARN,
            "reviewer_checklist.json missing beside artifact; bundle mode should generate it",
        )

    try:
        artifact_data = _json_loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return _check("packet_checklist_current", FAIL, f"cannot read artifact.json: {exc}")
    if not isinstance(artifact_data, dict):
        return _check("packet_checklist_current", FAIL, "artifact JSON is not an object")
    try:
        actual = _json_loads(checklist_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return _check("packet_checklist_current", FAIL, f"cannot read reviewer_checklist.json: {exc}")

    spec = importlib.util.spec_from_file_location("kry_verified_artifact_for_doctor_checklist", root / "scripts" / "kry_verified_artifact.py")
    if spec is None or spec.loader is None:
        return _check("packet_checklist_current", FAIL, "cannot load scripts/kry_verified_artifact.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    review_basis = artifact_data.get("review_basis") or {}
    base_inputs = dict(review_basis.get("inputs") or {})
    base_inputs["review_basis_sha256"] = review_basis.get("sha256")
    expected = mod._reviewer_checklist(
        base_inputs,
        artifact_path=path.name,
        artifact_hash=artifact_data.get("artifact_hash"),
    )
    if actual != expected:
        return _check("packet_checklist_current", FAIL, "reviewer_checklist.json does not match artifact.json")
    return _check("packet_checklist_current", PASS, "reviewer_checklist.json matches artifact.json")


def _load_artifact_json(path: Path) -> dict | None:
    try:
        data = _json_loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _is_packet_like(path: Path) -> bool:
    if any((path.parent / name).exists() for name in PACKET_SURFACE_FILES):
        return True
    data = _load_artifact_json(path)
    if not data:
        return False
    if data.get("ship_scope") == "external_verified_savings_candidate":
        return True
    claim_allowed = data.get("claim_allowed")
    if isinstance(claim_allowed, dict) and claim_allowed.get("external_verified_savings") is True:
        return True
    claim_register = data.get("claim_register")
    claims = claim_register.get("claims") if isinstance(claim_register, dict) else []
    if any(
        isinstance(claim, dict)
        and claim.get("id") == "external_verified_savings"
        and claim.get("status") == "allowed"
        for claim in claims
    ):
        return True
    command_inputs = data.get("command_inputs")
    if not isinstance(command_inputs, dict):
        return False
    return any(
        isinstance(value, str) and value and not Path(value).is_absolute()
        for key, value in command_inputs.items()
        if key in ARTIFACT_PATH_INPUTS
    )


def _artifact_ship_scope_status(root: Path, artifact: str | None, *, artifact_verified: bool) -> dict | None:
    path = _resolve_artifact_path(root, artifact)
    if path is None or not path.exists() or not artifact_verified:
        return None
    data = _load_artifact_json(path)
    if not data:
        return _check("artifact_ship_scope_status", FAIL, "artifact JSON is unreadable")

    scope = data.get("ship_scope")
    if scope == "external_verified_savings_candidate":
        return _check(
            "artifact_ship_scope_status",
            PASS,
            "artifact ship_scope=external_verified_savings_candidate",
        )
    if scope == "internal_or_demo_only":
        return _check(
            "artifact_ship_scope_status",
            WARN,
            "artifact ship_scope=internal_or_demo_only; use only as demo/internal evidence",
        )
    if scope == "do_not_ship":
        return _check(
            "artifact_ship_scope_status",
            FAIL,
            "artifact ship_scope=do_not_ship; do not hand off as a verified-savings packet",
        )
    return _check("artifact_ship_scope_status", FAIL, f"artifact has unknown ship_scope={scope!r}")


def _packet_privacy_boundary(root: Path, artifact: str | None) -> dict | None:
    path = _resolve_artifact_path(root, artifact)
    if path is None or not path.exists() or not _is_packet_like(path):
        return None

    data = _load_artifact_json(path)
    expected_files = {path.name, *PACKET_SURFACE_FILES}
    command_inputs = data.get("command_inputs") if isinstance(data, dict) else None
    packet_dir = path.parent.resolve()
    if isinstance(command_inputs, dict):
        for key in ARTIFACT_PATH_INPUTS:
            value = command_inputs.get(key)
            if key == "mint_log" or not isinstance(value, str) or not value:
                continue
            input_path = Path(value)
            if input_path.is_absolute():
                continue
            resolved = (packet_dir / input_path).resolve()
            try:
                expected_files.add(resolved.relative_to(packet_dir).as_posix())
            except ValueError:
                continue
    expected_dirs = {
        parent.as_posix()
        for rel in expected_files
        for parent in Path(rel).parents
        if parent.as_posix() not in (".", "")
    }
    packet_errors: list[str] = []
    for candidate in path.parent.rglob("*"):
        rel = candidate.relative_to(path.parent).as_posix()
        name = candidate.name.lower()
        if candidate.is_symlink():
            packet_errors.append(f"symlink present in packet: {rel}")
            continue
        if name in PRIVATE_PACKET_NAMES or any(fragment in name for fragment in PRIVATE_PACKET_NAME_FRAGMENTS):
            packet_errors.append(f"private ledger/mint-log material present in packet: {rel}")
        if candidate.is_dir():
            if rel not in expected_dirs:
                packet_errors.append(f"unbound directory present in packet: {rel}")
            continue
        if not candidate.is_file():
            packet_errors.append(f"non-regular entry present in packet: {rel}")
            continue
        if rel not in expected_files:
            packet_errors.append(f"unbound file present in packet: {rel}")
    if packet_errors:
        return _check(
            "packet_privacy_boundary",
            FAIL,
            "; ".join(sorted(packet_errors)),
        )

    content_errors: list[str] = []
    if isinstance(command_inputs, dict):
        usage_log = command_inputs.get("usage_log")
        attestation = command_inputs.get("attestation")
        t1_manifest = command_inputs.get("t1_manifest")
        provider_export = command_inputs.get("provider_export")
        provider_export_manifest = command_inputs.get("provider_export_manifest")
        corpus_manifest = command_inputs.get("corpus_manifest")
        outside_review = command_inputs.get("outside_review")
        buyer_feedback = command_inputs.get("buyer_feedback")
        legal_review = command_inputs.get("legal_review")

        def declared_path(value) -> str | None:
            if not isinstance(value, str) or not value:
                return None
            input_path = Path(value)
            if input_path.is_absolute():
                return str(input_path)
            return str(path.parent / input_path)

        usage_path = declared_path(usage_log)
        attestation_path = declared_path(attestation)
        t1_path = declared_path(t1_manifest)
        provider_path = declared_path(provider_export)
        provider_manifest_path = declared_path(provider_export_manifest)
        corpus_manifest_path = declared_path(corpus_manifest)
        outside_path = declared_path(outside_review)
        buyer_path = declared_path(buyer_feedback)
        legal_path = declared_path(legal_review)
        if usage_path:
            spec = importlib.util.spec_from_file_location(
                "kry_verified_artifact_for_doctor_privacy_scan",
                root / "scripts" / "kry_verified_artifact.py",
            )
            if spec is None or spec.loader is None:
                content_errors.append("cannot load scripts/kry_verified_artifact.py for packet privacy scan")
            else:
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                content_errors.extend(
                    mod._bundle_input_privacy_errors(
                        usage_path,
                        provider_path,
                        attestation=attestation_path,
                        t1_manifest=t1_path,
                        provider_export_manifest=provider_manifest_path,
                        corpus_manifest=corpus_manifest_path,
                        outside_review=outside_path,
                        buyer_feedback=buyer_path,
                        legal_review=legal_path,
                    )
                )
    if content_errors:
        return _check(
            "packet_privacy_boundary",
            FAIL,
            "private prompt/message/raw-body material present in packet inputs: "
            + "; ".join(content_errors),
        )
    return _check(
        "packet_privacy_boundary",
        PASS,
        "no private mint log, ledger, symlink, or raw prompt/message/body material found in packet",
    )


def _packet_input_portability(root: Path, artifact: str | None) -> dict | None:
    path = _resolve_artifact_path(root, artifact)
    if path is None or not path.exists() or not _is_packet_like(path):
        return None
    data = _load_artifact_json(path)
    if not data:
        return _check("packet_input_portability", FAIL, "artifact JSON is unreadable")
    command_inputs = data.get("command_inputs")
    if not isinstance(command_inputs, dict):
        return _check("packet_input_portability", FAIL, "command_inputs missing")

    errors: list[str] = []
    packet_dir = path.parent.resolve()
    for key in ARTIFACT_PATH_INPUTS:
        value = command_inputs.get(key)
        if value is None:
            continue
        if not isinstance(value, str) or not value:
            errors.append(f"{key} is not a path string")
            continue
        input_path = Path(value)
        if input_path.is_absolute():
            errors.append(f"{key} is absolute: {value}")
            continue
        resolved = (packet_dir / input_path).resolve()
        try:
            resolved.relative_to(packet_dir)
        except ValueError:
            errors.append(f"{key} escapes packet: {value}")
    if errors:
        return _check("packet_input_portability", FAIL, "; ".join(errors))
    return _check("packet_input_portability", PASS, "packet command_inputs are relative and stay inside packet")


def _claims_by_id(artifact_data: dict) -> dict:
    register = artifact_data.get("claim_register") or {}
    claims = register.get("claims") if isinstance(register, dict) else []
    return {
        claim.get("id"): claim
        for claim in claims
        if isinstance(claim, dict)
    }


def _external_evidence_status(root: Path, artifact: str | None, *, artifact_verified: bool) -> dict:
    if not artifact:
        return _check(
            "external_evidence_status",
            WARN,
            "no artifact supplied; required external evidence not inspected: "
            + ", ".join(REMAINING_EXTERNAL_REQUIREMENTS),
        )
    path = _resolve_artifact_path(root, artifact)
    if path is None or not path.exists():
        return _check("external_evidence_status", WARN, "artifact unavailable; external evidence not inspected")
    if not artifact_verified:
        return _check("external_evidence_status", WARN, "artifact did not verify; external evidence status not trusted")
    try:
        artifact_data = _json_loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return _check("external_evidence_status", WARN, f"artifact unreadable; external evidence not inspected: {exc}")
    if not isinstance(artifact_data, dict):
        return _check("external_evidence_status", WARN, "artifact JSON is not an object; external evidence not inspected")

    claims = _claims_by_id(artifact_data)
    external_claim = claims.get("external_verified_savings") or {}
    if (
        artifact_data.get("ship_scope") == "external_verified_savings_candidate"
        and external_claim.get("status") == "allowed"
    ):
        return _check(
            "external_evidence_status",
            PASS,
            "verified artifact claim_register allows external_verified_savings; "
            "doctor checks local bindings/report freshness but does not certify upstream evidence truth",
        )

    blockers = external_claim.get("blockers") or artifact_data.get("external_blockers") or []
    if blockers:
        detail = ", ".join(str(blocker) for blocker in blockers[:8])
        if len(blockers) > 8:
            detail += f", ... +{len(blockers) - 8} more"
    else:
        detail = "claim_register does not allow external_verified_savings"
    return _check("external_evidence_status", WARN, "artifact is not externally claimable; blockers: " + detail)


def run_checks(root: str | Path = ROOT, *, artifact: str | None = None) -> dict:
    root_path = Path(root)
    checks = [
        _python_version(),
        _required_files(root_path),
        _pytest_config(root_path),
        _module_available("pytest", required=False),
        _module_available("cryptography", required=False),
        _module_available("oqs", required=False),
        _runtime_data_ignored(root_path),
        _stdlib_verifier_independent(root_path),
        _public_docs_current(root_path),
        _verified_artifact_surface(root_path),
        _sample_log_disclosure(root_path),
    ]
    artifact_check = _artifact_verification(root_path, artifact)
    if artifact_check is not None:
        checks.append(artifact_check)
    ship_scope_check = _artifact_ship_scope_status(
        root_path,
        artifact,
        artifact_verified=artifact_check is not None and artifact_check["status"] == PASS,
    )
    if ship_scope_check is not None:
        checks.append(ship_scope_check)
    packet_report_check = _packet_report_current(root_path, artifact)
    if packet_report_check is not None:
        checks.append(packet_report_check)
    packet_checklist_check = _packet_checklist_current(root_path, artifact)
    if packet_checklist_check is not None:
        checks.append(packet_checklist_check)
    packet_privacy_check = _packet_privacy_boundary(root_path, artifact)
    if packet_privacy_check is not None:
        checks.append(packet_privacy_check)
    packet_portability_check = _packet_input_portability(root_path, artifact)
    if packet_portability_check is not None:
        checks.append(packet_portability_check)
    checks.append(_external_evidence_status(
        root_path,
        artifact,
        artifact_verified=artifact_check is not None and artifact_check["status"] == PASS,
    ))
    summary = {
        "pass": sum(1 for c in checks if c["status"] == PASS),
        "warn": sum(1 for c in checks if c["status"] == WARN),
        "fail": sum(1 for c in checks if c["status"] == FAIL),
    }
    return {
        "schema": SCHEMA,
        "root": str(root_path),
        "artifact": artifact,
        "summary": summary,
        "checks": checks,
    }


def _print_text(result: dict) -> None:
    print(f"KRY doctor ({result['schema']})")
    print(f"root: {result['root']}")
    for check in result["checks"]:
        print(f"[{check['status']}] {check['name']}: {check['detail']}")
    s = result["summary"]
    print(f"summary: {s['pass']} pass, {s['warn']} warn, {s['fail']} fail")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Check local KRY verifier/reviewer readiness")
    p.add_argument("--artifact", default=None, help="optional packet/artifact.json to verify")
    p.add_argument("--json", action="store_true", help="emit machine-readable kry_doctor/v1 JSON")
    args = p.parse_args(argv)
    result = run_checks(artifact=args.artifact)
    if args.json:
        print(_json_pretty(result))
    else:
        _print_text(result)
    return 1 if result["summary"]["fail"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
