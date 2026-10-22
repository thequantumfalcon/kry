"""Regressions for optional signing boundaries, using real ephemeral ML-DSA keys."""
from __future__ import annotations

import argparse
import base64
import copy
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile

import pytest

_REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(_REPO), str(_REPO / "src")]
pytest.importorskip("oqs")
os.environ.setdefault("KRY_DATA_DIR", tempfile.mkdtemp(prefix="kry_pqc_review_"))
from kry import kry_attest, kry_mint  # noqa: E402
from kry_pqc import signer, threshold, verify  # noqa: E402


@pytest.fixture
def signed_case(tmp_path):
    kry_mint.mint("cache_hit", 1000, "constructed signing regression",
                  evidence="constructed-event", avoided_model="gh/claude-opus-4.8")
    att = tmp_path / "attestation.json"
    att.write_text(kry_attest.build_attestation().to_public_json())
    keys = [signer.generate_keypair() for _ in range(3)]
    policy = threshold.make_policy([(f"member{i}", key[0]) for i, key in enumerate(keys)], 2)
    contributions = [threshold.contribute(att, key[1], key[0], policy) for key in keys[:2]]
    artifact = threshold.combine(att, policy, contributions)
    single = signer.sign_attestation(att, keys[0][1], keys[0][0])
    # Secret keys never leave this fixture; only public artifacts become test arguments.
    return att, policy, artifact, single


@pytest.mark.parametrize("prefix_length", [0, 1, 15])
def test_short_fingerprint_does_not_establish_authority(signed_case, tmp_path, prefix_length):
    att, _, _, single = signed_case
    sig = tmp_path / "signature.json"
    sig.write_text(json.dumps(single))
    full = hashlib.sha256(base64.b64decode(single["public_key"])).hexdigest()
    assert verify.main(["--attestation", str(att), "--signature", str(sig),
                        "--expect-fingerprint", full[:prefix_length]]) == 1


@pytest.mark.parametrize("existing", ["pair", "public", "secret"])
def test_keygen_preserves_existing_files(tmp_path, existing):
    public = tmp_path / "kry_pqc_public.key"
    secret = tmp_path / "kry_pqc_secret.key"
    if existing in ("pair", "public"):
        public.write_bytes(b"existing-public-material")
    if existing in ("pair", "secret"):
        secret.write_bytes(b"existing-secret-placeholder")
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    with pytest.raises(FileExistsError):
        signer._cmd_keygen(argparse.Namespace(alg=signer.DEFAULT_ALG, out_dir=str(tmp_path)))
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir()} == before


@pytest.mark.parametrize("field,value", [("alg", "ML-DSA-999"), ("alg", None),
                                          ("council_size", 50), ("council_size", 3.0),
                                          ("threshold", 2.0), ("threshold", True)])
def test_threshold_envelope_matches_policy(signed_case, field, value):
    att, policy, artifact, _ = signed_case
    artifact[field] = value
    assert threshold.verify_threshold(att.read_bytes(), artifact, policy)[0] is False


@pytest.mark.parametrize("value", [0, -1, 4, True, 2.0, "2", None, [], {}, float("nan")])
def test_threshold_bad_type_or_range_rejected_cleanly(signed_case, value):
    att, policy, artifact, _ = signed_case
    policy["threshold"] = value
    artifact["threshold"] = value
    assert threshold.verify_threshold(att.read_bytes(), artifact, policy)[0] is False


@pytest.mark.parametrize("value", [0, -1, 4, True, 2.0, "2", None, [], {}, float("nan")])
def test_policy_constructor_rejects_invalid_threshold(signed_case, value):
    _, policy, _, _ = signed_case
    members = [(entry["name"], base64.b64decode(entry["public_key"])) for entry in policy["signers"]]
    with pytest.raises(ValueError):
        threshold.make_policy(members, value)


@pytest.mark.parametrize("value", [1, None, [], {}, "not-base64!", ""])
def test_threshold_malformed_public_key_fails_cleanly(signed_case, value):
    att, policy, artifact, _ = signed_case
    policy["signers"][0]["public_key"] = value
    assert threshold.verify_threshold(att.read_bytes(), artifact, policy)[0] is False


