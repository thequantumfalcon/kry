# kry_pqc — optional post-quantum authenticity tier for KRY

An **opt-in** tier that signs a KRY attestation with NIST-standard post-quantum
signatures (ML-DSA / FIPS 204), so a stranger can verify a signature over the exact
attestation bytes under a published public key. Key ownership must be established
separately. ML-DSA is designed to resist classical and quantum forgery; running
this optional tier is not a FIPS validation or a guarantee about future attacks.

It is built to be adopted by KRY *as its own* without compromising anything:

> **KRY core (`src/kry/*`) stays zero-dependency, pure stdlib.** Nothing in the
> core imports this tier; this tier only reads KRY's *output* (an attestation
> file). Installing it changes the core's dependency footprint by exactly zero.
> Verified: `grep -rn "import oqs\|import kry_pqc" src/kry` → no matches (a looser
> `grep -r "oqs\|kry_pqc" src/kry` finds only one comment in `kry_mint.py` mentioning this tier).

## Why this exists — the three axes

KRY already separates *integrity* from *veracity*. There is a third axis hiding
between them — *authenticity* — that a SHA-256 hash chain structurally cannot
provide (stdlib has no public-key crypto). This tier fills exactly that gap:

| Axis | Question it answers | Owned by |
|------|--------------------|----------|
| **integrity** | Is the ledger intact / untampered? | KRY core — SHA-256 chain (`scripts/kry_verify.py`) |
| **authenticity** | Did the holder of the pinned key sign these bytes? | **this tier** — ML-DSA signatures |
| **veracity** | Did the savings actually happen? | External source and financial evidence; not established by this tier |

Today a stranger can recompute KRY's chain from the same receipts, so a real
ledger and a fabricated one are cryptographically indistinguishable. A signature
binds the attestation to a **published public key**. A legitimate key holder can
still sign two divergent histories. Detecting that behavior requires retained
prior commitments or external witnessing. A signature does not establish that
the underlying events happened, that a bill was paid, or that a saving occurred.

## Two modes

### 1. Single-signer authenticity (`signer.py` + `verify.py`)
One operator key signs the exact attestation bytes.

```bash
pip install -r kry_pqc/requirements.txt # liboqs-python — this tier only

# produce ./attestation.json (KRY core) from the bundled SYNTHETIC sample log
export KRY_DATA_DIR="$(mktemp -d)"
python3 scripts/kry_savings_report.py examples/sample_usage_log.jsonl --mint --attest attestation.json

python -m kry_pqc.signer keygen --out-dir keys/
python -m kry_pqc.signer sign \
 --attestation attestation.json \
 --secret-key keys/kry_pqc_secret.key \
 --public-key keys/kry_pqc_public.key \
 --out attestation.sig.json

# The operator publishes keys/kry_pqc_public.key (or its sha256 fingerprint) out-of-band.
# With KRY importable, require both the signature and chain checks:
python -m kry_pqc.verify --attestation attestation.json --signature attestation.sig.json \
 --public-key keys/kry_pqc_public.key --require-v2 --require-chain
```
```
[PASS] message digest matches signed bytes
[PASS] ML-DSA signature valid (authenticity)
[PASS] KRY hash chain intact (integrity) # auto-runs if KRY is importable
RESULT: VERIFIED -- signed by the holder of the PINNED public key ... with an ML-DSA signature valid under the supplied key pin.
```
Without `--public-key` or `--expect-fingerprint <published fingerprint>`, the verifier can use only
the key embedded in the signature artifact, which anyone can generate and self-sign with. It then
prints `RESULT: UNVERIFIED` and exits 2: the signature is internally consistent, but authenticity is
not established.

Use the full public key obtained through a trusted channel when possible. A full
64-character SHA-256 fingerprint is also supported. Prefixes of 16–63 hex
characters remain accepted for compatibility, with a warning: a 16-character
prefix identifies only 64 bits and is not a full post-quantum identity binding.
Shorter prefixes fail. Key generation refuses either existing key file and keeps
both halves of an existing keypair unchanged.

Legacy `kry-pqc/v1` signatures over genuine attestation bytes remain supported,
with a warning; `--require-v2` rejects them. V1 cannot relabel messages beginning
with KRY's reserved v2 single-signer or threshold signing domains. Standalone
signature checks authenticate bytes, not a valid KRY chain: use `--require-chain`
with KRY installed when both checks are required. Verifier documents are bounded
to 16 MiB, and malformed or excessively nested JSON returns a controlled failure.

