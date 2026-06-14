"""Veracity tier (falsifier #1b: external evidence binding).

The hash chain proves INTEGRITY (untampered + conserved); it cannot prove
VERACITY (the events happened). These tests pin the honest primitive that makes
the trust surface explicit and machine-checkable:

  - legacy v1 receipts (no tier) still verify bit-for-bit (freeze-safety)
  - v2+ binds the tier into the receipt hash → a forged tier breaks the chain
  - the attestation surfaces a verifiable veracity_floor a recipient can audit
"""
from __future__ import annotations

import hashlib
import json

import pytest


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    import kry.kry_token as kt
    import kry.kry_mint as km
    import kry.kry_attest as ka
    log = tmp_path / "mint.jsonl"
    monkeypatch.setattr(km, "_MINT_LOG_PATH", log)
    monkeypatch.setattr(ka, "_MINT_LOG_PATH", log)
    monkeypatch.setattr(kt, "_LEDGER_PATH", tmp_path / "ledger.json")
    monkeypatch.setattr(km, "_DECAY_STATE_PATH", tmp_path / "decay.json")
    km._RECEIPT_COUNTER = 0
    km._CHAIN_TIP = "0" * 64
    km._evidence_mints = {}
    km._decay_loaded = True
    kt._ledger_instance = kt.KRYLedger()
    return kt, km, ka, log


