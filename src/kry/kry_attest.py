"""KRY Attestation — public proof-of-balance with content sealed.

The mint log (data/kry_mint_log.jsonl) is PRIVATE: it records which efficiency
events occurred, tied to the operator's real Copilot/Anthropic usage. That raw
data must never leave the machine.

But the PROOF that a KRY balance was honestly earned can be public — without
revealing a single prompt, response, or cache key. This is the content-sealed
attestation seam — a hash-chain integrity proof, NOT a zero-knowledge proof: an
external verifier confirms "this balance was minted from real efficiency events
whose hash chain is intact" without seeing the content.

What an attestation EXPOSES (safe to share):
    - The number of mint receipts
    - The chain head hash (Merkle-style tip)
    - The total KRY minted (aggregate)
    - Each receipt's receipt_hash + chain_hash (opaque SHA-256)
    - Each receipt timestamp (epoch seconds; content-free, needed for billing windows)
    - Event TYPE counts (how many cache_hits, compressions — not their content)

What an attestation NEVER exposes (sealed):
    - The `detail` field (may name a model or path)
    - The `evidence_hash` is already a hash, but we re-hash it under a salt
      so the attestation can't be correlated back to the private log
    - Any prompt, response, intent, or outcome text (these were never in the
      mint log to begin with — only their hashes were)

Verification model (how a provider checks an attestation):
    1. Provider receives the attestation (chain of {receipt_hash, chain_hash, kry}).
    2. Provider recomputes: chain_hash[i] == SHA256(chain_hash[i-1] + receipt_hash[i]).
       If every link holds, the chain is intact — no receipt was inserted,
       removed, or altered after minting.
    3. The aggregate KRY is the sum of receipt amounts. Tampering with any amount
       breaks the chain, so a verifier confirms INTEGRITY + conservation + magnitude
       — event truth still rests on the evidence tier + any published anchor
       (integrity != veracity; read veracity_floor).
    4. The provider learns the balance is INTERNALLY consistent WITHOUT learning what
       generated it (not proof the events happened).

This mirrors:
    - Carbon registries: "X tonnes CO2e avoided, verified" — not the factory logs
    - Basel III: banks prove reserve adequacy — not every transaction
    - ZK citation attestation: "engagement verified" — not the reading session
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from collections import Counter
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional


def _kry_data_dir() -> Path:
    """Portable data dir. Set KRY_DATA_DIR to relocate; defaults to ./kry_data."""
    # IMPORT-PURITY: path CONSTRUCTION only — no mkdir. This runs at MODULE level to build
    # _MINT_LOG_PATH, so creating the dir here made a bare `import kry.kry_attest` write to the
    # caller's cwd (and raise PermissionError under a read-only one). This module only ever READS
    # the mint log, so unlike the writers there is no lazy mkdir to add — the reader already
    # guards with path.exists().
    return Path(os.environ.get("KRY_DATA_DIR", "kry_data")).expanduser()

_MINT_LOG_PATH = _kry_data_dir() / "kry_mint_log.jsonl"

# Public salt for re-hashing evidence so attestations do not directly expose the private mint log.
# Rotating this salt makes old attestations harder to correlate. NOTE: the seal is DETERMINISTIC
# under a fixed public salt, so a party who holds the private evidence_hash can still link it — this
# HIDES the raw hash, it is not unlinkability.
_ATTEST_SALT = "kry-attest-v1"

# SPEC §3.5 (P-TOL): the ONE absolute tolerance for every §3.1/§3.5 derived-vs-declared numeric
# comparison, shared verbatim by scripts/kry_verify.py and verifiers/js/verify.mjs. Those values are
# all SPEC-mandated round(x,4) / round(x,6), so the smallest REAL discrepancy is 1e-6; 1e-9 sits
# three decades below that and three decades above IEEE-754 accumulation noise at corpus scale. It
# does NOT apply to the constants SPEC states separately: §3.4.1's 1e-6 rate / 1e-3 multiplier,
# §3.7's -0.01 outcome guard, §4's 0.01 action floor.
_COMPARE_EPS = 1e-9


def _reject_json_constant(value: str):
    raise ValueError(f"non-standard JSON constant rejected: {value}")


def _json_loads(text: str):
    return json.loads(text, parse_constant=_reject_json_constant)


def _json_dumps(data: object, **kwargs) -> str:
    kwargs.setdefault("allow_nan", False)
    return json.dumps(data, **kwargs)


def _json_clean(data: object) -> object:
    return json.loads(_json_dumps(data))


def _finite_number(value, field: str, *, nonnegative: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be a finite JSON number")
    value = float(value)
    if not math.isfinite(value):
        raise ValueError(f"{field} must be finite")
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


@dataclass
class AttestationLink:
    """One public link in the proof chain. Content-free."""
    seq: int
    event_type: str       # type only (cache_hit/compression) — not the content
    kry_minted: float
    ts: float             # receipt timestamp; content-free and used for provider billing windows
    receipt_hash: str     # opaque SHA-256 (no content recoverable)
    chain_hash: str       # running chain hash
    sealed_evidence: str  # re-hashed under salt — hides the raw evidence hash (deterministic seal)
    evidence_tier: str = "self_reported"  # how the event was witnessed (veracity, not integrity)
    tokens_saved: float = 0.0   # F2: magnitude input (a count — no content) — lets a verifier
    earn_rate: float = 0.0      # F2: magnitude input — recompute kry = tokens × rate × multiplier
    metered_tokens: list | None = None  # F1: provider-metered token counts, no content
    hash_version: int = 1       # v4 binds the public economic block into chain_hash; the verifier
                                # needs the version to recompute the chain. Default 1 = legacy.
    supersedes: str | None = None  # F2: a T2 promotion's re-tiering target, bound into the v4 block
    receipt_id: str = ""           # F5: content-free seq id so a stranger can resolve `supersedes` and
                                   # reproduce the promotion overlay (else the declared by_tier is unverifiable)


@dataclass
class Attestation:
    """Public, shareable proof of a KRY balance — content sealed."""
    receipts: int
    total_kry: float
    usd_equivalent: float
    chain_head: str                       # the tip — the single hash that anchors all
    chain_valid: bool
    event_type_counts: dict               # {cache_hit: 5, compression: 2} — aggregate only
    links: list                           # list[AttestationLink]
    veracity: dict = field(default_factory=dict)  # trust surface: KRY by tier + veracity_floor
    attestation_hash: str = ""            # SHA-256 of the whole attestation

    def to_public_json(self) -> str:
        """Serialise for sharing. Contains zero content — only hashes + aggregates."""
        return _json_dumps(asdict(self), indent=2)


def _seal(evidence_hash: str) -> str:
    """Re-hash an evidence hash under the public salt → hides the raw hash. Deterministic, so a
    holder of the private evidence_hash can still link it (this is not unlinkability)."""
    return hashlib.sha256(f"{_ATTEST_SALT}:{evidence_hash}".encode()).hexdigest()[:16]


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


def _magnitude_errors(link: dict) -> list[str]:
    """Recompute exposed magnitude from public arithmetic when F2 fields exist."""
    seq = link.get("seq")
    errors: list[str] = []
    try:
        kry_minted = _finite_number(link.get("kry_minted"), f"seq {seq}: kry_minted",
                                    nonnegative=True)
        tokens_saved = _finite_number(link.get("tokens_saved", 0.0),
                                      f"seq {seq}: tokens_saved",
                                      nonnegative=True)
        earn_rate = _finite_number(link.get("earn_rate", 0.0),
                                   f"seq {seq}: earn_rate",
                                   nonnegative=True)
    except ValueError as exc:
        return [str(exc)]
    # A link that DECLARES its F2 inputs cannot mint positive KRY from zero tokens/rate:
    # 0 × 0 × M = 0, so a positive kry_minted here is fabricated (the zero-rate magnitude
    # bypass). Only a genuine legacy link that OMITS the inputs is honestly uncheckable.
    declares_inputs = "earn_rate" in link and "tokens_saved" in link
    if tokens_saved <= 0 or earn_rate <= 0:
        if declares_inputs and kry_minted > 0:
            errors.append(
                f"seq {seq}: kry_minted {kry_minted} with tokens_saved={tokens_saved} / "
                f"earn_rate={earn_rate} — magnitude not derivable from declared inputs")
        return errors
    try:
        from kry.kry_mint import _EARN_RATES
        from kry.kry_token import published_multipliers
    except Exception as exc:
        return [f"seq {seq}: magnitude reference unavailable: {exc}"]
    event_type = link.get("event_type", "")
    # F3: an UNKNOWN event_type must still use mint's 0.5 fallback rate — reject an arbitrary rate
    # paired with an off-table event_type instead of silently skipping the check.
    published_rate = _EARN_RATES.get(event_type, 0.5)
    if abs(earn_rate - published_rate) > 1e-6:
        errors.append(
            f"seq {seq}: earn_rate {earn_rate} != published {published_rate} "
            f"for '{event_type}' — non-standard rate")
    implied = kry_minted / (tokens_saved * earn_rate)
    if not any(abs(implied - m) <= 1e-3 for m in set(published_multipliers().values())):
        errors.append(
            f"seq {seq}: implied price multiplier {implied:.4f} is "
            f"not a published value — magnitude used a non-public price")
    return errors


def _veracity_number(v: dict, key: str, errors: list[str]) -> float:
    try:
        return _finite_number(v.get(key, 0.0), f"veracity.{key}", nonnegative=True)
    except ValueError as exc:
        errors.append(str(exc))
        return 0.0


def build_attestation(mint_log_path: Optional[Path] = None) -> Attestation:
    """Build a public attestation from the private mint log.

    Reads the private log, verifies the chain, and emits a content-free proof.
    The private log is never modified and never included in the output.
    """
    path = mint_log_path or _MINT_LOG_PATH
    links: list[AttestationLink] = []
    type_counts: Counter = Counter()
    tier_kry: dict[str, float] = {}
    total_kry = 0.0
    promotions: list = []      # (superseded_receipt_id, promoting_tier) — F5 overlay, shared w/ veracity_breakdown
    kry_by_receipt: dict = {}  # receipt_id -> (tier, kry), for the overlay
    ambiguous_ids: set = set()  # hash-bound ids seen more than once — never overlay anchors
    chain_head = "0" * 64
    valid = True

    prev_chain = "0" * 64
    prev_version = 0
    if path.exists():
        with open(path, encoding="utf-8") as f:
            for i, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                rec = _json_loads(line)

                # Verify chain link (same logic an external verifier runs) — v4 binds the public block.
                hv = rec.get("hash_version", 1)
                if not (isinstance(hv, int) and not isinstance(hv, bool)):
                    hv = 1
                if hv < prev_version:
                    valid = False   # monotonic version: a legacy line after a v4 line = downgrade/tamper
                prev_version = max(prev_version, hv)
                if hv >= 4:
                    from kry.kry_mint import _v4_public_block
                    block = _v4_public_block(
                        hash_version=hv,
                        tokens_saved=rec.get("tokens_saved", 0.0), ts=rec.get("ts"),
                        evidence_tier=rec.get("evidence_tier", "self_reported"),
                        metered_tokens=rec.get("metered_tokens"),
                        kry_minted=rec.get("kry_minted"), earn_rate=rec.get("earn_rate", 0.0),
                        supersedes=rec.get("supersedes"),   # F2: bind the promotion target
                        receipt_id=rec.get("receipt_id"),   # v6: bind receipt_id (overlay anchor)
                        event_type=rec.get("event_type"))   # v7: bind event_type
                    expected = hashlib.sha256(
                        f"{prev_chain}:{rec['receipt_hash']}:{block}".encode()).hexdigest()
                else:
                    expected = hashlib.sha256(
                        f"{prev_chain}:{rec['receipt_hash']}".encode()).hexdigest()
                if rec["chain_hash"] != expected:
                    valid = False

                tier = rec.get("evidence_tier", "self_reported")
                links.append(AttestationLink(
                    seq=i,
                    event_type=rec["event_type"],
                    kry_minted=rec["kry_minted"],
                    ts=rec["ts"],
                    receipt_hash=rec["receipt_hash"],
                    chain_hash=rec["chain_hash"],
                    sealed_evidence=_seal(rec.get("evidence_hash", "")),
                    evidence_tier=tier,
                    tokens_saved=rec.get("tokens_saved", 0.0),
                    earn_rate=rec.get("earn_rate", 0.0),
                    metered_tokens=rec.get("metered_tokens"),
                    hash_version=rec.get("hash_version", 1),
                    supersedes=rec.get("supersedes"),   # F2: expose the bound promotion target
                    receipt_id=rec.get("receipt_id", ""),   # F5: lets a stranger reproduce the overlay
                ))
                type_counts[rec["event_type"]] += 1
                tier_kry[tier] = tier_kry.get(tier, 0.0) + rec["kry_minted"]
                total_kry += rec["kry_minted"]
                rid = rec.get("receipt_id")
                _hv = rec.get("hash_version", 1)   # A1-1: only v6+ (hash-bound) receipts anchor the overlay
                if rid and isinstance(_hv, int) and not isinstance(_hv, bool) and _hv >= 6:
                    # invariant #2 (see kry_mint._apply_promotion_overlay): two hash-bound receipts
                    # sharing an id make the overlay target ambiguous. verify_attestation REJECTS such
                    # a chain, so a last-wins overwrite here would PUBLISH a veracity_floor the verifier
                    # refuses — mark the chain invalid and let neither row anchor a promotion.
                    if rid in kry_by_receipt or rid in ambiguous_ids:
                        ambiguous_ids.add(rid)
                        kry_by_receipt.pop(rid, None)
                        valid = False
                    else:
                        kry_by_receipt[rid] = (tier, rec["kry_minted"], i)   # i = forward-scan position
                sup = rec.get("supersedes")
                # invariant #4 ENFORCED: only a ZERO-value link may re-tier a prior receipt — a
                # positive-value promoter double-counts (see kry_mint._apply_promotion_overlay).
                if tier in ("tlsn_attested", "tee_attested") and sup and rec["kry_minted"] <= 0:
                    promotions.append((sup, tier, i))
                prev_chain = rec["chain_hash"]
                chain_head = rec["chain_hash"]

    # Veracity trust surface — what fraction rests on an external anchor vs the operator's word.
    # F5: apply the SAME promotion overlay the internal veracity_breakdown uses, so this PUBLIC
    # attestation's veracity_floor MATCHES the internal one (it previously ignored promotions and
    # under-reported the anchored fraction). Anchored = the non-self_reported tiers (_ANCHORED_TIERS).
    from kry.kry_mint import _ANCHORED_TIERS, _apply_promotion_overlay
    _apply_promotion_overlay(tier_kry, promotions, kry_by_receipt)
    anchored = sum(v for t, v in tier_kry.items() if t in _ANCHORED_TIERS)
    veracity = {
        "by_tier": {t: round(v, 4) for t, v in tier_kry.items()},
        "anchored_kry": round(anchored, 4),
        "self_reported_kry": round(tier_kry.get("self_reported", 0.0), 4),
        "veracity_floor": round(anchored / total_kry, 4) if total_kry > 0 else 0.0,
    }

    att = Attestation(
        receipts=len(links),
        total_kry=round(total_kry, 4),
        usd_equivalent=round(total_kry * 0.000025, 6),
        chain_head=chain_head,
        chain_valid=valid,
        event_type_counts=dict(type_counts),
        links=[asdict(lk) for lk in links],
        veracity=veracity,
    )
    att.attestation_hash = _attestation_hash(asdict(att))
    return att


def verify_attestation(attestation_json: str) -> tuple[bool, list[str]]:
    """An external party runs THIS to verify an attestation they received.

    Recomputes the chain from the public links alone — no access to the private
    mint log needed. Returns (is_valid, errors).
    """
    errors: list[str] = []
    try:
        data = _json_loads(attestation_json)
    except (json.JSONDecodeError, ValueError) as e:
        return False, [f"invalid JSON: {e}"]
    if not isinstance(data, dict):
        return False, ["attestation must be a JSON object"]

    prev_chain = "0" * 64
    prev_link_version = 0
    running_kry = 0.0
    type_counts: Counter = Counter()
    tier_kry: dict[str, float] = {}
    promotions: list = []      # F5: (superseded_receipt_id, promoting_tier) — reproduce the overlay
    kry_by_receipt: dict = {}  # receipt_id -> (tier, kry), so the declared overlaid by_tier is verifiable
    links = data.get("links", [])
    if not isinstance(links, list):
        errors.append("links must be a JSON list")
        links = []
    try:
        receipts = _json_integer(data.get("receipts"), "receipts", nonnegative=True)
    except ValueError as exc:
        errors.append(str(exc))
        receipts = None
    if receipts != len(links):
        errors.append(
            f"receipts mismatch: claimed {data.get('receipts')}, "
            f"links contain {len(links)}")
    if data.get("chain_valid") is not True:
        errors.append("chain_valid is not true")
    # SPEC §3.1: these envelope fields MUST be PRESENT. A wholly absent key is not a silent default
    # — an absent total_kry is not a declared 0.0, an absent veracity is not "no claim". Same rule
    # the stranger-facing scripts/kry_verify.py and verifiers/js enforce.
    for required in ("total_kry", "usd_equivalent", "veracity"):
        if required not in data:
            errors.append(f"{required} missing — SPEC §3.1 requires it")

    for _pos, link in enumerate(links):
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
        # v4 binds the public economic block into chain_hash, so a forged evidence_tier / kry_minted /
        # earn_rate / token count breaks the chain HERE (on the public surface), not just in the private
        # receipt_hash the stranger can't re-derive. Legacy (<4) links use the prev:receipt formula.
        hv = link.get("hash_version", 1)
        # SPEC §3.6 (fail closed): this verifier defines the v4..v7 block shapes plus the legacy
        # <=3 formula. An unrecognized version must be INVALID — never hashed with the newest shape
        # we happen to know, which would verify a v8 link against a v7-shaped block. Same rule the
        # stranger-facing scripts/kry_verify.py and verifiers/js enforce.
        if isinstance(hv, bool) or not isinstance(hv, int) or hv > 7:
            errors.append(f"seq {seq}: unrecognized hash_version {link.get('hash_version')!r} — "
                          f"this verifier understands 7 and below (fail closed)")
            prev_chain = chain_hash
            continue
        # Monotonic version: a v4 link cannot be followed by a legacy one — that is a partial-tail
        # downgrade (forge a suffix link's tier + re-stamp it under the weaker legacy formula). The
        # private verify_chain enforces this too; the PUBLIC verifier must as well (GPT v4-review HIGH).
        if hv < prev_link_version:
            errors.append(f"seq {seq}: hash_version {hv} < previous {prev_link_version} — "
                          f"version downgrade (partial-tail rollback attempt)")
        prev_link_version = max(prev_link_version, hv)
        if hv >= 4:
            from kry.kry_mint import _v4_public_block
            block = _v4_public_block(
                hash_version=hv,
                tokens_saved=link.get("tokens_saved", 0.0), ts=link.get("ts"),
                evidence_tier=link.get("evidence_tier", "self_reported"),
                metered_tokens=link.get("metered_tokens"),
                # RAW kry_minted (not the _finite_number-normalized one) so this matches the standalone
                # kry_verify replica byte-for-byte (GPT v4-review MEDIUM: the two diverged on int vs float).
                kry_minted=link.get("kry_minted"), earn_rate=link.get("earn_rate", 0.0),
                supersedes=link.get("supersedes"),   # F2: bind the promotion target on the public surface
                receipt_id=link.get("receipt_id"),   # v6: bind receipt_id so a relabel breaks the chain
                event_type=link.get("event_type"))   # v7: bind event_type (closes same-rate relabel)
            expected = hashlib.sha256(f"{prev_chain}:{receipt_hash}:{block}".encode()).hexdigest()
        else:
            expected = hashlib.sha256(f"{prev_chain}:{receipt_hash}".encode()).hexdigest()
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
        # The tier is only bound on the PUBLIC surface at v4 (the v4 block above). A pre-v4 link
        # claiming a non-self_reported tier is operator-asserted, not chain-bound, so it must not
        # inflate the anchored fraction of the veracity floor. The standalone kry_verify enforces
        # this; the package verifier (used by kry_settlement) MUST too — reject and coerce.
        if hv < 4 and tier != "self_reported":
            errors.append(f"seq {seq}: hash_version {hv} cannot carry a non-self_reported "
                          f"tier ({tier}) — unbound on the public surface (only v4+ binds it)")
            tier = "self_reported"
        tier_kry[tier] = tier_kry.get(tier, 0.0) + kry_minted
        rid = link.get("receipt_id")
        # A1-1: only a HASH-BOUND (v6+) receipt may anchor a promotion overlay (a v4/v5 receipt_id is
        # mutable → relabel/swap to redirect a promotion onto a larger receipt). Reject duplicate ids
        # too (a forged chain could collide two v6+ ids; the last-wins dict would pick the larger).
        if rid and isinstance(hv, int) and not isinstance(hv, bool) and hv >= 6:
            if rid in kry_by_receipt:
                errors.append(f"seq {seq}: duplicate receipt_id {rid!r} among hash-bound receipts")
            kry_by_receipt[rid] = (tier, kry_minted, _pos)
        sup = link.get("supersedes")
        # invariant #4 ENFORCED on the verifier boundary: a promotion must be zero-value, so a forged
        # positive-value tlsn/tee link with `supersedes` cannot re-tier a prior receipt onto itself
        # (the double-count that read veracity_floor 1.0 vs the honest 0.333).
        if tier in ("tlsn_attested", "tee_attested") and sup and kry_minted <= 0:
            promotions.append((sup, tier, _pos))
        errors.extend(_magnitude_errors(link))
        errors.extend(_tier_schema_errors(link))
        prev_chain = chain_hash

    # Verify aggregate matches the sum
    try:
        total_kry = _finite_number(data.get("total_kry", 0.0), "total_kry",
                                   nonnegative=True)
    except ValueError as exc:
        errors.append(str(exc))
        total_kry = 0.0
    # SPEC §3.1: total_kry MUST equal round(Σ kry_minted, 4) — compare against the SAME rounding
    # the spec mandates, at the one _COMPARE_EPS tolerance (P-TOL).
    derived_total = round(running_kry, 4)
    if abs(derived_total - total_kry) > _COMPARE_EPS:
        errors.append(
            f"total_kry mismatch: claimed {data.get('total_kry')}, "
            f"chain sums to {derived_total}")

    # Verify the head matches the last link. SPEC §3.1: an EMPTY chain is checked too — its head
    # must be the genesis value, so a declared (or absent) head cannot ride along unverified.
    if links:
        if data.get("chain_head") != links[-1].get("chain_hash"):
            errors.append("chain_head does not match last link")
    elif data.get("chain_head") != "0" * 64:
        errors.append("chain_head must be the genesis value for an empty chain")
    if not isinstance(data.get("event_type_counts"), dict):
        errors.append("event_type_counts must be a JSON object")
    elif data.get("event_type_counts") != dict(type_counts):
        errors.append(
            f"event_type_counts mismatch: claimed {data.get('event_type_counts')}, "
            f"links imply {dict(type_counts)}")
    # SPEC §3.1: usd_equivalent MUST equal round(total_kry * 0.000025, 6) — over the ROUNDED
    # total_kry, so a cold implementer derives the same number from the spec text alone.
    expected_usd = round(derived_total * 0.000025, 6)
    try:
        usd_equivalent = _finite_number(data.get("usd_equivalent", 0.0),
                                        "usd_equivalent", nonnegative=True)
    except ValueError as exc:
        errors.append(str(exc))
        usd_equivalent = 0.0
    if abs(usd_equivalent - expected_usd) > _COMPARE_EPS:
        errors.append(
            f"usd_equivalent mismatch: claimed {data.get('usd_equivalent')}, "
            f"links imply {expected_usd}")
    claimed_hash = data.get("attestation_hash")
    if not isinstance(claimed_hash, str) or not claimed_hash:
        errors.append("attestation_hash missing")
    else:
        try:
            expected_hash = _attestation_hash(data)
        except ValueError as exc:
            errors.append(f"attestation JSON is not standards-compliant: {exc}")
        else:
            if claimed_hash != expected_hash:
                errors.append("attestation_hash mismatch — public metadata may have been altered")

    # F5: apply the SAME promotion overlay build_attestation uses, so the declared (overlaid) by_tier is
    # reproducible by a verifier — a promotion re-tiers its superseded receipt's value onto the promoting
    # tier. No-op when there are no promotions, so non-promotion attestations are byte-unaffected.
    from kry.kry_mint import _ANCHORED_TIERS, _apply_promotion_overlay
    _apply_promotion_overlay(tier_kry, promotions, kry_by_receipt)
    # OUTCOME GUARD (see _apply_promotion_overlay's SAFETY CONTRACT): the overlay is a pure transfer,
    # so no tier may be negative afterwards. A negative tier means a promotion moved value that was
    # not there (a forged chain / a future overlay bug) — caught even if an invariant were missed.
    if any(val < -0.01 for val in tier_kry.values()):
        errors.append("veracity by_tier went negative after the promotion overlay — "
                      "a promotion moved value that was not there")

    # Verify the veracity breakdown matches the links — the trust surface itself
    # must be honest. An operator can't claim a high external-anchor floor unless
    # the per-link tiers back it (and tiers are bound into v2+ receipt hashes, so
    # a forged tier also breaks the chain above).
    v = data.get("veracity")
    # SPEC §3.1 lists `veracity` as required and §3.5 defines it as an OBJECT, so a key that is
    # PRESENT but null / a number / a string / a list is INVALID — a non-object cannot carry the
    # trust surface, and treating it as "no claim" would let an operator drop the floor silently.
    if "veracity" in data and not isinstance(v, dict):
        errors.append("veracity must be a JSON object — SPEC §3.5")
    # SPEC §3.5 requires all four fields. A missing sub-key used to SKIP the whole block below
    # (by_tier) or silently read as 0.0 (the numbers), so an operator could drop the trust surface
    # and still verify — while verifiers/js rejected the same input. Require them explicitly.
    # `externally_anchored_kry` stays accepted as the pre-rename alias for `anchored_kry`.
    if isinstance(v, dict):
        for _field in ("by_tier", "self_reported_kry", "veracity_floor"):
            if _field not in v:
                errors.append(f"veracity.{_field} missing — SPEC §3.5 requires it")
        if "anchored_kry" not in v and "externally_anchored_kry" not in v:
            errors.append("veracity.anchored_kry missing — SPEC §3.5 requires it")
    if isinstance(v, dict) and v.get("by_tier") is not None:
        # S3: anchored = only KNOWN anchored tiers, not "anything that isn't self_reported". A forged
        # or typo'd tier (e.g. magic_attested) must NOT inflate the veracity floor. Matches the builder.
        anchored = sum(val for t, val in tier_kry.items() if t in _ANCHORED_TIERS)
        # SPEC §3.5: every one of these is a round(...,4) value derived from the ROUNDED
        # anchored/total, so the comparisons below are against the same rounding (P-TOL).
        derived_anchored = round(anchored, 4)
        derived_self = round(tier_kry.get("self_reported", 0.0), 4)
        derived_floor = round(derived_anchored / derived_total, 4) if derived_total > 0 else 0.0
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
        # P-TOL: the key SET must match exactly (size + every derived key present, so an invented or
        # dropped tier is caught) and every value must land within _COMPARE_EPS of its round-4
        # derivation. Same rule, same number, as scripts/kry_verify.py and verifiers/js.
        if len(claimed_by_tier) != len(by_tier) or any(
                not isinstance(claimed_by_tier.get(t), (int, float))
                or isinstance(claimed_by_tier.get(t), bool)
                or abs(claimed_by_tier[t] - by_tier[t]) > _COMPARE_EPS
                for t in by_tier):
            errors.append(
                f"veracity by_tier mismatch: claimed {v.get('by_tier')}, "
                f"links imply {by_tier}")
        # Legacy alias: pre-round-4 attestations used `externally_anchored_kry` (renamed to
        # `anchored_kry` because the metered/holdout tiers are operator-run). Accept the old field so
        # an otherwise-valid older attestation still verifies — the value is cross-checked either way.
        _vh = v if "anchored_kry" in v else {**v, "anchored_kry": v.get("externally_anchored_kry", 0.0)}
        if abs(_veracity_number(_vh, "anchored_kry", errors) - derived_anchored) > _COMPARE_EPS:
            errors.append("anchored_kry mismatch")
        if abs(_veracity_number(v, "self_reported_kry", errors) - derived_self) > _COMPARE_EPS:
            errors.append("self_reported_kry mismatch")
        if abs(_veracity_number(v, "veracity_floor", errors) - derived_floor) > _COMPARE_EPS:
            errors.append(
                f"veracity_floor mismatch: claimed {v.get('veracity_floor')}, "
                f"links imply {derived_floor:.4f} — trust surface misstated")

    return len(errors) == 0, errors


def assert_no_content_leak(attestation_json: str, private_strings: list[str]) -> bool:
    """Safety check: confirm no private content appears in the attestation.

    Pass any sensitive strings (prompt fragments, model names, file paths) and
    this confirms NONE of them appear anywhere in the public attestation.
    Returns True if clean (safe to share), False if a leak is detected.
    """
    text = attestation_json.lower()
    for secret in private_strings:
        if secret and len(secret) >= 4 and secret.lower() in text:
            return False
    return True
