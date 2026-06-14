# kry_pqc — optional post-quantum authenticity tier for KRY

An **opt-in** tier that signs a KRY attestation with NIST-standard post-quantum
signatures (ML-DSA / FIPS 204), so a stranger can verify **who** produced an
attestation — unforgeably, and safe against a future quantum adversary.

It is built to be adopted by KRY *as its own* without compromising anything:

> **KRY core (`src/kry/*`) stays zero-dependency, pure stdlib.** Nothing in the
> core imports this tier; this tier only reads KRY's *output* (an attestation
> file). Installing it changes the core's dependency footprint by exactly zero.
> Verified: `grep -r "oqs\|kry_pqc" src/kry` → no matches.

## Why this exists — the three axes

KRY already separates *integrity* from *veracity*. There is a third axis hiding
between them — *authenticity* — that a SHA-256 hash chain structurally cannot
provide (stdlib has no public-key crypto). This tier fills exactly that gap:

| Axis | Question it answers | Owned by |
|------|--------------------|----------|
| **integrity** | Is the ledger intact / untampered? | KRY core — SHA-256 chain (`scripts/kry_verify.py`) |
| **authenticity** | *Who* produced this attestation? Can they fork history? | **this tier** — ML-DSA signatures |
| **veracity** | Did the savings actually happen? | KRY tiers — TEE (Nitro/SEV-SNP) + TLSNotary |

Today a stranger can recompute KRY's chain from the same receipts, so a real
ledger and a fabricated one are cryptographically indistinguishable. A signature
binds the attestation to a **published public key**: the operator can no longer
silently re-vouch a different history, and because KRY is a credit meant to
*retain dollar value over time*, the signature is **post-quantum** so it can't be
retroactively forged decades later. (SHA-256 integrity is fine under Grover;
signed *authenticity* is the part that needs PQC.)

## Two modes

### 1. Single-signer authenticity (`signer.py` + `verify.py`)
One operator key signs the exact attestation bytes.

```bash
pip install -r kry_pqc/requirements.txt # liboqs-python — this tier only

python examples/try_kry.py # produce an attestation.json (KRY core)

python -m kry_pqc.signer keygen --out-dir keys/
python -m kry_pqc.signer sign \
 --attestation attestation.json \
 --secret-key keys/kry_pqc_secret.key \
 --public-key keys/kry_pqc_public.key \
 --out attestation.sig.json

# A stranger runs this (needs only liboqs-python + stdlib):
python -m kry_pqc.verify --attestation attestation.json --signature attestation.sig.json
```
```
[PASS] message digest matches signed bytes
[PASS] ML-DSA signature valid (authenticity)
[PASS] KRY hash chain intact (integrity) # auto-runs if KRY is importable
RESULT: VERIFIED -- ... post-quantum authentic.
```

### 2. m-of-n threshold — make operator-trust a tunable number (`threshold.py`)
Distribute trust across a council of N independent signers; require M to vouch.
**No single party — not even the operator — can produce a valid proof alone.**
Operator-trust becomes **M/N**, driven toward zero by adding independent signers.

```bash
# each council member generates their own keypair, shares only the public key
python -m kry_pqc.threshold init-policy \
 --signer alice=alice_public.key \
 --signer bob=bob_public.key \
 --signer carol=carol_public.key \
 --threshold 2 --out council.json # 2-of-3 -> trust 2/3

# each signer independently signs the attestation
python -m kry_pqc.threshold contribute --attestation attestation.json \
 --secret-key alice_secret.key --public-key alice_public.key --out alice.json
# ... bob.json ...

python -m kry_pqc.threshold combine --attestation attestation.json \
 --policy council.json --contribution alice.json --contribution bob.json \
 --out threshold.json

python -m kry_pqc.threshold verify \
 --attestation attestation.json --artifact threshold.json --policy council.json
```
```
RESULT: VERIFIED -- 2 of 3 independent signers vouched (>= 2 required).
 No single party could have produced this.
```

**Honest framing of the mechanism:** this is an M-of-N *multi-signature* (N
independent ML-DSA keys, verify requires ≥M distinct valid ones), **not** a single
aggregated FROST-style threshold signature. Multi-sig needs no interactive key
ceremony and is exactly as sound for distributing trust; the only cost is artifact
size (M signatures). FROST is the optional future upgrade if compactness matters.

## Threat model — what it does and does NOT prove

- **Does prove:** these exact attestation bytes were signed by the holder(s) of
 the named public key(s); with the council, that ≥M independent parties agreed;
 and that this remains unforgeable against a quantum adversary.
- **Does NOT prove veracity** — that the underlying savings happened. That is the
 job of KRY's TEE + TLSNotary tiers. This tier adds *who attested*, not *was it
 true*. Don't conflate the two.
- Key custody is out of scope: a leaked secret key can sign. Threshold mode is the
 mitigation (compromise M keys, not one).

## Status (optional; not part of the default release gate)

- `kry_pqc/test_kry_pqc.py` passes only when `liboqs-python` provides the `oqs`
 module. In environments without `oqs`, the optional test is skipped and this
 tier must be treated as unverified for that release run.
- Prior validation covered **8 tests** against a *real* KRY attestation:
 single-signer roundtrip / tamper / wrong-key; threshold quorum-met /
 insufficient-quorum / outsider-ignored / tamper / wrong-council.
- Validated with **liboqs-python 0.15.0** (liboqs 0.15.0), ML-DSA-65, Python 3.13.
- Default algorithm: **ML-DSA-65** (FIPS 204, NIST level 3); override with `--alg`
 (e.g. `ML-DSA-87`, or `SPHINCS+-SHA2-128f-simple` for hash-based signatures).

See `kry_pqc/PLAN.md` for the full adoption map, including the designed (not yet
built) tiers: PQC-inside-the-enclave, privacy-preserving verifiable totals, and a
FIPS-204 known-answer test harness.
