# KRY Claims Boundary

This file states what the repository is allowed to cause a reader to believe.
It is stricter than marketing language and should be treated as the release
boundary until real external evidence changes it.

## Proven In This Repository

- The package installs from a tracked checkout with no runtime dependencies.
- The core stdlib suite passes locally.
- The mint chain, public attestation, settlement guard, verifier, savings report,
 doctor, artifact bundle, artifact verifier, and FinOps report have automated
 regression coverage.
- The bundled sample packet verifies as an internal/demo efficiency artifact.
- The sample packet blocks external verified-savings, production-ready, and
 tradeable-token claims.
- The release verifier can run install, compileall, ruff, pytest, attribution,
 packet, doctor, diff-check, dry-run untracked-file, and reproducibility gates.

## Blocked Until External Evidence Exists

- External verified-savings claim.
- `research_grade` readiness.
- `production_ready` / A+ readiness.
- Provider-reconciled savings.
- Real-corpus validation.
- Outside-review-complete claim.
- Buyer reliance or materiality claim.
- Legal approval of external retained-savings language.

Required evidence:

- real provider export for the relevant billing window;
- real corpus manifest with collection window and validation plan;
- T1 manifest bound to the public attestation;
- outside reviewer evidence;
- buyer feedback evidence;
- legal review evidence.

## Forbidden Claims

- KRY is a tradeable token.
- KRY is a security, exchange instrument, or public market asset.
- The bundled sample proves external customer savings.
- Carbon estimates are certified carbon credits.
- Optional PQC/TLSNotary/Nitro prototype material is part of the default stdlib
 release gate.
- Passing tests means real-world validation has happened.

## Optional Claims

These may be claimed only when the named optional verification passes in the
release environment:

- PQC authenticity: requires `liboqs-python`/`oqs` and
 `python3 -m pytest kry_pqc/test_kry_pqc.py -q` with no skips.
- TEE/Nitro path: requires real AWS Nitro hardware, `cryptography`, Nitro tooling,
 and the PoC verifier output.
- TLSNotary T2 path: requires the TLSNotary toolchain and an independently run
 notary if claiming third-party neutrality.

## Approved Short Description

kry is a reproducible internal proof-of-efficiency accounting and packet
verification artifact. It separates integrity, magnitude, and veracity; it keeps
external verified-savings claims blocked until real provider, corpus, reviewer,
buyer, and legal evidence exist.
