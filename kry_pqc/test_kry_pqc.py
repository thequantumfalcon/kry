"""Tests for the optional kry_pqc tier.

Generates a REAL KRY attestation (via kry core in a temp data dir), then exercises
single-signer authenticity and m-of-n threshold signing: roundtrip, tamper,
wrong-key, insufficient-quorum, outsider-signer, and wrong-council.

Run from the repo root:
    python -m pytest kry_pqc/test_kry_pqc.py -q
"""
from __future__ import annotations

import base64
import json
import os
import re
import sys
import tempfile
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(_REPO), str(_REPO / "src")]

oqs = pytest.importorskip("oqs")

# KRY core must be importable with a data dir set before its modules bind paths.
os.environ.setdefault("KRY_DATA_DIR", tempfile.mkdtemp(prefix="kry_pqc_test_"))
from kry import kry_attest, kry_mint  # noqa: E402
from kry_pqc import signer, threshold, verify  # noqa: E402


def _build_attestation_text() -> str:
    kry_mint.mint("cache_hit", 1000, "served from cache instead of Opus",
                  evidence="resp-sha-abc", avoided_model="gh/claude-opus-4.8")
    kry_mint.mint("compression", 800, "directive trimmed output",
                  evidence="cmp-001", avoided_model="or/anthropic/claude-opus-4.8")
    return kry_attest.build_attestation().to_public_json()


# Built once at import (kry binds its data dir at import); copied per test.
_ATT_TEXT = _build_attestation_text()


@pytest.fixture
def attestation(tmp_path) -> Path:
    p = tmp_path / "attestation.json"
    p.write_text(_ATT_TEXT)
    return p


# ----------------------------------------------------------------- single signer


def test_single_roundtrip_verifies_when_key_pinned(attestation, tmp_path):
    pk, sk = signer.generate_keypair()
    artifact = signer.sign_attestation(attestation, sk, pk)
    sig = tmp_path / "att.sig.json"
    sig.write_text(json.dumps(artifact))
    pk_file = tmp_path / "signer.pub"           # the signer's PUBLISHED key, obtained out-of-band
    pk_file.write_text(base64.b64encode(pk).decode())
    assert verify.main(["--attestation", str(attestation), "--signature", str(sig),
                        "--public-key", str(pk_file)]) == 0


def test_single_selfprovided_key_is_unverified(attestation, tmp_path):
    """Finding #2: a valid signature under the artifact's OWN embedded key is NOT authenticity."""
    pk, sk = signer.generate_keypair()
    sig = tmp_path / "att.sig.json"
    sig.write_text(json.dumps(signer.sign_attestation(attestation, sk, pk)))
    # no --public-key / --expect-fingerprint -> self-provided key -> UNVERIFIED (exit 2), not 0
    assert verify.main(["--attestation", str(attestation), "--signature", str(sig)]) == 2


def test_single_expect_fingerprint_pins(attestation, tmp_path):
    import hashlib
    pk, sk = signer.generate_keypair()
    sig = tmp_path / "att.sig.json"
    sig.write_text(json.dumps(signer.sign_attestation(attestation, sk, pk)))
    good_fp = hashlib.sha256(pk).hexdigest()[:16]
    assert verify.main(["--attestation", str(attestation), "--signature", str(sig),
                        "--expect-fingerprint", good_fp]) == 0
    assert verify.main(["--attestation", str(attestation), "--signature", str(sig),
                        "--expect-fingerprint", "deadbeefdeadbeef"]) == 1


def test_single_tampered_attestation_fails(attestation, tmp_path):
    pk, sk = signer.generate_keypair()
    sig = tmp_path / "att.sig.json"
    sig.write_text(json.dumps(signer.sign_attestation(attestation, sk, pk)))
    data = json.loads(attestation.read_text())
    data["total_kry"] = data["total_kry"] + 1.0          # inflate the balance post-signing
    attestation.write_text(json.dumps(data, indent=2))
    assert verify.main(["--attestation", str(attestation), "--signature", str(sig)]) == 1


def test_single_wrong_key_fails(attestation, tmp_path):
    pk, sk = signer.generate_keypair()
    artifact = signer.sign_attestation(attestation, sk, pk)
    other_pk, _ = signer.generate_keypair()
    artifact["public_key"] = base64.b64encode(other_pk).decode()  # claim a different signer
    sig = tmp_path / "att.sig.json"
    sig.write_text(json.dumps(artifact))
    assert verify.main(["--attestation", str(attestation), "--signature", str(sig)]) == 1


