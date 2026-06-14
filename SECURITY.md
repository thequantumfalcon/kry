# Security Policy

KRY is a **stdlib-only measurement-and-proof tool**, not a network service. Its security model
is unusual and worth stating plainly, because it shapes what counts as a vulnerability.

## What KRY defends against

KRY's threat model is **false savings claims**, not remote attackers. The system's job is to let a
stranger who does **not** trust the operator verify a savings receipt offline. So the security-
relevant surface is:

- the **integrity hash-chain** and conservation checks (a tampered or non-conserving ledger must
 be detected),
- the **magnitude recompute** (a fabricated price multiplier must be rejected against the public
 price table),
- the **stranger verifier** (`scripts/kry_verify.py`) and the attestation format,
- the optional **audited crypto** tiers (TEE / PQC) behind the `tee` / `oqs` extras.

By design, **integrity ≠ veracity**: the chain proves *untampered + conserved*, not that an event
happened. A balance with no external anchor honestly reports `veracity_floor = 0.0`. That is the
**honest label, not a defect** — do not report it as a vulnerability.

## Supported versions

| Version | Supported |
|---|---|
| 0.1.0 (current) | ✅ |
| < 0.1.0 | ❌ |

## Reporting a vulnerability

**Please do not open a public issue for a security problem.**

1. Preferred: GitHub **Private Vulnerability Reporting** — the repository's **Security** tab →
 **Report a vulnerability**.
2. Or email **thequantumfalcon@gmail.com** with `KRY SECURITY` in the subject.

Include: a description (CWE/CVE class if known), steps to reproduce, a proof-of-concept if you have
one, and your assessment of impact.

## Response targets

- **Acknowledgement:** within **72 hours**.
- **Triage + severity (CVSS 4.0):** within **7 days**.
- **Fix:** severity-dependent; critical issues prioritized.
- **Coordinated disclosure:** we request up to **90 days** before public disclosure, and will
 credit you in the advisory unless you ask to remain anonymous. We will **not** pursue legal
 action against good-faith research.

## Out of scope

- The **disclosed honest limits** in `src/kry/kry_capabilities.py` (`per_event_counterfactual_proof`,
 `source_truth_of_self_report`, `sybil_resistant_identity`, `real_world_validated_savings`). These
 are *datasheet disclosures*, not defects — see `docs/KRY_READINESS.md`.
- Resource exhaustion from a hostile input log (malformed/`NaN`/`inf` token counts already clamp to
 0 — see `tests/test_stress.py`).
- Social engineering of the maintainer.
- Findings from automated scanners without a manually verified, KRY-specific impact.

## Reproducibility & supply chain

The core package has **zero third-party dependencies** (pure stdlib), which minimizes the supply-
chain surface. CI pins third-party Actions by commit SHA, runs read-only by default, and a
`no-ai-attribution` workflow plus local git hooks enforce provenance hygiene on every commit.
