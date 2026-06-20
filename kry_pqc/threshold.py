"""KRY-PQC threshold signing — make "trust the operator" a tunable m-of-n number.

Single-signer authenticity (signer.py) still rests on ONE key: compromise it and
history can be silently re-vouched. This module distributes that trust across an
explicit council of N independent signers and requires M of them to vouch for an
attestation. No single party — not even the operator — can produce a valid
threshold proof alone. Operator-trust becomes M/N, and you drive it toward zero
by adding independent signers.

Honest framing of the mechanism: this is an M-of-N MULTI-signature — N independent
ML-DSA keys, each producing its own signature over the same attestation bytes,
verified by requiring >= M distinct valid signatures from the registered council.
It is NOT a single aggregated FROST-style threshold signature (that needs an
interactive distributed key generation + threshold-sig scheme). Multi-sig needs
no interaction and is exactly as sound for the trust-distribution goal; the only
cost is artifact size (M signatures instead of one). FROST is the optional future
upgrade if compact signatures ever matter.
"""
from __future__ import annotations

import argparse
import binascii
import hashlib
import json
import sys
from pathlib import Path

from kry_pqc.signer import DEFAULT_ALG, _b64, _unb64, fingerprint, sign_bytes

try:
    import oqs
except ImportError:  # pragma: no cover
    sys.stderr.write("kry_pqc threshold requires liboqs-python (the `oqs` module).\n")
    raise

POLICY_SCHEME = "kry-pqc-council/v1"
ARTIFACT_SCHEME = "kry-pqc-threshold/v1"


