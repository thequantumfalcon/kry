# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Security (audit round 4 — two independent deep external audits)

- **A1-1 (HIGH) — v4/v5 promotion relabel could inflate the anchored floor.** `receipt_id` is
  hash-bound only at v6+, so a v4/v5 receipt's id was mutable. The promotion overlay matched
  superseded receipts by `receipt_id`, so relabeling/colliding a large v5 receipt's id onto a
  promotion's `supersedes` redirected its re-tiering onto the larger value (floor ~10/1010 →
  ~1000/1010) with the chain unbroken. Fix: the overlay now honors ONLY hash-bound (v6+) receipts,
  and both public verifiers reject duplicate ids — in `kry_mint.veracity_breakdown`, `kry_attest`
  (build + verify), and `kry_verify`.
- **F1 (MED) — PQC threshold v1 back-compat reopened cross-context replay.** The threshold verifier
  accepted legacy `kry-pqc-threshold/v1` (raw-byte) artifacts, so an attacker could declare
  `scheme=v1` to opt out of the v2 domain separation (replay a standalone signature as a contribution,
  or a contribution across councils). Fix: the threshold verifier now REQUIRES v2; single-signer v1
  authenticity stays (now killable via `--require-v2`, and it warns).
- **A1-3 (MED) — unpinned TLSNotary minted anchored `tlsn_attested`.** The notary pin was enforced
  only when `--notary-key` was given, so an unpinned presentation minted anchored credit at floor 1.0.
  Fix: `kry_tlsn_verify` refuses to mint `tlsn_attested` without a pinned notary (`NO_NOTARY_PIN`).
- **F2 (MED) — "externally anchored" overstated the veracity floor.** `provider_metered` and
  `holdout_validated` are operator-run, and the metered payload bounds the EVENT, not the magnitude:
  an anchor witnesses that a call happened, not the counterfactual `tokens_saved`/`avoided_model`. Fix:
  renamed the public field `externally_anchored_kry` → `anchored_kry` across the package, the stranger
  verifier, and attestations; the note + CLI now state the floor is "stronger than self-report (external
  OR operator-run)" and that an anchor does not prove the magnitude.
- **A1-4 (MED) — release dev pins were stale + un-hashed.** `kry_release_verify` pinned
  `pytest==9.1.0 / ruff==0.15.17` while pyproject moved to `9.1.1 / 0.15.18` (a stale duplicate on the
  `id-token:write` runner). Fix: synced the pins + a drift-guard test. (Hash-pinning / de-privileging
  the dev install on the release runner remains a tracked follow-up, with L1 below.)
- **Confirmed by-design** (both auditors): M3/M4 (no remote path to `attested_balance == -1` / the
  labeled `self_asserted` basis). **L1** (a magnitude may cite a more-expensive avoided model than the
  real one — bounded ≤1.0, disclosed for self_reported; on anchored tiers it compounds F2's now-honest
  "magnitude is operator-asserted" labeling) is tracked, not yet bound. Regressions:
  `tests/test_audit_deep_external.py`.

### Changed

- **License: PolyForm-Noncommercial-1.0.0 → Apache-2.0.** KRY is now permissively open source
  (OSI-approved, with an explicit patent grant + defensive-termination clause). Commercial use is
  free; the prior noncommercial restriction is removed. Copyright remains Thomas Albrecht; inbound
  contributions are accepted under Apache-2.0 (inbound=outbound).

### Security (supply chain)

- **L5 — hash-pinned release build frontend.** The Release workflow installed `build` via
  `pip install --upgrade pip build` on the `id-token:write` runner; it now installs from
  `.github/build-requirements.txt` with `--require-hashes` (`build`/`packaging`/`pyproject_hooks`
  pinned by sha256), so a tampered or substituted artifact fails the release closed. Verified to
  install and run (`build 1.5.0`) in a clean venv.
