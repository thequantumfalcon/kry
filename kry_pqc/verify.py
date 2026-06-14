"""KRY-PQC verifier — a stranger runs THIS to check an attestation's authenticity.

Dependencies: liboqs-python (``oqs``) + Python stdlib. It does NOT need KRY
installed to check authenticity. If KRY *is* importable, it additionally re-runs
KRY's own stdlib chain check, so one command covers both axes.

Checks performed:
    1. message digest    — sha256(attestation bytes) matches the signature artifact
    2. authenticity      — ML-DSA signature verifies under the public key   [this tier]
    3. integrity (opt.)  — KRY's verify_attestation: hash chain intact       [KRY core]

Exit code 0 = all performed checks passed; 1 = any failure.
"""
from __future__ import annotations

import argparse
import base64
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
                   help="public key file (default: the key embedded in the signature artifact)")
    p.add_argument("--require-chain", action="store_true",
                   help="fail unless KRY's chain-integrity check can also run and passes")
    args = p.parse_args(argv)

    att_path = Path(args.attestation)
    attestation_bytes = att_path.read_bytes()
    artifact = json.loads(Path(args.signature).read_text())

    alg = artifact["alg"]
    signature = _unb64(artifact["signature"])
    if args.public_key:
        public_key = _unb64(Path(args.public_key).read_text().strip())
    else:
        public_key = _unb64(artifact["public_key"])
    pk_fp = hashlib.sha256(public_key).hexdigest()[:16]

    print(f"attestation : {att_path}")
    print(f"scheme/alg  : {artifact.get('scheme', '?')} / {alg}")
    print(f"public key  : {pk_fp}")

    ok = True

    digest = hashlib.sha256(attestation_bytes).hexdigest()
    digest_ok = (digest == artifact.get("message_sha256"))
    print(f"[{'PASS' if digest_ok else 'FAIL'}] message digest matches signed bytes")
    ok = ok and digest_ok

    sig_ok = verify_signature(attestation_bytes, signature, public_key, alg)
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
    if ok:
        print("RESULT: VERIFIED -- this attestation was signed by the holder of public "
              f"key {pk_fp} and is post-quantum authentic.")
        return 0
    print("RESULT: FAILED — do not trust this attestation.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
