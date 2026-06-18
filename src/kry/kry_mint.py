"""KRY Mint — cryptographically anchored token minting protocol.

Every KRY earn event produces a MintReceipt: a hash-chain anchored record
that proves WHICH efficiency event occurred, WHEN, and HOW MANY tokens it
saved. This makes KRY a proof-of-efficiency token — the earning is verifiable
by any auditor with access to the hash chain.

Architecture:
    Efficiency event (cache hit, compression, etc.)
        ↓
    MintReceipt(event_type, tokens_saved, timestamp, evidence_hash)
        ↓
    SHA-256(receipt) → anchored to the standalone KRY mint chain
        ↓
    KRYLedger.earn() records balance delta
        ↓
    External verifier can audit: "this KRY was minted from real efficiency"

External market protocol:
    A provider wanting to accept KRY asks: "prove this KRY came from real
    efficiency." The answer is the mint receipt chain — a sequence of
    hash-anchored events showing EXACTLY which cache hits, compressions,
    and short-circuits generated the balance.

This is fundamentally different from:
    - OpenRouter credits (no proof of origin — just USD deposit)
    - Bittensor TAO (proof of work / hardware provision, not inference efficiency)
    - GPU cloud credits (proof of purchase, not proof of efficiency)
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import struct
import tempfile
import threading
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional


def _kry_data_dir() -> Path:
    """Portable data dir. Set KRY_DATA_DIR to relocate; defaults to ./kry_data."""
    d = Path(os.environ.get("KRY_DATA_DIR", "kry_data")).expanduser()
    d.mkdir(parents=True, exist_ok=True)
    return d

_MINT_LOG_PATH = _kry_data_dir() / "kry_mint_log.jsonl"
_MINT_LOCK = threading.Lock()

# ── Earning rates (mirrored from kry_token for independence) ─────────────────
_EARN_RATES = {
    "cache_hit":         1.0,
    "l3_semantic_match": 0.8,
    "short_circuit":     1.0,
    "compression":       0.6,
    "feed_bag_deposit":  0.7,
    "cache_creation":    0.0,   # COST, not saving: a cache write is a 1.25x premium/bet.
                                # The realized saving is the later cache_hit (1.0); crediting
                                # creation too = double-count. (own-data audit 2026-06-03)
    "continuity_capsule": 0.1,
}

# ── Veracity tiers — how an efficiency event was WITNESSED (weakest→strongest) ─
# The hash chain proves INTEGRITY (no post-hoc tampering + conservation). It does
# NOT prove VERACITY (the event actually happened) — an operator can author a
# conserved chain of fabricated receipts. This field classifies the TRUST SOURCE
# so an external verifier sees exactly what fraction of a balance rests on
# operator self-report alone vs an external anchor. See docs/KRY_VERACITY_BINDING.md.
TIER_SELF_REPORTED    = "self_reported"     # T0: runtime asserts the saving (cache hits live here permanently)
TIER_HOLDOUT_VALIDATED = "holdout_validated" # T1*: counterfactual MEASURED by a randomized holdout with
                                            #      retained provider receipts (a population estimate valued at
                                            #      the CI lower bound) — stronger than self-report, weaker than
                                            #      per-event metering. The honest middle tier for cache hits.
                                            #      See kry_baseline.py + docs/KRY_COUNTERFACTUAL_HOLDOUT.md.
TIER_PROVIDER_METERED = "provider_metered"  # T1: backed by a retained real provider usage payload (a call that DID happen)
TIER_TEE_ATTESTED     = "tee_attested"      # T2: measured inside a TEE / hardware-signed (slot only — not yet built)
TIER_TLSN_ATTESTED    = "tlsn_attested"     # T2: the provider's response is cryptographically PROVEN via a TLS-notary
                                            #     signature — a real provider call, notarized, so the operator cannot
                                            #     fabricate the bytes. Strictly stronger than provider_metered (which
                                            #     trusts the operator RETAINED a real usage payload). Witnesses a call
                                            #     that HAPPENED (displacement's cheap leg), NOT a cache-hit counterfactual
                                            #     (no provider footprint to notarize — that stays TEE-only). Minted via
                                            #     scripts/kry_tlsn_verify.py. See docs/KRY_T2_FINDINGS_REPORT.md.
_VALID_TIERS    = {TIER_SELF_REPORTED, TIER_HOLDOUT_VALIDATED, TIER_PROVIDER_METERED,
                   TIER_TEE_ATTESTED, TIER_TLSN_ATTESTED}
# Anchored = NOT resting on operator self-report alone. holdout_validated qualifies:
# its rate is measured by a randomized holdout with real provider receipts (the
# anchor), though it is a population estimate, not a per-event witness — hence it is
# reported on its own tier line so a verifier sees exactly how much rests on it.
_ANCHORED_TIERS = {TIER_HOLDOUT_VALIDATED, TIER_PROVIDER_METERED,
                   TIER_TEE_ATTESTED, TIER_TLSN_ATTESTED}


def _reject_json_constant(value: str):
    raise ValueError(f"non-standard JSON constant rejected: {value}")


def _json_loads(raw: str):
    return json.loads(raw, parse_constant=_reject_json_constant)


def _json_dumps(value, **kwargs) -> str:
    return json.dumps(value, allow_nan=False, **kwargs)


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


def _metered_pair(value) -> list[int]:
    if not isinstance(value, list) or len(value) != 2:
        raise ValueError("metered_tokens must be [prompt, completion]")
    if not all(isinstance(v, int) and not isinstance(v, bool) for v in value):
        raise ValueError("metered_tokens must be integers")
    prompt, completion = value
    if prompt < 0 or completion < 0:
        raise ValueError("metered_tokens must be non-negative")
    return [prompt, completion]


# ── MintReceipt dataclass ─────────────────────────────────────────────────────

@dataclass
class MintReceipt:
    """Cryptographically anchored proof of KRY minting.

    receipt_hash = SHA-256(event_type + tokens_saved + ts + evidence_hash)
    chain_hash   = SHA-256(previous_chain_hash + receipt_hash)

    These two fields make the mint log an append-only hash chain — any
    tampering breaks the chain and is detectable by an external verifier.
    """
    receipt_id:     str       # sequential identifier
    event_type:     str       # what efficiency event generated this KRY
    tokens_saved:   float     # raw tokens the event saved (compute basis — carbon)
    kry_minted:     float     # KRY = tokens_saved * earn_rate * value_multiplier (dollar basis)
    earn_rate:      float     # earn rate applied (from _EARN_RATES)
    ts:             float     # unix timestamp
    detail:         str       # human-readable provenance
    evidence_hash:  str       # SHA-256 of the efficiency evidence (e.g. cached response hash)
    receipt_hash:   str       # SHA-256 of this receipt's content fields
    chain_hash:     str       # running chain hash (SHA-256 of prev_chain + receipt_hash)
    usd_equivalent: float     # edge-weighted KRY × $0.000025 (honest avoided dollars)
    avoided_model:  str | None = None   # model the event avoided — edge-weights kry_minted
    evidence_tier:  str = TIER_SELF_REPORTED  # how the event was witnessed (veracity, not integrity)
    hash_version:   int = 1   # receipt_hash format: 1=legacy, 2=+tier, 3=+metered_tokens
    metered_tokens: list | None = None  # F1: [prompt, completion] from a real provider usage payload
                                        # (T1 only) — what an auditor reconciles against the provider's own log
    supersedes:     str | None = None   # T2 tier-promotion: the receipt_id of the prior T1 receipt this
                                        # attestation UPGRADES in place (a zero-value promotion carries this;
                                        # the breakdown re-tiers the superseded receipt's value, never re-mints
                                        # it). Not part of the content hash — provenance, not value.

    @classmethod
    def create(
        cls,
        receipt_id: str,
        event_type: str,
        tokens_saved: float,
        detail: str,
        evidence_hash: str,
        previous_chain_hash: str,
        avoided_model: str | None = None,
        evidence_tier: str = TIER_SELF_REPORTED,
        metered_tokens: list | None = None,
        served_model: str | None = None,
        supersedes: str | None = None,
    ) -> "MintReceipt":
        tokens_saved = _finite_number(tokens_saved, "tokens_saved", nonnegative=True)
        rate = _finite_number(_EARN_RATES.get(event_type, 0.5), "earn_rate",
                              nonnegative=True)
        # Edge-weight by the avoided model so kry_minted == the ledger earn (and so
        # reconcile_ledger_from_chain, which rebuilds balance from Σ kry_minted, does
        # NOT re-inflate free-tier mints back to full value). Free tier → 0 KRY.
        # served_model nets a paid server's own cost out of the credit (a cheaper-
        # PAID displacement saves only the price difference); free/None → no netting.
        # Mirrors kry_token.earn so the receipt's kry_minted stays == the ledger earn.
        # tokens_saved stays RAW — it is the compute/energy basis carbon reads.
        from kry.kry_token import net_value_multiplier
        multiplier = _finite_number(net_value_multiplier(avoided_model, served_model),
                                    "value_multiplier", nonnegative=True)
        kry = _finite_number(tokens_saved * rate * multiplier, "kry_minted",
                             nonnegative=True)
        ts = _finite_number(time.time(), "ts", positive=True)
        usd_equivalent = _finite_number(kry * 0.000025, "usd_equivalent",
                                        nonnegative=True)
        tier = evidence_tier if evidence_tier in _VALID_TIERS else TIER_SELF_REPORTED
        if tier == TIER_PROVIDER_METERED:
            metered_tokens = _metered_pair(metered_tokens)
        else:
            metered_tokens = None

        # v3 binds the veracity tier and provider-metered token counts into the
        # receipt hash. Legacy v1/v2 receipts still verify under verify_chain's
        # version dispatch.
        metered_for_hash = _json_dumps(metered_tokens, sort_keys=True, separators=(",", ":"))
        content = f"{event_type}:{tokens_saved}:{ts}:{evidence_hash}:{tier}:{metered_for_hash}"
        receipt_hash = hashlib.sha256(content.encode()).hexdigest()
        # v4 ALSO binds the PUBLIC economic block into chain_hash, so a forged tier / kry_minted /
        # earn_rate / token count breaks the chain on the PUBLIC attestation surface (where the
        # receipt_hash preimage can't be re-derived — evidence_hash is sealed). receipt_hash itself
        # is unchanged (still privately binds tier via evidence_hash) — defence in depth.
        public_block = _v4_public_block(
            hash_version=5, tokens_saved=tokens_saved, ts=ts,
            evidence_tier=tier, metered_tokens=metered_tokens, kry_minted=kry, earn_rate=rate,
            supersedes=supersedes)
        chain_hash   = hashlib.sha256(
            f"{previous_chain_hash}:{receipt_hash}:{public_block}".encode()
        ).hexdigest()

        return cls(
            receipt_id=receipt_id,
            event_type=event_type,
            tokens_saved=tokens_saved,
            kry_minted=kry,
            earn_rate=rate,
            ts=ts,
            detail=detail,
            evidence_hash=evidence_hash,
            receipt_hash=receipt_hash,
            chain_hash=chain_hash,
            usd_equivalent=usd_equivalent,
            avoided_model=avoided_model,
            evidence_tier=tier,
            hash_version=5,
            metered_tokens=metered_tokens,
            supersedes=supersedes,
        )


# ── Mint log state ─────────────────────────────────────────────────────────────

_RECEIPT_COUNTER = 0
_CHAIN_TIP = "0" * 64   # genesis hash (all zeros)
_COUNTER_LOCK = threading.Lock()


def _load_chain_tip() -> tuple[int, str]:
    """Load last receipt counter and chain tip from mint log."""
    if not _MINT_LOG_PATH.exists():
        return 0, "0" * 64
    last_count, last_hash = 0, "0" * 64
    with open(_MINT_LOG_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = _json_loads(line)
            rid = rec.get("receipt_id", "KRY-00000000")
            try:
                last_count = int(rid.split("-")[1])
            except (IndexError, ValueError):
                pass
            last_hash = rec.get("chain_hash", last_hash)
    return last_count, last_hash


def _initialise_from_log() -> None:
    global _RECEIPT_COUNTER, _CHAIN_TIP
    with _COUNTER_LOCK:
        count, tip = _load_chain_tip()
        _RECEIPT_COUNTER = count
        _CHAIN_TIP = tip


# Load state once at module import
try:
    _initialise_from_log()
except Exception:
    pass


# ── Supply control: per-evidence decay (anti-phantom-minting) ─────────────────
#
# A supply-side falsifier showed replaying ONE cached response 1000× minted
# 1000× phantom KRY. But naive dedup is wrong: genuine recurring use (a cron
# asking the same question 144×/day) DID avoid real calls. The counterfactual
# (would the call have happened?) is unobservable, so we bound it honestly:
# full credit on first avoidance of an evidence_hash, geometrically decaying
# credit on repeats within a window. Total from infinite replays of ONE
# evidence converges to tokens/(1-decay) — a finite cap, not 1000×.
#
#   decay=0.5 → replays of one evidence sum to ≤ 2× a single mint (not 1000×)
#   distinct evidence_hashes each get full credit (genuine distinct savings)

try:
    _DECAY = _finite_number(float(os.environ.get("KRY_MINT_DECAY", "0.5")),
                            "KRY_MINT_DECAY", nonnegative=True)
    # Replay decay MUST be in [0, 1): factor = _DECAY**count. At 1 it never decays (replay
    # protection disabled); above 1 it AMPLIFIES repeated evidence (phantom-minting). Reject
    # out-of-range config and fall back to the safe default rather than honour a hostile env.
    if not (0.0 <= _DECAY < 1.0):
        raise ValueError("KRY_MINT_DECAY must be in [0, 1)")
except ValueError:
    _DECAY = 0.5
try:
    _DECAY_WINDOW = _finite_number(
        float(os.environ.get("KRY_MINT_DECAY_WINDOW", "86400")),
        "KRY_MINT_DECAY_WINDOW",
        positive=True,
    )
except ValueError:
    _DECAY_WINDOW = 86400.0  # 24h
_DECAY_STATE_PATH = _kry_data_dir() / "kry_evidence_decay.json"
_evidence_mints: dict[str, list] = {}  # hash → [count_in_window, window_start] (kept for test reset)
_decay_loaded = False                  # vestigial cache flag (kept for test reset compatibility)


def _decayed_tokens(evidence_hash: str, tokens: float) -> float:
    """Apply geometric decay to repeated mints of the same evidence in-window.
    State persists across restarts so the supply guarantee is auditable.

    Cross-process safe: the whole read-modify-write runs under a file lock and RELOADS
    fresh from disk (an in-memory cache can't see another process's increments), so the
    replay/supply cap holds even when several nodes mint the same evidence concurrently —
    the same fix applied to the ledger after the lab's Test 6."""
    global _evidence_mints
    from kry._locks import cross_process_lock
    now = time.time()
    with cross_process_lock(_DECAY_STATE_PATH):
        state: dict = {}
        if _DECAY_STATE_PATH.exists():
            try:
                state = _json_loads(_DECAY_STATE_PATH.read_text(encoding="utf-8"))   # fresh, not a cache
            except Exception as exc:
                # An EXISTING-but-unparseable decay file is corruption/tamper. Fail CLOSED: refuse to
                # mint (propagates → mint() returns None) rather than resetting to {}, which would
                # re-open first-avoidance FULL credit for every evidence_hash.
                raise RuntimeError(
                    f"decay state file is corrupt — refusing to mint until repaired: {exc}")
        if not isinstance(state, dict):
            raise RuntimeError("decay state file is not a JSON object — refusing to mint")
        rec = state.get(evidence_hash)
        if not rec:
            count, start = 0, now            # genuinely new evidence — first avoidance, full credit
        else:
            try:
                count_f = _finite_number(rec[0], "decay_count", nonnegative=True)
                start = _finite_number(rec[1], "decay_window_start", positive=True)
                if not count_f.is_integer():
                    raise ValueError("decay_count must be an integer")
                count = int(count_f)
            except Exception:
                # A record EXISTS but is malformed/tampered. Fail CLOSED: treat as heavily
                # replayed (_DECAY**10000 -> ~0) rather than resetting to first-avoidance full
                # credit, which would re-open the phantom-mint window for this evidence_hash.
                count, start = 10_000, now
        if now - start > _DECAY_WINDOW:
            count, start = 0, now   # window reset — first avoidance again
        factor = _DECAY ** count
        state[evidence_hash] = [count + 1, start]
        _evidence_mints = state
        try:
            _DECAY_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=_DECAY_STATE_PATH.parent, prefix=".decay_")
            with os.fdopen(fd, "w") as f:
                f.write(_json_dumps(state))
            os.replace(tmp, _DECAY_STATE_PATH)
        except Exception:
            pass
    return tokens * factor


