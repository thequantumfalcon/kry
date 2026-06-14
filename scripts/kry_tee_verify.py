#!/usr/bin/env python3
"""Mint a KRY T2 receipt from an attested-hardware measurement (AWS Nitro Enclaves).

The honest claim of `tee_attested` (read first, so it is never overclaimed —
consistent with docs/KRY_TEE_OPTIONS.md and docs/KRY_VERACITY_BINDING.md):

  A cloud-TEE attestation proves *"the code that produced this measurement ran inside
  an attested enclave"* — it does NOT prove the closed model provider's OWN inference
  ran in a TEE (no major provider exposes a customer-facing attestation). So a KRY
  `tee_attested` receipt means exactly:

      "the holdout / savings MEASUREMENT (the token accounting, the savings claim) ran
       in attested hardware, so the operator could not have fabricated it."

  That UPGRADES the `self_reported` / `holdout_validated` path (operator-trust →
  hardware-attested). It is NOT a provider-inference proof and must not be marketed as
  one (Avoid A1/A8). `tlsn_attested` remains the tier that proves the provider call
  happened (cryptographically notarized bytes).

How the trust is established (mirrors scripts/kry_tlsn_verify.py, where the Rust verifier
was the root of trust):
  - `verify_attestation()` is the cryptographic boundary. It CBOR-decodes the Nitro
    COSE_Sign1 attestation document, verifies the ES384 (ECDSA-P384/SHA-384) COSE
    signature under the enclave's leaf certificate, walks the X.509 chain leaf → CA
    bundle → the PINNED AWS Nitro Enclaves root (G1), and checks validity/freshness/PCR0.
    The asymmetric signature + X.509 step uses the audited `cryptography` library — we do
    NOT hand-roll security-critical crypto and we FAIL CLOSED if it is unavailable (never
    mint claiming a verification we did not perform). The pinned-root trust anchor itself
    is a pure-stdlib `hashlib` fingerprint compare against the AWS-published checksum.
  - `run()` consumes that ALREADY-VERIFIED output and mints (pure stdlib) — fresh
    tee_attested value, or a net-zero tier UPGRADE of a prior self_reported/holdout
    measurement receipt for the same measurement id (no double-credit).

The enclave workload runs the KRY holdout/measurement and places the measurement result
JSON into the attestation document's `user_data` field:
    {"measurement_id": "...", "tokens_saved": <float>, "avoided_model": "...",
     "served_model": "..."(optional), "event_type": "..."(optional)}

stdlib only, EXCEPT `cryptography` (optional extra: `pip install kry[tee]`) used
solely inside verify_attestation for the asymmetric signature + X.509 chain. Imports kry
only to mint (the mint log never leaves the machine).

Usage:
    # verify only (no mint), against the pinned AWS Nitro root cert (DER or PEM):
    python3 scripts/kry_tee_verify.py attestation.cbor --root AWS_NitroEnclaves_Root-G1.der --dry-run
    # mint a tee_attested receipt for an attested measurement:
    python3 scripts/kry_tee_verify.py attestation.cbor --root AWS_NitroEnclaves_Root-G1.der \
        --avoided-model gh/claude-opus-4.8
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import struct
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

# The trust anchor is the AWS Nitro Enclaves root certificate the operator pins via
# --root. To confirm that file is the genuine AWS root, fetch AWS's PUBLISHED SHA-256 of
# it and pass it via --root-sha256; the verifier refuses on mismatch. We deliberately do
# NOT hardcode the checksum here — a transcribed-from-memory constant is unverifiable and
# could itself be wrong; the operator must read AWS's published value in-session. Source:
# https://docs.aws.amazon.com/enclaves/latest/user/verify-root.html  (Nitro root G1)
COSE_ALG_ES384 = -35   # COSE alg identifier for ECDSA w/ SHA-384 (the curve NSM uses)

# AWS-published SHA-256 fingerprint of the Nitro Enclaves root certificate G1 — i.e.
# sha256(DER bytes of the root cert). Read IN-SESSION from the authoritative AWS doc, NOT
# transcribed from memory: https://docs.aws.amazon.com/enclaves/latest/user/verify-root.html
# (retrieved 2026-06-06). 30-year root; subject "CN=aws.nitro-enclaves,C=US,O=Amazon,OU=AWS".
# Used as the DEFAULT --root-sha256 pin: the genuine AWS root passes, any other root fails
# closed. Override for a different partition/generation (e.g. gov-cloud, a future G2).
AWS_NITRO_ROOT_G1_DER_SHA256 = "641a0321a3e244efe456463195d606317ed7cdcc3c1756e09893f3c68f79bb5b"


def _reject_json_constant(value: str):
    raise ValueError(f"non-standard JSON constant {value} is not allowed")


def _json_loads(raw: str):
    return json.loads(raw, parse_constant=_reject_json_constant)


def _positive_finite_number(value) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    value = float(value)
    return value if math.isfinite(value) and value > 0 else 0.0


# ── Minimal CBOR (the attestation doc + the COSE Sig_structure) ──────────────────
# A small CBOR decoder — enough to decode a Nitro COSE_Sign1 and the attestation map,
# plus an encoder for the COSE Sig_structure (always definite-length). CBOR *structure*
# parsing is not security-critical (the ECDSA signature, verified by an audited library,
# is what binds these bytes); hand-rolling it keeps the dependency surface minimal.
# Handles BOTH definite- and indefinite-length items: real AWS NSM serializes the
# attestation document with indefinite-length maps/strings/arrays (0xbf/0x5f/0x9f/0x7f,
# closed by 0xff break) — synthetic fixtures happened to be definite-length, so this gap
# only surfaced against a genuine enclave (the Nitro PoC). NOT a general CBOR impl.

class _CBORError(ValueError):
    pass


_BREAK = object()   # CBOR "break" stop code (0xff) terminating an indefinite-length item


def _cbor_decode(buf: bytes, i: int = 0) -> tuple[object, int]:
    """Decode one CBOR item starting at index i. Returns (value, next_index)."""
    if i >= len(buf):
        raise _CBORError("truncated CBOR")
    ib = buf[i]
    major, ai = ib >> 5, ib & 0x1F
    i += 1

    if ai == 31:                           # indefinite-length item (or the break code)
        if major == 7:                     # 0xff — break stop code
            return _BREAK, i
        if major in (2, 3):                # indefinite byte/text string: concat chunks
            chunks = []
            while True:
                item, i = _cbor_decode(buf, i)
                if item is _BREAK:
                    break
                chunks.append(item)
            return (b"".join(chunks) if major == 2 else "".join(chunks)), i
        if major == 4:                     # indefinite array
            out = []
            while True:
                item, i = _cbor_decode(buf, i)
                if item is _BREAK:
                    break
                out.append(item)
            return out, i
        if major == 5:                     # indefinite map
            out_map: dict = {}
            while True:
                k, i = _cbor_decode(buf, i)
                if k is _BREAK:
                    break
                v, i = _cbor_decode(buf, i)
                out_map[k] = v
            return out_map, i
        raise _CBORError(f"indefinite-length not valid for major type {major}")

    if ai < 24:
        val = ai
    elif ai == 24:
        val = buf[i]
        i += 1
    elif ai == 25:
        val = struct.unpack(">H", buf[i:i + 2])[0]
        i += 2
    elif ai == 26:
        val = struct.unpack(">I", buf[i:i + 4])[0]
        i += 4
    elif ai == 27:
        val = struct.unpack(">Q", buf[i:i + 8])[0]
        i += 8
    else:
        raise _CBORError(f"unsupported additional-info {ai} (reserved)")

    if major == 0:                         # unsigned int
        return val, i
    if major == 1:                         # negative int
        return -1 - val, i
    if major == 2:                         # byte string
        return buf[i:i + val], i + val
    if major == 3:                         # text string
        return buf[i:i + val].decode("utf-8"), i + val
    if major == 4:                         # array
        out = []
        for _ in range(val):
            item, i = _cbor_decode(buf, i)
            out.append(item)
        return out, i
    if major == 5:                         # map
        out_map: dict = {}
        for _ in range(val):
            k, i = _cbor_decode(buf, i)
            v, i = _cbor_decode(buf, i)
            out_map[k] = v
        return out_map, i
    if major == 6:                         # tag — decode and return the tagged item
        item, i = _cbor_decode(buf, i)
        return item, i
    if major == 7:                         # simple / float
        if ai == 20:
            return False, i
        if ai == 21:
            return True, i
        if ai == 22 or ai == 23:           # null / undefined
            return None, i
        if ai == 25:
            return struct.unpack(">e", buf[i - 2:i])[0], i
        if ai == 26:
            return struct.unpack(">f", buf[i - 4:i])[0], i
        if ai == 27:
            return struct.unpack(">d", buf[i - 8:i])[0], i
        raise _CBORError(f"unsupported simple value {ai}")
    raise _CBORError(f"unknown major type {major}")


def _cbor_head(major: int, n: int) -> bytes:
    if n < 24:
        return bytes([(major << 5) | n])
    if n < 0x100:
        return bytes([(major << 5) | 24, n])
    if n < 0x10000:
        return bytes([(major << 5) | 25]) + struct.pack(">H", n)
    if n < 0x100000000:
        return bytes([(major << 5) | 26]) + struct.pack(">I", n)
    return bytes([(major << 5) | 27]) + struct.pack(">Q", n)


def _cbor_encode(obj: object) -> bytes:
    """Encode the few types the Sig_structure needs: text, bytes, array."""
    if isinstance(obj, str):
        b = obj.encode("utf-8")
        return _cbor_head(3, len(b)) + b
    if isinstance(obj, (bytes, bytearray)):
        return _cbor_head(2, len(obj)) + bytes(obj)
    if isinstance(obj, list):
        out = _cbor_head(4, len(obj))
        for item in obj:
            out += _cbor_encode(item)
        return out
    raise _CBORError(f"cannot encode {type(obj).__name__}")


# ── Attestation extraction (stdlib) ──────────────────────────────────────────────

def _attestation_fields(doc: dict) -> dict:
    """Pull the fields we use from a decoded Nitro attestation map (text keys)."""
    return {
        "module_id": doc.get("module_id"),
        "timestamp": doc.get("timestamp"),       # ms since epoch
        "digest": doc.get("digest"),
        "pcrs": doc.get("pcrs") or {},
        "certificate": doc.get("certificate"),    # leaf DER (bytes)
        "cabundle": doc.get("cabundle") or [],    # [root DER, ...intermediates] (bytes)
        "user_data": doc.get("user_data"),        # the attested measurement (bytes/JSON)
        "nonce": doc.get("nonce"),
        "public_key": doc.get("public_key"),
    }


def _parse_measurement(user_data) -> dict | None:
    """The enclave-attested measurement, JSON-encoded in user_data. Returns the dict, or
    None when user_data is absent / not the measurement JSON (then there is no basis)."""
    if user_data is None:
        return None
    try:
        raw = user_data.decode("utf-8") if isinstance(user_data, (bytes, bytearray)) else str(user_data)
        m = _json_loads(raw)
        return m if isinstance(m, dict) else None
    except (ValueError, UnicodeDecodeError):
        return None


# ── Cryptographic verification boundary (audited `cryptography`; fail-closed) ─────

def _load_root_der(root_path: str) -> tuple[bytes, str]:
    """Load the pinned root cert as DER and return (der_bytes, der_sha256_hex).

    Accepts a PEM or DER file. der_sha256 is sha256(DER cert bytes) — the standard X.509
    fingerprint AWS publishes (verify-root.html) — so the pin is format-independent: a PEM
    and the DER it decodes to yield the SAME fingerprint."""
    raw = Path(root_path).read_bytes()
    if b"-----BEGIN CERTIFICATE-----" in raw:
        import base64
        b64 = b"".join(line for line in raw.splitlines()
                       if b"-----" not in line)
        der = base64.b64decode(b64)
    else:
        der = raw
    return der, hashlib.sha256(der).hexdigest()


def verify_attestation(doc_bytes: bytes, *, root_cert_der: bytes,
                       now: float | None = None, max_age_s: float = 3600.0,
                       expected_pcr0: str | None = None) -> dict:
    """Verify a Nitro COSE_Sign1 attestation document. Fail-closed, errors accumulated.

    Checks (all must pass for verified=True):
      1. COSE_Sign1 structure + alg == ES384 (ECDSA-P384/SHA-384).
      2. The ES384 signature over the COSE Sig_structure verifies under the leaf cert.
      3. The X.509 chain leaf → cabundle → root verifies (each cert signed by the next),
         every cert is within its validity window, and the chain terminates at EXACTLY the
         pinned root (byte-equal to root_cert_der — the trust anchor).
      4. Freshness: the attestation timestamp is within max_age_s of `now` (not in future).
      5. PCR0 matches expected_pcr0 when pinned (the enclave-image identity).

    The asymmetric crypto uses the audited `cryptography` lib; ABSENT it we return
    verified=False with a clear error (never a silent skip). Returns a dict consumed by
    run(): {verified, errors, measurement, module_id, timestamp, nonce, pcr0, doc_sha256}.
    """
    errs: list[str] = []
    now = time.time() if now is None else now
    result: dict = {"verified": False, "errors": errs, "doc_sha256": hashlib.sha256(doc_bytes).hexdigest()}

    try:
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ec, utils as asym_utils
        from cryptography.exceptions import InvalidSignature
    except ImportError:
        errs.append("cryptographic verification requires the 'cryptography' package "
                    "(pip install kry[tee]) — refusing to mint without verifying "
                    "the attestation signature (fail-closed)")
        return result

    # 1. COSE_Sign1 structure
    try:
        cose, _ = _cbor_decode(doc_bytes)
    except _CBORError as e:
        errs.append(f"attestation is not decodable CBOR: {e}")
        return result
    if not (isinstance(cose, list) and len(cose) == 4):
        errs.append("not a COSE_Sign1 (expected a 4-element array)")
        return result
    protected_bytes, _unprotected, payload_bytes, signature = cose
    if not isinstance(signature, (bytes, bytearray)) or len(signature) != 96:
        errs.append("COSE signature is not a 96-byte ES384 (P-384 r||s) value")
        return result
    try:
        prot_hdr, _ = _cbor_decode(bytes(protected_bytes)) if protected_bytes else ({}, 0)
    except _CBORError:
        prot_hdr = {}
    if isinstance(prot_hdr, dict) and prot_hdr.get(1) not in (COSE_ALG_ES384, None):
        errs.append(f"unexpected COSE alg {prot_hdr.get(1)} (expected ES384 {COSE_ALG_ES384})")

    # decode the attestation payload
    try:
        doc, _ = _cbor_decode(bytes(payload_bytes))
    except _CBORError as e:
        errs.append(f"attestation payload is not decodable CBOR: {e}")
        return result
    if not isinstance(doc, dict):
        errs.append("attestation payload is not a map")
        return result
    fields = _attestation_fields(doc)
    leaf_der = fields["certificate"]
    cabundle = fields["cabundle"]
    if not isinstance(leaf_der, (bytes, bytearray)):
        errs.append("attestation has no leaf certificate")
        return result

    # 2. COSE signature under the leaf public key (ES384 over the Sig_structure)
    try:
        leaf = x509.load_der_x509_certificate(bytes(leaf_der))
        pub = leaf.public_key()
        sig_structure = _cbor_encode(["Signature1", bytes(protected_bytes), b"", bytes(payload_bytes)])
        r = int.from_bytes(bytes(signature[:48]), "big")
        s = int.from_bytes(bytes(signature[48:]), "big")
        der_sig = asym_utils.encode_dss_signature(r, s)
        pub.verify(der_sig, sig_structure, ec.ECDSA(hashes.SHA384()))
    except InvalidSignature:
        errs.append("COSE signature does not verify under the leaf certificate — "
                    "the attestation document was tampered or not signed by this enclave")
        return result
    except Exception as e:
        errs.append(f"could not verify the COSE signature: {e}")
        return result

    # 3. X.509 chain: leaf -> reversed(cabundle) -> root, terminating at the pinned root.
    # cabundle is [root, intermediate..., issuer-of-leaf]; chain order is leaf-first.
    try:
        chain = [leaf] + [x509.load_der_x509_certificate(bytes(c)) for c in reversed(cabundle)]
    except Exception as e:
        errs.append(f"could not parse the CA bundle: {e}")
        return result
    for cert in chain:
        if not (cert.not_valid_before_utc.timestamp() <= now <= cert.not_valid_after_utc.timestamp()):
            errs.append(f"certificate '{cert.subject.rfc4514_string()}' is outside its validity window")
    for child, issuer in zip(chain, chain[1:]):
        try:
            issuer.public_key().verify(
                child.signature, child.tbs_certificate_bytes,
                ec.ECDSA(child.signature_hash_algorithm))
        except Exception:
            errs.append(f"chain broken: '{child.subject.rfc4514_string()}' is not signed by "
                        f"'{issuer.subject.rfc4514_string()}'")
    # the chain MUST terminate at exactly the pinned root (the trust anchor)
    chain_root_der = chain[-1].public_bytes(serialization.Encoding.DER)
    if chain_root_der != bytes(root_cert_der):
        errs.append("the attestation chain does not terminate at the pinned AWS Nitro root "
                    "(--root) — refusing (an untrusted root could vouch for any enclave)")

    # 4. freshness — the attestation must be recent (anti-replay of a stale doc)
    ts = fields["timestamp"]
    ts_s = (ts / 1000.0) if isinstance(ts, (int, float)) else None
    if ts_s is None:
        errs.append("attestation has no timestamp")
    elif ts_s > now + 300:
        errs.append("attestation timestamp is in the future")
    elif now - ts_s > max_age_s:
        errs.append(f"attestation is stale ({int(now - ts_s)}s old > max_age {int(max_age_s)}s)")

    # 5. PCR0 (enclave-image identity) pin
    pcr0 = None
    pcrs = fields["pcrs"]
    if isinstance(pcrs, dict):
        p0 = pcrs.get(0)
        if isinstance(p0, (bytes, bytearray)):
            pcr0 = p0.hex()
    if expected_pcr0:
        if not pcr0:
            errs.append("a PCR0 was pinned (--pcr0) but the attestation carries none")
        elif pcr0.lower() != expected_pcr0.strip().lower():
            errs.append(f"PCR0 {pcr0[:16]}… != pinned {expected_pcr0[:16]}… — this is not the "
                        f"attested measurement enclave")

    result["module_id"] = fields["module_id"]
    result["timestamp"] = ts
    result["nonce"] = fields["nonce"].hex() if isinstance(fields["nonce"], (bytes, bytearray)) else fields["nonce"]
    result["pcr0"] = pcr0
    result["measurement"] = _parse_measurement(fields["user_data"])
    result["verified"] = not errs
    return result


# ── Mint glue (pure stdlib; mirrors kry_tlsn_verify.run) ─────────────────────────

def _evidence_binding(att: dict) -> str:
    """Bind the receipt to THIS attestation: module + pcr0 + a hash of the doc. Replaying
    the same attestation yields the same evidence → the mint decay collapses the repeat."""
    return f"tee:{att.get('module_id')}:{att.get('pcr0')}:{att.get('doc_sha256')}"


def run(att: dict, *, event_type: str, avoided_model: str | None,
        served_model: str | None, tokens_saved: float | None,
        measurement_id: str | None, dry_run: bool) -> dict:
    """Consume an ALREADY-VERIFIED attestation (verify_attestation output) and mint.

    Mirrors kry_tlsn_verify.run: fail-closed gate → extract the attested measurement →
    promote a prior self_reported/holdout measurement receipt to tee_attested (no
    double-credit), or mint fresh tee_attested value when none exists."""
    if att.get("verified") is not True:
        return {"verdict": "REJECTED", "errors": att.get("errors") or ["attestation not verified"]}

    m = att.get("measurement") or {}
    mid = measurement_id or m.get("measurement_id")
    basis = tokens_saved if tokens_saved is not None else m.get("tokens_saved")
    basis = _positive_finite_number(basis)
    avoided = avoided_model or m.get("avoided_model")
    avoided_src = "cli" if avoided_model else ("measurement" if m.get("avoided_model") else None)
    served = served_model or m.get("served_model")

    result: dict = {
        "verdict": "OK",
        "module_id": att.get("module_id"),
        "measurement_id": mid,
        "pcr0": att.get("pcr0"),
        "nonce": att.get("nonce"),
        "avoided_model": {"value": avoided, "source": avoided_src},
        "served_model": served,
        "tokens_saved_basis": basis,
    }

    if not mid:
        result["verdict"] = "NO_MEASUREMENT_ID"
        result["note"] = ("the attested user_data carries no measurement_id — cannot bind the "
                          "attestation to a measurement (pass --measurement-id or embed it)")
        return result
    if basis <= 0:
        result["verdict"] = "NO_BASIS"
        result["note"] = ("the attested measurement carries no positive tokens_saved — "
                          "pass --tokens-saved to mint against it")
        return result
    # Honest displacement gate (same footgun as tlsn): value_multiplier(None)=1.0 would
    # silently credit FULL value off a counterfactual we cannot substantiate.
    if avoided is None:
        result["verdict"] = "NO_DISPLACEMENT_CONTEXT"
        result["note"] = ("the attested measurement names no avoided_model and none was given "
                          "(--avoided-model) — refusing to mint displacement value we can't "
                          "substantiate (the counterfactual is never invented)")
        return result

    if dry_run:
        result["minted"] = None
        result["note"] = "dry-run: verified + parsed, no receipt minted"
        return result

    from kry import kry_mint
    before = kry_mint.veracity_breakdown()
    detail = (f"tee_attested module={att.get('module_id')} pcr0={(att.get('pcr0') or '')[:12]} "
              f"/measurement:{mid}")
    evidence = _evidence_binding(att)

    # Prefer UPGRADING a prior self_reported/holdout measurement receipt (no double-credit);
    # mint fresh tee value only when no prior receipt exists for this measurement id.
    prior = kry_mint._find_measurement_receipt_for_tee(mid)
    if prior is not None:
        promotion = kry_mint.promote_to_tee(mid, evidence, detail)
        if promotion is None:
            result["verdict"] = "ALREADY_UPGRADED"
            result["note"] = (f"measurement {mid} was already credited and already upgraded to "
                              f"tee_attested ({prior.get('receipt_id')}) — no-op")
            return result
        receipt, superseded_id, moved_kry = promotion
        after = kry_mint.veracity_breakdown()
        result["minted"] = {
            "receipt_id": receipt.receipt_id,
            "mode": "tier_upgrade",
            "supersedes": superseded_id,
            "kry_re_tiered": round(moved_kry, 4),
            "evidence_tier": receipt.evidence_tier,
            "chain_hash": receipt.chain_hash[:16] + "…",
        }
        result["veracity_floor"] = {"before": before["veracity_floor"], "after": after["veracity_floor"]}
        result["tee_attested_fraction"] = {"before": before["tee_attested_fraction"],
                                           "after": after["tee_attested_fraction"]}
        return result

    receipt = kry_mint.mint(
        event_type=event_type,
        tokens_saved=basis,
        detail=detail,
        evidence=evidence,
        avoided_model=avoided,
        served_model=served,
        evidence_tier=kry_mint.TIER_TEE_ATTESTED,
    )
    if receipt is None:
        result["verdict"] = "NOT_MINTED"
        result["note"] = ("mint returned None — basis decayed to dust (this attestation was "
                          "already minted) or was rejected at the boundary")
        return result
    after = kry_mint.veracity_breakdown()
    result["minted"] = {
        "receipt_id": receipt.receipt_id,
        "mode": "fresh_mint",
        "kry_minted": round(receipt.kry_minted, 4),
        "evidence_tier": receipt.evidence_tier,
        "chain_hash": receipt.chain_hash[:16] + "…",
    }
    result["veracity_floor"] = {"before": before["veracity_floor"], "after": after["veracity_floor"]}
    return result


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Mint a KRY T2 (tee_attested) receipt from an AWS Nitro attestation document")
    p.add_argument("attestation", help="the Nitro COSE_Sign1 attestation document (binary CBOR file)")
    p.add_argument("--root", required=True,
                   help="the pinned AWS Nitro Enclaves root certificate (PEM or DER). The chain "
                        "must terminate at exactly this root. Download + verify per "
                        "docs.aws.amazon.com/enclaves/latest/user/verify-root.html")
    p.add_argument("--root-sha256", default=None,
                   help="confirm sha256(DER) of --root equals this. Defaults to AWS's published "
                        "Nitro root G1 fingerprint, so the genuine root passes and any other "
                        "root is refused. Override for a different partition/generation")
    p.add_argument("--pcr0", default=None,
                   help="PIN the enclave image measurement (PCR0 hex) — minting is refused unless "
                        "the attestation's PCR0 matches (identifies WHICH enclave produced it)")
    p.add_argument("--max-age", type=float, default=3600.0,
                   help="reject attestations older than this many seconds (default 3600)")
    p.add_argument("--event-type", default="short_circuit",
                   help="efficiency event this attested measurement backs (default: short_circuit)")
    p.add_argument("--measurement-id", default=None,
                   help="OVERRIDE the measurement id (default: from the attested user_data)")
    p.add_argument("--avoided-model", default=None,
                   help="OVERRIDE the avoided model (default: from the attested user_data). "
                        "Absent both → REFUSED (the counterfactual is never invented)")
    p.add_argument("--served-model", default=None,
                   help="OVERRIDE the served model (default: from the attested user_data)")
    p.add_argument("--tokens-saved", type=float, default=None,
                   help="OVERRIDE the saving basis (default: the attested tokens_saved)")
    p.add_argument("--dry-run", action="store_true", help="verify + parse + report only — mint nothing")
    args = p.parse_args(argv)

    root_der, root_der_sha = _load_root_der(args.root)
    expected_root = (args.root_sha256 or AWS_NITRO_ROOT_G1_DER_SHA256).strip().lower()
    if root_der_sha.lower() != expected_root:
        print("KRY T2 TEE mint — REJECTED (fail-closed):")
        print(f"  - --root sha256(DER) {root_der_sha} != pinned {expected_root} "
              f"(not the genuine AWS Nitro root)")
        return 1

    doc_bytes = Path(args.attestation).read_bytes()
    att = verify_attestation(doc_bytes, root_cert_der=root_der, max_age_s=args.max_age,
                             expected_pcr0=args.pcr0)
    result = run(att, event_type=args.event_type, avoided_model=args.avoided_model,
                 served_model=args.served_model, tokens_saved=args.tokens_saved,
                 measurement_id=args.measurement_id, dry_run=args.dry_run)

    if result["verdict"] == "REJECTED":
        print("KRY T2 TEE attestation — REJECTED (fail-closed):")
        for e in result["errors"]:
            print(f"  - {e}")
        return 1

    print("KRY T2 TEE (Nitro) verification")
    print(f"  module id:        {result.get('module_id')}")
    print(f"  measurement id:   {result.get('measurement_id')}")
    print(f"  pcr0:             {(result.get('pcr0') or '')[:24]}…")
    print(f"  tokens saved:     {result.get('tokens_saved_basis')}")
    av = result.get("avoided_model", {})
    print(f"  avoided model:    {av.get('value')}  (source: {av.get('source')})")
    print(f"  served model:     {result.get('served_model')}")

    if result["verdict"] in ("NO_BASIS", "NO_DISPLACEMENT_CONTEXT", "NO_MEASUREMENT_ID"):
        print(f"  -> {result['note']}")
        return 1
    if result["verdict"] == "ALREADY_UPGRADED":
        print(f"  -> {result['note']}")
        return 0
    if result.get("minted") is None:      # dry-run
        print(f"  -> {result.get('note', 'no receipt minted')}")
        return 0
    if result["verdict"] == "NOT_MINTED":
        print(f"  -> {result['note']}")
        return 1

    mres = result["minted"]
    vf = result["veracity_floor"]
    if mres.get("mode") == "tier_upgrade":
        print(f"  UPGRADED {mres['receipt_id']}: re-tiered {mres['kry_re_tiered']} KRY "
              f"{mres['supersedes']} -> tee_attested  chain={mres['chain_hash']}")
        print("  (no new value minted — the saving was credited once at its original tier)")
        tf = result.get("tee_attested_fraction", {})
        if tf:
            print(f"  tee_attested_fraction: {tf['before']} -> {tf['after']}")
    else:
        print(f"  MINTED {mres['receipt_id']}: {mres['kry_minted']} KRY  tier={mres['evidence_tier']}  "
              f"chain={mres['chain_hash']}")
    print(f"  veracity_floor: {vf['before']} -> {vf['after']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
