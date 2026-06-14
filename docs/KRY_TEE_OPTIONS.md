# KRY tee_attested — options & build decision (research 2026-06-06)

Distilled from a web-research sweep of TEE / confidential-compute attestation and
non-TEE alternatives. Decides how (and whether) to build the unbuilt `tee_attested`
tier, mirroring the existing `promote_to_tlsn` pattern.

## What tee_attested can HONESTLY claim (read first)

A cloud-TEE attestation proves *"the code that produced this measurement ran inside an
attested enclave on AWS/Azure/Phala"* — it does **NOT** prove the closed model
provider's *own* inference ran in a TEE (no major provider exposes a customer-facing
attestation; Anthropic's confidential inference is internal-only). So the honest meaning
of a KRY `tee_attested` receipt is: **"the counterfactual/holdout measurement (token
accounting, the savings claim) ran in attested hardware, so the operator could not have
fabricated it."** That is a real upgrade to the `self_reported`/`holdout_validated`
path (operator-trust → hardware-attested) — NOT a provider-inference proof, and must
not be marketed as one (Avoid A1/A8). `tlsn_attested` remains the tier that proves the
provider call happened.

## Decision: build order

1. **AWS Nitro Enclaves FIRST.** No surcharge beyond the EC2 instance (~few $ for a PoC).
 Attestation doc = **COSE-Sign1 / CBOR**, verified against ONE pinned root
 (`AWS_NitroEnclaves_Root-G1`). Verify path is **pure-Python-feasible** (CBOR decode +
 COSE ECDSA-P384 verify + X.509 chain to the pinned root) — no Intel SDK, mirrors
 `promote_to_tlsn` (parse doc → verify chain to pinned root → mint receipt).
2. **Azure Confidential VM (TDX or SEV-SNP) via the MAA JWT path SECOND.** ~$0.11/hr,
 GA. Verify = **JWS-against-JWKS** (pull MAA signing cert from its OpenID metadata) —
 trivially pure-Python. Gives a second independent vendor root so the tier isn't
 single-vendor. Skip the raw-quote/DCAP path; let MAA do the heavy lifting.

## Build sketch (next session)

- `kry_mint.py`: add `TIER_TEE_ATTESTED` producer `promote_to_tee(gen_id, attestation,
 detail)` mirroring `promote_to_tlsn` (zero-value `tier_promotion` or fresh mint;
 idempotent; chain off the file tip under the cross-process lock).
- `scripts/kry_tee_verify.py`: parse + verify a Nitro COSE doc (and/or MAA JWT) against
 the pinned root; fail-closed on bad signature/root/expiry; `--root` / `--maa-jwks` pins.
- The enclave workload = run the KRY holdout/measurement inside the enclave so the
 attested measurement is the thing minted against.
- Tests mirror `test_tlsn_verify.py` (valid/invalid-sig/wrong-root/replay).

## Neutrality (the standing notary trust ceiling)

- PSE `notary.pse.dev` is the cheapest neutral-notary fix **IF** our prover can speak its
 protocol — our checkout uses a custom `notary_tcp`, not a `notary-server` client, so a
 **compatibility spike is required first** (version-pinned; ~25 MB fixed + per-byte
 overhead per session; PSE is dev-only: "don't build your business on it"). If the spike
 fails, the **hand-the-binary-to-a-third-party** route stays the zero-code neutrality
 path. Either way: pin the notary key, document the trust source.

## Skip (overkill / not-ready / theater for a solo consumer-HW operator)

SGX enclaves (Gramine/Occlum — enclave porting), standalone SEV-SNP/TDX (datacenter HW
we don't have), **ARM CCA (no production silicon)**, **zkML (can't prove closed-model
inference; 10³–10⁴ proving blowup)**, managed/decentralized TEE networks (Fortanix /
Integritee / full notary networks — proprietary or chain-coupled heft, no solo payoff),
optimistic/fraud-proof tiers (need a challenger ecosystem to bootstrap).

## One real dependency to weigh

Raw SGX/TDX DCAP quotes are the only SDK-heavy verify path; the lightest is `dcap-qvl`
(Rust, Python bindings on PyPI). Avoid it by using Nitro (COSE) + Azure (MAA JWT), both
of which verify with stdlib crypto — keeping the zero-dependency ethos intact.
