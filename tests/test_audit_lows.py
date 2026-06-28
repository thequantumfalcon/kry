"""Regressions for the Low-severity audit hardening (2026-06-28).

Each pins fail-CLEAN behaviour on attacker-supplied input to a stranger verifier — no RecursionError
or KeyError leaking out: EXT-2 (CBOR depth bound), F2 (privacy-scan depth bound), PQC-1/2 (threshold
policy validation + alg allowlist).
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]


def _load(name: str, rel: str):
    path = _ROOT / rel
    parent = str(path.parent)          # so the module's sibling imports (e.g. kry_artifact_io) resolve
    if parent not in sys.path:
        sys.path.insert(0, parent)
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_ext2_cbor_decoder_bounds_recursion():
    """EXT-2: a deeply-nested CBOR document fails with a clean _CBORError, not a RecursionError that
    escapes the AWS-Nitro attestation parser."""
    ktv = _load("kry_tee_verify_lows", "scripts/kry_tee_verify.py")
    deep = b"\x81" * 200 + b"\x00"   # array(1) nested 200 deep > _CBOR_MAX_DEPTH
    with pytest.raises(ktv._CBORError):
        ktv._cbor_decode(deep)


def test_f2_privacy_gate_bounds_recursion():
    """F2: the privacy scanner returns a clean error on a deeply-nested dict instead of RecursionError."""
    kap = _load("kry_artifact_privacy_lows", "scripts/kry_artifact_privacy.py")
    d: dict = {}
    cur = d
    for _ in range(200):
        cur["x"] = {}
        cur = cur["x"]
    errs = kap._private_key_errors(d, allowed_token_keys=set(), source_label="t")
    assert any("deeply" in e for e in errs), errs


def test_pqc_threshold_rejects_bad_alg_and_malformed_policy():
    """PQC-1: a non-allowlisted alg is refused before oqs.Signature is ever constructed.
    PQC-2: a malformed / non-dict policy fails clean (no uncaught KeyError)."""
    pytest.importorskip("oqs")
    if str(_ROOT) not in sys.path:
        sys.path.insert(0, str(_ROOT))
    import kry_pqc.threshold as kt
    ok, rep = kt.verify_threshold(b"x", {"scheme": kt.ARTIFACT_SCHEME},
                                  {"alg": "BAD-ALG", "threshold": 1,
                                   "signers": [{"public_key": "AA", "fingerprint": "f"}]})
    assert ok is False and "alg" in rep.get("error", "")
    ok2, rep2 = kt.verify_threshold(b"x", {}, {"threshold": 1})        # missing alg / signers
    assert ok2 is False and "error" in rep2
    ok3, _ = kt.verify_threshold(b"x", {}, "not-a-dict")               # non-dict policy
    assert ok3 is False