def test_verify_rejects_unsupported_alg(attestation, tmp_path):
    """M1: a bogus/unsupported `alg` in the artifact fails CLOSED (exit 1), not an uncaught
    MechanismNotSupportedError crash. The alg is allowlisted inside the parse guard, before
    it can reach oqs.Signature(alg)."""
    pk, sk = signer.generate_keypair()
    artifact = signer.sign_attestation(attestation, sk, pk)
    artifact["alg"] = "ML-DSA-9999"                       # not in the ML-DSA allowlist
    sig = tmp_path / "att.sig.json"
    sig.write_text(json.dumps(artifact))
    pk_file = tmp_path / "signer.pub"
    pk_file.write_text(base64.b64encode(pk).decode())
    assert verify.main(["--attestation", str(attestation), "--signature", str(sig),
                        "--public-key", str(pk_file)]) == 1


def test_keygen_writes_secret_key_owner_only(tmp_path):
    """The keygen CLI must write the private key owner-only (0o600), never group/world-readable."""
    import stat
    from argparse import Namespace
    assert signer._cmd_keygen(Namespace(alg=signer.DEFAULT_ALG, out_dir=str(tmp_path))) == 0
    sk = tmp_path / "kry_pqc_secret.key"
    assert sk.exists()
    if os.name == "posix":  # mode bits are POSIX-only
        assert stat.S_IMODE(sk.stat().st_mode) == 0o600


# ------------------------------------------------------------------- m-of-n council


def _council(n: int, threshold_m: int):
    keys = [signer.generate_keypair() for _ in range(n)]          # [(pk, sk), ...]
    policy = threshold.make_policy([(f"signer{i}", pk) for i, (pk, _) in enumerate(keys)],
                                   threshold_m)
    return keys, policy


def test_threshold_quorum_met_verifies(attestation):
    keys, policy = _council(3, 2)
    contribs = [threshold.contribute(attestation, sk, pk) for pk, sk in keys[:2]]
    artifact = threshold.combine(attestation, policy, contribs)
    ok, report = threshold.verify_threshold(attestation.read_bytes(), artifact, policy)
    assert ok and report["valid_count"] == 2 and report["trust_ratio"] == "2/3"


def test_threshold_insufficient_quorum_fails(attestation):
    keys, policy = _council(3, 2)
    contribs = [threshold.contribute(attestation, *reversed(keys[0]))]  # only 1 of 2 needed
    # reversed(keys[0]) -> (sk, pk); contribute(att, secret, public)
    artifact = threshold.combine(attestation, policy, contribs)
    ok, report = threshold.verify_threshold(attestation.read_bytes(), artifact, policy)
    assert not ok and report["valid_count"] == 1


def test_threshold_outsider_signature_ignored(attestation):
    keys, policy = _council(3, 2)
    outsider_pk, outsider_sk = signer.generate_keypair()             # not on the council
    contribs = [
        threshold.contribute(attestation, keys[0][1], keys[0][0]),
        threshold.contribute(attestation, outsider_sk, outsider_pk),
    ]
    artifact = threshold.combine(attestation, policy, contribs)
    ok, report = threshold.verify_threshold(attestation.read_bytes(), artifact, policy)
    assert not ok and report["valid_count"] == 1                     # outsider not counted


def test_threshold_tampered_attestation_fails(attestation):
    keys, policy = _council(3, 2)
    contribs = [threshold.contribute(attestation, sk, pk) for pk, sk in keys[:2]]
    artifact = threshold.combine(attestation, policy, contribs)
    # Tamper a field guaranteed present in any attestation (not the receipts count,
    # which varies with ledger state) and assert the tamper actually changed bytes.
    tampered = re.sub(r'("total_kry":\s*)[0-9.]+', r"\g<1>999999.0",
                      attestation.read_text(), count=1).encode()
    assert tampered != attestation.read_bytes()  # guard: tamper must not silently no-op
    ok, _ = threshold.verify_threshold(tampered, artifact, policy)
    assert not ok


def test_threshold_wrong_council_rejected(attestation):
    keys, policy = _council(3, 2)
    contribs = [threshold.contribute(attestation, sk, pk) for pk, sk in keys[:2]]
    artifact = threshold.combine(attestation, policy, contribs)
    _, other_policy = _council(3, 2)                                 # different keys entirely
    ok, _ = threshold.verify_threshold(attestation.read_bytes(), artifact, other_policy)
    assert not ok                                                    # policy digest mismatch