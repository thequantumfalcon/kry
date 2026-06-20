"""Independent-audit regressions — the one testable code change (F1 verifier caveat).

The other actionable findings are doc/UX: F2 (qualify the research_grade scope), F3 (disclose the
CPython float->JSON cross-language binding), F4 (lead the T2 wording with the trust ceiling). F5/F6
are disclosed/inert; F7/F8 are positive. This pins F1: the DEFAULT verifier (no --anchor) must
LOUDLY flag that any non-self_reported (anchored) fraction is operator-asserted — closing the
"VALID + veracity_floor 1.0, no warning" gap a genesis re-mint exploited.
"""
import importlib.util
import json


def _load_verifier():
    spec = importlib.util.spec_from_file_location("kry_verify_ia", "scripts/kry_verify.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_f1_default_verifier_warns_on_unanchored_anchored_fraction(capsys, tmp_path):
    import kry.kry_attest as a
    import kry.kry_mint as m
    # Forge a genesis re-mint: every link claims tee_attested -> veracity_floor 1.0.
    for i in range(3):
        m.mint("cache_hit", 1000, evidence=f"x{i}", evidence_tier=m.TIER_TEE_ATTESTED)
    forged = tmp_path / "forged.json"
    forged.write_text(a.build_attestation().to_public_json())
    kv = _load_verifier()

    rc = kv.main([str(forged)])                       # the natural command — NO --anchor
    out = capsys.readouterr().out
    assert rc == 0                                    # integrity/conservation/magnitude still hold ...
    assert "OPERATOR-ASSERTED" in out                 # ... but the anchored fraction is now caveated
    assert "--anchor" in out and "re-mint" in out

    # With a published anchor the caveat is replaced by the real anchor check (no false caveat).
    anchor = tmp_path / "anchor.json"
    anchor.write_text(json.dumps(m.export_chain_anchor()))
    kv.main([str(forged), "--anchor", str(anchor)])
    out2 = capsys.readouterr().out
    assert "OPERATOR-ASSERTED" not in out2
    assert "anchor check:" in out2


# ── F3: v5 canonical (language-neutral) hash encoding — additive, backward-compatible ──
def test_f3_v5_block_is_exact_f64_and_cross_language_reproducible():
    import hashlib
    import json
    import struct
    import kry.kry_attest as a
    import kry.kry_mint as m
    m.mint("cache_hit", 1234.5, evidence="x", avoided_model="gh/claude-opus-4.8")
    link = json.loads(a.build_attestation().to_public_json())["links"][0]
    assert link["hash_version"] == 6
    # Reproduce the block following ONLY the documented spec — the IEEE-754 big-endian double as hex —
    # with no kry code, exactly as a non-Python (Rust/JS/Go) verifier would. It regenerates the chain_hash.
    # v6 additionally binds receipt_id (a plain string, language-neutral).
    def canon(v):
        return struct.pack(">d", float(v)).hex()
    block = json.dumps({
        "hash_version": link["hash_version"],
        "tokens_saved": canon(link["tokens_saved"]), "ts": canon(link["ts"]),
        "evidence_tier": link["evidence_tier"], "metered_tokens": link.get("metered_tokens"),
        "kry_minted": canon(link["kry_minted"]), "earn_rate": canon(link["earn_rate"]),
        "receipt_id": link.get("receipt_id") or "",
    }, sort_keys=True, separators=(",", ":"))
    expected = hashlib.sha256(("0" * 64 + ":" + link["receipt_hash"] + ":" + block).encode()).hexdigest()
    assert expected == link["chain_hash"]            # cross-language verifier reproduces the hash
    assert canon(link["kry_minted"]) in block        # bound as the EXACT 16-hex f64 (no float formatting)
    assert len(canon(1.0)) == 16


def test_f3_v4_encoding_unchanged_backward_compat():
    import struct
    import kry.kry_mint as m
    kw = dict(tokens_saved=1234.5, ts=1781743499.615759, evidence_tier="self_reported",
              metered_tokens=None, kry_minted=1234500000.0, earn_rate=1.0)
    v4 = m._v4_public_block(hash_version=4, **kw)
    # v4 and earlier MUST keep CPython float encoding so every existing receipt/anchor hashes identically.
    assert "1234.5" in v4 and "1781743499.615759" in v4
    v5 = m._v4_public_block(hash_version=5, **kw)
    # v5 binds the EXACT IEEE-754 double in hex (no precision loss) — and no float string form survives.
    assert struct.pack(">d", 1234.5).hex() in v5 and struct.pack(">d", 1781743499.615759).hex() in v5
    assert "1234.5" not in v5
    assert v4 != v5


def test_f3_canon_f64_is_exact_total_and_replica_agrees():
    import importlib.util
    import struct
    import kry.kry_mint as m
    assert m._canon_f64(1.5) == struct.pack(">d", 1.5).hex()    # EXACT — no precision loss
    assert m._canon_f64(0) == struct.pack(">d", 0.0).hex()
    for bad in (None, "garbage", float("nan"), float("inf")):
        assert m._canon_f64(bad) == m._V5_BAD       # tampered field -> sentinel, never a crash
    spec = importlib.util.spec_from_file_location("kv_f3", "scripts/kry_verify.py")
    kv = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(kv)
    for x in (1.5, 0, 1234.5, 1781743499.615759, None, "x", float("nan")):
        assert kv._canon_f64(x) == m._canon_f64(x)   # minter and stdlib replica agree byte-for-byte


# ── Round-5 F5 completeness (surfaced by the v5 adversarial pass): a promotion attestation must be
#    STRANGER-verifiable. build_attestation applies the promotion overlay; BOTH verifiers must reproduce
#    it (else a valid promotion attestation was rejected by everyone). ──
def test_f5_promotion_attestation_is_stranger_verifiable():
    import importlib.util
    import json
    import kry.kry_attest as a
    import kry.kry_mint as m
    m.mint("displacement", 1000, "served /openrouter:gen-f5b", avoided_model="gh/claude-opus-4.8")
    assert m.promote_to_tlsn("gen-f5b", "tlsn:bytes", "T2") is not None
    att = json.loads(a.build_attestation().to_public_json())
    assert att["veracity"]["veracity_floor"] > 0.0                 # the overlay lifted the floor
    assert a.verify_attestation(json.dumps(att))[0]                # package verifier accepts
    spec = importlib.util.spec_from_file_location("kv_f5", "scripts/kry_verify.py")
    kv = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(kv)
    assert kv.verify_attestation(att)[0]                           # stdlib stranger verifier accepts
    att["veracity"]["by_tier"] = {"self_reported": 0.0, "tee_attested": 99999.0}   # forge the floor
    att["attestation_hash"] = a._attestation_hash(att)
    assert not kv.verify_attestation(att)[0]                       # still caught
