#!/usr/bin/env python3
"""Run the configured optional crypto tests with native-library and zero-skip checks."""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import importlib
import importlib.metadata
import json
import os
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
NATIVE_VERSION = "0.16.0"
ALGORITHMS = {"ML-DSA-44", "ML-DSA-65", "ML-DSA-87"}
# In the pinned wrapper this invalid version is rejected before its fallback clone.
NO_AUTO_INSTALL = "disabled-by-kry-ci"
# Reviewed existing suite size; growth is allowed, reductions require a collection review.
MINIMUM_CASES = 126
REQUIRED_MODULES = {
    "tests/test_tee_verify.py", "tests/test_snp_verify.py", "tests/test_audit_lows.py",
    "kry_pqc/test_kry_pqc.py", "kry_pqc/test_pqc_review_regressions.py",
}


def check_native(library: Path) -> dict:
    expected = library.resolve(strict=True)  # Fail before importing the auto-installing wrapper.
    prefix = Path(os.environ["OQS_INSTALL_PATH"]).resolve()
    if expected.parent != prefix / "lib":
        raise ValueError("native library must be inside OQS_INSTALL_PATH/lib")
    ctypes.CDLL(str(expected))  # A present but unloadable library also fails before wrapper import.
    os.environ["PYOQS_VERSION"] = NO_AUTO_INSTALL
    oqs = importlib.import_module("oqs")
    loaded = Path(oqs.native()._name).resolve()
    if loaded != expected:
        raise ValueError("wrapper loaded a different native library")
    if oqs.oqs_version() != NATIVE_VERSION:
        raise ValueError("unexpected native liboqs version")
    if set(oqs.get_enabled_sig_mechanisms()) != ALGORITHMS:
        raise ValueError("native build must enable exactly ML-DSA-44/65/87")
    message = b"KRY optional crypto CI smoke"
    for algorithm in sorted(ALGORITHMS):
        with oqs.Signature(algorithm) as signer:
            public_key = signer.generate_keypair()
            signature = signer.sign(message)
        with oqs.Signature(algorithm) as verifier:
            if not verifier.verify(message, signature, public_key):
                raise ValueError(f"{algorithm}: real signature failed verification")
            if verifier.verify(message + b"tampered", signature, public_key):
                raise ValueError(f"{algorithm}: altered message accepted")
    importlib.import_module("cryptography")
    return {
        "liboqs": oqs.oqs_version(),
        "liboqs_python": importlib.metadata.version("liboqs-python"),
        "cryptography": importlib.metadata.version("cryptography"),
        "native_sha256": hashlib.sha256(expected.read_bytes()).hexdigest(),
        "algorithms": sorted(ALGORITHMS),
    }


def check_junit(path: Path) -> int:
    report = ET.parse(path).getroot()
    cases = list(report.iter("testcase"))
    if not cases:
        raise ValueError("optional crypto report contains no test cases")
    if any(case.find(tag) is not None for case in cases for tag in ("skipped", "failure", "error")):
        raise ValueError("optional crypto report contains skipped or unsuccessful cases")
    modules = {case.get("file", "").replace("\\", "/") for case in cases}
    if not REQUIRED_MODULES <= modules:
        raise ValueError("optional crypto report omitted a required test module")
    if len(cases) < MINIMUM_CASES:
        raise ValueError("optional crypto report collected fewer cases than the reviewed minimum")
    return len(cases)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--native-library", required=True, type=Path)
    parser.add_argument("--junit", required=True, type=Path)
    args = parser.parse_args(argv)
    print(json.dumps(check_native(args.native_library), sort_keys=True), flush=True)
    result = subprocess.run([
        sys.executable, "-m", "pytest", "-q", "-rs", "-p", "no:cacheprovider",
        "-o", "junit_family=xunit1", "--junitxml", str(args.junit.resolve()),
        "tests/test_tee_verify.py", "tests/test_snp_verify.py", "tests/test_audit_lows.py",
        "kry_pqc/",
    ], cwd=ROOT)
    if result.returncode:
        return result.returncode
    count = check_junit(args.junit)
    print(f"Optional crypto: {count} passed, zero skipped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
