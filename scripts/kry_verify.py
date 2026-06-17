#!/usr/bin/env python3
"""KRY external verifier — a STRANGER's independent check (falsifier #1).

This is the executable form of the claim "a third party can verify a KRY
settlement from the public artifacts alone, without trusting the operator's
runtime." To make that claim honest rather than asserted, this file imports
NOTHING from the KRY package — only the Python standard library. If a verifier
needed package code or a live process, external verifiability would be false.

What a stranger can confirm with only:
    - an attestation JSON (kry.kry_attest.Attestation.to_public_json output)
    - optionally, the settlement registry JSONL (kry_data/kry_settlement_registry.jsonl)
    - optionally, an offer (party + KRY amount)

  1. CHAIN INTEGRITY  — chain_hash[i] == SHA256(chain_hash[i-1] : receipt_hash[i])
                        for every link; no receipt inserted/removed/altered.
  2. CONSERVATION     — sum of per-link kry_minted == declared total_kry, and
                        chain_head == the last link's chain_hash.
  3. VERACITY SURFACE — declared veracity_floor matches the per-link tiers
                        (how much of the balance is externally anchored vs the
                        operator's word). See docs/KRY_VERACITY_BINDING.md.
  4. SETTLEMENT       — the registry chain (entry_hash == SHA256(prev:party:amount:grant_ids))
                        is intact, and the offer fits inside
                        attested_balance − already_settled[party] (double-spend).

What a stranger CANNOT learn (by design): any prompt, response, model name, or
cache key — the attestation is content-sealed.

Honest ceiling: this proves INTEGRITY + CONSERVATION + the declared trust surface.
It does NOT prove VERACITY (that the underlying efficiency events happened) — that
is the veracity_floor's job to disclose, not this script's to certify.

Usage:
    python3 scripts/kry_verify.py attestation.json
    python3 scripts/kry_verify.py attestation.json --registry kry_data/kry_settlement_registry.jsonl --party A --offer 5000
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import math
import sys

_GENESIS = "0" * 64

# ── Public reference constants (F2: publicly-checkable magnitude) ─────────────
# These MIRROR the published kry package constants (kry_mint._EARN_RATES, the
# per-model $/M price table in kry_token, the frontier baseline) as of
# _PRICE_AS_OF. A stranger copies them from the public repo — that is why a
# standalone verifier can carry them. A drift-guard test
# (tests/test_external_verify.py) asserts they still match the package source,
# so this copy can never silently diverge.
_PRICE_AS_OF = "2026-06-03"
_FRONTIER_USD_PER_M = 25.0
_EARN_RATES = {
    "cache_hit": 1.0, "l3_semantic_match": 0.8, "short_circuit": 1.0,
    "compression": 0.6, "feed_bag_deposit": 0.7, "continuity_capsule": 0.1,
    "cache_creation": 0.0,
}
_MODEL_USD_PER_M = {
    "opus": 25.0, "sonnet": 7.5, "haiku": 1.25, "gpt-5": 10.0,
    "gpt-4o-mini": 0.60, "gpt-4o": 10.0,  # OpenAI list prices (mini before gpt-4o for substring match)
    "deepseek-v4-pro": 1.10, "deepseek": 0.55, "qwen": 1.25, "gemini": 0.0,
}


def legal_multipliers() -> set[float]:
    """The set of value multipliers a magnitude may legally use: each model's
    $/M ÷ frontier, plus 0.0 (free), 0.05 (unknown floor), 1.0 (legacy None), AND
    every non-negative pairwise DIFFERENCE of those. A cheaper-PAID displacement
    (e.g. OpenRouter serving instead of a frontier) saves only the price
    DIFFERENCE — vm(avoided) - vm(served) — which is still publicly-checkable
    arithmetic over the same public price table, so it is a legal magnitude."""
    base = {min(1.0, p / _FRONTIER_USD_PER_M) for p in _MODEL_USD_PER_M.values()}
    base |= {0.0, 0.05, 1.0}
    diffs = {round(a - b, 6) for a in base for b in base if a - b > 0}
    return base | diffs


def _sha(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def _v4_public_block(link: dict) -> str:
    """Standalone REPLICA of kry_mint._v4_public_block — it MUST serialize byte-for-byte identically
    (pinned by test_external_verify, which verifies a real attestation through THIS stdlib verifier).
    v4 binds the public economic block into chain_hash so a forged tier / kry_minted / earn_rate /
    token count breaks the chain on the public surface here too, not just in the un-recomputable
    private receipt_hash."""
    return json.dumps({
        "hash_version": link.get("hash_version", 1),
        "tokens_saved": link.get("tokens_saved", 0.0),
        "ts": link.get("ts"),
        "evidence_tier": link.get("evidence_tier", "self_reported"),
        "metered_tokens": link.get("metered_tokens"),
        "kry_minted": link.get("kry_minted"),
        "earn_rate": link.get("earn_rate", 0.0),
    }, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _reject_json_constant(value: str):
    raise ValueError(f"non-standard JSON constant rejected: {value}")


def _json_load(f):
    return json.load(f, parse_constant=_reject_json_constant)


def _json_loads(text: str):
    return json.loads(text, parse_constant=_reject_json_constant)


def _json_dumps(data: object, **kwargs) -> str:
    kwargs.setdefault("allow_nan", False)
    return json.dumps(data, **kwargs)


def _json_clean(data: object) -> object:
    return json.loads(_json_dumps(data))


def _finite_number(value, field: str, *, positive: bool = False,
                   nonnegative: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be a finite JSON number")
    value = float(value)
    if not math.isfinite(value):
        raise ValueError(f"{field} must be finite")
    if positive and value <= 0:
        raise ValueError(f"{field} must be positive")
    if nonnegative and value < 0:
        raise ValueError(f"{field} must be non-negative")
    return value


def _json_integer(value, field: str, *, nonnegative: bool = False) -> int:
    value = _finite_number(value, field, nonnegative=nonnegative)
    if not value.is_integer():
        raise ValueError(f"{field} must be a JSON integer")
    return int(value)


def _required_string(value, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} must be a non-empty string")
    return value


def _attestation_hash(data: dict) -> str:
    """Canonical hash of an attestation with its hash field blanked."""
    canonical = _json_clean(data)
    canonical["attestation_hash"] = ""
    return hashlib.sha256(
        _json_dumps(canonical, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _magnitude_errors(link: dict) -> list[str]:
    """F2: recompute magnitude from public data. A receipt that exposes its
    inputs (tokens_saved, earn_rate) must satisfy kry = tokens × rate × M where
    rate is the published EARN_RATES value and M is a published multiplier.
    Legacy/pre-F2 links (no inputs exposed) are skipped — honestly uncheckable."""
    errors: list[str] = []
    try:
        kry_minted = _finite_number(link.get("kry_minted"),
                                    f"seq {link.get('seq')}: kry_minted",
                                    nonnegative=True)
        ts = _finite_number(link.get("tokens_saved", 0.0),
                            f"seq {link.get('seq')}: tokens_saved",
                            nonnegative=True)
        rate = _finite_number(link.get("earn_rate", 0.0),
                              f"seq {link.get('seq')}: earn_rate",
                              nonnegative=True)
    except ValueError as exc:
        return [str(exc)]
    # A link that DECLARES its inputs cannot mint positive KRY from zero tokens/rate
    # (0 × 0 × M = 0) — a positive kry_minted is fabricated (zero-rate magnitude bypass).
    # Only a genuine legacy link that OMITS the inputs is honestly uncheckable.
    declares_inputs = "earn_rate" in link and "tokens_saved" in link
    if ts <= 0 or rate <= 0:
        if declares_inputs and kry_minted > 0:
            return [f"seq {link.get('seq')}: kry_minted {kry_minted} with tokens_saved={ts} "
                    f"/ earn_rate={rate} — magnitude not derivable from declared inputs"]
        return []
    et = link.get("event_type", "")
    # F3: an UNKNOWN event_type must still use mint's 0.5 fallback rate — reject an arbitrary rate
    # paired with an off-table event_type instead of silently skipping the check.
    pub_rate = _EARN_RATES.get(et, 0.5)
    if abs(rate - pub_rate) > 1e-6:
        errors.append(
            f"seq {link.get('seq')}: earn_rate {rate} != published {pub_rate} "
            f"for '{et}' — non-standard rate")
    implied = kry_minted / (ts * rate)
    if not any(abs(implied - m) <= 1e-3 for m in legal_multipliers()):
        errors.append(
            f"seq {link.get('seq')}: implied price multiplier {implied:.4f} is "
            f"not a published value — magnitude used a non-public price")
    return errors


def _tier_schema_errors(link: dict) -> list[str]:
    """T1 public links must expose provider token counts used for reconciliation."""
    if link.get("evidence_tier", "self_reported") != "provider_metered":
        return []
    ts = link.get("ts")
    try:
        _finite_number(ts, "ts", nonnegative=True)
    except ValueError:
        return [f"seq {link.get('seq')}: provider_metered link missing numeric ts"]
    metered = link.get("metered_tokens")
    if not isinstance(metered, list) or len(metered) != 2:
        return [f"seq {link.get('seq')}: provider_metered link missing metered_tokens"]
    if not all(isinstance(v, int) and not isinstance(v, bool) for v in metered):
        return [f"seq {link.get('seq')}: provider_metered metered_tokens must be integers"]
    p, c = metered
    if p < 0 or c < 0:
        return [f"seq {link.get('seq')}: provider_metered metered_tokens must be non-negative"]
    return []


def verify_attestation(attestation: dict) -> tuple[bool, list[str]]:
    """Re-derive the proof chain from the public links alone."""
    errors: list[str] = []
    if not isinstance(attestation, dict):
        return False, ["attestation must be a JSON object"]
    links = attestation.get("links", [])
    if not isinstance(links, list):
        errors.append("links must be a JSON list")
        links = []
    try:
        receipts = _json_integer(attestation.get("receipts"), "receipts", nonnegative=True)
    except ValueError as exc:
        errors.append(str(exc))
        receipts = None
    if receipts != len(links):
        errors.append(
            f"receipts mismatch: declared {attestation.get('receipts')}, "
            f"links contain {len(links)}")
    if attestation.get("chain_valid") is not True:
        errors.append("chain_valid is not true")

    prev = _GENESIS
    prev_version = 0
    running_kry = 0.0
    tier_kry: dict[str, float] = {}
    type_counts: Counter = Counter()
    for link in links:
        if not isinstance(link, dict):
            errors.append("link must be a JSON object")
            continue
        seq = link.get("seq")
        try:
            _json_integer(seq, f"seq {seq}", nonnegative=True)
            receipt_hash = _required_string(link.get("receipt_hash"), f"seq {seq}: receipt_hash")
            chain_hash = _required_string(link.get("chain_hash"), f"seq {seq}: chain_hash")
            event_type = _required_string(link.get("event_type"), f"seq {seq}: event_type")
            kry_minted = _finite_number(link.get("kry_minted"), f"seq {seq}: kry_minted",
                                        nonnegative=True)
        except ValueError as exc:
            errors.append(str(exc))
            continue
        # v4 binds the public economic block into chain_hash (forged tier/payout/rate breaks the chain
        # on the public surface); legacy (<4) links use the prev:receipt formula.
        hv = link.get("hash_version", 1)
        if isinstance(hv, bool) or not isinstance(hv, int):
            hv = 1
        # Monotonic version: a v4 link can't be followed by a legacy one (partial-tail downgrade).
        if hv < prev_version:
            errors.append(f"seq {seq}: hash_version {hv} < previous {prev_version} — "
                          f"version downgrade (partial-tail rollback attempt)")
        prev_version = max(prev_version, hv)
        if hv >= 4:
            try:
                block = _v4_public_block(link)
            except (ValueError, TypeError) as exc:   # e.g. a NaN in a v4 block field (dict input)
                errors.append(f"seq {seq}: v4 block field not serializable: {exc}")
                prev = chain_hash
                continue
            expected = _sha(f"{prev}:{receipt_hash}:{block}")
        else:
            expected = _sha(f"{prev}:{receipt_hash}")
        if chain_hash != expected:
            errors.append(
                f"seq {seq}: chain link broken — "
                f"receipt inserted/removed/altered")
        running_kry += kry_minted
        type_counts[event_type] += 1
        tier = link.get("evidence_tier", "self_reported")
        if not isinstance(tier, str):
            errors.append(f"seq {seq}: evidence_tier must be a string")
            tier = "self_reported"
        # The tier is only bound on the PUBLIC surface at v4 (the v4 block above). A pre-v4
        # link claiming a non-self_reported tier is operator-asserted, not chain-bound, so it
        # must not inflate the anchored fraction of the veracity floor — reject and coerce.
        if hv < 4 and tier != "self_reported":
            errors.append(f"seq {seq}: hash_version {hv} cannot carry a non-self_reported "
                          f"tier ({tier}) — unbound on the public surface (only v4+ binds it)")
            tier = "self_reported"
        tier_kry[tier] = tier_kry.get(tier, 0.0) + kry_minted
        errors.extend(_magnitude_errors(link))   # F2: magnitude is public arithmetic
        errors.extend(_tier_schema_errors(link))
        prev = chain_hash

    # Conservation: the declared aggregate must equal the chain sum.
    try:
        total_kry = _finite_number(attestation.get("total_kry", 0.0), "total_kry",
                                   nonnegative=True)
    except ValueError as exc:
        errors.append(str(exc))
        total_kry = 0.0
    if abs(running_kry - total_kry) > 0.01:
        errors.append(
            f"total_kry mismatch: declared {attestation.get('total_kry')}, "
            f"chain sums to {running_kry:.4f}")

    # Head anchor must match the last link.
    if links and attestation.get("chain_head") != links[-1].get("chain_hash"):
        errors.append("chain_head does not match last link")
    if not isinstance(attestation.get("event_type_counts"), dict):
        errors.append("event_type_counts must be a JSON object")
    elif attestation.get("event_type_counts") != dict(type_counts):
        errors.append(
            f"event_type_counts mismatch: declared {attestation.get('event_type_counts')}, "
            f"links imply {dict(type_counts)}")
    expected_usd = round(running_kry * (_FRONTIER_USD_PER_M / 1_000_000), 6)
    try:
        usd_equivalent = _finite_number(attestation.get("usd_equivalent", 0.0),
                                        "usd_equivalent", nonnegative=True)
    except ValueError as exc:
        errors.append(str(exc))
        usd_equivalent = 0.0
    if abs(usd_equivalent - expected_usd) > 1e-6:
        errors.append(
            f"usd_equivalent mismatch: declared {attestation.get('usd_equivalent')}, "
            f"links imply {expected_usd}")
    claimed_hash = attestation.get("attestation_hash")
    if not isinstance(claimed_hash, str) or not claimed_hash:
        errors.append("attestation_hash missing")
    else:
        try:
            expected_hash = _attestation_hash(attestation)
        except ValueError as exc:
            errors.append(f"attestation JSON is not standards-compliant: {exc}")
        else:
            if claimed_hash != expected_hash:
                errors.append("attestation_hash mismatch — public metadata may have been altered")

    # Trust surface must be honest: declared floor must match the per-link tiers.
    v = attestation.get("veracity")
    if isinstance(v, dict) and v.get("by_tier") is not None:
        anchored = sum(val for t, val in tier_kry.items() if t != "self_reported")
        derived = (anchored / running_kry) if running_kry > 0 else 0.0
        by_tier = {t: round(val, 4) for t, val in tier_kry.items()}
        claimed_by_tier = v.get("by_tier")
        if not isinstance(claimed_by_tier, dict):
            errors.append("veracity.by_tier must be a JSON object")
            claimed_by_tier = {}
        else:
            for tier, value in claimed_by_tier.items():
                if not isinstance(tier, str):
                    errors.append("veracity.by_tier keys must be strings")
                    continue
                try:
                    _finite_number(value, f"veracity.by_tier.{tier}", nonnegative=True)
                except ValueError as exc:
                    errors.append(str(exc))
        if claimed_by_tier != by_tier:
            errors.append(
                f"veracity by_tier mismatch: declared {v.get('by_tier')}, "
                f"links imply {by_tier}")
        try:
            externally_anchored = _finite_number(
                v.get("externally_anchored_kry", 0.0),
                "veracity.externally_anchored_kry",
                nonnegative=True,
            )
        except ValueError as exc:
            errors.append(str(exc))
            externally_anchored = 0.0
        try:
            self_reported = _finite_number(v.get("self_reported_kry", 0.0),
                                           "veracity.self_reported_kry",
                                           nonnegative=True)
        except ValueError as exc:
            errors.append(str(exc))
            self_reported = 0.0
        try:
            veracity_floor = _finite_number(v.get("veracity_floor", 0.0),
                                            "veracity.veracity_floor",
                                            nonnegative=True)
        except ValueError as exc:
            errors.append(str(exc))
            veracity_floor = 0.0
        if abs(externally_anchored - round(anchored, 4)) > 0.01:
            errors.append("externally_anchored_kry mismatch")
        if abs(self_reported - round(tier_kry.get("self_reported", 0.0), 4)) > 0.01:
            errors.append("self_reported_kry mismatch")
        if abs(veracity_floor - derived) > 0.01:
            errors.append(
                f"veracity_floor mismatch: declared {v.get('veracity_floor')}, "
                f"links imply {derived:.4f} — trust surface misstated")
    elif v is not None:
        errors.append("veracity must be a JSON object")

    return len(errors) == 0, errors


def verify_registry(entries: list[dict]) -> tuple[bool, list[str]]:
    """Replay the settlement registry chain; any edit breaks a hash."""
    errors: list[str] = []
    prev = _GENESIS
    for i, e in enumerate(entries, 1):
        if not isinstance(e, dict):
            errors.append(f"entry {i}: registry entry must be a JSON object")
            continue
        try:
            party = _required_string(e.get("party"), f"entry {i}: party")
            amount = _finite_number(e.get("amount"), f"entry {i}: amount", positive=True)
            entry_hash = _required_string(e.get("entry_hash"), f"entry {i}: entry_hash")
        except ValueError as exc:
            errors.append(str(exc))
            continue
        if len(entry_hash) != 64 or any(ch not in "0123456789abcdef" for ch in entry_hash):
            errors.append(f"entry {i}: entry_hash must be 64 lowercase hex characters")
            continue
        prev_hash = e.get("prev_hash")
        if prev_hash is not None and prev_hash != prev:
            errors.append(f"entry {i} ({party}): prev_hash does not match previous entry")
        # Grant-id component: individual settlements bind their grant_id; compaction
        # checkpoints bind the sorted union of collapsed ids (the no-double-settle guard).
        try:
            if "grant_ids" in e:
                grant_payload = ",".join(sorted(e.get("grant_ids") or ()))
            else:
                grant_payload = e.get("grant_id") or ""
        except TypeError:
            errors.append(f"entry {i} ({party}): malformed grant ids — tampered")
            prev = entry_hash
            continue
        expected = _sha(f"{prev}:{party}:{amount}:{grant_payload}")
        if entry_hash != expected:
            errors.append(f"entry {i} ({party}): hash broken — tampered")
        prev = entry_hash
    return len(errors) == 0, errors


def settled_by_party(entries: list[dict]) -> dict[str, float]:
    """Cumulative {party: settled_kry} replayed from the registry."""
    totals: dict[str, float] = {}
    for e in entries:
        party = e["party"]
        amount = float(e["amount"])
        totals[party] = totals.get(party, 0.0) + amount
    return totals


def verify_settlement(
    attestation: dict,
    registry_entries: list[dict],
    party: str,
    offer_amount: float,
) -> tuple[bool, list[str]]:
    """Full end-to-end stranger check: attestation valid + registry intact +
    the offer fits inside the attested balance net of what the party already
    settled (double-spend guard).

    NOTE (HOLE D corollary): this registry check is post-facto and snapshot-based
    — it is sound only against the COMPLETE, MERGED federation registry. It cannot
    catch two nodes concurrently settling the same balance against unmerged
    registries; real-time atomic prevention is per-process. See
    docs/KRY_VERACITY_BINDING.md."""
    ok_att, errs = verify_attestation(attestation)
    errors = list(errs)
    try:
        party = _required_string(party, "settlement party")
        offer_amount = _finite_number(offer_amount, "offer amount", positive=True)
    except ValueError as exc:
        errors.append(str(exc))
    ok_reg, reg_errs = verify_registry(registry_entries)
    errors += reg_errs
    if ok_att and ok_reg and not errors:
        attested = attestation.get("total_kry", 0.0)
        already = settled_by_party(registry_entries).get(party, 0.0)
        available = attested - already
        if offer_amount > available + 0.01:
            errors.append(
                f"double-spend/overclaim: offer {offer_amount:.0f} > available "
                f"{available:.0f} (attested {attested:.0f} − settled {already:.0f})")
    return len(errors) == 0, errors


def _read_registry(path: str) -> list[dict]:
    entries: list[dict] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                entries.append(_json_loads(line))
    return entries


def verify_attestation_against_anchor(attestation: dict, anchor: dict) -> tuple[bool, list[str]]:
    """A stranger's RE-MINT check. verify_attestation proves the chain is internally consistent;
    it cannot tell an honest chain from one the operator re-derived from genesis. A chain-head
    anchor {count, tip} the operator PUBLISHED externally closes that gap: the attestation's link
    at seq==count must still have chain_hash==tip. A re-mint of any receipt <= count changes that
    hash. Only meaningful if the anchor came from the operator's external publication, obtained
    out-of-band — an anchor handed over at verify time proves nothing."""
    if not isinstance(anchor, dict) or anchor.get("schema") != "kry_chain_anchor/v1":
        return False, ["anchor must be a kry_chain_anchor/v1 object"]
    count, tip = anchor.get("count"), anchor.get("tip")
    if isinstance(count, bool) or not isinstance(count, int) or count < 0:
        return False, ["anchor.count must be a non-negative integer"]
    if not isinstance(tip, str) or len(tip) != 64:
        return False, ["anchor.tip must be a 64-char hex chain hash"]
    if count == 0:
        return (tip == _GENESIS), ([] if tip == _GENESIS else ["anchor.count 0 but tip is not genesis"])
    links = attestation.get("links")
    if not isinstance(links, list):
        return False, ["attestation has no links to check against the anchor"]
    match = next((ln for ln in links if isinstance(ln, dict) and ln.get("seq") == count), None)
    if match is None:
        return False, [f"attestation has no link at seq {count} — chain shorter than the published "
                       f"anchor (rollback/re-mint/truncation)"]
    if match.get("chain_hash") != tip:
        return False, [f"chain hash at seq {count} does not match the published anchor — "
                       f"retroactive re-mint detected"]
    return True, []


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="KRY external verifier (stdlib-only)")
    p.add_argument("attestation", help="path to attestation JSON")
    p.add_argument("--registry", help="path to settlement registry JSONL")
    p.add_argument("--party", help="settling party (with --offer)")
    p.add_argument("--offer", type=float, help="offered KRY amount to check")
    p.add_argument("--anchor", help="path to a PUBLISHED kry_chain_anchor JSON; checks the "
                                    "attestation's chain still carries the anchored prefix (re-mint check)")
    args = p.parse_args(argv)

    try:
        with open(args.attestation, encoding="utf-8") as f:
            att = _json_load(f)
    except Exception as exc:
        print("KRY external verification — attestation")
        print("  VERDICT: INVALID")
        print(f"    - attestation unreadable: {exc}")
        return 1
    if not isinstance(att, dict):
        print("KRY external verification — attestation")
        print("  VERDICT: INVALID")
        print("    - attestation JSON must be an object")
        return 1

    if args.registry and args.party and args.offer is not None:
        scope = f"settlement: {args.party} offering {args.offer:.0f} KRY"
        try:
            entries = _read_registry(args.registry)
        except Exception as exc:
            ok, errors = False, [f"registry unreadable: {exc}"]
        else:
            ok, errors = verify_settlement(att, entries, args.party, args.offer)
    else:
        ok, errors = verify_attestation(att)
        scope = "attestation"

    anchor_line = None
    if args.anchor:
        try:
            with open(args.anchor, encoding="utf-8") as f:
                anchor = _json_load(f)
            a_ok, a_errs = verify_attestation_against_anchor(att, anchor)
        except Exception as exc:
            a_ok, a_errs = False, [f"anchor unreadable: {exc}"]
        ok = ok and a_ok
        errors = errors + [f"anchor: {e}" for e in a_errs]
        anchor_line = ("PASS — chain still carries the published anchor prefix (no re-mint)"
                       if a_ok else "FAIL — re-mint/rollback vs the published anchor")

    v = att.get("veracity", {})
    # Display the verifier's OWN recomputed figures, not the operator-declared `receipts`/
    # `total_kry` fields — so a reader never mistakes an echoed claim for a verified number.
    _links = att.get("links") if isinstance(att.get("links"), list) else []
    _recomputed_total = sum(
        ln["kry_minted"] for ln in _links
        if isinstance(ln, dict) and isinstance(ln.get("kry_minted"), (int, float))
        and not isinstance(ln.get("kry_minted"), bool))
    print(f"KRY external verification — {scope}")
    print(f"  receipts:        {len(_links)} (recomputed from links)")
    print(f"  total_kry:       {round(_recomputed_total, 4)} (recomputed from links, not the declared field)")
    print(f"  veracity_floor:  {v.get('veracity_floor', 0.0)} "
          f"(fraction externally anchored; rest rests on operator self-report)")
    print(f"  price basis:     ${_FRONTIER_USD_PER_M}/M frontier, as of {_PRICE_AS_OF} "
          f"(magnitude recomputed from the public price table)")
    if anchor_line:
        print(f"  anchor check:    {anchor_line}")
    if ok:
        print("  VERDICT: VALID — integrity + conservation + magnitude (where checkable) hold; "
              "trust surface honest (read veracity_floor for what is operator-asserted).")
    else:
        print("  VERDICT: INVALID")
        for e in errors:
            print(f"    - {e}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
