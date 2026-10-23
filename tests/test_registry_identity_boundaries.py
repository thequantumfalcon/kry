"""Delimiter-safe registry identities preserve accounting and consumed grants."""
import hashlib
import json

import pytest

from kry import kry_settlement as ks


def _write(entries):
    ks._REGISTRY_PATH.write_text("".join(json.dumps(row) + "\n" for row in entries), encoding="utf-8")


def _entry(fields, payload, prev="0" * 64):
    row = {"party": "account", "amount": 1.0, "prev_hash": prev, **fields}
    row["entry_hash"] = hashlib.sha256(f"{prev}:account:1.0:{payload}".encode()).hexdigest()
    return row


def test_colon_collision_cannot_move_settled_amount():
    ks._record_settled("account:1.0:marker", 2.0, "0123456789ab")
    row = ks._registry_entries()[0]
    assert ks.verify_registry()[0]
    changed = {**row, "party": "account", "amount": 1.0, "grant_id": "marker:2.0:0123456789ab"}
    assert hashlib.sha256(
        f"{changed['prev_hash']}:{changed['party']}:{changed['amount']}:{changed['grant_id']}".encode()
    ).hexdigest() == row["entry_hash"]
    _write([changed])
    ok, errors = ks.verify_registry()
    assert not ok and errors
    assert "__tampered__" in ks._load_registry()


def test_comma_collision_cannot_hide_consumed_grants():
    for gid in ("0123456789ab", "0123456789ac", "0123456789ad"):
        ks._record_settled("account", 1.0, gid)
    assert ks.compact_registry(keep_recent=1)
    entries = ks._registry_entries()
    assert entries[0]["grant_ids"] == ["0123456789ab", "0123456789ac"]
    entries[0]["grant_ids"] = [",".join(entries[0]["grant_ids"])]
    _write(entries)
    before = ks._REGISTRY_PATH.read_bytes()
    ok, errors = ks.verify_registry()
    assert not ok and errors
    with pytest.raises(ks.SettlementPersistenceError):
        ks._record_settled("account", 1.0, "0123456789ab")
    assert ks._REGISTRY_PATH.read_bytes() == before


@pytest.mark.parametrize("fields,payload", [
    ({"grant_id": "a:b"}, "a:b"),
    ({"grant_id": "a,b"}, "a,b"),
    ({"grant_id": ""}, ""),
    ({"grant_id": None}, ""),
    ({"grant_id": 1}, "1"),
    ({"grant_id": True}, "True"),
    ({"grant_ids": "ab"}, "a,b"),
    ({"grant_ids": None}, ""),
    ({"grant_ids": {}}, ""),
    ({"grant_ids": [""]}, ""),
    ({"grant_ids": ["a:b"]}, "a:b"),
    ({"grant_ids": ["a,b"]}, "a,b"),
    ({"grant_ids": ["a", "a"]}, "a,a"),
    ({"grant_ids": [1]}, "1"),
    ({"grant_id": "a", "grant_ids": ["a"]}, "a"),
    ({"grant_id": None, "grant_ids": []}, ""),
])
def test_malformed_grant_identity_fails_closed(fields, payload):
    _write([_entry(fields, payload)])
    ok, errors = ks.verify_registry()
    assert not ok and errors
    before = ks._REGISTRY_PATH.read_bytes()
    assert not ks.compact_registry(keep_recent=0)
    assert ks._REGISTRY_PATH.read_bytes() == before


@pytest.mark.parametrize("gid", ["a:b", "a,b", "", None, 1, True])
def test_writer_rejects_ambiguous_or_malformed_grant_id(gid):
    with pytest.raises(ks.SettlementPersistenceError):
        ks._record_settled("account", 1.0, gid)
    assert not ks._REGISTRY_PATH.exists()


def test_legacy_no_id_rows_and_empty_compaction_ids_remain_valid():
    entries = []
    previous = "0" * 64
    for _ in range(3):
        row = _entry({}, "", previous)
        entries.append(row)
        previous = row["entry_hash"]
    _write(entries)
    assert ks.verify_registry()[0]
    assert ks.compact_registry(keep_recent=1)
    assert ks._registry_entries()[0]["grant_ids"] == []
    assert ks.verify_registry()[0]
    assert ks._load_registry() == {"account": 3.0}


def test_compaction_keeps_delimiter_free_grants_consumed():
    for gid in ("g1", "g2", "g3"):
        ks._record_settled("account:with:colons", 1.0, gid)
    assert ks.compact_registry(keep_recent=1)
    assert ks.verify_registry()[0]
    assert ks._load_registry() == {"account:with:colons": 3.0}
    with pytest.raises(ks.SettlementPersistenceError, match="already settled"):
        ks._record_settled("account:with:colons", 1.0, "g1")