- **L7 (partial) — digest-pinned PoC enclave bases.** `poc/nitro/enclave/Dockerfile` pins
  `rust:1-bookworm` and `debian:bookworm-slim` by `@sha256:` digest (fetched from the registry) for a
  reproducible PCR0. Committing `Cargo.lock` (+ a `--locked` build) for full dependency reproducibility
  needs the Rust toolchain and is documented inline as the one remaining manual step.

### Security (PQC threshold — L3)

- **PQC signature domain separation (`kry-pqc/v2`).** Single-signer and threshold signatures now
  commit to their *context*, not just the attestation bytes: a single-signer signature is taken over
  `"kry-pqc/v2/single\0" || bytes`, and a threshold contribution over
  `"kry-pqc/v2/threshold\0" || policy_sha256 || bytes`. This closes (a) replaying a standalone
  signature as a council contribution, and (b) replaying a contribution into a *different* council
  that shares a member. `threshold.contribute()` now takes the council `policy`. Additive and
  version-dispatched — the verifiers accept both `v2` and legacy `v1` (raw-byte) artifacts, so
  existing signatures still verify. Four new regression tests cover domain separation, cross-council
  replay, and v1 single + threshold back-compat (PQC suite 12 → 16).

### Security (audit round 3)

- **PQC verifier `alg` allowlist (M1)** — `kry_pqc/verify.py` pins the attacker-supplied `alg` to the
  FIPS-204 ML-DSA sets (`ML-DSA-44/65/87`) inside the parse guard, so a bogus/unsupported mechanism fails
  closed (`RESULT: FAILED`, exit 1) instead of reaching `oqs.Signature(alg)` and raising an uncaught
  `MechanismNotSupportedError`. The three sets have distinct key lengths, so this also blocks
  alg-confusion under a pinned key. A `--expect-fingerprint` shorter than 16 hex chars now warns.
- **Nitro COSE `alg` guard fails closed (M2)** — `scripts/kry_tee_verify.py` now requires the COSE
  protected header to decode to a map pinning `alg = ES384`; a missing/undecodable/non-dict header is
  rejected, not silently accepted. (The ES384 verify was already hard-pinned; now the documented guard
  fails closed too.)
