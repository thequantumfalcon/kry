"""Registry grant IDs must have an unambiguous legacy hash representation."""
from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path

import pytest

_GENESIS = "0" * 64


def _verifier():
    path = Path(__file__).resolve().parents[1] / "scripts/kry_verify.py"
    spec = importlib.util.spec_from_file_location("registry_identity_verifier", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _entry(payload, *, party="account", amount=1.0, **fields):
    preimage = f"{_GENESIS}:{party}:{amount}:{payload}"
    return {"party": party, "amount": amount, "prev_hash": _GENESIS,
            "entry_hash": hashlib.sha256(preimage.encode()).hexdigest(), **fields}


def test_colon_move_cannot_reassign_a_settlement_without_changing_its_hash():
    verify = _verifier().verify_registry
    original = _entry("0123456789ab", party="account:1.0:marker", amount=2.0,
                      grant_id="0123456789ab")
    assert verify([original])[0]
    changed = {**original, "party": "account", "amount": 1.0,
               "grant_id": "marker:2.0:0123456789ab"}
    assert changed["entry_hash"] == _entry(changed["grant_id"])["entry_hash"]
    valid, errors = verify([changed])
    assert not valid
    assert any("grant" in error for error in errors)


def test_comma_collapse_cannot_hide_consumed_grant_ids():
    verify = _verifier().verify_registry
    ids = ["0123456789ab", "0123456789ac"]
    original = _entry(",".join(ids), grant_ids=ids, checkpoint=True)
    assert verify([original])[0]
    changed = {**original, "grant_ids": [",".join(ids)]}
    assert ",".join(sorted(changed["grant_ids"])) == ",".join(sorted(ids))
    valid, errors = verify([changed])
    assert not valid
    assert any("grant" in error for error in errors)


@pytest.mark.parametrize("fields,payload", [
    ({"grant_id": ""}, ""), ({"grant_id": None}, ""),
    ({"grant_id": 0}, ""), ({"grant_id": 123}, "123"),
    ({"grant_id": []}, ""), ({"grant_id": {}}, ""),
    ({"grant_id": "a:b"}, "a:b"), ({"grant_id": "a,b"}, "a,b"),
    ({"grant_ids": None}, ""), ({"grant_ids": "ab"}, "a,b"),
    ({"grant_ids": {"b": 1, "a": 2}}, "a,b"),
    ({"grant_ids": ["a", "a"]}, "a,a"), ({"grant_ids": [""]}, ""),
    ({"grant_ids": [None]}, ""), ({"grant_ids": [1]}, "1"),
    ({"grant_ids": [{}]}, ""),
    ({"grant_ids": ["a:b"]}, "a:b"), ({"grant_ids": ["a,b"]}, "a,b"),
    ({"grant_id": "a", "grant_ids": ["a"]}, "a"),
    ({"grant_id": None, "grant_ids": []}, ""),
])
def test_malformed_grant_fields_are_rejected_even_when_the_hash_matches(fields, payload):
    valid, errors = _verifier().verify_registry([_entry(payload, **fields)])
    assert not valid
    assert any("grant" in error for error in errors)


@pytest.mark.parametrize("fields,payload", [
    ({}, ""),  # historical rows without consumed-ID metadata
    ({"grant_ids": []}, ""),  # compaction of those historical rows
    ({"grant_id": "grant-1"}, "grant-1"),
    ({"grant_ids": ["grant-1"]}, "grant-1"),
    ({"grant_ids": ["grant-2", "grant-1"]}, "grant-1,grant-2"),
])
def test_valid_and_legacy_grant_fields_keep_their_hashes(fields, payload):
    entry = _entry(payload, party="account:with:colons", **fields)
    valid, errors = _verifier().verify_registry([entry])
    assert valid, errors