# ── Public API ─────────────────────────────────────────────────────────────────

def mint(
    event_type: str,
    tokens_saved: float,
    detail: str = "",
    evidence: str = "",
    avoided_model: str | None = None,
    evidence_tier: str = TIER_SELF_REPORTED,
    metered_tokens: list | None = None,
    served_model: str | None = None,
) -> Optional[MintReceipt]:
    """Mint KRY from an efficiency event. Returns MintReceipt or None on failure.

    Also calls kry_token.earn() to update the balance ledger. The MintReceipt
    is the external-facing proof; the KRYLedger is the internal accounting.

    avoided_model: the model the efficiency event avoided — edge-weights the
    credit (free-tier avoidance earns 0; Opus avoidance earns full value).

    evidence_tier: how the event was WITNESSED (veracity, not integrity). Default
    self_reported — the honest tier for a runtime-asserted saving such as a cache
    hit (a counterfactual call that never reached a provider). Displacement's
    cheap leg can opt into provider_metered when a real provider usage payload is
    retained. See docs/KRY_VERACITY_BINDING.md.

    Supply control: repeated mints of the same evidence_hash in-window decay
    geometrically (bounds phantom-minting from cache replay to a finite cap).
    """
    try:
        tokens_saved = _finite_number(tokens_saved, "tokens_saved", positive=True)
    except ValueError:
        return None   # R5: reject NaN/inf/non-positive at the mint boundary
    tier = evidence_tier if evidence_tier in _VALID_TIERS else TIER_SELF_REPORTED
    if tier == TIER_PROVIDER_METERED:
        try:
            metered_tokens = _metered_pair(metered_tokens)
        except ValueError:
            return None
    else:
        metered_tokens = None
    try:
        global _CHAIN_TIP, _RECEIPT_COUNTER
        from kry._locks import cross_process_lock
        evidence_hash = hashlib.sha256(
            (evidence or detail or event_type).encode()
        ).hexdigest()[:16]

        with _MINT_LOCK:
            # Supply control: decay repeated mints of the same evidence
            effective_tokens = _decayed_tokens(evidence_hash, tokens_saved)
            if effective_tokens < 1.0:
                return None   # decayed to dust — replay saving already banked

            # Cross-process atomic append: hold an exclusive file lock and re-read
            # the AUTHORITATIVE chain tip + counter from the file. A long-running
            # writer's in-memory _CHAIN_TIP / _RECEIPT_COUNTER go stale the moment
            # another process (or node) appends; building against the file's real
            # tip is what keeps the chain unforked under concurrent writers.
            _MINT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
            with cross_process_lock(_MINT_LOG_PATH):
                count, prev_chain = _load_chain_tip()
                receipt = MintReceipt.create(
                    receipt_id=f"KRY-{count + 1:08d}",
                    event_type=event_type,
                    tokens_saved=effective_tokens,
                    detail=detail,
                    evidence_hash=evidence_hash,
                    previous_chain_hash=prev_chain,
                    avoided_model=avoided_model,
                    evidence_tier=evidence_tier,
                    metered_tokens=metered_tokens,
                    served_model=served_model,
                )
                # Append to mint log (append-only, like an event store)
                with open(_MINT_LOG_PATH, "a", encoding="utf-8") as f:
                    f.write(_json_dumps(asdict(receipt)) + "\n")
                    f.flush()
                    os.fsync(f.fileno())
                _write_mint_tip(count + 1, receipt.chain_hash)   # truncation/rollback checkpoint
            # Refresh the in-memory cache to the just-written tip.
            _RECEIPT_COUNTER = count + 1
            _CHAIN_TIP = receipt.chain_hash

        # Also update the KRY balance ledger (edge-weighted by avoided model)
        try:
            from kry.kry_token import earn as _earn
            _earn(effective_tokens, event_type, detail, avoided_model=avoided_model,
                  served_model=served_model)
        except Exception:
            pass

        return receipt
    except Exception:
        return None