def _write_v1_line(log, prev_chain, *, event_type, tokens_saved, ts, evidence_hash,
                   kry_minted, receipt_id):
    """Hand-build a pre-tier (v1) receipt line, exactly as the old code wrote it."""
    content = f"{event_type}:{tokens_saved}:{ts}:{evidence_hash}"
    receipt_hash = hashlib.sha256(content.encode()).hexdigest()
    chain_hash = hashlib.sha256(f"{prev_chain}:{receipt_hash}".encode()).hexdigest()
    rec = {
        "receipt_id": receipt_id, "event_type": event_type,
        "tokens_saved": tokens_saved, "kry_minted": kry_minted, "earn_rate": 1.0,
        "ts": ts, "detail": "legacy", "evidence_hash": evidence_hash,
        "receipt_hash": receipt_hash, "chain_hash": chain_hash,
        "usd_equivalent": kry_minted * 0.000025, "avoided_model": "gh/claude-opus-4.8",
        # NOTE: no hash_version, no evidence_tier — this is the legacy on-disk shape
    }
    with open(log, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")
    return chain_hash


def _write_v2_line(log, prev_chain, *, event_type, tokens_saved, ts, evidence_hash,
                   kry_minted, receipt_id, evidence_tier):
    """Hand-build a tier-bound v2 receipt line, before metered_tokens entered the hash."""
    content = f"{event_type}:{tokens_saved}:{ts}:{evidence_hash}:{evidence_tier}"
    receipt_hash = hashlib.sha256(content.encode()).hexdigest()
    chain_hash = hashlib.sha256(f"{prev_chain}:{receipt_hash}".encode()).hexdigest()
    rec = {
        "receipt_id": receipt_id, "event_type": event_type,
        "tokens_saved": tokens_saved, "kry_minted": kry_minted, "earn_rate": 1.0,
        "ts": ts, "detail": "legacy-v2", "evidence_hash": evidence_hash,
        "receipt_hash": receipt_hash, "chain_hash": chain_hash,
        "usd_equivalent": kry_minted * 0.000025, "avoided_model": "gh/claude-opus-4.8",
        "evidence_tier": evidence_tier, "hash_version": 2,
        "metered_tokens": [120, 340],
    }
    with open(log, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")
    return chain_hash


def test_legacy_v1_chain_still_verifies(isolated):
    """Freeze-safety: receipts minted before the tier field verify unchanged."""
    kt, km, ka, log = isolated
    _write_v1_line(log, "0" * 64, event_type="cache_hit", tokens_saved=1000.0,
                   ts=111.0, evidence_hash="abc123", kry_minted=1000.0,
                   receipt_id="KRY-00000001")
    ok, errs = km.verify_chain()
    assert ok, errs
    # default tier for a legacy receipt is self_reported (the honest assumption)
    vb = km.veracity_breakdown()
    assert vb["veracity_floor"] == 0.0
    assert vb["self_reported_kry"] == 1000.0


def test_legacy_v2_chain_still_verifies(isolated):
    """Freeze-safety: tier-bound receipts minted before v3 verify unchanged."""
    kt, km, ka, log = isolated
    _write_v2_line(log, "0" * 64, event_type="short_circuit", tokens_saved=340.0,
                   ts=222.0, evidence_hash="v2seed", kry_minted=340.0,
                   receipt_id="KRY-00000001", evidence_tier=km.TIER_PROVIDER_METERED)
    ok, errs = km.verify_chain()
    assert ok, errs


def test_mixed_v1_then_v3_chain_verifies(isolated):
    """A v3 mint chained onto a legacy v1 line keeps the chain continuous."""
    kt, km, ka, log = isolated
    tip = _write_v1_line(log, "0" * 64, event_type="cache_hit", tokens_saved=500.0,
                         ts=100.0, evidence_hash="seed", kry_minted=500.0,
                         receipt_id="KRY-00000001")
    km._CHAIN_TIP = tip
    km._RECEIPT_COUNTER = 1
    r = km.mint("compression", 2000, "disp", evidence="x",
                avoided_model="gh/claude-opus-4.8",
                evidence_tier=km.TIER_PROVIDER_METERED,
                metered_tokens=[120, 340])
    assert r is not None and r.hash_version == 4   # current mint format (v4: +public-block chain bind)
    ok, errs = km.verify_chain()
    assert ok, errs


def test_v3_tier_is_tamper_evident(isolated):
    """Forging a tier (self_reported → provider_metered) breaks the receipt hash."""
    kt, km, ka, log = isolated
    km.mint("cache_hit", 1000, "c", evidence="e",
            avoided_model="gh/claude-opus-4.8")  # default self_reported
    assert km.verify_chain()[0]

    lines = log.read_text(encoding="utf-8").splitlines()
    rec = json.loads(lines[0])
    assert rec["evidence_tier"] == "self_reported" and rec["hash_version"] == 4
    rec["evidence_tier"] = "provider_metered"   # forge an upgrade, leave hashes as-is
    log.write_text(json.dumps(rec) + "\n", encoding="utf-8")

    ok, errs = km.verify_chain()
    assert not ok
    assert any("receipt_hash mismatch" in e for e in errs)


def test_v3_metered_tokens_are_tamper_evident(isolated):
    """Forging T1 token counts breaks new receipt hashes."""
    kt, km, ka, log = isolated
    km.mint("short_circuit", 400, "disp", evidence="e",
            avoided_model="gh/claude-opus-4.8",
            evidence_tier=km.TIER_PROVIDER_METERED,
            metered_tokens=[120, 400])
    assert km.verify_chain()[0]

    rec = json.loads(log.read_text(encoding="utf-8"))
    assert rec["hash_version"] == 4
    rec["metered_tokens"] = [1, 1]
    log.write_text(json.dumps(rec) + "\n", encoding="utf-8")

    ok, errs = km.verify_chain()
    assert not ok
    assert any("receipt_hash mismatch" in e for e in errs)


def test_provider_metered_mint_requires_json_integer_metered_tokens(isolated):
    kt, km, ka, log = isolated

    missing = km.mint("short_circuit", 400, "disp", evidence="e",
                      avoided_model="gh/claude-opus-4.8",
                      evidence_tier=km.TIER_PROVIDER_METERED)
    boolean = km.mint("short_circuit", 400, "disp2", evidence="e2",
                      avoided_model="gh/claude-opus-4.8",
                      evidence_tier=km.TIER_PROVIDER_METERED,
                      metered_tokens=[True, 400])
    stringy = km.mint("short_circuit", 400, "disp3", evidence="e3",
                      avoided_model="gh/claude-opus-4.8",
                      evidence_tier=km.TIER_PROVIDER_METERED,
                      metered_tokens=["120", 400])

    assert missing is None
    assert boolean is None
    assert stringy is None
    assert not log.exists() or log.read_text(encoding="utf-8") == ""


def test_veracity_breakdown_floor(isolated):
    """Floor = fraction of KRY backed by an external anchor, not self-report."""
    kt, km, ka, log = isolated
    km.mint("cache_hit", 1000, "a", evidence="a",
            avoided_model="gh/claude-opus-4.8")                       # self_reported
    km.mint("compression", 3000, "b", evidence="b",
            avoided_model="gh/claude-opus-4.8",
            evidence_tier=km.TIER_PROVIDER_METERED,
            metered_tokens=[120, 340])                                # anchored
    vb = km.veracity_breakdown()
    # floor is the anchored share of total kry, computed from the receipts themselves.
    assert 0.0 < vb["veracity_floor"] < 1.0
    assert vb["externally_anchored_kry"] > 0
    assert vb["self_reported_kry"] > 0
    assert abs(vb["externally_anchored_kry"] + vb["self_reported_kry"]
               - vb["total_kry"]) < 0.01


def test_attestation_surfaces_and_verifies_veracity(isolated):
    """Attestation exposes the trust surface; a forged floor is caught."""
    kt, km, ka, log = isolated
    km.mint("cache_hit", 1000, "a", evidence="a", avoided_model="gh/claude-opus-4.8")
    km.mint("compression", 1000, "b", evidence="b",
            avoided_model="gh/claude-opus-4.8", evidence_tier=km.TIER_PROVIDER_METERED,
            metered_tokens=[1000, 1000])

    att = ka.build_attestation(log)
    j = att.to_public_json()
    assert ka.verify_attestation(j)[0]
    assert "veracity_floor" in att.veracity
    assert att.veracity["veracity_floor"] > 0

    # Forge a higher trust floor than the links support → must be rejected.
    data = json.loads(j)
    data["veracity"]["veracity_floor"] = 0.99
    ok, errs = ka.verify_attestation(json.dumps(data))
    assert not ok
    assert any("veracity_floor mismatch" in e for e in errs)


def test_package_attestation_rejects_conserved_magnitude_inflation(isolated):
    kt, km, ka, log = isolated
    km.mint("cache_hit", 1000, "a", evidence="a", avoided_model="gh/claude-opus-4.8")
    data = json.loads(ka.build_attestation(log).to_public_json())
    data["links"][0]["kry_minted"] = 1500.0
    data["total_kry"] = 1500.0
    data["usd_equivalent"] = round(1500.0 * 0.000025, 6)
    data["veracity"]["self_reported_kry"] = 1500.0
    data["veracity"]["by_tier"]["self_reported"] = 1500.0
    data["attestation_hash"] = ka._attestation_hash(data)

    ok, errs = ka.verify_attestation(json.dumps(data))

    assert not ok
    assert any("non-public price" in e for e in errs)


def test_package_attestation_rejects_stringy_numeric_fields_without_crashing(isolated):
    kt, km, ka, log = isolated
    km.mint("cache_hit", 1000, "a", evidence="a", avoided_model="gh/claude-opus-4.8")
    data = json.loads(ka.build_attestation(log).to_public_json())
    data["links"][0]["kry_minted"] = "NaN"
    data["total_kry"] = "NaN"
    data["attestation_hash"] = ka._attestation_hash(data)

    ok, errs = ka.verify_attestation(json.dumps(data))

    assert not ok
    assert any("seq 1: kry_minted must be a finite JSON number" in e for e in errs)
    assert any("total_kry must be a finite JSON number" in e for e in errs)


def test_package_attestation_rejects_malformed_links_shape_without_crashing(isolated):
    kt, km, ka, log = isolated
    data = {
        "receipts": 1,
        "links": {"not": "a list"},
        "chain_valid": True,
        "total_kry": 0.0,
        "usd_equivalent": 0.0,
        "event_type_counts": {},
        "attestation_hash": "x",
    }

    ok, errs = ka.verify_attestation(json.dumps(data))

    assert not ok
    assert any("links must be a JSON list" in e for e in errs)


def test_attestation_rejects_provider_metered_without_metered_tokens(isolated):
    """T1 links need public token counts for provider reconciliation."""
    kt, km, ka, log = isolated
    km.mint("short_circuit", 1000, "disp", evidence="e",
            avoided_model="gh/claude-opus-4.8",
            evidence_tier=km.TIER_PROVIDER_METERED,
            metered_tokens=[120, 400])
    data = json.loads(ka.build_attestation(log).to_public_json())
    del data["links"][0]["metered_tokens"]
    data["attestation_hash"] = ka._attestation_hash(data)

    ok, errs = ka.verify_attestation(json.dumps(data))

    assert not ok
    assert any("provider_metered link missing metered_tokens" in e for e in errs)


def test_attestation_rejects_non_integer_provider_metered_tokens(isolated):
    kt, km, ka, log = isolated
    km.mint("short_circuit", 1000, "disp", evidence="e",
            avoided_model="gh/claude-opus-4.8",
            evidence_tier=km.TIER_PROVIDER_METERED,
            metered_tokens=[120, 400])
    data = json.loads(ka.build_attestation(log).to_public_json())
    data["links"][0]["metered_tokens"] = ["120", 400]
    data["attestation_hash"] = ka._attestation_hash(data)

    ok, errs = ka.verify_attestation(json.dumps(data))

    assert not ok
    assert any("provider_metered metered_tokens must be integers" in e for e in errs)


def test_package_attestation_json_boundary_rejects_nonstandard_numbers(isolated):
    kt, km, ka, log = isolated
    att = ka.Attestation(
        receipts=0,
        total_kry=float("nan"),
        usd_equivalent=0.0,
        chain_head="0" * 64,
        chain_valid=True,
        event_type_counts={},
        links=[],
    )

    with pytest.raises(ValueError, match="Out of range float values"):
        att.to_public_json()
    with pytest.raises(ValueError, match="Out of range float values"):
        ka._attestation_hash({"attestation_hash": "", "bad": float("inf")})

    ok, errs = ka.verify_attestation(
        '{"receipts":0,"links":[],"chain_valid":true,"total_kry":NaN}\n'
    )

    assert not ok
    assert errs == ["invalid JSON: non-standard JSON constant rejected: NaN"]

    log.write_text(
        '{"receipt_id":"bad","event_type":"cache_hit","receipt_hash":"0",'
        '"chain_hash":"0","kry_minted":NaN}\n'
    , encoding="utf-8")
    with pytest.raises(ValueError, match="non-standard JSON constant rejected: NaN"):
        ka.build_attestation(log)


def test_v4_forged_tier_is_rejected_by_both_verifiers(isolated):
    """H1 regression (the execution-proven HIGH): v4 binds evidence_tier into chain_hash, so forging a
    link's tier to inflate veracity_floor and re-stamping attestation_hash must be REJECTED by BOTH the
    in-package verifier and the standalone stdlib stranger verifier. (Before v4 this forgery passed.)"""
    import importlib.util
    import pathlib
    kt, km, ka, log = isolated
    km.mint("cache_hit", 100000, "p", evidence="ev-forge")            # self_reported
    honest = json.loads(ka.build_attestation().to_public_json())
    assert ka.verify_attestation(json.dumps(honest))[0]
    assert honest["veracity"]["veracity_floor"] == 0.0

    forged = json.loads(json.dumps(honest))
    for lk in forged["links"]:
        lk["evidence_tier"] = "tee_attested"                          # forge an external anchor
    tot = sum(l["kry_minted"] for l in forged["links"])
    forged["veracity"] = {"by_tier": {"tee_attested": round(tot, 4)},
                          "externally_anchored_kry": round(tot, 4),
                          "self_reported_kry": 0.0, "veracity_floor": 1.0}
    forged["attestation_hash"] = ka._attestation_hash(forged)          # re-stamp (operator-controlled)

    ok_a, errs_a = ka.verify_attestation(json.dumps(forged))
    assert ok_a is False and any("chain link broken" in e for e in errs_a), errs_a

    # the standalone stdlib stranger verifier must reject it too (its replica of the v4 block)
    spec = importlib.util.spec_from_file_location(
        "kv_standalone", pathlib.Path(__file__).resolve().parents[1] / "scripts" / "kry_verify.py")
    kv = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(kv)
    ok_v, _ = kv.verify_attestation(forged)
    assert ok_v is False


def test_v4_partial_tail_downgrade_is_rejected_by_both_verifiers(isolated):
    """GPT v4-review HIGH regression: leave link 1 as v4 but DOWNGRADE link 2 to legacy v1 (to dodge the
    public-block binding and forge its tier), recompute only link 2. The monotonic-version check in BOTH
    public verifiers must reject this partial-tail downgrade."""
    import importlib.util
    import pathlib
    import hashlib
    kt, km, ka, log = isolated
    km.mint("cache_hit", 1000, "a", evidence="pe1")
    km.mint("cache_hit", 2000, "b", evidence="pe2")
    att = json.loads(ka.build_attestation().to_public_json())
    l2 = att["links"][1]
    l2["evidence_tier"] = "tee_attested"
    l2["hash_version"] = 1
    prev = att["links"][0]["chain_hash"]
    l2["chain_hash"] = hashlib.sha256(f"{prev}:{l2['receipt_hash']}".encode()).hexdigest()
    att["chain_head"] = l2["chain_hash"]
    att["attestation_hash"] = ka._attestation_hash(att)

    ok_a, errs_a = ka.verify_attestation(json.dumps(att))
    assert ok_a is False and any("version downgrade" in e for e in errs_a), errs_a

    spec = importlib.util.spec_from_file_location(
        "kv_pt", pathlib.Path(__file__).resolve().parents[1] / "scripts" / "kry_verify.py")
    kv = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(kv)
    ok_v, errs_v = kv.verify_attestation(att)
    assert ok_v is False and any("version downgrade" in e for e in errs_v), errs_v
