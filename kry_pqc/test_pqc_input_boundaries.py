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
