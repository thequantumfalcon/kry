# KRY Claims Boundary

This file states what the repository is allowed to cause a reader to believe.
It is stricter than marketing language and should be treated as the release
boundary until real external evidence changes it.

**Separation invariant:** the thing evaluated must be external to the logic
evaluating it — a verifier must not trust the producer's code. This is why
`scripts/kry_verify.py` imports nothing from the package and `verifiers/js`
reads only `SPEC.md` + `vectors/`. An analysis that builds its own test
subject is void.

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
- `research_grade` readiness, reached 2026-06-10: a fresh real-traffic run reconciled
 52/52 against the provider's own per-request records at agreement 1.00 (≥ the 0.80 bar).
 Evidence: `docs/evidence/research_grade/` (offline-verifiable) and `docs/KRY_READINESS.md`.
 Honest scope: graded the fresh-run window; the all-time ledger is lower only because some
 legacy generation-ids are provider-purged (un-fetchable, **not** refuted).
- Provider-reconciled savings **for that window** (same bundle): the operator cannot inflate
 the reconciled token counts without the check against the provider export breaking. This is
 reconciliation veracity, **not** an external verified-savings claim (that stays blocked below).
- The **action-receipt layer** (`kry_action`) gives tamper-evident, content-free, hash-chained
 receipts for agent ACTIONS, with a stdlib stranger verifier (`scripts/kry_action_verify.py`) and
 automated regression coverage (`tests/test_action.py`, `tests/test_action_concurrency.py`, incl. a
 cross-process no-fork test). It proves an action log is intact, ordered, and append-only — **not**
 that an action's real-world effect occurred.
- The **promotion overlay** (a `supersedes` link re-tiering an earlier receipt) is an optional,
 normatively-specified conformance **profile** (SPEC §3.7, v1.1) enforced under five invariants
 plus an outcome guard (the SAFETY CONTRACT on `kry_mint._apply_promotion_overlay`; four prior
 HIGH-severity findings landed in exactly this mechanism — see the CHANGELOG), pinned by its
 own vector category (`vectors/savings/overlay/`: one valid promotion, four adversarial). The
 Python reference and the bundled `verifiers/js` both implement the profile and agree on the
 full corpus. A verifier that does not claim the profile must **fail closed on any attestation
 containing a `supersedes` link**.

## Blocked Until External Evidence Exists

- External verified-savings claim.
- `production_ready` / A+ readiness.
- Real-corpus validation (independent real-world corpus + counterparty, for the savings claim).
- Outside-review-complete claim.
- Buyer reliance or materiality claim.
- Legal approval of external retained-savings language.
- Action-layer T1 (`server_witnessed`) third-party-witness claim: the witness is operator-supplied
 until `kry_action` is wired to a real MCP-server signature, so read its `veracity_floor` as
 operator-asserted until then (the verifier already coerces a witness-less anchored tier to T0).

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
