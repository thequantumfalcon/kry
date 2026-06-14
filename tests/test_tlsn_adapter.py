"""Adapter: attestation_verify stdout -> kry_tlsn_verify presentation JSON.

The fixture mirrors the verbatim output format of tlsn @ 28614ef (the commit the
openrouter-t2.patch pins). These tests pin that the adapter extracts the verified
server, notary key, and the revealed response transcript, and that its output feeds
straight into kry_tlsn_verify (parse + mint), closing the T2 pipeline end-to-end.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_ADAPTER = Path(__file__).resolve().parents[1] / "scripts" / "kry_tlsn_adapter.py"
_VERIFY = Path(__file__).resolve().parents[1] / "scripts" / "kry_tlsn_verify.py"

_DELIM = "-" * 67


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _verifier_stdout(*, server="openrouter.ai", ok=True, body=None):
    """Reproduce the exact attestation_verify console format."""
    if body is None:
        body = ('{"data":{"id":"gen-abc","native_tokens_prompt":120,'
                '"native_tokens_completion":300,"total_cost":0.0}}')
    recv = ("HTTP/1.1 200 OK\r\n"
            "Date: Fri, 05 Jun 2026 03:39:15 GMT\r\n"
            "Content-Type: application/json\r\n"
            "Server: cloudflare\r\n\r\n" + body)
    sent = "GET /api/v1/generation?id=gen-abc HTTP/1.1\r\nHost: openrouter.ai\r\n\r\n"
    verified_line = (
        f"Successfully verified that the data below came from a session with "
        f"{server} at 2026-06-05 03:39:15 UTC.\n" if ok else
        "Error: presentation verification failed\n")
    return (
        "Verifying presentation with k256 key: 04deadbeefcafe1234567890\n\n"
        "**Ask yourself, do you trust this key?**\n\n"
        f"{_DELIM}\n"
        f"{verified_line}"
        "Note that the data which the Prover chose not to disclose are shown as X.\n\n"
        "Data sent:\n"
        f"{sent}\n\n"
        "Data received:\n"
        f"{recv}\n"
        f"{_DELIM}\n")


def test_parses_verified_session():
    mod = _load(_ADAPTER, "kry_tlsn_adapter_standalone")
    pres = mod.parse_verify_output(_verifier_stdout())
    assert pres["verified"] is True
    assert pres["server_name"] == "openrouter.ai"
    assert pres["notary_key"] == "04deadbeefcafe1234567890"
    assert pres["notary_key_alg"] == "k256"
    assert pres["time"] == "2026-06-05 03:39:15 UTC"
    # the revealed response transcript is intact (status line + json body)
    assert pres["recv"].startswith("HTTP/1.1 200 OK")
    assert '"native_tokens_completion":300' in pres["recv"]
    # sent block does not bleed into recv
    assert "Data received:" not in pres["sent"]
    assert _DELIM not in pres["recv"]


def test_failed_verification_marks_unverified():
    mod = _load(_ADAPTER, "kry_tlsn_adapter_standalone")
    pres = mod.parse_verify_output(_verifier_stdout(ok=False))
    assert pres["verified"] is False
    assert pres["server_name"] is None


def test_adapter_output_feeds_kry_tlsn_verify(monkeypatch, tmp_path):
    """End-to-end: adapter dict -> kry_tlsn_verify.run parses + mints a T2 receipt."""
    import kry.kry_token as kt
    import kry.kry_mint as km
    log = tmp_path / "mint.jsonl"
    monkeypatch.setattr(km, "_MINT_LOG_PATH", log)
    monkeypatch.setattr(kt, "_LEDGER_PATH", tmp_path / "ledger.json")
    monkeypatch.setattr(km, "_DECAY_STATE_PATH", tmp_path / "decay.json")
    km._RECEIPT_COUNTER = 0
    km._CHAIN_TIP = "0" * 64
    km._evidence_mints = {}
    km._decay_loaded = True
    kt._ledger_instance = kt.KRYLedger()

    adapter = _load(_ADAPTER, "kry_tlsn_adapter_standalone")
    verify = _load(_VERIFY, "kry_tlsn_verify_e2e")

    pres = adapter.parse_verify_output(_verifier_stdout())
    res = verify.run(pres, expect_server="openrouter.ai", event_type="short_circuit",
                     avoided_model="gh/claude-opus-4.8", served_model=None,
                     tokens_saved=None, require_status=200, dry_run=False)
    assert res["verdict"] == "OK"
    assert res["attested_tokens"] == {"prompt": 120, "completion": 300}
    assert res["minted"]["evidence_tier"] == "tlsn_attested"
    assert km.verify_chain()[0]


def test_server_override_mismatch_errors():
    mod = _load(_ADAPTER, "kry_tlsn_adapter_standalone")
    rc = mod.main([_write(_verifier_stdout()), "--server", "evil.example.com"])
    assert rc == 2


def test_adapter_output_rejects_nonstandard_numbers():
    mod = _load(_ADAPTER, "kry_tlsn_adapter_standalone")

    with pytest.raises(ValueError, match="Out of range float values"):
        mod._json_dumps({"verified": True, "time": float("nan")}, indent=2)


def _write(text):
    import tempfile
    fd, path = tempfile.mkstemp(suffix=".txt")
    import os
    with os.fdopen(fd, "w") as f:
        f.write(text)
    return path
