"""Exact measurement binding and malformed Nitro document boundaries."""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location(
        "tee_boundary_review", _ROOT / "scripts/kry_tee_verify.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_tee_promotion_matches_complete_measurement_id():
    from kry import kry_mint as km

    small = km.mint("cache_hit", 10, detail="holdout /measurement:m1", evidence="small")
    large = km.mint("cache_hit", 5000, detail="holdout /measurement:m10", evidence="large")
    before = km.veracity_breakdown()["total_kry"]

    promotion, target, moved = km.promote_to_tee("m1", "proof-small")
    assert target == small.receipt_id
    assert promotion.supersedes == small.receipt_id
    assert moved == small.kry_minted
    assert km.veracity_breakdown()["tee_attested_kry"] == small.kry_minted
    assert km.promote_to_tee("m1", "proof-small-again") is None

    _, target, moved = km.promote_to_tee("m10", "proof-large")
    assert target == large.receipt_id
    assert moved == large.kry_minted
    assert km.veracity_breakdown()["total_kry"] == before
    assert km.verify_chain()[0]


@pytest.mark.parametrize("tier,lookup", [
    ("self_reported", "_find_measurement_receipt_for_tee"),
    ("holdout_validated", "_find_measurement_receipt_for_tee"),
    ("tee_attested", "_find_fresh_tee_receipt_for_measurement"),
])
@pytest.mark.parametrize("suffix", ["", " next=value", "/other:value"])
def test_measurement_lookup_requires_complete_id(tier, lookup, suffix):
    from kry import kry_mint as km

    receipt = km.mint("cache_hit", 100, detail=f"holdout /measurement:m10{suffix}",
                      evidence="measurement", evidence_tier=tier)
    find = getattr(km, lookup)
    assert find("m1") is None
    assert find("m10")["receipt_id"] == receipt.receipt_id
    assert find("m100") is None


@pytest.mark.parametrize("raw", [
    "8440a05affff", "8461ffa04040",  # truncated length and invalid UTF-8
    "18", "19ff", "1affff", "1bffff", "42ff", "62ff",
    "ff", "81ff", "a101ff", "bf01ff",  # break outside an indefinite container
    "a18000", "bf8000ff",  # unsupported map keys
    "5f6161ff", "7f4161ff", "5f5f40ffff", "7f7f60ffff",  # invalid chunks
])
def test_malformed_cbor_has_one_error_type(raw):
    mod = _load()
    with pytest.raises(mod._CBORError):
        mod._cbor_decode(bytes.fromhex(raw))


@pytest.mark.parametrize("raw,expected", [
    ("83010203", [1, 2, 3]), ("9f010203ff", [1, 2, 3]),
    ("a1016161", {1: "a"}), ("bf016161ff", {1: "a"}),
    ("426162", b"ab"), ("5f41614162ff", b"ab"),
    ("626162", "ab"), ("7f61616162ff", "ab"),
])
def test_valid_cbor_forms_still_decode(raw, expected):
    mod = _load()
    encoded = bytes.fromhex(raw)
    assert mod._cbor_decode(encoded) == (expected, len(encoded))


@pytest.mark.parametrize("field,encoded_value", [
    (0, b"\x64text"), (0, b"\x1a\x00\x0f\x42\x40"), (1, b"\x40"),
    (2, b"\xf6"), (2, b"\x64text"), (3, b"\x18\x60"),
])
def test_cose_fields_are_checked_before_conversion(field, encoded_value):
    pytest.importorskip("cryptography")
    mod = _load()
    values = [b"\x44\xa1\x01\x38\x22", b"\xa0", b"\x41\xa0",
              mod._cbor_encode(b"\0" * 96)]
    values[field] = encoded_value
    raw = b"\x84" + b"".join(values)
    result = mod.verify_attestation(raw, root_cert_der=b"unused")
    assert result["verified"] is False
    assert any("COSE" in error for error in result["errors"])


@pytest.mark.parametrize("raw", ["8440a05affff", "8461ffa04040"])
def test_malformed_cbor_cli_returns_rejected_without_traceback(raw, tmp_path):
    pytest.importorskip("cryptography")
    document = tmp_path / "malformed.cbor"
    document.write_bytes(bytes.fromhex(raw))
    result = subprocess.run(
        [sys.executable, str(_ROOT / "scripts/kry_tee_verify.py"), str(document),
         "--root", str(_ROOT / "tests/fixtures/nitro_real_root.pem"), "--dry-run"],
        capture_output=True, text=True, check=False)
    assert result.returncode == 1
    assert "REJECTED" in result.stdout
    assert "not decodable CBOR" in result.stdout
    assert "Traceback" not in result.stderr


@pytest.mark.parametrize("part", ["document", "protected", "payload"])
def test_cose_rejects_trailing_bytes(part):
    pytest.importorskip("cryptography")
    mod = _load()
    encoded = (_ROOT / "tests/fixtures/nitro_real_attestation.bin").read_bytes()
    cose, _ = mod._cbor_decode(encoded)
    payload, _ = mod._cbor_decode(cose[2])
    now = payload["timestamp"] / 1000.0 + 5
    root, _ = mod._load_root_der(str(_ROOT / "tests/fixtures/nitro_real_root.pem"))
    if part == "document":
        encoded += b"\0"
    else:
        cose[0 if part == "protected" else 2] += b"\0"
        encoded = b"\x84" + mod._cbor_encode(cose[0]) + b"\xa0"
        encoded += mod._cbor_encode(cose[2]) + mod._cbor_encode(cose[3])
    result = mod.verify_attestation(encoded, root_cert_der=root, now=now)
    assert result["verified"] is False
    assert any("trailing" in error or "protected header" in error for error in result["errors"])
