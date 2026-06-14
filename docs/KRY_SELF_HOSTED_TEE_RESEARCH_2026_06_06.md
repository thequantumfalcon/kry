# ARCHIVE — Self-hosted TEE options for KRY `tee_attested` — research (2026-06-06)

> Historical research snapshot. This is not part of the default kry
> release gate and does not prove the optional TEE tier for the current release.
> Current optional-tier pass/skip semantics live in `docs/RELEASE_CHECKLIST.md`.

Companion to `KRY_TEE_OPTIONS.md` (which covers **cloud** TEE build order: AWS Nitro → Azure
CVM). This doc answers the **self-hosted home-lab** question: can we run a `tee_attested`
producer on our own 4-node x86 lab instead of cloud, and verify it with a hand-rolled
stdlib-style verifier like the Nitro one we just proved?

Method: deep-research harness — 6 search angles, 25 sources fetched, 94 claims extracted,
25 adversarially verified (3-vote, need 2/3 to kill). 5 confirmed high-confidence, 20 killed
or unverified. Verification stage had agent flakiness (many structured-output drops), so
**only the 3-0 confirmed findings are load-bearing**; the rest are flagged.

## Bottom line

**Rank for a self-hosted `tee_attested` tier:**

1. **AMD SEV-SNP — recommended.** The only major TEE with a vendor-endorsed, fully
 open-source attestation toolchain (VirTEE `sev` crate incl. a **pure-Rust `crypto_nossl`**
 mode, `snphost`/`snpguest`) **and** the AMD root (ARK) is **pinnable offline** — so we can
 hand-roll a Nitro-style verifier checking the report signature up the **VCEK/VLEK → ASK →
 ARK** chain with no mandatory always-online vendor call. A Python toolset already exists:
 `github.com/Isaac-Matthews/snp_pytools`. Needs an **EPYC** SKU (not consumer Ryzen).
2. **Intel TDX / SGX — second.** Real, with Intel's open-source **DCAP Quote Verification
 Library** as reference. But the root of trust is **Intel's PKI**: verification needs
 Intel-issued, Intel-signed collateral (PCK cert chain, CRLs, TCB info). The online PCS
 dependency is **removable** via a locally-cached PCCS, but the collateral stays
 Intel-issued — **you cannot self-root the chain**. Heavier vendor coupling than SEV-SNP.
3. **AWS Nitro — cloud-only reference baseline.** Already proven; **cannot be self-hosted**.
4. **ARM CCA — no verified data.** Named in the question but produced zero confirmed claims;
 open question whether purchasable consumer/prosumer CCA hardware even exists in 2025-26.

## Confirmed findings (3-0 verified)

1. **SEV-SNP has the open, self-hostable verifier path** — VirTEE `sev` (`crypto_nossl` pure
 Rust: p384, rsa), `snphost`/`snpguest`, AMD-endorsed (guide 58217). Report-signing cert
 normally from AMD KDS but **cacheable/embeddable offline** (AMD 56860/58217) → no mandatory
 online dependency. [arxiv 2406.01186 (SNPGuard), github.com/virtee/sev, /snpguest]
2. **SEV-SNP root of trust = AMD silicon** — VCEK derived on the AMD Secure Processor from
 chip-unique fuses; chain ARK (self-signed AMD root) → ASK → VCEK (VLEK is the certified
 variant). **Operator can pin ARK offline but cannot become the root** — trust is anchored
 in AMD as CPU vendor. [arxiv 2406.01186, GCP attestation docs]
3. **SEV-SNP measures launch state only** — OVMF firmware + initial vCPU registers (+ kernel/
 initramfs/cmdline only via SNP-enabled OVMF), plus TCB/microcode version fields. **Does NOT
 prove runtime behavior after attestation.** [arxiv 2406.01186, CCC technical analysis]
4. **Intel DCAP is a real open reference verifier** for ECDSA SGX/TDX quotes (TDQE-signed,
 MRTD/RTMR). But it's Intel's SDK, not a from-scratch stdlib effort.
 [github.com/intel/SGX-TDX-DCAP-QuoteVerificationLibrary]
5. **Intel verification is Intel-rooted** — needs Intel-signed PCK chain/CRLs/TCB info;
 self-signed/operator-rooted chain **not possible** in standard DCAP. Online PCS removable
 via PCCS `local_cache_only`, but collateral stays Intel-issued. [Intel DCAP repo]

## Honest threat model (medium confidence — corroborative, consistent with finding 3)

All these TEEs prove only that **a measured platform state existed at evidence-generation time
on genuine vendor silicon**. They do **NOT**: bind/identify the operator or host (a verifier
can't tell datacenter from self-hosted node); cover runtime behavior, side-channels, or
physical/supply-chain attacks; or prove a **model provider's inference** ran in a TEE.

→ For KRY the disciplined claim is unchanged from the Nitro tier: **`tee_attested` proves our
own measurement code launched unforgeably on attested vendor silicon — nothing about an
external provider's inference.** `tlsn_attested` remains the provider-call proof.

## Refuted / unverified (transparency)

- **REFUTED (0-2):** "Intel PCS is a *mandatory* always-online dependency" → it's removable
 via PCCS (but Intel-rooted). Same for "SEV-SNP KDS is always-online" → certs are cacheable.
- **UNVERIFIED (0-0, flagged not relied on):** the **`tee.fail`** DDR5 memory-bus interposition
 attack (claims a sub-$1000 hobbyist rig extracts attestation keys from UpToDate SGX/TDX and
 **Zen 5 EPYC incl. ciphertext-hiding**, June 2025). If real, it materially bounds what *any*
 self-hosted TEE can claim physically — worth a focused follow-up before building.

## Open questions (for a build decision)

1. Concrete **EPYC SKU + motherboard/BIOS** that minimally enables SEV-SNP attestation in
 2025-26, and realistic cost vs a non-confidential node.
2. Can VirTEE `crypto_nossl` + offline-pinned ARK do **fully offline** end-to-end verification
 at home (no KDS at verify time), and does it hold across TCB updates that rotate the VCEK?
3. ARM CCA 2025-26 attestation model + whether any self-hostable consumer hardware exists.
4. How to honestly bound the physical-attack threat given the (unverified) `tee.fail` class.

## Recommendation for KRY

If a self-hosted `tee_attested` is wanted, **target AMD SEV-SNP on one EPYC lab node**, mirror
the Nitro build (`promote_to_tee` + a `kry_snp_verify.py` checking VCEK→ASK→ARK with ARK pinned
offline, referencing `snp_pytools`). Keep the honest meaning identical to the Nitro tier. Treat
the `tee.fail` physical-attack question as a gating unknown, not a blocker. **Do NOT** pursue a
path whose verification can't run offline against a pinned root (rules SEV-SNP **in**, keeps
Intel as a heavier-coupling fallback).

— Sources: see the verified set above; full citation list in the workflow transcript
(`wf_d27afa33-0e8`). Time-sensitive: verify VirTEE/`snp_pytools` crate features against HEAD
before building.