def _find_t1_receipt_for_gen(gen_id: str) -> Optional[dict]:
    """The prior displacement receipt the HOST minted for this OpenRouter gen id.

    The host stamps `/openrouter:<id>` into `detail` when it routes a displacement
    (its bridge layer). We read that record back so a T2 attestation can UPGRADE its
    tier instead of crediting the saving a second time (the double-credit fix). Skips
    tlsn_attested rows (don't promote a promotion) and zero-value rows. Last match
    wins (most recent). Returns the raw receipt dict, or None when the host logged no
    displacement for this id (then T2 mints fresh value — no double to avoid)."""
    if not gen_id or not _MINT_LOG_PATH.exists():
        return None
    marker = f"/openrouter:{gen_id}"
    found: Optional[dict] = None
    try:
        with open(_MINT_LOG_PATH, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or marker not in line:
                    continue
                rec = _json_loads(line)
                if marker not in str(rec.get("detail") or ""):
                    continue
                if rec.get("evidence_tier") == TIER_TLSN_ATTESTED:
                    continue
                if _finite_number(rec.get("kry_minted", 0.0), "kry_minted",
                                  positive=True) <= 0:
                    continue
                found = rec
    except Exception:
        return None
    return found


def promote_to_tlsn(
    gen_id: str,
    evidence_binding: str,
    detail: str = "",
) -> Optional[tuple[MintReceipt, str, float]]:
    """Upgrade a prior T1 displacement (same gen id) to tlsn_attested WITHOUT
    re-crediting the saving — the (iii) net-out resolution of the double-credit.

    The T2 TLS-notary attestation strengthens HOW a saving was witnessed (operator-
    retained payload → cryptographically notarized, can't-fabricate-the-bytes); it
    does NOT create a new saving. So we append a ZERO-value `tier_promotion` receipt
    that records `supersedes=<T1 id>` and the tlsn evidence binding, and DO NOT call
    earn() — balance and total supply are unchanged. `veracity_breakdown` re-tiers the
    superseded receipt's value to tlsn_attested (and exposes the T2 sub-fraction).

    Idempotent: a second promotion of the same gen id is a no-op (returns None) so a
    re-run of the verify can't stack promotion receipts.

    Returns (promotion_receipt, superseded_receipt_id, moved_kry), or None when no
    matching un-promoted T1 receipt exists (caller then mints fresh T2 value)."""
    t1 = _find_t1_receipt_for_gen(gen_id)
    if t1 is None:
        return None
    t1_id = t1.get("receipt_id")
    try:
        moved_kry = _finite_number(t1.get("kry_minted", 0.0), "kry_minted",
                                   positive=True)
    except ValueError:
        return None
    try:
        global _CHAIN_TIP, _RECEIPT_COUNTER
        from kry._locks import cross_process_lock
        evidence_hash = hashlib.sha256(
            (evidence_binding or f"tlsn_promote:{gen_id}").encode()
        ).hexdigest()[:16]
        with _MINT_LOCK:
            _MINT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
            with cross_process_lock(_MINT_LOG_PATH):
                # Idempotency under the SAME lock that serializes appends, so two
                # concurrent verifiers can't both stack a promotion for one T1 id.
                if _already_promoted(t1_id):
                    return None
                # Re-read the AUTHORITATIVE tip so the promotion chains off the file,
                # not a stale in-memory tip (same fix as mint()).
                count, prev_chain = _load_chain_tip()
                # tokens_saved=0 → kry_minted=0 (no new value); tier bound into the hash.
                receipt = MintReceipt.create(
                    receipt_id=f"KRY-{count + 1:08d}",
                    event_type="tier_promotion",
                    tokens_saved=0.0,
                    detail=detail or f"tlsn_promote /openrouter:{gen_id} supersedes={t1_id}",
                    evidence_hash=evidence_hash,
                    previous_chain_hash=prev_chain,
                    evidence_tier=TIER_TLSN_ATTESTED,
                    supersedes=t1_id,        # F2: bound into the chain hash at creation (was set after)
                )
                with open(_MINT_LOG_PATH, "a", encoding="utf-8") as f:
                    f.write(_json_dumps(asdict(receipt)) + "\n")
                    f.flush()
                    os.fsync(f.fileno())
                _write_mint_tip(count + 1, receipt.chain_hash)   # truncation/rollback checkpoint
            _RECEIPT_COUNTER = count + 1
            _CHAIN_TIP = receipt.chain_hash
        # Deliberately NO earn() call — a promotion moves a tier, it does not mint value.
        return receipt, t1_id, moved_kry
    except Exception:
        return None


def _already_promoted(t1_receipt_id: str,
                      to_tier: str = TIER_TLSN_ATTESTED) -> bool:
    """True if a promotion to `to_tier` already supersedes this prior receipt.

    Guards both tier upgrades (tlsn_attested, tee_attested) against a re-run
    stacking a second zero-value promotion receipt for the same source id."""
    if not t1_receipt_id or not _MINT_LOG_PATH.exists():
        return False
    try:
        with open(_MINT_LOG_PATH, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or t1_receipt_id not in line:
                    continue
                rec = _json_loads(line)
                if (rec.get("evidence_tier") == to_tier
                        and rec.get("supersedes") == t1_receipt_id):
                    return True
    except Exception:
        return False
    return False


def _find_measurement_receipt_for_tee(measurement_id: str) -> Optional[dict]:
    """The prior self_reported / holdout_validated receipt for this measurement.

    tee_attested upgrades the operator-measurement path: when the holdout/savings
    measurement is RE-RUN inside an attested enclave, a prior unattested receipt for
    the SAME measurement (stamped `/measurement:<id>` in detail) should be UPGRADED in
    place, not credited a second time. Mirrors `_find_t1_receipt_for_gen` but matches
    the tiers tee upgrades. Skips already-tee rows (don't promote a promotion) and
    zero-value rows; last (most recent) match wins. Returns the raw receipt dict, or
    None when no prior measurement receipt exists (then tee mints fresh value)."""
    if not measurement_id or not _MINT_LOG_PATH.exists():
        return None
    marker = f"/measurement:{measurement_id}"
    _UPGRADABLE = {TIER_SELF_REPORTED, TIER_HOLDOUT_VALIDATED}
    found: Optional[dict] = None
    try:
        with open(_MINT_LOG_PATH, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or marker not in line:
                    continue
                rec = _json_loads(line)
                if marker not in str(rec.get("detail") or ""):
                    continue
                if rec.get("evidence_tier") not in _UPGRADABLE:
                    continue
                if _finite_number(rec.get("kry_minted", 0.0), "kry_minted",
                                  positive=True) <= 0:
                    continue
                found = rec
    except Exception:
        return None
    return found


def promote_to_tee(
    measurement_id: str,
    evidence_binding: str,
    detail: str = "",
) -> Optional[tuple[MintReceipt, str, float]]:
    """Upgrade a prior self_reported/holdout measurement (same measurement id) to
    tee_attested WITHOUT re-crediting the saving — the net-out twin of
    `promote_to_tlsn`.

    The TEE attestation strengthens HOW the saving was witnessed (operator self-report /
    population holdout → the measurement RAN in attested hardware the operator cannot
    fabricate); it does NOT create a new saving. So we append a ZERO-value
    `tier_promotion` receipt recording `supersedes=<prior id>` + the attestation evidence
    binding, and DO NOT call earn() — balance and total supply are unchanged.
    `veracity_breakdown` re-tiers the superseded receipt's value to tee_attested (and
    exposes the T2 sub-fraction). Promoting a self_reported receipt RAISES the binary
    veracity_floor (self-report → anchored); promoting a holdout_validated one keeps the
    floor (both anchored) but moves it to the stronger tee sub-tier.

    Idempotent: a second promotion of the same measurement id is a no-op (returns None).

    Returns (promotion_receipt, superseded_receipt_id, moved_kry), or None when no
    matching un-promoted measurement receipt exists (caller then mints fresh tee value)."""
    prior = _find_measurement_receipt_for_tee(measurement_id)
    if prior is None:
        return None
    prior_id = prior.get("receipt_id")
    try:
        moved_kry = _finite_number(prior.get("kry_minted", 0.0), "kry_minted",
                                   positive=True)
    except ValueError:
        return None
    try:
        global _CHAIN_TIP, _RECEIPT_COUNTER
        from kry._locks import cross_process_lock
        evidence_hash = hashlib.sha256(
            (evidence_binding or f"tee_promote:{measurement_id}").encode()
        ).hexdigest()[:16]
        with _MINT_LOCK:
            _MINT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
            with cross_process_lock(_MINT_LOG_PATH):
                # Idempotency under the SAME lock that serializes appends.
                if _already_promoted(prior_id, to_tier=TIER_TEE_ATTESTED):
                    return None
                count, prev_chain = _load_chain_tip()
                # tokens_saved=0 → kry_minted=0 (no new value); tier bound into the hash.
                receipt = MintReceipt.create(
                    receipt_id=f"KRY-{count + 1:08d}",
                    event_type="tier_promotion",
                    tokens_saved=0.0,
                    detail=detail or f"tee_promote /measurement:{measurement_id} supersedes={prior_id}",
                    evidence_hash=evidence_hash,
                    previous_chain_hash=prev_chain,
                    evidence_tier=TIER_TEE_ATTESTED,
                    supersedes=prior_id,     # F2: bound into the chain hash at creation (was set after)
                )
                with open(_MINT_LOG_PATH, "a", encoding="utf-8") as f:
                    f.write(_json_dumps(asdict(receipt)) + "\n")
                    f.flush()
                    os.fsync(f.fileno())
                _write_mint_tip(count + 1, receipt.chain_hash)   # truncation/rollback checkpoint
            _RECEIPT_COUNTER = count + 1
            _CHAIN_TIP = receipt.chain_hash
        # Deliberately NO earn() call — a promotion moves a tier, it does not mint value.
        return receipt, prior_id, moved_kry
    except Exception:
        return None


_V5_BAD = "nonfinite"      # sentinel for non-numeric/NaN/inf input (never collides with a 16-hex double)


def _canon_f64(x) -> str:
    """v5 canonical hash-preimage number: the IEEE-754 big-endian double (8 bytes), hex-encoded. Binds
    the EXACT stored value (no precision loss vs v4's float) AND is language-neutral — any verifier parses
    the JSON number to a 64-bit double and emits its 8 big-endian bytes: Python struct.pack('>d'); JS
    DataView.setFloat64(0, x, false); Rust f64::to_be_bytes; Go math.Float64bits + BigEndian. No rounding,
    no scale, no 2^53 limit, no float→string formatting. TOTAL: a non-numeric / NaN / inf field (only from
    a TAMPERED link — the minter validates its inputs) maps to a fixed sentinel so the verifier yields a
    hash MISMATCH (clean INVALID), never a crash. Must be byte-identical to the stdlib kry_verify replica."""
    try:
        f = float(x)
    except (TypeError, ValueError):
        return _V5_BAD
    if f != f or f in (float("inf"), float("-inf")):   # NaN / ±inf
        return _V5_BAD
    return struct.pack(">d", f).hex()


def _v4_public_block(*, hash_version: int, tokens_saved: float, ts: float,
                     evidence_tier: str, metered_tokens, kry_minted: float, earn_rate: float,
                     supersedes: str | None = None) -> str:
    """v4: canonical serialization of the PUBLIC economic block bound into chain_hash, so a forged
    tier / kry_minted / earn_rate / tokens_saved / metered count breaks the chain on the PUBLIC
    attestation surface (where the receipt-hash preimage is un-recomputable — evidence_hash is sealed).
    The minter and EVERY verifier must emit this byte-for-byte: kry_attest imports it; the standalone
    stdlib kry_verify replicates it (pinned by test_external_verify). For v4 the numbers serialize as
    CPython floats (Python-portable); **v5** binds them as the EXACT IEEE-754 double in big-endian hex
    (`_canon_f64`) so a NON-Python verifier reproduces the hash byte-for-byte — version-dispatched and
    additive, so v4 receipts are byte-unchanged. `hash_version` is bound too, so an attacker can't change just the
    version without breaking the chain. (`event_type`
    is deliberately NOT bound here — it carries no economic value and is already bound to the savings
    report by the artifact's `attestation_matches_report_event_counts` product gate.)

    F2: `supersedes` (a T2 promotion's re-tiering target) is bound too, but ONLY when present — so
    non-promotion receipts serialize byte-identically to before (no format change for existing v4
    receipts), while a promotion's target becomes tamper-evident: any add/remove/change of supersedes
    breaks the chain. Previously it was unbound, so an operator could re-point a promotion at a larger
    receipt and inflate veracity_floor while verify_chain still passed."""
    # v5+ binds economic numbers + ts as the EXACT IEEE-754 double in big-endian hex (language-neutral,
    # no precision loss) so a non-Python stranger can recompute the hash; v4 and earlier keep CPython
    # float encoding — byte-UNCHANGED for every existing receipt (backward compatible). Only the hash
    # preimage changes; the stored receipt keeps its float fields, so consumers (carbon, savings,
    # magnitude recompute) are unaffected.
    if hash_version >= 5:
        block = {
            "hash_version": hash_version,
            "tokens_saved": _canon_f64(tokens_saved),
            "ts": _canon_f64(ts),
            "evidence_tier": evidence_tier,
            "metered_tokens": metered_tokens,
            "kry_minted": _canon_f64(kry_minted),
            "earn_rate": _canon_f64(earn_rate),
        }
    else:
        block = {
            "hash_version": hash_version,
            "tokens_saved": tokens_saved,
            "ts": ts,
            "evidence_tier": evidence_tier,
            "metered_tokens": metered_tokens,
            "kry_minted": kry_minted,
            "earn_rate": earn_rate,
        }
    if supersedes is not None:
        block["supersedes"] = supersedes
    return _json_dumps(block, sort_keys=True, separators=(",", ":"))


def _mint_tip_path() -> Path:
    return _MINT_LOG_PATH.with_name("kry_mint_tip.json")


def _read_mint_tip() -> Optional[dict]:
    """Monotonic {count, tip} checkpoint for the mint log — the truncation/rollback guard
    (mirrors the settlement HOLE-F tip). Honest ceiling: the checkpoint is a local file too,
    so a sufficiently privileged attacker can roll BOTH the log and the checkpoint back; the
    real fix is to PUBLISH the tip (content-free, like the attestation chain_head)."""
    p = _mint_tip_path()
    if p.exists():
        tip = _json_loads(p.read_text(encoding="utf-8"))
        count = _finite_number(tip.get("count", 0), "mint_tip.count", nonnegative=True)
        if not float(count).is_integer():
            raise ValueError("mint_tip.count must be an integer")
        if not isinstance(tip.get("tip"), str):
            raise ValueError("mint_tip.tip must be a string")
        return {"count": int(count), "tip": tip["tip"]}
    return None


def _write_mint_tip(count: int, tip: str) -> None:
    p = _mint_tip_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=p.parent, prefix=".minttip_")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(_json_dumps({"count": count, "tip": tip}))
            f.flush()
            os.fsync(f.fileno())   # durability: shrink the crash window between append and checkpoint
        os.replace(tmp, p)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def verify_chain() -> tuple[bool, list[str]]:
    """Verify the entire mint log chain integrity.

    Returns (is_valid, list_of_errors).
    An empty error list means the chain is intact — no receipts were tampered.
    """
    errors: list[str] = []
    if not _MINT_LOG_PATH.exists():
        # A missing log with a non-empty checkpoint means the whole log was deleted/rolled back.
        try:
            tip = _read_mint_tip()
        except ValueError:
            tip = None
        if tip is not None and int(tip.get("count", 0)) > 0:
            return False, [f"mint log missing but checkpoint claims {tip['count']} "
                           f"receipts — log deleted/rolled back"]
        return True, []
    prev_chain = "0" * 64
    receipt_count = 0
    prev_version = 0
    try:
        with open(_MINT_LOG_PATH, encoding="utf-8") as f:
            for lineno, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = _json_loads(line)
                except (json.JSONDecodeError, ValueError) as e:
                    errors.append(f"Line {lineno}: JSON error: {e}")
                    continue
                receipt_count += 1
                try:
                    _finite_number(rec.get("tokens_saved"), "tokens_saved", nonnegative=True)
                    _finite_number(rec.get("kry_minted"), "kry_minted", nonnegative=True)
                    _finite_number(rec.get("earn_rate"), "earn_rate", nonnegative=True)
                    _finite_number(rec.get("ts"), "ts", positive=True)
                    _finite_number(rec.get("usd_equivalent"), "usd_equivalent",
                                   nonnegative=True)
                except ValueError as e:
                    errors.append(f"Line {lineno} ({rec.get('receipt_id', '?')}): {e}")
                    continue

                # Re-derive receipt_hash from content fields. v2 binds the
                # veracity tier; v3 also binds provider metered token counts.
                # Legacy receipts verify bit-for-bit under their original format.
                if rec.get("hash_version", 1) >= 3:
                    metered_for_hash = _json_dumps(
                        rec.get("metered_tokens"), sort_keys=True, separators=(",", ":")
                    )
                    content = (
                        f"{rec['event_type']}:{rec['tokens_saved']}"
                        f":{rec['ts']}:{rec['evidence_hash']}"
                        f":{rec.get('evidence_tier', TIER_SELF_REPORTED)}"
                        f":{metered_for_hash}"
                    )
                elif rec.get("hash_version", 1) >= 2:
                    content = (
                        f"{rec['event_type']}:{rec['tokens_saved']}"
                        f":{rec['ts']}:{rec['evidence_hash']}"
                        f":{rec.get('evidence_tier', TIER_SELF_REPORTED)}"
                    )
                else:
                    content = (
                        f"{rec['event_type']}:{rec['tokens_saved']}"
                        f":{rec['ts']}:{rec['evidence_hash']}"
                    )
                expected_receipt = hashlib.sha256(content.encode()).hexdigest()
                if rec["receipt_hash"] != expected_receipt:
                    errors.append(
                        f"Line {lineno} ({rec['receipt_id']}): "
                        f"receipt_hash mismatch — content may have been tampered")

                # Monotonic version: once the chain reaches v4 it can't drop back to a legacy format
                # that binds nothing public — a v-decrease is a partial downgrade/rollback attempt.
                hv = rec.get("hash_version", 1)
                # Legacy v1 does NOT bind evidence_tier into the receipt hash, so a forged v1
                # receipt claiming an external tier would otherwise verify. A v1 receipt may
                # ONLY be self_reported (the honest legacy default); a higher tier is rejected.
                if hv < 2 and rec.get("evidence_tier", TIER_SELF_REPORTED) != TIER_SELF_REPORTED:
                    errors.append(
                        f"Line {lineno} ({rec.get('receipt_id', '?')}): hash_version {hv} cannot "
                        f"carry a non-self_reported tier ({rec.get('evidence_tier')}) — "
                        f"the tier is unbound at this version")
                if hv < prev_version:
                    errors.append(
                        f"Line {lineno} ({rec.get('receipt_id', '?')}): hash_version {hv} < "
                        f"previous {prev_version} — version downgrade (rollback attempt)")
                prev_version = max(prev_version, hv)

                # Re-derive chain_hash. v4 binds the public economic block; legacy uses prev:receipt.
                if hv >= 4:
                    block = _v4_public_block(
                        hash_version=hv,
                        tokens_saved=rec["tokens_saved"], ts=rec["ts"],
                        evidence_tier=rec.get("evidence_tier", TIER_SELF_REPORTED),
                        metered_tokens=rec.get("metered_tokens"),
                        kry_minted=rec["kry_minted"], earn_rate=rec["earn_rate"],
                        supersedes=rec.get("supersedes"))   # F2: bind the promotion target
                    expected_chain = hashlib.sha256(
                        f"{prev_chain}:{rec['receipt_hash']}:{block}".encode()).hexdigest()
                else:
                    expected_chain = hashlib.sha256(
                        f"{prev_chain}:{rec['receipt_hash']}".encode()).hexdigest()
                if rec["chain_hash"] != expected_chain:
                    errors.append(
                        f"Line {lineno} ({rec['receipt_id']}): "
                        f"chain_hash broken — chain integrity violated")

                prev_chain = rec["chain_hash"]
    except Exception as e:
        errors.append(f"Chain verification error: {e}")

    # Truncation/rollback guard (mirrors settlement HOLE F): a monotonic {count, tip}
    # checkpoint catches a shorter/rolled-back log that the chain replay alone cannot see.
    try:
        tip = _read_mint_tip()
    except ValueError as exc:
        errors.append(f"mint tip invalid: {exc}")
        tip = None
    if tip is not None:
        if receipt_count < int(tip["count"]):
            errors.append(
                f"mint log truncated: {receipt_count} receipts < checkpoint "
                f"{tip['count']} — rollback/replay attempt")
        elif receipt_count == int(tip["count"]) and prev_chain != tip["tip"]:
            errors.append("mint log tip mismatch — tampered")

    return len(errors) == 0, errors


# ── External chain-head anchor (the fix the verify_chain docstring names: PUBLISH the tip) ──
#
# verify_chain proves the chain is INTERNALLY consistent — it cannot tell an honest chain
# from one an operator re-derived from genesis (keyless SHA-256, local checkpoint). The only
# way to make a retroactive re-mint detectable is an EXTERNAL root of trust on the chain head.
# These two functions provide it without any new dependency: the operator exports a content-free
# commitment {count, tip} and PUBLISHES it to an append-only medium they cannot silently rewrite
# (a git commit, a public timestamp, a transparency log, a notarized note). A verifier who holds
# that PUBLISHED anchor (obtained out-of-band, like a pinned key — never handed over at verify
# time) can then prove the live chain still contains the anchored prefix. Optionally sign the
# anchor with kry_pqc for authenticity; the re-mint detection itself comes from the publication.

CHAIN_ANCHOR_SCHEMA = "kry_chain_anchor/v1"


def export_chain_anchor() -> dict:
    """A content-free commitment to the current chain head, for the operator to PUBLISH
    externally. Leaks nothing (only a receipt count + a hash). Once published, any later
    re-mint of a receipt at or before `count` changes the head and is detectable via
    verify_chain_against_anchor()."""
    count, tip = 0, "0" * 64
    if _MINT_LOG_PATH.exists():
        rows = [_json_loads(ln) for ln in _MINT_LOG_PATH.read_text(encoding="utf-8").splitlines()
                if ln.strip()]
        count = len(rows)
        if rows:
            tip = rows[-1].get("chain_hash", tip)
    return {"schema": CHAIN_ANCHOR_SCHEMA, "count": count, "tip": tip}


def verify_chain_against_anchor(anchor: dict) -> tuple[bool, list[str]]:
    """Detect retroactive re-mint against a PUBLISHED anchor obtained out-of-band. The live
    chain must (a) be internally valid and (b) still carry the anchored prefix: at receipt
    #anchor['count'] its chain_hash must equal anchor['tip']. A re-mint of any receipt <= count
    changes that hash; a truncation makes the chain too short — both are caught. Returns
    (False, [...]) on mismatch. NOTE: this is only as strong as the anchor's external
    publication — an anchor handed over by the operator at verify time proves nothing."""
    if not isinstance(anchor, dict) or anchor.get("schema") != CHAIN_ANCHOR_SCHEMA:
        return False, [f"anchor must be a {CHAIN_ANCHOR_SCHEMA} object"]
    count, tip = anchor.get("count"), anchor.get("tip")
    if isinstance(count, bool) or not isinstance(count, int) or count < 0:
        return False, ["anchor.count must be a non-negative integer"]
    if not isinstance(tip, str) or len(tip) != 64:
        return False, ["anchor.tip must be a 64-char hex chain hash"]
    valid, errs = verify_chain()
    if not valid:
        return False, ["live chain is not internally valid: " + "; ".join(errs[:2])]
    rows = []
    if _MINT_LOG_PATH.exists():
        rows = [_json_loads(ln) for ln in _MINT_LOG_PATH.read_text(encoding="utf-8").splitlines()
                if ln.strip()]
    if count == 0:
        return (tip == "0" * 64), ([] if tip == "0" * 64 else ["anchor.count 0 but tip is not genesis"])
    if len(rows) < count:
        return False, [f"live chain has {len(rows)} receipts < anchored {count} — "
                       f"rollback/re-mint/truncation"]
    live_tip_at_count = rows[count - 1].get("chain_hash")
    if live_tip_at_count != tip:
        return False, [f"chain head at receipt {count} does not match the published anchor — "
                       f"retroactive re-mint detected"]
    return True, []


def retained_dollars_dated() -> dict:
    """R4 (peg stability): the AUTHORITATIVE retained-dollars figure — sum of each
    receipt's usd_equivalent STAMPED AT MINT TIME, not recomputed from the live
    constant. Each MintReceipt records its dated USD basis immutably (hash-chained),
    so a later change to FRONTIER_USD_PER_M_OUTPUT cannot retroactively revalue
    history. This is the dated-basis fix: KRY minted at time T is valued at T's
    reference, summed over the tamper-evident chain.
    """
    valid, errs = verify_chain()
    dated_usd = 0.0
    total_kry = 0.0
    total_tokens = 0.0
    if _MINT_LOG_PATH.exists():
        try:
            with open(_MINT_LOG_PATH, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        rec = _json_loads(line)
                        dated_usd += _finite_number(rec.get("usd_equivalent", 0.0),
                                                    "usd_equivalent", nonnegative=True)
                        total_kry += _finite_number(rec.get("kry_minted", 0.0),
                                                    "kry_minted", nonnegative=True)
                        total_tokens += _finite_number(rec.get("tokens_saved", 0.0),
                                                       "tokens_saved", nonnegative=True)
        except Exception:
            pass
    return {
        "retained_usd_dated": round(dated_usd, 6),   # authoritative — no revaluation
        "total_kry_minted": round(total_kry, 4),
        "total_tokens_saved": round(total_tokens, 4),  # raw compute avoided (carbon/energy basis)
        "chain_valid": valid,
        "basis": "sum of per-receipt usd_equivalent stamped at mint time (dated)",
        "note": "R4: immune to retroactive revaluation when the frontier constant changes",
    }


def _apply_promotion_overlay(by_tier: dict, promotions: list, kry_by_receipt: dict) -> None:
    """Re-tier promoted value IN PLACE: a zero-value tlsn/tee promotion moves the value of the
    receipt it supersedes OFF its original tier and ONTO the promoting tier (total unchanged — the
    value was minted exactly once). SHARED by veracity_breakdown (internal) and
    kry_attest.build_attestation (public) so both veracity surfaces compute the SAME floor from the
    SAME overlay (F5: they diverged — the public attestation ignored promotions and under-reported
    the anchored fraction)."""
    for src_id, to_tier in promotions:
        src = kry_by_receipt.get(src_id)
        if not src:
            continue
        src_tier, src_kry = src
        if src_kry <= 0:
            continue
        by_tier[src_tier] = by_tier.get(src_tier, 0.0) - src_kry
        by_tier[to_tier] = by_tier.get(to_tier, 0.0) + src_kry


def veracity_breakdown() -> dict:
    """Itemize minted KRY by veracity tier — the honest trust surface.

    The chain proves integrity (untampered + conserved), not veracity (the events
    happened). This reports what fraction of the balance rests on operator
    self-report alone vs an external anchor (provider metering / TEE). A verifier
    reads `veracity_floor` to know exactly how much trust the operator is asking
    for. Currently ~0.0 → the balance is internal-operator-measurement scope.
    """
    by_tier: dict[str, float] = {}
    total = 0.0
    # promotions: superseded receipt_id → the tier it was upgraded TO (tlsn or tee)
    promotions: list[tuple[str, str]] = []
    kry_by_receipt: dict[str, tuple] = {}  # receipt_id → (tier, kry)
    if _MINT_LOG_PATH.exists():
        try:
            with open(_MINT_LOG_PATH, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    rec = _json_loads(line)
                    tier = rec.get("evidence_tier", TIER_SELF_REPORTED)
                    k = _finite_number(rec.get("kry_minted", 0.0), "kry_minted",
                                       nonnegative=True)
                    by_tier[tier] = by_tier.get(tier, 0.0) + k
                    total += k
                    rid = rec.get("receipt_id")
                    if rid:
                        kry_by_receipt[rid] = (tier, k)
                    sup = rec.get("supersedes")
                    if tier in (TIER_TLSN_ATTESTED, TIER_TEE_ATTESTED) and sup:
                        promotions.append((sup, tier))
        except Exception:
            pass
    # T2 tier-promotion overlay (shared with the public attestation so both surfaces agree).
    _apply_promotion_overlay(by_tier, promotions, kry_by_receipt)
    anchored = sum(v for t, v in by_tier.items() if t in _ANCHORED_TIERS)
    tlsn = by_tier.get(TIER_TLSN_ATTESTED, 0.0)
    tee = by_tier.get(TIER_TEE_ATTESTED, 0.0)
    return {
        "by_tier": {t: round(v, 4) for t, v in by_tier.items()},
        "total_kry": round(total, 4),
        "externally_anchored_kry": round(anchored, 4),
        "self_reported_kry": round(by_tier.get(TIER_SELF_REPORTED, 0.0), 4),
        "tlsn_attested_kry": round(tlsn, 4),                       # T2: cryptographically notarized
        "tlsn_attested_fraction": round(tlsn / total, 4) if total > 0 else 0.0,
        "tee_attested_kry": round(tee, 4),                         # T2: measured in attested hardware
        "tee_attested_fraction": round(tee / total, 4) if total > 0 else 0.0,
        "veracity_floor": round(anchored / total, 4) if total > 0 else 0.0,
        "note": ("veracity_floor = fraction backed by an external anchor "
                 "(provider metering / TEE / TLS-notary), not operator self-report alone. "
                 "tlsn_attested_fraction (notarized provider bytes) and tee_attested_fraction "
                 "(measurement run in attested hardware) are surfaced separately because the binary "
                 "floor cannot distinguish them from provider_metered. IMPORTANT: the chain binds the "
                 "tier LABEL, not the underlying proof — a label is trustless ONLY when its external "
                 "verifier (kry_tee_verify / kry_tlsn_verify / F1 reconcile) was run on the evidence "
                 "AND the chain head is externally anchored; absent that, treat the label as "
                 "operator-asserted — see docs/KRY_VERACITY_BINDING.md"),
    }


def reconcile_ledger_from_chain() -> dict:
    """Rebuild the mutable KRY balance ledger from the append-only mint chain.

    The /kry surface exposed that the mutable ledger (data/kry_ledger.json) can be
    polluted by dev/test earns that delta-merge in, while the hash-chained mint log
    is tamper-evident TRUTH. This makes the balance self-correcting: balance =
    sum of verified mint receipts. Run on startup or on demand to guarantee the
    reported balance reflects only real, chain-anchored earning. Refuses to
    reconcile a tampered chain (fails closed).
    """
    valid, errs = verify_chain()
    if not valid:
        return {"reconciled": False, "reason": "mint chain tampered", "errors": errs[:2]}
    total = 0.0
    n = 0
    if _MINT_LOG_PATH.exists():
        with open(_MINT_LOG_PATH, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    rec = _json_loads(line)
                    total += _finite_number(rec.get("kry_minted", 0.0), "kry_minted",
                                            nonnegative=True)
                    n += 1
    try:
        from kry import kry_token as _kt
        led = _kt.get_ledger()
        led.balance = total
        led.total_earned = total
        led._baseline = {k: getattr(led, k) for k in _kt._MERGE_FIELDS}
        led.save()
    except Exception as exc:
        return {"reconciled": False, "reason": f"ledger write failed: {exc}"}
    return {"reconciled": True, "receipts": n, "balance_kry": round(total, 4),
            "note": "balance rebuilt from tamper-evident mint chain (truth)"}


def chain_summary() -> dict:
    """Return a summary of the mint chain state."""
    try:
        count, tip = _load_chain_tip()
        tip_errors: list[str] = []
    except Exception as exc:
        count, tip = 0, "0" * 64
        tip_errors = [f"mint log tip error: {exc}"]
    valid, errs = verify_chain()
    errs = tip_errors + errs
    total_kry = 0.0
    dated_usd = 0.0
    if _MINT_LOG_PATH.exists():
        try:
            with open(_MINT_LOG_PATH, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        rec = _json_loads(line)
                        total_kry += _finite_number(rec.get("kry_minted", 0.0),
                                                    "kry_minted", nonnegative=True)
                        dated_usd += _finite_number(rec.get("usd_equivalent", 0.0),
                                                    "usd_equivalent", nonnegative=True)
        except Exception:
            pass
    return {
        "receipts": count,
        "chain_tip": tip[:16] + "...",
        "chain_valid": valid,
        "chain_errors": errs,
        "total_kry_minted": round(total_kry, 4),
        "usd_equivalent_dated": round(dated_usd, 6),  # R4: summed at mint-time basis
        "veracity": veracity_breakdown(),             # honest trust surface (integrity ≠ veracity)
        "log_path": str(_MINT_LOG_PATH),
    }