### 2. m-of-n multi-signature quorum (`threshold.py`)
Require M distinct registered keys from a council of N to sign. **M/N is a quorum
setting, not a probability or measure of operator trust.** A threshold of one
requires only one key. The verifier cannot establish independent ownership or
custody: it accepts whatever keys the
`council.json` it is given lists. A stranger must take the council's public keys from where each
member published them; a `council.json` handed over with the attestation proves nothing, because
an operator who generates all N keys *is* the council.

```bash
# each council member generates their own keypair, shares only the public key
python -m kry_pqc.threshold init-policy \
 --signer alice=alice_public.key \
 --signer bob=bob_public.key \
 --signer carol=carol_public.key \
 --threshold 2 --out council.json # 2-of-3 distinct registered keys

# each signer independently signs the attestation
python -m kry_pqc.threshold contribute --attestation attestation.json --policy council.json \
 --secret-key alice_secret.key --public-key alice_public.key --out alice.json
# ... bob.json ...

python -m kry_pqc.threshold combine --attestation attestation.json \
 --policy council.json --contribution alice.json --contribution bob.json \
 --out threshold.json

python -m kry_pqc.threshold verify \
 --attestation attestation.json --artifact threshold.json --policy council.json
```
```
RESULT: VERIFIED -- 2 of 3 distinct registered keys signed (>= 2 required).
 Key ownership and signer independence are not established by this check.
```

**Honest framing of the mechanism:** this is an M-of-N *multi-signature* (N
ML-DSA keys, verification requires ≥M distinct valid ones), **not** an aggregated
threshold signature. Each contribution binds the exact attestation bytes and
the council policy. Policy setup and independent key custody remain operational
responsibilities; the verifier checks signatures, not the identity of people.

## Threat model — what it does and does NOT prove

- **Does prove:** these exact attestation bytes were signed by the holder(s) of
 the named public key(s); with the council, that ≥M of the listed keys signed (≥M
 independent parties only if those keys were independently published and held).
- **Does NOT prove veracity** — that the underlying savings happened. That is the
 subject of separate source, counterfactual, and financial evidence. This tier
 binds a key to signed bytes and does not increase the receipt's evidence tier.
- Key custody is out of scope: a leaked secret key can sign. Threshold mode
 requires M keys; protection depends on how those keys are controlled.

## Status (optional; not part of the default release gate)

The dedicated `optional-crypto` CI job uses pinned Python packages and a hash-verified
native liboqs build. It exercises real ML-DSA-44/65/87 signing, verification and
altered-message rejection, then runs this complete suite alongside the TEE verifier
fixtures with zero skips required. See [CI dependency locks](../docs/CI_DEPENDENCIES.md)
for source identity, loader controls and the limits of this coverage. Nitro coverage
replays a retained genuine fixture alongside constructed cases; SNP coverage uses
constructed reports, without a genuine EPYC fixture or fresh hardware capture.

- `kry_pqc/test_kry_pqc.py` passes only when `liboqs-python` provides the `oqs`
 module. In environments without `oqs`, the optional test is skipped and this
 tier must be treated as unverified for that release run.
- Run `python -m pytest kry_pqc/ -q -rs` to include both the signing suite and
 boundary regressions. The default `pytest tests/` command does not collect them.
- Prior validation covered **8 tests** against a *real* KRY attestation:
 single-signer roundtrip / tamper / wrong-key; threshold quorum-met /
 insufficient-quorum / outsider-ignored / tamper / wrong-council.
- That historical validation used **liboqs-python 0.15.0** (liboqs 0.15.0),
 ML-DSA-65, Python 3.13; it does not replace testing the current release environment.
- Default algorithm: **ML-DSA-65** (FIPS 204, NIST level 3); override with `--alg`
 using `ML-DSA-44`, `ML-DSA-65`, or `ML-DSA-87`. Other signature families are
 outside this verifier's allowlist.

The native liboqs dependency describes its scope as research and prototyping;
successful local tests are not an independent security audit or FIPS module
validation. Review the [upstream limitations](https://github.com/open-quantum-safe/liboqs/tree/0.16.0#limitations-and-security)
and the exact dependency versions before deployment.

Designed but not yet built: PQC-inside-the-enclave, privacy-preserving verifiable
totals, and a FIPS-204 known-answer test harness.
