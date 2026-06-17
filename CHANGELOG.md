# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
