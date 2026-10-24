"""Optional verifier inputs fail cleanly without relaxing genuine v1 compatibility."""
import base64
import hashlib
import json

import pytest

pytest.importorskip("oqs")
from kry_pqc import signer, threshold, verify


@pytest.mark.parametrize("prefix", [b"kry-pqc/v2/single\x00", b"kry-pqc/v2/threshold\x00"])
def test_v1_cannot_relabel_reserved_v2_messages(tmp_path, monkeypatch, prefix):
    # Standalone operation is supported; no core chain checker is available in this control.
    monkeypatch.setattr(verify, "_chain_check", lambda text: (False, False, []))
    public, secret = signer.generate_keypair()
    message = prefix + b"synthetic message"
    attestation = tmp_path / "message"
    attestation.write_bytes(message)
    artifact = {"scheme": "kry-pqc/v1", "alg": signer.DEFAULT_ALG,
                "message_sha256": hashlib.sha256(message).hexdigest(),
                "signature": base64.b64encode(signer.sign_bytes(message, secret)).decode(),
                "public_key": base64.b64encode(public).decode()}
    signature = tmp_path / "signature"
    signature.write_text(json.dumps(artifact))
    assert verify.main(["--attestation", str(attestation), "--signature", str(signature),
                        "--expect-fingerprint", hashlib.sha256(public).hexdigest()]) == 1


@pytest.mark.parametrize("kind", ["single", "threshold"])
@pytest.mark.parametrize("oversized", [False, True])
def test_signature_json_boundaries(tmp_path, capsys, kind, oversized):
    attestation = tmp_path / "attestation"
    attestation.write_text("{}")
    bad = tmp_path / "bad"
    bad.write_bytes(b" " * (16 * 1024 * 1024 + 1) if oversized
                    else b"[" * 2000 + b"0" + b"]" * 2000)
    if kind == "single":
        result = verify.main(["--attestation", str(attestation), "--signature", str(bad)])
    else:
        result = threshold.main(["verify", "--attestation", str(attestation),
                                 "--artifact", str(bad), "--policy", str(bad)])
    assert result == 1
    output = capsys.readouterr().out
    assert "malformed" in output
    if oversized:
        assert "16 MiB" in output


@pytest.mark.parametrize("depth", [64, 65])
@pytest.mark.parametrize("shape", ["array", "object"])
def test_json_reader_has_a_fixed_container_depth_limit(tmp_path, depth, shape):
    opening, closing = ("[", "]") if shape == "array" else ('{"child":', "}")
    path = tmp_path / "nested.json"
    path.write_text(opening * depth + "0" + closing * depth)
    if depth == 64:
        assert isinstance(verify._read_json(path), list if shape == "array" else dict)
    else:
        with pytest.raises(ValueError, match=r"JSON nesting limit exceeded \(64\)"):
            verify._read_json(path)


def test_json_reader_ignores_brackets_and_escaped_quotes_in_strings(tmp_path):
    value = '[{"quoted": "\\\\\\\""}]' * 100
    path = tmp_path / "strings.json"
    path.write_text(json.dumps({"value": value}))
    assert verify._read_json(path) == {"value": value}


@pytest.mark.parametrize("depth", [64, 65])
@pytest.mark.parametrize("scheme", ["kry-pqc/v1", "kry-pqc/v2"])
def test_single_signature_depth_limit_preserves_supported_schemes(
        tmp_path, monkeypatch, capsys, depth, scheme):
    monkeypatch.setattr(verify, "_chain_check", lambda text: (False, False, []))
    public, secret = signer.generate_keypair()
    attestation = tmp_path / "attestation"
    attestation.write_bytes(b"synthetic signed bytes")
    artifact = signer.sign_attestation(attestation, secret, public)
    if scheme == "kry-pqc/v1":
        artifact["scheme"] = scheme
        artifact["signature"] = base64.b64encode(
            signer.sign_bytes(attestation.read_bytes(), secret)).decode()
    nested = 0
    for _ in range(depth - 1):
        nested = [nested]
    artifact["extra_metadata"] = nested
    signature = tmp_path / "signature.json"
    signature.write_text(json.dumps(artifact))
    result = verify.main(["--attestation", str(attestation), "--signature", str(signature),
                          "--expect-fingerprint", hashlib.sha256(public).hexdigest()])
    assert result == (0 if depth == 64 else 1)
    if depth == 65:
        assert "malformed signature artifact: JSON nesting limit exceeded (64)" in capsys.readouterr().out


@pytest.mark.parametrize("depth", [64, 65])
@pytest.mark.parametrize("document", ["policy", "artifact"])
def test_threshold_json_depth_limit_before_policy_or_signature_checks(
        tmp_path, capsys, depth, document):
    public, secret = signer.generate_keypair()
    attestation = tmp_path / "attestation"
    attestation.write_bytes(b"synthetic threshold signed bytes")
    policy = threshold.make_policy([("member", public)], 1)
    nested = 0
    for _ in range(depth - 1):
        nested = [nested]
    if document == "policy":
        policy["extra_metadata"] = nested
    contribution = threshold.contribute(attestation, secret, public, policy)
    artifact = threshold.combine(attestation, policy, [contribution])
    if document == "artifact":
        artifact["extra_metadata"] = nested
    policy_path = tmp_path / "policy.json"
    artifact_path = tmp_path / "artifact.json"
    policy_path.write_text(json.dumps(policy))
    artifact_path.write_text(json.dumps(artifact))
    result = threshold.main(["verify", "--attestation", str(attestation),
                             "--policy", str(policy_path), "--artifact", str(artifact_path)])
    assert result == (0 if depth == 64 else 1)
    if depth == 65:
        assert "malformed threshold input: JSON nesting limit exceeded (64)" in capsys.readouterr().out
