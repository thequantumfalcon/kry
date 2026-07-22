#!/usr/bin/env python3
"""Generate the KRY-SPEC conformance vector corpus from the REAL kry code.

Every vector's expected canonical bytes / hashes / verdict is produced by importing
the shipping implementation (src/kry + scripts/kry_verify + scripts/kry_action_verify),
so the corpus cannot drift from the code. Regenerate with:

    PYTHONPATH=src python3 vectors/generate.py

A second implementation is conformant iff, reading ONLY SPEC.md + this corpus
(never src/kry), it reproduces each vector's `expected` verdict from its `input`.
Valid savings attestations are built with the real kry_attest builder; adversarial
ones tamper a real attestation and (per test_external_verify) recompute the outer
attestation_hash so the INNER defence is what is exercised.
"""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import shutil
import sys
import time as _time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import kry.kry_token as kt        # noqa: E402
import kry.kry_mint as km         # noqa: E402
import kry.kry_attest as ka       # noqa: E402
import kry.kry_settlement as ks   # noqa: E402
import kry.kry_action as kax       # noqa: E402


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


kv = _load(ROOT / "scripts" / "kry_verify.py", "kry_verify")
kav = _load(ROOT / "scripts" / "kry_action_verify.py", "kry_action_verify")

OUT = ROOT / "vectors"
WORK = OUT / ".mintwork"
GENESIS = "0" * 64
manifest: list[dict] = []

# Deterministic clock so regeneration produces byte-identical vectors.
_clock = [1_700_000_000.0]
_time.time = lambda: _clock[0]   # noqa: E731  (kry_mint/kry_attest call time.time())


