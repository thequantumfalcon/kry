"""KRY-PQC verifier — a stranger runs THIS to check an attestation's authenticity.

Dependencies: liboqs-python (``oqs``) + Python stdlib. It does NOT need KRY
installed to check authenticity. If KRY *is* importable, it additionally re-runs
KRY's own stdlib chain check, so one command covers both axes.

Checks performed:
    1. message digest    — sha256(attestation bytes) matches the signature artifact
    2. authenticity      — ML-DSA signature verifies under the public key   [this tier]
    3. integrity (opt.)  — KRY's verify_attestation: hash chain intact       [KRY core]

Exit codes: 0 = authentic under a PINNED key; 2 = signature internally consistent but
the key is SELF-PROVIDED (authenticity UNVERIFIED — supply --public-key or
--expect-fingerprint); 1 = a check failed (digest / signature / fingerprint / chain).
"""
from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import json
import sys
from pathlib import Path

try:
    import oqs
except ImportError:  # pragma: no cover
    sys.stderr.write("kry_pqc verifier requires liboqs-python (the `oqs` module).\n")
    raise


def _unb64(s: str) -> bytes:
    return base64.b64decode(s.encode())


# M1: the signature algorithm is read from the attacker-supplied .sig.json. Pin it to the
# FIPS-204 ML-DSA parameter sets KRY actually signs with, so a bogus/unsupported `alg` fails
# closed (RESULT: FAILED, exit 1) inside the parse guard instead of reaching oqs.Signature(alg)
# and raising an uncaught MechanismNotSupportedError. The three sets have distinct key lengths,
# so pinning also blocks alg-confusion: a key pinned for one set won't verify under another.
ALLOWED_ALGS = frozenset({"ML-DSA-44", "ML-DSA-65", "ML-DSA-87"})

# L3 domain separation — these MUST match kry_pqc.signer (the sign<->verify roundtrip tests are the
# drift guard). A v2 single-signer signature is over _DOMAIN_SINGLE || attestation_bytes; v1 signed
# the raw bytes. main() dispatches on the artifact `scheme` so legacy v1 artifacts still verify.
_SCHEME_V2 = "kry-pqc/v2"
_SCHEME_V1 = "kry-pqc/v1"
_DOMAIN_SINGLE = b"kry-pqc/v2/single\x00"


def verify_signature(attestation_bytes: bytes, signature: bytes, public_key: bytes,
                     alg: str) -> bool:
    with oqs.Signature(alg) as verifier:
        return bool(verifier.verify(attestation_bytes, signature, public_key))


