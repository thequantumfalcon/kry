# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

_Nothing yet._

## [0.1.1] - 2026-07-21

### Added (SPEC v1.1 — the promotion-overlay profile)

- **SPEC §3.7: informative → optional, normatively-specified profile.** The overlay's five
  invariants + outcome guard are now spec text with their own vector category
  (`vectors/savings/overlay/`: one VALID real promotion built via `promote_to_tlsn`; four
  adversarial — forward-reference capture, positive-value promoter, duplicate hash-bound
  `receipt_id`, double-claim; expected verdicts generated from the reference). A verifier
  either claims the profile and matches these vectors, or MUST fail closed on any attestation
  containing a non-null `supersedes`. `verifiers/js` now **implements the profile** (replacing
  its interim fail-closed refusal) and agrees with the Python reference on the full corpus.
  The CLAIMS_BOUNDARY overlay-conformance block is lifted accordingly; published-anchor
  semantics remain deferred (see below).
- **`docs/SPEC_DEVELOPMENT.md` — the spec development sheet.** Shipped revisions, the ground
  rules every spec change must clear, the v1.2 anchor-profile candidate (re-mint + trailing-
  truncation vectors — the one §3.7-deferred item still uncovered), four attestation-surface
  candidates (veracity-floor reasons enumeration, optional falsifier field, per-field
  provenance kinds, minimum-n reporting), adopted process disciplines, and the
  considered-and-rejected list.

### Changed (contribution + claims process)

- CONTRIBUTING rule 7 — evidence discipline (seal the artifact's sha256 before analysis;
  literal note before interpretation; verbatim claim-mutation log), adapted from the author's
  Regurgitate protocol as prose norms, not machinery. CLAIMS_BOUNDARY now states the
  **separation invariant** (the thing evaluated must be external to the logic evaluating it).

### Added (spec + independent verification surface)

- **KRY-SPEC v1.0** (`SPEC.md`, 2026-07-04) — the first normative wire-format spec: canonical
  JSON, `canon_f64`, the savings v4–v7 chain + magnitude + tier-schema + veracity + envelope
  verdict, and the action profile. Promotion-overlay/anchor semantics explicitly deferred
  (§3.7). Ships with a conformance-vector corpus (`vectors/` — exact-bytes primitives plus
  valid/adversarial savings and action attestations, generated from the reference by
  `vectors/generate.py` so they cannot drift).
- **Independent JS verifier + browser page** — `verifiers/js/` (dependency-free Node ESM, with
  a corpus runner: `node verifiers/js/cli.mjs --vectors vectors`) and a static browser verify
  page (`verifiers/web/`).
- **CI job `conformance-vectors`** — runs the JS verifier over the corpus plus a drift guard
  (regenerate from the reference, `git diff --exit-code`) on every push/PR; the independent
  verifier and the corpus were previously not exercised in CI at all.

### Fixed (audit hardening — five findings, severity re-rated on reproduction)

- Settlement: `_record_settled` undoes the registry append if the tip-checkpoint write fails,
  so no phantom settlement can linger (fail-safe). Mint: a tip-write failure after a durable,
  chain-valid receipt keeps and returns the receipt instead of under-reporting the mint (the
  stale tip self-heals on the next mint). `kry_verify`: a malformed declared `veracity_floor`
  prints a clean `VERDICT: INVALID`, not a traceback. `kry_baseline`: adopts the repo-wide
  strict-JSON boundary (reject NaN/Infinity) and validates `observe_treated(n)`.
  `kry_pending`: uses the shared cross-process lock (which has an msvcrt path), closing a
  Windows double-mint window.

### Security (JS verifier fails closed on the overlay)

- `verifiers/js` now rejects any savings link carrying a non-null `supersedes` with an
  explicit reason, instead of silently computing an overlay-free `veracity_floor` (the
  promotion overlay is informative in SPEC v1.0 §3.7 and this verifier does not implement
  it). Closes a split-verdict window: an attestation declaring the overlay-free floor
  previously passed this verifier while the reference implementation re-tiered.

### Changed (packaging)

- **PyPI distribution name: `kry` → `kry-attest`.** The PyPI name `kry` belongs to an
  unrelated package ("Simple cryptography library"), so `pip install kry` fetches someone
  else's code. The wheel now builds as `kry_attest-<version>-py3-none-any.whl`, and the
  `[tee]` extra hints in the TEE/SNP verifiers say `pip install "kry-attest[tee]"`. The
  import name (`import kry`) and every receipt/attestation wire format are unchanged.

### Security (release path)