def canon(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def write(category: str, vid: str, obj: dict) -> None:
    d = OUT / category
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{vid}.json").write_text(json.dumps({"id": vid, **obj}, indent=2) + "\n")
    manifest.append({"id": vid, "category": category,
                     "verdict": obj.get("expected", {}).get("verdict")})


def reset_mint_state() -> Path:
    if WORK.exists():
        shutil.rmtree(WORK)
    WORK.mkdir(parents=True)
    log = WORK / "mint.jsonl"
    km._MINT_LOG_PATH = log
    ka._MINT_LOG_PATH = log
    kt._LEDGER_PATH = WORK / "ledger.json"
    km._DECAY_STATE_PATH = WORK / "decay.json"
    ks._REGISTRY_PATH = WORK / "reg.jsonl"
    km._RECEIPT_COUNTER = 0
    km._CHAIN_TIP = GENESIS
    km._evidence_mints = {}
    km._decay_loaded = True
    kt._ledger_instance = kt.KRYLedger()
    _clock[0] = 1_700_000_000.0
    return log


def build(mints: list[dict]) -> dict:
    """Mint the given events into a fresh log and return the real public attestation."""
    log = reset_mint_state()
    for m in mints:
        _clock[0] += 1.0
        r = km.mint(**m)
        assert r is not None, f"mint returned None for {m}"
    return json.loads(ka.build_attestation(log).to_public_json())


def verdict_savings(att) -> dict:
    ok, errors = kv.verify_attestation(att)
    return {"verdict": "VALID" if ok else "INVALID", "reasons": errors}


def reseal(att: dict) -> dict:
    """Recompute the outer attestation_hash over a (tampered) attestation, so the
    verifier's INNER checks (chain / magnitude / tier / totals) are what fire."""
    att["attestation_hash"] = kv._attestation_hash(att)
    return att


def rechain(att: dict) -> dict:
    """Recompute link chain_hashes, chain_head, totals and the outer hash from the
    (tampered) link fields — yields a self-consistent chain whose ONLY fault is the
    value we changed (e.g. an illegal magnitude), isolating one defence."""
    prev = GENESIS
    total = 0.0
    counts: dict[str, int] = {}
    by_tier: dict[str, float] = {}
    for link in att["links"]:
        block = km._v4_public_block(
            hash_version=link["hash_version"], tokens_saved=link["tokens_saved"],
            ts=link["ts"], evidence_tier=link["evidence_tier"],
            metered_tokens=link.get("metered_tokens"), kry_minted=link["kry_minted"],
            earn_rate=link["earn_rate"], supersedes=link.get("supersedes"),
            receipt_id=link.get("receipt_id"), event_type=link.get("event_type"))
        link["chain_hash"] = hashlib.sha256(
            f"{prev}:{link['receipt_hash']}:{block}".encode()).hexdigest()
        prev = link["chain_hash"]
        total += link["kry_minted"]
        counts[link["event_type"]] = counts.get(link["event_type"], 0) + 1
        by_tier[link["evidence_tier"]] = by_tier.get(link["evidence_tier"], 0.0) + link["kry_minted"]
    anchored = round(sum(v for t, v in by_tier.items() if t in km._ANCHORED_TIERS), 4)
    self_rep = round(by_tier.get("self_reported", 0.0), 4)
    att["chain_head"] = prev
    att["total_kry"] = round(total, 4)
    att["usd_equivalent"] = round(total * 0.000025, 6)
    att["event_type_counts"] = counts
    att["receipts"] = len(att["links"])
    att["veracity"] = {"by_tier": {t: round(v, 4) for t, v in by_tier.items()},
                       "anchored_kry": anchored, "self_reported_kry": self_rep,
                       "veracity_floor": round(anchored / total, 4) if total > 0 else 0.0}
    return reseal(att)


# ── 1. Encoding primitives ────────────────────────────────────────────────────

def gen_primitives() -> None:
    f64 = [{"label": lbl, "input_number": x, "expected_hex": km._canon_f64(x)}
           for lbl, x in [("zero", 0.0), ("one_float", 1.0), ("one_int", 1),
                          ("neg_one", -1.0), ("thousand", 1000.0), ("tenth", 0.1),
                          ("small", 2.5e-08), ("big", 1e308)]]
    f64.append({"label": "sentinel_savings_nonfinite",
                "input_note": "NaN / Infinity / non-numeric in a savings block field",
                "expected_hex": km._V5_BAD})
    f64.append({"label": "sentinel_action_nonfinite",
                "input_note": "NaN / Infinity / non-numeric in an action payload ts",
                "expected_hex": kax._V5_BAD})
    write("primitives", "canon_f64", {
        "kind": "canon_f64",
        "description": "number -> IEEE-754 big-endian double, hex (16 chars). int and float "
                       "of equal value encode identically. Savings non-finite sentinel = "
                       "'nonfinite'; action non-finite sentinel = 'ffffffffffffffff'.",
        "cases": f64})

    cj = [{"label": lbl, "input_object": obj, "expected_bytes": canon(obj)}
          for lbl, obj in [
              ("key_sort", {"b": 1, "a": 0, "m": 2}),
              ("nested_sort", {"z": [3, 2, 1], "a": {"y": 1, "x": 2}}),
              ("unicode_escaped", {"k": "café", "emoji": "\U0001f600"}),
              ("number_null_bool", {"n": 1.5, "z": None, "b": True}),
              ("empty_and_list", {"list": [1, {"b": 2, "a": 1}], "obj": {}})]]
    write("primitives", "canonical_json", {
        "kind": "canonical_json",
        "description": "Canonical JSON = json.dumps(sort_keys=True, separators=(',',':'), "
                       "allow_nan=False, ensure_ascii=True): keys sorted at every level; no "
                       "whitespace; non-ASCII escaped \\uXXXX; NaN/Infinity rejected.",
        "cases": cj})

    write("primitives", "legal_multipliers", {
        "kind": "legal_multipliers",
        "description": "The authoritative published price-multiplier set for the SPEC §3.4.1 "
                       "magnitude check. A link's implied multiplier kry_minted/(tokens_saved*"
                       "earn_rate) is legal iff within 1e-3 of one of these values. Generated "
                       "from the reference price table so it cannot drift.",
        "multipliers": sorted(kv.legal_multipliers())})


# ── 2. Savings attestation vectors ────────────────────────────────────────────

CACHE = {"event_type": "cache_hit", "tokens_saved": 1000, "avoided_model": "gh/claude-opus-4.8"}


def gen_savings() -> None:
    # valid: single self_reported cache_hit
    att = build([{**CACHE, "detail": "q0", "evidence": "u0"}])
    write("savings/valid", "single_self_reported", {
        "kind": "savings_attestation", "description": "one self_reported cache_hit link",
        "input": att, "expected": {**verdict_savings(att), "chain_tip": att["chain_head"]},
        "rationale": "Well-formed: chain recomputes, totals match, magnitude is a published multiplier."})
    assert verdict_savings(att)["verdict"] == "VALID"

    # valid: 3-link self_reported chain
    att3 = build([{**CACHE, "detail": f"q{i}", "evidence": f"u{i}"} for i in range(3)])
    write("savings/valid", "chain_three", {
        "kind": "savings_attestation", "description": "three self_reported cache_hit links",
        "input": att3, "expected": {**verdict_savings(att3), "chain_tip": att3["chain_head"]},
        "rationale": "Multi-link chain: each link binds the previous chain_hash."})
    assert verdict_savings(att3)["verdict"] == "VALID"

    base = build([{**CACHE, "detail": "q0", "evidence": "u0"},
                  {**CACHE, "detail": "q1", "evidence": "u1"}])

    # adversarial: illegal magnitude only (self-consistent chain via rechain)
    a = copy.deepcopy(base)
    a["links"][0]["kry_minted"] = round(a["links"][0]["tokens_saved"]
                                        * a["links"][0]["earn_rate"] * 0.5, 6)  # 0.5 not a legal multiplier
    a = rechain(a)
    write("savings/adversarial", "magnitude_illegal_multiplier", {
        "kind": "savings_attestation",
        "description": "kry_minted set to tokens*rate*0.5 (0.5 is not a published price multiplier)",
        "input": a, "expected": verdict_savings(a),
        "rationale": "Chain + totals are self-consistent; only the public magnitude arithmetic is illegal."})

    # adversarial: event_type relabel (v7 binds event_type -> chain breaks)
    b = copy.deepcopy(base)
    b["links"][0]["event_type"] = "compression"
    reseal(b)
    write("savings/adversarial", "event_type_relabel", {
        "kind": "savings_attestation", "description": "link0 event_type changed after minting",
        "input": b, "expected": verdict_savings(b),
        "rationale": "v7 binds event_type into the public block; a relabel makes chain_hash mismatch."})

    # adversarial: hash_version downgrade on the tail
    c = copy.deepcopy(base)
    c["links"][1]["hash_version"] = 6
    reseal(c)
    write("savings/adversarial", "version_downgrade", {
        "kind": "savings_attestation", "description": "second link hash_version set to 6 (< 7)",
        "input": c, "expected": verdict_savings(c),
        "rationale": "hash_version must be non-decreasing (partial-tail rollback attempt)."})

    # adversarial: forge an anchored tier on a link (provider_metered w/o metered_tokens)
    d = copy.deepcopy(base)
    d["links"][0]["evidence_tier"] = "provider_metered"
    reseal(d)
    write("savings/adversarial", "tier_forged", {
        "kind": "savings_attestation",
        "description": "link0 evidence_tier changed to provider_metered (no metered_tokens)",
        "input": d, "expected": verdict_savings(d),
        "rationale": "Tier is bound at v4+; a forged tier breaks the chain and fails the tier schema."})

    # adversarial: outer attestation_hash blanked
    e = copy.deepcopy(base)
    e["attestation_hash"] = ""
    write("savings/adversarial", "attestation_hash_blank", {
        "kind": "savings_attestation", "description": "attestation_hash cleared",
        "input": e, "expected": verdict_savings(e),
        "rationale": "The attestation must carry a valid self-hash."})

    # adversarial: declared receipt count disagrees with links
    f = copy.deepcopy(base)
    f["receipts"] = 99
    reseal(f)
    write("savings/adversarial", "receipts_mismatch", {
        "kind": "savings_attestation", "description": "declared receipts=99 with 2 links",
        "input": f, "expected": verdict_savings(f),
        "rationale": "Declared receipt count must equal len(links)."})

    # adversarial: raw JSON containing NaN must be rejected at parse time
    write("savings/adversarial", "parse_reject_nan", {
        "kind": "raw_json", "description": "raw attestation text embedding the JSON constant NaN",
        "input_raw_text": '{"receipts":1,"links":[{"seq":0,"kry_minted":NaN}]}',
        "expected": {"verdict": "PARSE_ERROR",
                     "reasons": ["non-standard JSON constant rejected: NaN"]},
        "rationale": "A conforming parser MUST reject NaN/Infinity (not standard JSON)."})


# ── 3. Action attestation vectors ─────────────────────────────────────────────

def act_payload(link: dict) -> dict:
    return {"action_hash_version": kax.ACTION_HASH_VERSION, "tool": link["tool"],
            "args_commit": link["args_commit"], "result_commit": link.get("result_commit"),
            "status": link["status"], "ts": kax._canon_f64(link["ts"]),
            "agent_id": link["agent_id"], "evidence_tier": link["evidence_tier"],
            "server_evidence_commit": link.get("server_evidence_commit")}


def act_link(seq, prev, *, tool="read_file", status="ok", ts=1_700_000_000.0, agent_id="agent-A",
             tier="self_reported", args="a", result="r", server_evidence_commit=None):
    link = {"receipt_id": f"ACT-{seq:08d}", "tool": tool, "args_commit": kax.commit(args),
            "result_commit": kax.commit(result), "status": status, "ts": ts,
            "agent_id": agent_id, "evidence_tier": tier,
            "server_evidence_commit": server_evidence_commit,
            "action_hash_version": kax.ACTION_HASH_VERSION}
    rh = hashlib.sha256(canon(act_payload(link)).encode()).hexdigest()
    ch = hashlib.sha256(f"{prev}:{rh}".encode()).hexdigest()
    link["receipt_hash"] = rh
    link["chain_hash"] = ch
    return link, ch


def act_att(links, floor):
    att = {"kind": "kry_action_attestation", "action_hash_version": kax.ACTION_HASH_VERSION,
           "links": links, "chain_tip": links[-1]["chain_hash"], "action_count": len(links)}
    if floor is not None:
        att["veracity"] = {"veracity_floor": floor}
    return att


def verdict_action(att) -> dict:
    ok, errors, warnings = kav.verify_action_attestation(att)
    return {"verdict": "VALID" if ok else "INVALID", "reasons": errors, "warnings": warnings}


# ── 2b. Promotion-overlay profile vectors (SPEC §3.7) ─────────────────────────

def build_promoted() -> dict:
    """Mint a displacement, then a REAL zero-value tlsn promotion of it (via the
    shipping promote_to_tlsn path), and return the public attestation — declared
    veracity carries the overlaid by_tier/floor the profile must reproduce."""
    log = reset_mint_state()
    _clock[0] += 1.0
    r = km.mint("displacement", 1000, "served via cheap leg /openrouter:gen-vec-p1",
                evidence="u-promo", avoided_model="gh/claude-opus-4.8")
    assert r is not None
    _clock[0] += 1.0
    p = km.promote_to_tlsn("gen-vec-p1", "tlsn:conformance-vector-evidence", "T2 vector")
    assert p is not None
    return json.loads(ka.build_attestation(log).to_public_json())


def gen_overlay() -> None:
    att = build_promoted()
    promo_idx = next(i for i, ln in enumerate(att["links"]) if ln.get("supersedes"))
    target_idx = next(i for i, ln in enumerate(att["links"])
                      if ln.get("receipt_id") == att["links"][promo_idx]["supersedes"])
    moved = att["links"][target_idx]["kry_minted"]
    exp = verdict_savings(att)
    assert exp["verdict"] == "VALID", exp
    assert att["veracity"]["veracity_floor"] > 0, att["veracity"]   # the overlay moved value
    write("savings/overlay", "promotion_legit", {
        "kind": "savings_attestation",
        "description": "a zero-value tlsn_attested promotion supersedes an EARLIER hash-bound "
                       "(v6+) displacement receipt; declared veracity carries the overlaid "
                       "by_tier and floor",
        "input": att, "expected": {**exp, "chain_tip": att["chain_head"]},
        "rationale": "Overlay profile (SPEC 3.7): all five invariants hold, so the target's "
                     "value re-tiers to tlsn_attested and the declared floor matches."})

    # adversarial: forward-reference capture — the promotion PRECEDES its target in the scan
    a = copy.deepcopy(att)
    a["links"] = [a["links"][promo_idx], a["links"][target_idx]]
    a["links"][0]["seq"] = 0
    a["links"][1]["seq"] = 1                      # seq is not hash-bound; keep it clean so the
    declared = copy.deepcopy(att["veracity"])     # ONLY fault is the overlay order rule
    a = rechain(a)
    a["veracity"] = declared
    reseal(a)
    exp = verdict_savings(a)
    assert exp["verdict"] == "INVALID", exp
    write("savings/overlay", "promotion_forward_reference", {
        "kind": "savings_attestation",
        "description": "the promotion appears BEFORE the receipt it supersedes; declared "
                       "veracity still claims the promoted floor",
        "input": a, "expected": exp,
        "rationale": "Invariant 3 (PRIOR target): a promotion may re-tier only a receipt seen "
                     "earlier in the scan; a forward reference is refused, so the declared "
                     "(promoted) veracity mismatches the derived one."})

    # adversarial: positive-value promoter — the tlsn link mints its OWN value AND supersedes
    b = copy.deepcopy(att)
    t = b["links"][target_idx]
    pl = b["links"][promo_idx]
    pl["tokens_saved"] = t["tokens_saved"]
    pl["earn_rate"] = t["earn_rate"]
    pl["kry_minted"] = t["kry_minted"]            # legal magnitude, copied from a real link
    b = rechain(b)
    b["veracity"] = {"by_tier": {"tlsn_attested": round(2 * moved, 4)},
                     "anchored_kry": round(2 * moved, 4), "self_reported_kry": 0.0,
                     "veracity_floor": 1.0}       # the forged floor-1.0 double-count claim
    reseal(b)
    exp = verdict_savings(b)
    assert exp["verdict"] == "INVALID", exp
    write("savings/overlay", "promotion_positive_value_promoter", {
        "kind": "savings_attestation",
        "description": "a POSITIVE-value tlsn link also carries supersedes and the declared "
                       "veracity claims the target's value moved on top of its own (floor 1.0)",
        "input": b, "expected": exp,
        "rationale": "Invariant 4 (zero-value promoter): a positive-value link keeps its own "
                     "value only and is NOT a promotion; the declared double-count mismatches."})

    # adversarial: duplicate hash-bound receipt_id (ambiguous overlay lookup)
    c = copy.deepcopy(att)
    c["links"][promo_idx]["receipt_id"] = c["links"][target_idx]["receipt_id"]
    declared = copy.deepcopy(att["veracity"])
    c = rechain(c)
    c["veracity"] = declared
    reseal(c)
    exp = verdict_savings(c)
    assert exp["verdict"] == "INVALID", exp
    assert any("duplicate receipt_id" in r for r in exp["reasons"]), exp
    write("savings/overlay", "promotion_duplicate_receipt_id", {
        "kind": "savings_attestation",
        "description": "two hash-bound (v6+) links share a receipt_id",
        "input": c, "expected": exp,
        "rationale": "Invariant 2 (UNIQUE target): duplicate hash-bound ids make the overlay "
                     "lookup ambiguous and are rejected outright."})

    # adversarial: double promotion claiming a double move
    d = copy.deepcopy(att)
    dup = copy.deepcopy(d["links"][promo_idx])
    dup["ts"] = dup["ts"] + 1.0
    dup["seq"] = len(d["links"])
    d["links"].append(dup)
    d = rechain(d)
    d["veracity"] = {"by_tier": {"tlsn_attested": round(2 * moved, 4)},
                     "anchored_kry": round(2 * moved, 4), "self_reported_kry": 0.0,
                     "veracity_floor": 1.0}       # claims the target moved TWICE
    reseal(d)
    exp = verdict_savings(d)
    assert exp["verdict"] == "INVALID", exp
    write("savings/overlay", "promotion_double_claim", {
        "kind": "savings_attestation",
        "description": "the same promotion appears twice and the declared veracity claims the "
                       "target's value moved twice",
        "input": d, "expected": exp,
        "rationale": "Invariant 5 (CONSUMED ONCE): the target is consumed on first use, so the "
                     "second promotion is a no-op and the declared double move mismatches."})


def gen_action() -> None:
    a0, t0 = act_link(0, GENESIS)
    att = act_att([a0], 0.0)
    write("action/valid", "single_self_reported", {
        "kind": "action_attestation", "description": "one self_reported action; floor 0.0",
        "input": att, "expected": {**verdict_action(att),
                                   "receipt_payload_bytes": canon(act_payload(a0)), "chain_tip": t0},
        "rationale": "Content-free receipt_hash + chain recompute; a pure-T0 log has veracity_floor 0.0."})
    assert verdict_action(att)["verdict"] == "VALID"

    b0, p0 = act_link(0, GENESIS)
    b1, p1 = act_link(1, p0, ts=1_700_000_001.0, tier="server_witnessed",
                      server_evidence_commit=kax.commit("server-resp"))
    att = act_att([b0, b1], 0.5)
    write("action/valid", "chain_two_witnessed", {
        "kind": "action_attestation",
        "description": "self_reported then server_witnessed (witness commit present); floor 0.5",
        "input": att, "expected": {**verdict_action(att), "chain_tip": p1},
        "rationale": "One of two actions is anchored -> veracity_floor 0.5."})
    assert verdict_action(att)["verdict"] == "VALID"

    c0, _ = act_link(0, GENESIS)
    c0 = dict(c0)
    c0["args_commit"] = kax.commit("DIFFERENT-args")
    att = act_att([c0], 0.0)
    write("action/adversarial", "args_tampered", {
        "kind": "action_attestation", "description": "args_commit changed after minting",
        "input": att, "expected": verdict_action(att),
        "rationale": "receipt_hash is over the payload incl. args_commit -> mismatch."})

    d0, _ = act_link(0, GENESIS, tier="attested", server_evidence_commit=None)
    att = act_att([d0], 1.0)
    write("action/adversarial", "forged_tier_no_witness", {
        "kind": "action_attestation",
        "description": "link claims 'attested' with no server_evidence_commit; declares floor 1.0",
        "input": att, "expected": verdict_action(att),
        "rationale": "Forged anchored tier is coerced to self_reported; re-derived floor 0.0 != 1.0."})

    e0, ep0 = act_link(0, GENESIS)
    e1, ep1 = act_link(1, ep0, ts=1_700_000_001.0)
    att = act_att([e1, e0], 0.0)
    att["chain_tip"] = ep1
    write("action/adversarial", "reordered_links", {
        "kind": "action_attestation", "description": "two valid links presented out of order",
        "input": att, "expected": verdict_action(att),
        "rationale": "Re-deriving from genesis, the first link's chain_hash no longer matches."})

    f0, fp0 = act_link(0, GENESIS)
    f1, fp1 = act_link(1, fp0, ts=1_700_000_001.0)
    f1 = dict(f1)
    f1["receipt_id"] = f0["receipt_id"]
    att = act_att([f0, f1], 0.0)
    att["chain_tip"] = fp1
    write("action/adversarial", "duplicate_receipt_id", {
        "kind": "action_attestation", "description": "two links share a receipt_id",
        "input": att, "expected": verdict_action(att),
        "rationale": "Receipt ids must be unique within an attestation."})


def main() -> None:
    for stale in OUT.rglob("*.json"):
        stale.unlink()
    gen_primitives()
    gen_savings()
    gen_overlay()
    gen_action()
    if WORK.exists():
        shutil.rmtree(WORK)
    (OUT / "manifest.json").write_text(json.dumps(
        {"spec": "KRY-SPEC v1.1", "count": len(manifest), "vectors": manifest}, indent=2) + "\n")
    print(f"wrote {len(manifest)} vectors + manifest.json")
    for m in manifest:
        print(f"  {m['category']:22} {m['id']:30} -> {m['verdict']}")


if __name__ == "__main__":
    main()