def _canon(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()


def make_policy(signers: list[tuple[str, bytes]], threshold: int,
                alg: str = DEFAULT_ALG) -> dict:
    """Build a public council policy: who may vouch, and how many are required."""
    if not (1 <= threshold <= len(signers)):
        raise ValueError(f"threshold {threshold} out of range 1..{len(signers)}")
    entries = [{"name": name, "public_key": _b64(pk), "fingerprint": fingerprint(pk)}
               for name, pk in signers]
    fps = [e["fingerprint"] for e in entries]
    if len(set(fps)) != len(fps):
        raise ValueError("duplicate signer public keys in council")
    return {"scheme": POLICY_SCHEME, "alg": alg, "threshold": threshold, "signers": entries}


def policy_digest(policy: dict) -> str:
    return hashlib.sha256(_canon(policy)).hexdigest()


def contribute(attestation_path: Path, secret_key: bytes, public_key: bytes,
               alg: str = DEFAULT_ALG) -> dict:
    """One council member signs the attestation bytes -> a contribution."""
    message = Path(attestation_path).read_bytes()
    sig = sign_bytes(message, secret_key, alg)
    return {"fingerprint": fingerprint(public_key), "signature": _b64(sig)}


def combine(attestation_path: Path, policy: dict, contributions: list[dict]) -> dict:
    """Bundle contributions (deduped, council-only) into a threshold artifact."""
    message = Path(attestation_path).read_bytes()
    known = {e["fingerprint"] for e in policy["signers"]}
    # HOLE #21: keep EVERY council-registered contribution (no last-wins dedup by fingerprint). The
    # old `seen[fp] = c` let a later same-fingerprint contribution carrying a garbage signature
    # overwrite a legitimate one, silently dropping that signer below quorum. verify_threshold already
    # counts a fingerprint at most once and only on a VALID signature, so keeping all is safe and
    # lets it select the valid one per signer.
    sigs = [c for c in contributions if c.get("fingerprint") in known]
    return {
        "scheme": ARTIFACT_SCHEME,
        "alg": policy["alg"],
        "attestation_file": Path(attestation_path).name,
        "message_sha256": hashlib.sha256(message).hexdigest(),
        "policy_sha256": policy_digest(policy),
        "threshold": policy["threshold"],
        "council_size": len(policy["signers"]),
        "signatures": sigs,
    }


def verify_threshold(attestation_bytes: bytes, artifact: dict,
                     policy: dict) -> tuple[bool, dict]:
    """Verify a threshold artifact against the council policy.

    ``ok`` is True iff >= threshold DISTINCT registered signers produced valid
    ML-DSA signatures over exactly these attestation bytes, the artifact is bound
    to this policy, and the message digest matches.
    """
    report = {"checks": [], "valid_signers": [], "valid_count": 0,
              "threshold": policy["threshold"], "council_size": len(policy["signers"]),
              "trust_ratio": f"{policy['threshold']}/{len(policy['signers'])}"}
    ok = True

    def check(name, passed):
        nonlocal ok
        report["checks"].append({"name": name, "pass": bool(passed)})
        ok = ok and bool(passed)

    check("artifact scheme recognised", artifact.get("scheme") == ARTIFACT_SCHEME)
    check("message digest matches signed bytes",
          hashlib.sha256(attestation_bytes).hexdigest() == artifact.get("message_sha256"))
    check("artifact bound to this council policy",
          artifact.get("policy_sha256") == policy_digest(policy))
    check("artifact threshold matches policy",
          artifact.get("threshold") == policy["threshold"])

    # HOLE #19: independently enforce the invariant make_policy guarantees. A degenerate threshold
    # (<= 0, > council size, or a non-int true/false/float) otherwise makes the quorum gate
    # `len(counted) >= threshold` vacuously True — a false VERIFIED on ZERO signatures.
    thr = policy.get("threshold")
    check("policy threshold is an integer in 1..council_size",
          isinstance(thr, int) and not isinstance(thr, bool)
          and 1 <= thr <= len(policy["signers"]))
    # HOLE #20: recompute each signer's fingerprint from its public key and reject a council that
    # mislabels a key or lists the SAME key under two fingerprints — otherwise ONE secret-key holder
    # could meet an m-of-n quorum by relabeling its single key, defeating "no single party could
    # have produced this". The constructor (make_policy) enforces this; the stranger verifier must too.
    _pks = [_unb64(e["public_key"]) for e in policy["signers"]]
    check("policy fingerprints match public keys",
          all(fingerprint(pk) == e.get("fingerprint")
              for pk, e in zip(_pks, policy["signers"])))
    check("policy has no duplicate public keys",
          len({bytes(pk) for pk in _pks}) == len(_pks))

    by_fp = {e["fingerprint"]: _unb64(e["public_key"]) for e in policy["signers"]}
    alg = policy["alg"]
    counted: set[str] = set()
    for s in artifact.get("signatures", []):
        fp = s.get("fingerprint")
        if fp not in by_fp or fp in counted:
            continue
        # HOLE #28: the artifact is attacker-supplied; a malformed signature entry (missing/non-string
        # "signature", or non-base64) must SKIP (not count, and not crash the stranger verifier with an
        # uncaught KeyError/binascii.Error). A skipped entry simply doesn't contribute to the quorum.
        raw = s.get("signature")
        if not isinstance(raw, str):
            continue
        try:
            sig = _unb64(raw)
        except (binascii.Error, ValueError):
            continue
        with oqs.Signature(alg) as v:
            if v.verify(attestation_bytes, sig, by_fp[fp]):
                counted.add(fp)
    report["valid_signers"] = sorted(counted)
    report["valid_count"] = len(counted)
    check(f"quorum reached (>= {policy['threshold']} distinct valid signers)",
          len(counted) >= policy["threshold"])
    return ok, report


# --------------------------------------------------------------------------- CLI


def _keyfile(p: str) -> bytes:
    return _unb64(Path(p).read_text().strip())


def _cmd_init_policy(args) -> int:
    signers = []
    for spec in args.signer:
        name, _, pk_path = spec.partition("=")
        if not pk_path:
            raise SystemExit(f"--signer must be NAME=public.key (got {spec!r})")
        signers.append((name, _keyfile(pk_path)))
    policy = make_policy(signers, args.threshold, args.alg)
    Path(args.out).write_text(json.dumps(policy, indent=2))
    print(f"council: {len(signers)} signers, threshold {args.threshold} "
          f"(trust {args.threshold}/{len(signers)}) -> {args.out}")
    print(f"policy digest: {policy_digest(policy)}")
    return 0


def _cmd_contribute(args) -> int:
    c = contribute(Path(args.attestation), _keyfile(args.secret_key),
                   _keyfile(args.public_key), args.alg)
    Path(args.out).write_text(json.dumps(c, indent=2))
    print(f"contribution by {c['fingerprint']} -> {args.out}")
    return 0


def _cmd_combine(args) -> int:
    policy = json.loads(Path(args.policy).read_text())
    contribs = [json.loads(Path(c).read_text()) for c in args.contribution]
    artifact = combine(Path(args.attestation), policy, contribs)
    Path(args.out).write_text(json.dumps(artifact, indent=2))
    print(f"combined {len(artifact['signatures'])} contributions "
          f"(threshold {artifact['threshold']}/{artifact['council_size']}) -> {args.out}")
    return 0


def _cmd_verify(args) -> int:
    attestation_bytes = Path(args.attestation).read_bytes()
    artifact = json.loads(Path(args.artifact).read_text())
    policy = json.loads(Path(args.policy).read_text())
    ok, report = verify_threshold(attestation_bytes, artifact, policy)
    print(f"council      : {report['trust_ratio']} (threshold/size)")
    for c in report["checks"]:
        print(f"[{'PASS' if c['pass'] else 'FAIL'}] {c['name']}")
    signers = ", ".join(report["valid_signers"]) or "(none)"
    print(f"valid signers: {report['valid_count']} -> {signers}")
    print()
    if ok:
        print(f"RESULT: VERIFIED -- {report['valid_count']} of {report['council_size']} "
              f"independent signers vouched (>= {report['threshold']} required). "
              "No single party could have produced this.")
        return 0
    print("RESULT: FAILED — quorum not met or artifact invalid; do not trust.")
    return 1


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        prog="kry_pqc.threshold",
        description="m-of-n post-quantum threshold signing for KRY attestations.")
    sub = p.add_subparsers(dest="cmd", required=True)

    ip = sub.add_parser("init-policy", help="create a council policy from public keys")
    ip.add_argument("--signer", action="append", required=True, metavar="NAME=public.key",
                    help="repeatable: a signer name and their public key file")
    ip.add_argument("--threshold", type=int, required=True)
    ip.add_argument("--alg", default=DEFAULT_ALG)
    ip.add_argument("--out", default="council.json")
    ip.set_defaults(func=_cmd_init_policy)

    c = sub.add_parser("contribute", help="one signer signs an attestation")
    c.add_argument("--attestation", required=True)
    c.add_argument("--secret-key", required=True)
    c.add_argument("--public-key", required=True)
    c.add_argument("--alg", default=DEFAULT_ALG)
    c.add_argument("--out", required=True)
    c.set_defaults(func=_cmd_contribute)

    cm = sub.add_parser("combine", help="bundle contributions into a threshold artifact")
    cm.add_argument("--attestation", required=True)
    cm.add_argument("--policy", required=True)
    cm.add_argument("--contribution", action="append", required=True)
    cm.add_argument("--out", required=True)
    cm.set_defaults(func=_cmd_combine)

    v = sub.add_parser("verify", help="verify a threshold artifact (stranger-runnable)")
    v.add_argument("--attestation", required=True)
    v.add_argument("--artifact", required=True)
    v.add_argument("--policy", required=True)
    v.set_defaults(func=_cmd_verify)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