def _chain_check(attestation_text: str):
    """Run KRY's own stdlib chain verifier if KRY is importable.

    Returns (ran: bool, ok: bool, errors: list[str]).
    """
    try:
        from kry.kry_attest import verify_attestation
    except Exception:
        return False, False, []
    ok, errors = verify_attestation(attestation_text)
    return True, ok, errors


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        prog="kry_pqc.verify",
        description="Verify the post-quantum authenticity of a KRY attestation.")
    p.add_argument("--attestation", required=True, help="the attestation JSON file")
    p.add_argument("--signature", required=True, help="the .sig.json artifact")
    p.add_argument("--public-key", default=None,
                   help="the SIGNER'S PUBLISHED public-key file, obtained out-of-band. Without "
                        "it the artifact's embedded key is self-provided and authenticity is "
                        "UNVERIFIABLE (anyone can self-sign their own artifact).")
    p.add_argument("--expect-fingerprint", default=None,
                   help="pin the signer's key by its published sha256 fingerprint (any prefix); "
                        "verification fails if the key does not match it.")
    p.add_argument("--require-chain", action="store_true",
                   help="fail unless KRY's chain-integrity check can also run and passes")
    p.add_argument("--require-v2", action="store_true",
                   help="refuse a legacy kry-pqc/v1 (raw-byte, non-domain-separated) artifact")
    args = p.parse_args(argv)

    att_path = Path(args.attestation)
    attestation_bytes = att_path.read_bytes()
    # The .sig.json artifact is attacker-supplied. Parse it and read its required fields behind a
    # guard so a malformed artifact (not JSON / not an object / missing alg|signature|public_key /
    # non-base64) yields the documented "RESULT: FAILED" + exit 1, not an uncaught traceback.
    try:
        artifact = json.loads(Path(args.signature).read_text())
        if not isinstance(artifact, dict):
            raise ValueError("signature artifact is not a JSON object")
        alg = artifact["alg"]
        if alg not in ALLOWED_ALGS:
            raise ValueError(f"unsupported alg {alg!r}; allowed: {sorted(ALLOWED_ALGS)}")
        scheme = artifact.get("scheme")
        if scheme not in (_SCHEME_V1, _SCHEME_V2):
            raise ValueError(f"unsupported scheme {scheme!r}; allowed: {_SCHEME_V1!r}/{_SCHEME_V2!r}")
        signature = _unb64(artifact["signature"])
        if args.public_key:
            public_key = _unb64(Path(args.public_key).read_text().strip())
            key_source = "out-of-band (--public-key)"
        else:
            public_key = _unb64(artifact["public_key"])
            key_source = "SELF-PROVIDED (embedded in the artifact)"
    except (ValueError, KeyError, TypeError, binascii.Error, json.JSONDecodeError) as e:
        print(f"RESULT: FAILED — malformed signature artifact: {e}")
        return 1
    full_fp = hashlib.sha256(public_key).hexdigest()
    pk_fp = full_fp[:16]
    # Authenticity is ESTABLISHED only if the key is pinned — supplied out-of-band, or
    # matched to a published fingerprint. A valid signature under a self-provided key
    # proves only internal consistency; anyone can self-sign their own attestation.
    pinned = bool(args.public_key) or bool(args.expect_fingerprint)

    print(f"attestation : {att_path}")
    print(f"scheme/alg  : {artifact.get('scheme', '?')} / {alg}")
    print(f"public key  : {pk_fp}  [{key_source}]")

    ok = True

    if scheme == _SCHEME_V1:
        print("[warn] legacy kry-pqc/v1 artifact — a raw-byte signature, NOT domain-separated; it is "
              "not valid as a threshold contribution. Pass --require-v2 to refuse v1 entirely.")
        if args.require_v2:
            print("[FAIL] --require-v2: legacy v1 artifact refused")
            ok = False

    if args.expect_fingerprint:
        want = args.expect_fingerprint.strip().lower()
        if 0 < len(want) < 16:
            print(f"[warn] --expect-fingerprint is only {len(want)} hex chars; a short prefix is "
                  "brute-forceable — pin >= 16 hex (64-bit) for a meaningful binding")
        fp_ok = bool(want) and full_fp.startswith(want)
        print(f"[{'PASS' if fp_ok else 'FAIL'}] key fingerprint matches pinned --expect-fingerprint")
        ok = ok and fp_ok

    digest = hashlib.sha256(attestation_bytes).hexdigest()
    digest_ok = (digest == artifact.get("message_sha256"))
    print(f"[{'PASS' if digest_ok else 'FAIL'}] message digest matches signed bytes")
    ok = ok and digest_ok

    # L3: v2 signs a domain-separated message; v1 signed raw bytes (scheme validated in the guard).
    signed = _DOMAIN_SINGLE + attestation_bytes if scheme == _SCHEME_V2 else attestation_bytes
    sig_ok = verify_signature(signed, signature, public_key, alg)
    print(f"[{'PASS' if sig_ok else 'FAIL'}] ML-DSA signature valid (authenticity)")
    ok = ok and sig_ok

    ran, chain_ok, errors = _chain_check(attestation_bytes.decode("utf-8", "replace"))
    if ran:
        print(f"[{'PASS' if chain_ok else 'FAIL'}] KRY hash chain intact (integrity)")
        for e in errors:
            print(f"        - {e}")
        ok = ok and chain_ok
    else:
        msg = "KRY not importable -- chain integrity NOT checked here (run scripts/kry_verify.py)"
        if args.require_chain:
            print(f"[FAIL] {msg}")
            ok = False
        else:
            print(f"[skip] {msg}")

    print()
    if ok and pinned:
        print(f"RESULT: VERIFIED -- signed by the holder of the PINNED public key {pk_fp} "
              "and post-quantum authentic.")
        return 0
    if ok and not pinned:
        print("RESULT: UNVERIFIED -- the signature is internally consistent, but the public "
              "key is SELF-PROVIDED, so this proves NOTHING about authenticity (anyone can "
              "self-sign). Re-run with --public-key <the signer's published key> or "
              "--expect-fingerprint <published fp> to establish authenticity.")
        return 2
    print("RESULT: FAILED — do not trust this attestation.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