- **Release workflow enforces signed-tag verification** (the operator item flagged in 0.1.0's
  release-workflow hardening, now done): `release.yml` runs `git verify-tag` against
  `.github/allowed_signers` before building, so a tag not signed by an allowed key fails the
  release closed. The action-receipt layer is also now disclosed in `docs/CLAIMS_BOUNDARY.md`
  (proven: tamper-evident content-free receipts; blocked: the T1 third-party-witness claim
  until a real MCP-server signature).

### Documentation

- **Promotion-overlay trust boundary made explicit** — `docs/CLAIMS_BOUNDARY.md` and the README's
  cross-language spec callout now state that the overlay (a `supersedes` link re-tiering an
  earlier receipt) is not exercised by any vector in the v1.0 conformance corpus: an independent
  verifier must reproduce the five SAFETY-CONTRACT invariants plus the outcome guard exactly, or
  fail closed on any attestation containing a `supersedes` link. The overlay conformance claim
  stays blocked until such vectors exist.
- Evidence docs: SC1 cold-implementer wording generalized.
- **README claims right-sizing** — the `readiness: research_grade` chip now carries its evidence
  scope inline (n=52 free-tier token-count reconciliation — grounds that the calls existed, not
  that dollars were saved), and the *Honest limitations* section moved up next to the trust
  ladder, gaining a bullet on cross-process locking over network filesystems (`flock`/NFS
  unreliability on a shared data dir).

## [0.1.0] - 2026-06-28

Initial public release — signed tag `v0.1.0` at `28d98ae`. `kry` turns the usage logs you
already have into a stranger-verifiable proof of what your caching and routing actually
saved — zero runtime dependencies, pure Python stdlib. Every audit-round entry below IS
included in this release: the entries accumulated under *Unreleased* while the release was
prepared, and were folded into this section after tagging (the heading previously carried
the 2026-06-17 date the version was first cut in `pyproject.toml`).

### Security (remediation — two independent deep audits, 2026-06-28)

Two independent maximum-depth audits each surfaced a real issue the other (and six prior rounds)
missed; both were reproduced before fixing, and every fix ships with a regression test (607 tests).

- **OVERLAY (HIGH) — positive-value promotion double-count.** A tlsn/tee link that BOTH minted its own
  value AND carried `supersedes` had its value booked to the anchored tier, then the overlay moved the
  superseded receipt's value on TOP — one anchored receipt double-counting an unrelated one (forged
  `veracity_floor` 1.0 vs the honest 0.333, confirmed passing the stranger verifier). The contract's
  invariant #4 ("a promotion is itself zero-value") was asserted but never enforced; it is now enforced
  at all four overlay enqueue sites (`kry_mint`, `kry_attest` build + verify, `kry_verify`).
