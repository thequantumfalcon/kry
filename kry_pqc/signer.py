"""KRY-PQC signer — post-quantum authenticity for KRY attestations.

OPTIONAL TIER. KRY's core (``src/kry/*``) is zero-dependency, pure stdlib, and does
NOT import this module. This tier alone depends on liboqs-python (the ``oqs``
module). It signs the *exact bytes* of a KRY attestation with a NIST-standard
post-quantum signature (ML-DSA / FIPS 204), so a third party can verify WHO
produced an attestation and be safe against a future quantum adversary forging
historical proofs.

Three independent axes — this tier owns exactly one:

    integrity    — KRY's SHA-256 hash chain (scripts/kry_verify.py)        [KRY core]
    authenticity — ML-DSA signature binds an attestation to a public key   [THIS tier]
    veracity     — TEE (Nitro / SEV-SNP) + TLSNotary tiers                 [KRY tiers]

The signature is taken over the attestation file's raw bytes; this tier never
parses or reconstructs KRY internals, so it cannot drift from — or break — the core.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import sys
from pathlib import Path

try:
    import oqs
except ImportError:  # pragma: no cover
    sys.stderr.write(
        "kry_pqc requires liboqs-python (the `oqs` module). Install it "
        "(see kry_pqc/requirements.txt) — KRY core does not need it.\n"
    )
    raise

DEFAULT_ALG = "ML-DSA-65"   # FIPS 204, NIST security level 3
SCHEME = "kry-pqc/v2"       # L3: v2 domain-separates the signed message (v1 signed raw bytes)

# L3 domain separation: a v2 signature commits to its CONTEXT, not just the attestation bytes, so a
# single-signer signature cannot be replayed as a threshold contribution (and vice-versa). The
# stranger verifier dispatches on the artifact's `scheme`, so v1 artifacts (raw-byte signatures)
# still verify unchanged.
_DOMAIN_SINGLE = b"kry-pqc/v2/single\x00"


def single_signed_message(attestation_bytes: bytes) -> bytes:
    """The exact bytes a v2 single-signer signature is computed over."""
    return _DOMAIN_SINGLE + attestation_bytes


def generate_keypair(alg: str = DEFAULT_ALG) -> tuple[bytes, bytes]:
    """Return ``(public_key, secret_key)`` for ``alg``. Keep the secret key secret."""
    with oqs.Signature(alg) as signer:
        public_key = signer.generate_keypair()
        secret_key = signer.export_secret_key()
    return public_key, secret_key


def sign_bytes(message: bytes, secret_key: bytes, alg: str = DEFAULT_ALG) -> bytes:
    """Sign raw bytes with ``secret_key`` under ``alg``."""
    with oqs.Signature(alg, secret_key=secret_key) as signer:
        return signer.sign(message)


def fingerprint(public_key: bytes) -> str:
    """Short, human-comparable id for a public key."""
    return hashlib.sha256(public_key).hexdigest()[:16]


def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode()


def _unb64(s: str) -> bytes:
    return base64.b64decode(s.encode())


def sign_attestation(attestation_path: Path, secret_key: bytes, public_key: bytes,
                     alg: str = DEFAULT_ALG) -> dict:
    """Sign an attestation file's exact bytes; return a detached signature artifact."""
    message = Path(attestation_path).read_bytes()
    signature = sign_bytes(single_signed_message(message), secret_key, alg)
    return {
        "scheme": SCHEME,
        "alg": alg,
        "attestation_file": Path(attestation_path).name,
        "message_sha256": hashlib.sha256(message).hexdigest(),
        "public_key": _b64(public_key),
        "public_key_fingerprint": fingerprint(public_key),
        "signature": _b64(signature),
    }


# --------------------------------------------------------------------------- CLI


def _cmd_keygen(args) -> int:
    public_key, secret_key = generate_keypair(args.alg)
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    pk_path = out / "kry_pqc_public.key"
    sk_path = out / "kry_pqc_secret.key"
    pk_path.write_text(_b64(public_key))
    # L2: create the SECRET key 0o600 from the start via O_EXCL — a plain write_text() then
    # chmod() leaves a brief window where the key is umask-default (group/world) readable.
    # O_EXCL also refuses to clobber an existing secret key rather than silently overwriting it.
    sk_fd = os.open(sk_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(sk_fd, "w") as f:
        f.write(_b64(secret_key))
    print(f"alg:         {args.alg}")
    print(f"public key:  {pk_path}  (share this)")
    print(f"secret key:  {sk_path}  (KEEP SECRET -- never share or commit)")
    print(f"fingerprint: {fingerprint(public_key)}")
    return 0


def _cmd_sign(args) -> int:
    secret_key = _unb64(Path(args.secret_key).read_text().strip())
    public_key = _unb64(Path(args.public_key).read_text().strip())
    artifact = sign_attestation(Path(args.attestation), secret_key, public_key, args.alg)
    default_out = Path(args.attestation).with_suffix(Path(args.attestation).suffix + ".sig.json")
    out = Path(args.out) if args.out else default_out
    out.write_text(json.dumps(artifact, indent=2))
    print(f"signed {args.attestation}")
    print(f"  alg          {artifact['alg']}")
    print(f"  fingerprint  {artifact['public_key_fingerprint']}")
    print(f"  signature -> {out}")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        prog="kry_pqc.signer",
        description="Post-quantum signing for KRY attestations (optional tier).")
    sub = p.add_subparsers(dest="cmd", required=True)

    g = sub.add_parser("keygen", help="generate an ML-DSA keypair")
    g.add_argument("--out-dir", default=".", help="directory to write the keypair")
    g.add_argument("--alg", default=DEFAULT_ALG)
    g.set_defaults(func=_cmd_keygen)

    s = sub.add_parser("sign", help="sign an attestation file")
    s.add_argument("--attestation", required=True)
    s.add_argument("--secret-key", required=True)
    s.add_argument("--public-key", required=True)
    s.add_argument("--out", default=None,
                   help="signature artifact path (default: <attestation>.sig.json)")
    s.add_argument("--alg", default=DEFAULT_ALG)
    s.set_defaults(func=_cmd_sign)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