@pytest.mark.parametrize("value", [1, None, [], {}, "%%%"])
def test_single_malformed_signature_fails_cleanly(signed_case, tmp_path, value):
    att, _, _, single = signed_case
    single["signature"] = value
    sig = tmp_path / "signature.json"
    sig.write_text(json.dumps(single))
    assert verify.main(["--attestation", str(att), "--signature", str(sig)]) == 1


@pytest.mark.parametrize("field,delta", [("public_key", -1), ("public_key", 1),
                                       ("signature", -1), ("signature", 1)])
def test_native_verifier_rejects_wrong_lengths(signed_case, field, delta):
    att, _, _, single = signed_case
    pk, sig = base64.b64decode(single["public_key"]), base64.b64decode(single["signature"])
    material = pk if field == "public_key" else sig
    material = material[:-1] if delta == -1 else material + b"x"
    if field == "public_key":
        pk = material
    else:
        sig = material
    assert verify.verify_signature(signer.single_signed_message(att.read_bytes()), sig, pk,
                                   signer.DEFAULT_ALG) is False


@pytest.mark.parametrize("case", ["artifact-list", "signatures-none", "entry-int", "fingerprint-list"])
def test_threshold_malformed_envelope_fails_cleanly(signed_case, case):
    att, policy, artifact, _ = signed_case
    if case == "artifact-list":
        artifact = []
    elif case == "signatures-none":
        artifact["signatures"] = None
    elif case == "entry-int":
        artifact["signatures"] = [1]
    else:
        artifact["signatures"] = [{"fingerprint": [], "signature": "AA=="}]
    assert threshold.verify_threshold(att.read_bytes(), artifact, policy)[0] is False


@pytest.mark.parametrize("case", ["error-report", "bad-json", "missing-file", "algorithm-list"])
def test_threshold_cli_reports_input_failure(signed_case, tmp_path, capsys, case):
    att, policy, artifact, _ = signed_case
    policy_path, artifact_path = tmp_path / "policy.json", tmp_path / "artifact.json"
    artifact_path.write_text(json.dumps(artifact))
    if case == "error-report":
        policy = []
    elif case == "algorithm-list":
        policy["alg"] = []
    if case != "missing-file":
        policy_path.write_text("{" if case == "bad-json" else json.dumps(policy))
    assert threshold.main(["verify", "--attestation", str(att), "--policy", str(policy_path),
                           "--artifact", str(artifact_path)]) == 1
    assert "RESULT: FAILED" in capsys.readouterr().out


def test_threshold_output_describes_keys_not_independent_people(signed_case, tmp_path, capsys):
    att, policy, artifact, _ = signed_case
    policy_path, artifact_path = tmp_path / "policy.json", tmp_path / "artifact.json"
    policy_path.write_text(json.dumps(policy))
    artifact_path.write_text(json.dumps(artifact))
    assert threshold.main(["verify", "--attestation", str(att), "--policy", str(policy_path),
                           "--artifact", str(artifact_path)]) == 0
    output = capsys.readouterr().out
    assert "distinct registered keys" in output
    assert "independence are not established" in output
    assert "No single party could have produced this" not in output


def test_later_malformed_entry_cannot_erase_valid_quorum(signed_case):
    att, policy, artifact, _ = signed_case
    artifact["signatures"].extend([1, {"fingerprint": [], "signature": None}])
    ok, report = threshold.verify_threshold(att.read_bytes(), artifact, policy)
    assert ok and report["valid_count"] == 2


def test_policy_rejects_wrong_length_unused_council_key(signed_case):
    att, policy, artifact, _ = signed_case
    # Require the key-length check to fail independently of the changed policy's signatures.
    policy = copy.deepcopy(policy)
    policy["signers"][2]["public_key"] = base64.b64encode(b"x").decode()
    policy["signers"][2]["fingerprint"] = signer.fingerprint(b"x")
    artifact["policy_sha256"] = threshold.policy_digest(policy)
    ok, report = threshold.verify_threshold(att.read_bytes(), artifact, policy)
    assert not ok
    assert any("required length" in item["name"] and not item["pass"] for item in report["checks"])