- **SETTLE-1 (HIGH) — cross-node double-spend via `offer_id` nonce collision.** `offer_id` is a
  spender-settable field, and idempotency keyed on it at THREE sites (the cross-node lease, the
  in-process reservation, and `settle()`'s reservation-clear). Reusing one `offer_id` across two offers
  to different recipients was taken as an idempotent replay and bypassed the ceiling. All three now key
  on a canonical content identity (`from:to:amount:tokens:ts`, one shared `_offer_identity` helper), and
  the lease re-asserts the ceiling on replay.
- **MINT-1 (MED) — fresh-T2/tee dedup race (TOCTOU).** The gen-id / measurement uniqueness check ran
  before `mint()` and outside its lock, so two transient-byte-differing presentations of one provider
  generation both minted. `mint()` now takes an in-lock `dedup_check`. Extended past the audit's
  tlsn-only scope to the tee/snp fresh-mint paths, which had no fresh-dedup at all.
- **CONC-2 (MED) — action-chain fork under multi-process serving.** `kry_action.record()` chained off a
  per-process in-memory tip, so concurrent workers (a multi-worker MCP server) forked the chain. It now
  re-reads the authoritative tip under a cross-process lock with an atomic fsync append (mirrors
  `kry_mint.mint()`). Also **ENV-1**: `kry_action._kry_data_dir()` gained `.expanduser()`.
- **Low hardening:** the PQC threshold verifier now allowlists the ML-DSA alg and fails clean on a
  malformed policy (no uncaught KeyError) [PQC-1/2]; the AWS-Nitro X.509 chain rejects an issuer that is
  not a CA (BasicConstraints) [EXT-1]; the CBOR decoder and the artifact privacy scan bound their
  recursion → clean failure, not a `RecursionError` [EXT-2 / F2].

### Added (new controls — opt-in, default OFF)

- **Per-window issuance cap** — `KRY_MINT_WINDOW_CAP` (+ `KRY_MINT_WINDOW_SEC`, default 86400) bounds
  KRY minted per rolling window; unset, minting is unbounded (default unchanged). Bounds
  honest-but-fabricated at-scale minting; supply visibility stays in `kry_token.supply()`.
- **Opt-in settlement policy guard** — `kry_settlement.set_settlement_guard(fn)` registers a
  `(offer, attestation_json) -> reason | None` hook to gate settlement on operator policy (reputation /
  audit-rate via `kry_referee` / `kry_sanctions`). Default OFF; SECURITY.md now documents that those
  modules are advisory scaffolding, not enforced by default [SANC-1].

### Changed

- Release-workflow hardening: a `concurrency` guard (one release per ref), `persist-credentials: false`
  on the release checkout, and an `environment: release` binding (configure required reviewers in repo
  settings to gate). Two items flagged for operator action: enforce signed-tag verification
  (`git verify-tag`, needs allowed-signers) and pin-or-drop the release job's dev-tool install.

### Added

- **Action-receipt layer (`kry_action`)** — tamper-evident, stranger-verifiable receipts for agent
  ACTIONS (the `kry_mint`/`kry_attest` discipline applied to "what did the agent DO?"). Content-free
  hash chain (canonical JSON + IEEE-754 big-endian floats + `chain_hash = SHA256(prev:receipt_hash)`),
  three veracity tiers (T0 `self_reported` / T1 `server_witnessed` / T2 `attested`) with a
  `veracity_floor`; a stdlib-only stranger verifier (`scripts/kry_action_verify.py`, imports nothing
  from the package and coerces a witness-less anchored tier to T0); an anchor for re-mint/dropped-action
  detection; and a zero-dependency MCP middleware (`scripts/kry_action_mcp.py`, `@attested_tool`).
  20 adversarial tests (`tests/test_action.py`), stdlib-only, ruff clean. By design it carries NO
  promotions (so no overlay/forward-reference class of bug) and a single hash version (no downgrade
  vector). **Known limits (disclosed):** T1 binds whatever the witness fn returns — until wired to a
  real MCP server signature it is operator-supplied; single-process writer only (the `kry._locks`
  cross-process swap is a one-liner). Not yet wired into the release gate / doctor / CLAIMS_BOUNDARY.

### Security (audit round 5 — third independent deep audit)

- **A1-1b (HIGH) — promotion order bug: an earlier promotion could capture a LATER receipt.** The
  round-4 fix gated promotions to v6+ targets and rejected duplicates, but built one global
  `receipt_id`→receipt map and applied promotions AFTER the scan — so a zero-value promotion at
  position 0 superseding `RID-future`, with a 1000-KRY receipt carrying `receipt_id="RID-future"`
  appended at position 1, captured it (`veracity_floor=1.0`, chain + anchor intact). Fix: promotions
  now resolve against the verified forward scan — a promotion may re-tier ONLY a positive-value,
  hash-bound (v6+) receipt seen EARLIER, and each receipt is consumed (promoted at most once). Applied
  in `kry_mint`, `kry_attest` (build + verify), and `kry_verify`; legit promotions (target before the
  promotion) still work.
- **F2 completion** — the round-4 rename `externally_anchored_kry` → `anchored_kry` is now also
  reflected in the README / examples / CLI prose (no more "externally anchored" where the tiers are
  operator-run), and the verifiers accept the OLD field name as a read-only **legacy alias** so a
  pre-rename attestation still verifies (the round-4 rename had been an un-aliased schema break).

### Deferred (audit round 5)

- **Release dev tooling un-hashed on the privileged job (MED).** `release.yml` still runs
  `pip install -e ".[dev]"` and `kry_release_verify` installs `DEV_REQUIREMENTS` without
  `--require-hashes`, on the `id-token:write` job. The correct fix is to **de-privilege** — split a
  read-only `gate` job (tests/lint/verify) from a privileged `publish` job (hash-pinned build +
  provenance only). Deferred because it is only verifiable on a real release run, and a full
  hash-pinned dev lock is blocked locally by `ruff`'s platform-specific binary wheel (a linux-targeted
  hash-pin cannot be install-verified on macOS). Tracked, not shipped blind.

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

### Changed (license)

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

### Added (initial feature set)

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

[Unreleased]: https://github.com/thequantumfalcon/kry/compare/v0.1.1...HEAD
[0.1.1]: https://github.com/thequantumfalcon/kry/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/thequantumfalcon/kry/releases/tag/v0.1.0
