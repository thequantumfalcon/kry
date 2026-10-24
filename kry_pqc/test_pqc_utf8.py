"""A valid byte signature must not repair invalid UTF-8 for a chain verdict."""
import base64
import builtins
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

pytest.importorskip("oqs")
from kry_pqc import signer, verify


@pytest.fixture
def signed_attestation(tmp_path, monkeypatch):
    from kry import kry_attest

    monkeypatch.setattr(kry_attest, "_MINT_LOG_PATH", tmp_path / "empty-mint.jsonl")
    att = json.loads(kry_attest.build_attestation().to_public_json())
    att["metadata"] = {"synthetic": "\ufffd"}
    att["attestation_hash"] = kry_attest._attestation_hash(att)
    valid = json.dumps(att, ensure_ascii=False).encode("utf-8")
    assert valid.count(b"\xef\xbf\xbd") == 1
    public, secret = signer.generate_keypair()

    def write(malformed):
        path = tmp_path / "attestation.json"
        path.write_bytes(valid.replace(b"\xef\xbf\xbd", b"\xff") if malformed else valid)
        signature = tmp_path / "signature.json"
        signature.write_text(json.dumps(signer.sign_attestation(path, secret, public)),
                             encoding="utf-8")
        key = tmp_path / "public.key"
        key.write_text(base64.b64encode(public).decode(), encoding="utf-8")
        return ["--attestation", str(path), "--signature", str(signature),
                "--public-key", str(key)]

    return write


@pytest.mark.parametrize("require_chain", [False, True])
@pytest.mark.parametrize("malformed", [False, True])
def test_signed_utf8_chain_cli(signed_attestation, tmp_path, require_chain, malformed):
    root = Path(__file__).resolve().parents[1]
    args = signed_attestation(malformed)
    if require_chain:
        args.append("--require-chain")
    env = dict(os.environ, PYTHONPATH=os.pathsep.join([str(root), str(root / "src")]),
               KRY_DATA_DIR=str(tmp_path / "data"), PYTHONDONTWRITEBYTECODE="1")
    result = subprocess.run([sys.executable, "-B", "-m", "kry_pqc.verify", *args],
                            cwd=tmp_path, env=env, capture_output=True, text=True, timeout=60)
    assert result.returncode == (1 if malformed else 0), result.stdout + result.stderr
    assert "[PASS] ML-DSA signature valid" in result.stdout
    assert "[PASS] message digest matches signed bytes" in result.stdout
    assert "Traceback" not in result.stderr
    if malformed:
        assert "[FAIL] KRY hash chain intact" in result.stdout
        assert "invalid UTF-8" in result.stdout
        assert "RESULT: FAILED" in result.stdout
    else:
        assert "[PASS] KRY hash chain intact" in result.stdout
        assert "RESULT: VERIFIED" in result.stdout


@pytest.mark.parametrize("require_chain", [False, True])
def test_arbitrary_signed_bytes_without_core(signed_attestation, monkeypatch, capsys,
                                            require_chain):
    args = signed_attestation(True)
    real_import = builtins.__import__

    def without_core(name, *positional, **keywords):
        if name == "kry.kry_attest":
            raise ImportError("synthetic standalone installation")
        return real_import(name, *positional, **keywords)

    monkeypatch.setattr(builtins, "__import__", without_core)
    if require_chain:
        args.append("--require-chain")
    assert verify.main(args) == (1 if require_chain else 0)
    output = capsys.readouterr().out
    assert "[PASS] ML-DSA signature valid" in output
    assert "chain integrity NOT checked" in output
    assert "[PASS] KRY hash chain" not in output
