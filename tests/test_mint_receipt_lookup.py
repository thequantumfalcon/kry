"""Receipt-lookup + hash-bound receipt_id uniqueness regressions (kry_mint / kry_attest).

Two double-credit surfaces, one per section:

  - the tier-promotion LOOKUPS (`_find_t1_receipt_for_gen`,
    `_find_measurement_receipt_for_tee`) filtered zero-value rows with a bare
    `_finite_number(..., positive=True)` call, which RAISES on a zero. mint() writes
    legitimate zero-value rows (free-tier avoided_model, every tier_promotion), so one
    such row carrying the same marker aborted the whole scan through the function-level
    `except Exception: return None` — the caller read that as "no prior receipt exists"
    and minted FRESH FULL VALUE for an already-credited event.
  - the promotion-overlay BUILD sites (`veracity_breakdown`,
    `kry_attest.build_attestation`) last-wins-overwrote a duplicated hash-bound
    receipt_id, so a chain-valid log with two v6+ receipts sharing an id reported a
    higher internal anchored fraction than the public verifiers (which reject it).
"""
from __future__ import annotations

import hashlib
import json

import pytest

_OPUS = "anthropic/claude-opus-4"      # paid → positive kry_minted
_FREE = "google/gemini-flash"          # free tier → kry_minted == 0 by design


def _rechain(km, rows: list[dict]) -> str:
    """Re-derive every chain_hash forward so a hand-edited log is otherwise chain-valid."""
    prev = "0" * 64
    for r in rows:
        block = km._v4_public_block(
            hash_version=r["hash_version"], tokens_saved=r["tokens_saved"], ts=r["ts"],
            evidence_tier=r["evidence_tier"], metered_tokens=r["metered_tokens"],
            kry_minted=r["kry_minted"], earn_rate=r["earn_rate"],
            supersedes=r["supersedes"], receipt_id=r["receipt_id"],
            event_type=r["event_type"])
        r["chain_hash"] = hashlib.sha256(
            f"{prev}:{r['receipt_hash']}:{block}".encode()).hexdigest()
        prev = r["chain_hash"]
    km._MINT_LOG_PATH.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    km._write_mint_tip(len(rows), prev)
    return prev


# ── zero-value rows must not abort the promotion lookups (the duplicate-credit bug) ──

def test_zero_value_row_does_not_hide_prior_t1_receipt():
    """A free-tier (kry_minted == 0) row stamped with the SAME /openrouter:<id> must be
    SKIPPED, not abort the scan and hide the positive-value T1 receipt behind it."""
    import kry.kry_mint as km
    gen = "gen-abc123"
    t1 = km.mint("short_circuit", 500.0, detail=f"displaced /openrouter:{gen}",
                 evidence="e1", avoided_model=_OPUS)
    zero = km.mint("cache_hit", 100.0, detail=f"free hit /openrouter:{gen}",
                   evidence="e2", avoided_model=_FREE)
    assert t1.kry_minted > 0 and zero.kry_minted == 0.0

    found = km._find_t1_receipt_for_gen(gen)
    assert found is not None, "zero-value row aborted the scan — caller would re-mint"
    assert found["receipt_id"] == t1.receipt_id

    # The consequence the bug produced: promote_to_tlsn returned None, so the verifier
    # minted fresh full value for a saving already credited. It must now net out instead.
    promoted = km.promote_to_tlsn(gen, "tlsn-binding-1")
    assert promoted is not None
    _receipt, superseded_id, moved_kry = promoted
    assert superseded_id == t1.receipt_id
    assert moved_kry == t1.kry_minted


def test_zero_value_row_does_not_hide_prior_measurement_receipt():
    """Same skip-don't-abort behaviour on the tee twin, keyed by /measurement:<id>."""
    import kry.kry_mint as km
    mid = "meas-xyz"
    prior = km.mint("cache_hit", 400.0, detail=f"holdout /measurement:{mid}",
                    evidence="m1", avoided_model=_OPUS)
    zero = km.mint("cache_hit", 100.0, detail=f"free hit /measurement:{mid}",
                   evidence="m2", avoided_model=_FREE)
    assert prior.kry_minted > 0 and zero.kry_minted == 0.0

    found = km._find_measurement_receipt_for_tee(mid)
    assert found is not None, "zero-value row aborted the scan — caller would re-mint"
    assert found["receipt_id"] == prior.receipt_id

    promoted = km.promote_to_tee(mid, "tee-binding-1")
    assert promoted is not None
    _receipt, superseded_id, moved_kry = promoted
    assert superseded_id == prior.receipt_id
    assert moved_kry == prior.kry_minted


# ── hash-bound receipt_id uniqueness: internal surface must agree with the verifiers ──

def test_verify_chain_reports_duplicate_hash_bound_receipt_id():
    """A chain-valid log whose only defect is two v6+ receipts sharing a receipt_id was
    called valid internally while both public verifiers reject it."""
    import kry.kry_mint as km
    for i, tokens in enumerate((100.0, 900.0, 50.0)):
        km.mint("cache_hit", tokens, evidence=f"e{i}", avoided_model=_OPUS)
    rows = [json.loads(ln) for ln in km._MINT_LOG_PATH.read_text().splitlines() if ln.strip()]
    rows[1]["receipt_id"] = rows[0]["receipt_id"]
    _rechain(km, rows)

    ok, errs = km.verify_chain()
    assert not ok
    assert any("duplicate receipt_id" in e for e in errs), errs


def test_duplicate_hash_bound_id_anchors_no_promotion_internally():
    """The overlay build site must not last-wins-pick the LARGER colliding receipt: an
    ambiguous target anchors nothing, so the internal floor stays 0 rather than ~0.99."""
    import kry.kry_mint as km
    rid = "KRY-00000001"
    recs = [
        {"evidence_tier": "self_reported", "kry_minted": 10.0, "receipt_id": rid,
         "hash_version": 7, "supersedes": None},
        {"evidence_tier": "self_reported", "kry_minted": 1000.0, "receipt_id": rid,
         "hash_version": 7, "supersedes": None},
        {"evidence_tier": "tee_attested", "kry_minted": 0.0, "receipt_id": "KRY-00000003",
         "hash_version": 7, "supersedes": rid},
    ]
    km._MINT_LOG_PATH.write_text("\n".join(json.dumps(r) for r in recs) + "\n")
    vb = km.veracity_breakdown()
    assert vb["anchored_kry"] == 0.0
    assert vb["veracity_floor"] == 0.0


def test_build_attestation_refuses_to_publish_a_duplicate_id_chain():
    """build_attestation previously emitted chain_valid=True with a last-wins overlay for a
    log verify_attestation rejects — the public floor must not exceed the verifier's."""
    pytest.importorskip("kry.kry_attest")
    import kry.kry_attest as ka
    import kry.kry_mint as km
    for i, tokens in enumerate((100.0, 900.0)):
        km.mint("cache_hit", tokens, evidence=f"e{i}", avoided_model=_OPUS)
    rows = [json.loads(ln) for ln in km._MINT_LOG_PATH.read_text().splitlines() if ln.strip()]
    rows[1]["receipt_id"] = rows[0]["receipt_id"]
    _rechain(km, rows)

    att = ka.build_attestation()
    assert att.chain_valid is False
    assert att.veracity["veracity_floor"] == 0.0
    ok, errs = ka.verify_attestation(att.to_public_json())
    assert not ok and errs