- **Pending store fails closed on corruption (M5)** — `kry_pending._load` quarantines a present-but-
  unparseable store to `<path>.corrupt`, logs, and raises `PendingStoreCorrupt` instead of silently
  resetting to `{}` (which would erase `confirm()`'s write-ahead idempotency and open a re-mint window).
  "File absent" still returns `{}`.
- **Cross-process lock degradation is logged (M6)** — `_locks.cross_process_lock` emits a one-time
  warning when neither `fcntl` nor `msvcrt` is available (cross-process serialization is then off;
  latent on macOS/Linux), instead of a silent no-op.
- **PQC secret-key write closes a chmod TOCTOU (L2)** — `kry_pqc/signer.py` creates the secret key with
  `O_EXCL | 0o600` rather than write-then-`chmod`, so it is never briefly umask-default readable (and it
  refuses to clobber an existing key).

### Fixed (audit round 3)

- **Reproducible wheel (L6)** — `build_backend.py` stamps every wheel entry with `SOURCE_DATE_EPOCH`
  (else the 1980 zip epoch) and fixed perms instead of the build-time wall clock, so the wheel is
  byte-reproducible (RECORD data-hashes unchanged; verified byte-identical across builds + installable).
- **Test isolation** — `kry_pending` is now repointed to a per-test data dir by the autouse fixture
  (it was the one persistence module missing from `conftest`).

### Documentation (audit round 3)

- **`cache_creation` rate drift (M7)** — the spec table, its prose, and the `kry_token` module-docstring
  table said `0.1`; the code earns `0.0` (a cache write is a cost/bet — the realized saving is the later
  cache hit; crediting both double-counts). All three now read `0.0`, and the missing
  `continuity_capsule = 0.1` row is added.
- **Veracity-ladder wording (L9)** — the README and `KRY_VERACITY_BINDING.md` framed `veracity_floor`
  as "external anchor (T1+T2)" only; both now state the floor counts anything stronger than bare self-
  report, including an operator-run randomized holdout (`holdout_validated`), matching `_ANCHORED_TIERS`.
- **Settlement trust boundaries (M3/M4)** — `settle()`'s docstring now states the two intentional
  operator-side boundaries explicitly: a directly-built grant (`attested_balance = -1`) is exempt from
  the commit-time ceiling re-check (no ceiling to check), and the `self_asserted` conservation basis is
  labeled, not verified. By design; not remote-exploitable.

### Residuals

- **Every audit finding is now addressed.** The only outstanding item is **L7's `Cargo.lock`** — a
  one-step manual task that needs the Rust toolchain (the enclave base images are already digest-pinned;
  see *Security (supply chain)*). The compact-signature **FROST** upgrade noted in
  `kry_pqc/threshold.py` remains an optional future enhancement, not an audit finding.

### Security (audit round 2)

- **Durability fail-closed** — `KRYLedger.save()` re-raises on a write failure (and fsyncs), adopting
  merged state only after a durable write so a failed save loses no delta; the replay-cap decay-state
  write fails closed (no mint if the count isn't durable); a corrupt ledger is quarantined and rebuilt
  from the chain rather than silently blanked.
- **`hash_version 7` binds `event_type`** into the chain (closes a same-`earn_rate` link relabel under
  a published anchor); additive, version-dispatched — v4/v5/v6 byte-unchanged. `evidence_hash` is now
  full SHA-256 (was 64-bit truncated).

### Fixed (audit round 2)

- **`kry_savings_report`** strict boolean parsing (`"false"` no longer counts as a cache hit) + a
  `--strict-baseline` mode valuing un-validated cache-hit savings at 0 for external reports.
- **`kry_reconcile`** CLI no longer crashes (`None * 100`) with no T1 receipts; **`kry_or_fetch`/privacy**
  — `provider_name` added to the export allowlist; **`kry_carbon`/`kry_baseline`** env constants reject
  NaN/inf/out-of-range; settlement lease stale-lock stealing is opt-in (`KRY_SETTLE_LEASE_STEAL_STALE`).
- **Honest wording** — "trustless settlement" → "federated, registry-backed"; "zero-knowledge seam" →
  "content-sealed attestation (not a ZK proof)"; sealed-evidence "uncorrelatable" qualified;
  `verify_capabilities` `clean` → `static_claims_resolve`.

### Security

- **Mint-chain magnitude gate in `verify_chain`** — the in-package chain verifier now recomputes
  each receipt's implied price multiplier (matching the standalone `scripts/kry_verify.py`) and
  rejects a fabricated `kry_minted`, a non-standard `earn_rate`, a `provider_metered` receipt
  missing its `metered_tokens`, or an edited `usd_equivalent`. Closes a path where
  `reconcile_ledger_from_chain` could rebuild a balance from a forged pre-v4 chain.
- **`hash_version = 6` binds `receipt_id` into the chain hash** (additive, version-dispatched —
  v4/v5 receipts and the evidence bundle are byte-unchanged). A T2 tier-promotion's `supersedes`
  target can no longer be relabeled onto a different, larger receipt to inflate `veracity_floor`.
  The cross-language hash spec in the README is updated accordingly.
- **PQC verifier hardening** — `kry_pqc.threshold.verify_threshold` now independently enforces a
  valid `1..council_size` threshold and recomputes each signer's fingerprint from its public key
  (rejecting a council that lists one key under two fingerprints); the single-signer and threshold
  verifiers fail closed (not crash) on malformed artifacts.

### Fixed

- **Accounting** — `reconcile_ledger_from_chain` now subtracts `total_spent` instead of resurrecting
  already-spent KRY; cross-process `spend()` can no longer drive the on-disk balance negative; the
  delta-merge `save()` no longer clobbers a concurrent writer's event records; `efficiency_ratio` is
  correct for sub-1-KRY ledgers.
- **Settlement** — a failed/under-reporting debit rolls the registry obligation back (no phantom
  obligation, grant stays retriable) while still never debiting on a commit failure; a rejected
  settle no longer leaks its in-process reservation.
- **Persistence fail-closed** — `kry_sanctions.record_reconciliation` raises instead of returning a
  sanction it never persisted; `kry_referee` ratify/sanction/revoke take the cross-process lock;
  `revoke_ascension` no longer reports failure after a successful revoke.
- **Pending displacements** — `confirm()` persists `confirmed` write-ahead (no double-mint on a
  crash between mint and persist); a non-finite `ttl` is rejected so a pending can't become
  un-expirable.
- **TLSNotary T2** — refuses a second fresh credit for a provider generation already minted, and
  matches gen ids exactly (not as a substring) so a short id can't mis-bind to another session.
- **Verifier CLIs** — `kry_verify` no longer crashes on a non-dict `veracity`; the TEE / SEV-SNP /
  TLSNotary mint scripts exit non-zero (not `0`) when a mint did not happen; the Nitro X.509 walk
  binds issuer names (`verify_directly_issued_by`).
- **Robustness** — `wilson_interval` clamps out-of-range inputs instead of crashing on a corrupted
  store; `kry_pending` rejects `NaN`/`Infinity` JSON constants on load.

## [0.1.0] - 2026-06-17

Initial public release. `kry` turns the usage logs you already have into a
stranger-verifiable proof of what your caching and routing actually saved —
zero runtime dependencies, pure Python stdlib.

### Added

- **Core lifecycle** — earn → mint → attest → a stranger verifies → carbon, on real
  efficiency events (`kry_token`, `kry_mint`, `kry_attest`; `examples/try_kry.py`).
- **Integrity ≠ veracity, made explicit** — SHA-256 hash-chain receipts prove a balance is
  intact and conserved; a published `veracity_floor` labels how much still rests on operator
  self-report, never hidden behind a green checkmark.
- **Veracity ladder** — T0 self-reported, T1 provider-metered (F1 reconciliation against a real
  provider export), T2 external anchor (`tee_attested` / `tlsn_attested`).
- **Stranger verifier** (`scripts/kry_verify.py`) — stdlib only, imports nothing from the
  package; checks integrity + conservation + magnitude (price arithmetic recomputed from the
  public price table).
- **External chain-head anchor** (`kry_mint.export_chain_anchor`, `scripts/kry_chain_anchor.py`)
  — makes a silent re-mint detectable against an operator-published anchor.
- **Conservation settlement** with single-host multi-process double-spend and rollback guards
  plus a published registry anchor (`kry_settlement`).
- **Counterfactual holdout + savings/FinOps reports** (`kry_baseline`, `scripts/kry_savings_report.py`).
- **Optional, audited crypto tiers** behind extras — AWS Nitro + AMD SEV-SNP attestation
  (`[tee]`), a TLSNotary T2 path proven end-to-end against production openrouter.ai, and a
  post-quantum ML-DSA authenticity tier (`[pqc]`). All fail closed without their optional
  dependency.
- **Computed readiness grade** (`kry_capabilities.readiness_label`) and a mechanically-checked
  capability matrix. Readiness is `research_grade` — a durable provider-reconciled anchor
  (agreement 1.00 ≥ the 0.80 bar) — with `production_ready`/A+ honestly gated on external
  real-world evidence.
- **Carbon estimate** (`kry_carbon`) — a second denomination, labelled ESTIMATE, not a
  certified carbon credit.
- **Hardening** — regression tests across the verifier, mint, and settlement attack surface
  (tier forgery, magnitude skim, double-spend, rollback, re-mint, tail-truncation, fail-closed
  crypto), exercised by the stdlib suite.

[Unreleased]: https://github.com/thequantumfalcon/kry/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/thequantumfalcon/kry/releases/tag/v0.1.0
