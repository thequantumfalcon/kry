# kry Release Checklist

This checklist defines the current release-candidate surface. It is intentionally
stricter than "tests pass": a release candidate must be installable from tracked
files, must run the public packet workflow, and must keep external-savings claims
blocked until real evidence exists.

## Shipped In This Release Candidate

- Core stdlib package under `src/kry/`.
- Hash-chain mint ledger, public attestation, stranger verifier, settlement guard,
 sanctions/referee/capability modules, and strict JSON boundary checks.
- Public command surface:
 - `scripts/kry_verify.py`
 - `scripts/kry_savings_report.py`
 - `scripts/kry_reconcile.py`
 - `scripts/kry_or_fetch.py`
 - `scripts/kry_research_grade.py`
 - `scripts/kry_tee_verify.py`
 - `scripts/kry_snp_verify.py`
 - `scripts/kry_tlsn_adapter.py`
 - `scripts/kry_tlsn_verify.py`
 - `scripts/kry_verified_artifact.py`
 - `scripts/kry_finops_report.py`
 - `scripts/kry_doctor.py`
- Additional checkout-only evidence tools in [0.1.11](../CHANGELOG.md#0111---2026-10-22).
 They require this checkout or the full GitHub source archive; the PyPI wheel and
 installable source distribution contain the core and standalone verifier only:
 - `scripts/kry_evidence_assess.py` — supplied-export token consistency assessment.
 - `scripts/kry_usage_link.py` — individual row commitment and linkage checks.
 - `scripts/kry_anthropic_usage.py` — a narrow, pinned-model list-price estimate.
 - `scripts/kry_anthropic_projection.py` — consistency checks on a supplied saved response.
 - `scripts/kry_litellm_observation.py` — optional private normalized-usage observations.
 These tools do not authenticate supplied records or establish account charges or settlement.
- Verified-savings packet workflow for internal/demo artifacts:
 doctor -> savings report -> mint/attest -> verify -> bundle -> verify artifact
 -> FinOps report.
- Packaging path: `python3 -m pip install -e .` works without downloading build
 dependencies; `python3 -m pip install .` is also verified. Runtime dependencies
 remain empty.
- Single release gate: `python3 scripts/kry_release_verify.py`; includes editable
 and wheel installs, compileall, pinned ruff/pytest, attribution guard, diff
 whitespace checks, dry-run untracked-file check, packet workflow, doctor, and
 reproducibility smoke.
- Full release gate: `python3 scripts/kry_release_verify.py --full`.
- New reports use `kry_release_verify/v2`: commit, tree, tags, Python version and
  tracked-diff identity are recorded before and after testing. Source changes fail
  the gate. Local dirty-source reports remain identifiable but cannot authorize publication.
- Local reproducibility harness: `bash lab/reproduce.sh 10`.

## Not Shipped As External Validation

- No external verified-savings claim is shipped from the bundled sample data.
- No `production_ready` claim is shipped for external use.
- No tradeable token, exchange, securities, carbon-credit, or legal approval claim
 is shipped.
- No buyer reliance claim is shipped without outside review, buyer feedback, legal
 review, real provider export, and real corpus manifests.

## Optional Or Prototype Surfaces

- `kry_pqc/`: optional post-quantum authenticity tier. Requires `oqs`
 (`liboqs-python`). It is not part of the stdlib release gate unless those
 dependencies are installed and `python3 -m pytest kry_pqc/test_kry_pqc.py -q`
 passes without skips.
- `tlsnotary/`: prototype/reference material for TLSNotary T2 work. It records
 prior prototype evidence and integration patches; the stdlib release gate only
 verifies the KRY-side adapters and fail-closed verifier tests.
- `poc/nitro/`: AWS Nitro Enclaves proof-of-concept. It requires AWS hardware,
 `cryptography`, Rust, Docker, and Nitro tooling; it is not part of the default
 stdlib release gate.

Optional-tier semantics:

- PASS: the optional command runs in the release environment with all required
 dependencies/hardware and no skipped tests for that tier.
- SKIP: dependencies or hardware are absent; the tier remains documented as
 optional/prototype and cannot support a release claim.
- FAIL: the tier was attempted and failed; do not claim that optional tier until
 the failure is fixed and rerun.

## External Blockers

- Real provider export for the same billing window as the T1 mint set.
- Real, non-synthetic corpus manifest with collection window and validation plan.
- Outside reviewer evidence that ran `--verify-artifact` and `kry_doctor.py`.
- Buyer feedback proving materiality/reliance threshold.
- Legal review explicitly approving external retained-savings language and
 confirming tradeable-token disclaimers.

## Required Local Verification

For 0.1.17, exercise the raw-input and explicit-commitment examples in the
[migration guide](MIGRATING_0_1_17.md) in a disposable data directory. Confirm
version metadata agrees before tagging. Runtime upgrades require their own
backup and writer-stop procedure; publication alone upgrades no running writer.

Run from a fresh checkout or tracked-file snapshot:

```bash
python3 scripts/kry_release_verify.py --full
git clean -nd
python3 -m pip install -e .
python3 -m pip install .
python3 -m pytest tests/ -q
bash lab/reproduce.sh 10
```

Run the packet happy path from scratch:

```bash
tmp=$(mktemp -d "${TMPDIR:-/tmp}/kry-release.XXXXXX")
export KRY_DATA_DIR="$tmp/kry_data"

python3 scripts/kry_doctor.py
python3 scripts/kry_savings_report.py examples/sample_usage_log.jsonl
python3 scripts/kry_savings_report.py examples/sample_usage_log.jsonl --mint --attest "$tmp/att.json"
python3 scripts/kry_verify.py "$tmp/att.json"
python3 scripts/kry_verified_artifact.py examples/sample_usage_log.jsonl \
 --attestation "$tmp/att.json" \
 --mint-log "$KRY_DATA_DIR/kry_mint_log.jsonl" \
 --bundle-dir "$tmp/packet"
python3 scripts/kry_verified_artifact.py --verify-artifact "$tmp/packet/artifact.json"
python3 scripts/kry_finops_report.py "$tmp/packet/artifact.json"
python3 scripts/kry_doctor.py --artifact "$tmp/packet/artifact.json"
```

Expected packet result on bundled sample data:

- `scripts/kry_verified_artifact.py --verify-artifact ...` returns `ok: true`.
- `ship_scope` remains `internal_or_demo_only`.
- `external_verified_savings` remains blocked.
- `scripts/kry_doctor.py --artifact ...` has zero FAIL items and keeps external
 evidence warnings until real provider/reviewer/buyer/legal evidence exists.

## Publishing

- Push a signed `vX.Y.Z` tag on the release commit, with the version matching `pyproject.toml`. Use
  the GitHub noreply address as the tagger email: GitHub rejects a push that would publish a private
  email address, and `.github/allowed_signers` lists the noreply address.
- The Release workflow checks the tag format, package-version match and signature before
  gating or publishing. Its `gate` job runs
  `scripts/kry_release_verify.py --full`.
- Before building, `publish` validates the downloaded full proof against its own
  clean checkout and expected tag with `--verify-proof ... --expected-tag ...`.
  Old-format, missing-check, failed, dirty or mismatched proofs are refused. The
  check also rejects untracked or ignored files that would enter the package, and
  runs again after frontend installation immediately before building. Cache files
  excluded by the backend remain excluded from this package-input check. The
  identical proof bytes are included in `checksums.txt` and the provenance subjects.
  Historical release assets remain unchanged; this binding applies to new releases.
- Approve `publish` in the `release` environment. It creates the GitHub Release with the wheel,
  sdist, source tarball, `checksums.txt`, the release-verify proof and build provenance.
- Approve `pypi` in the `pypi` environment. It uploads the same wheel and sdist to PyPI after matching
  them to the release's `checksums.txt`. PyPI must list this repository as a trusted publisher for
  `kry-attest` (workflow `release.yml`, environment `pypi`).
- Both environments accept deployments only from `v*.*.*` tags.

The proof is a test report, not independent evidence that the tests are sufficient.
Source checks compare the beginning and end of the gate; they do not detect a
transient edit that is restored between those observations. Run releases in the
isolated hosted checkout. Provenance authenticates the workflow and artifact bytes;
trust still depends on the reviewed workflow, repository access and approval policy.

## Release Stop Conditions

Do not ship an external-facing release if any of these are true:

- `git clean -nd` lists release-critical files.
- `python3 -m pip install -e .` fails in a fresh environment.
- The full test suite or `lab/reproduce.sh 10` fails.
- The packet happy path fails or emits `ship_scope = do_not_ship`.
- Public docs reference files or commands not present in `git ls-files`.
- Any doc or report promotes sample/synthetic data as externally verified savings.

## Review Documents

- `docs/CLAIMS_BOUNDARY.md`: allowed, blocked, forbidden, and optional claims.
