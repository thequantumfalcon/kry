# CI dependency locks

CI and the release verifier install external tools from complete SHA-256 locks,
using `pip --require-hashes --only-binary=:all:`. Workflow installs also force
reinstallation so a matching preinstalled package cannot bypass artifact checks.
Source distributions are excluded
so an unlocked build dependency cannot enter through an external package's build.
KRY itself is installed separately from the checked-out source with `--no-deps
--no-build-isolation`; its runtime and build-system requirements remain empty.
Hash checks identify the reviewed artifacts. They do not establish that upstream
code is safe, so the test gate retains read-only permissions and no signing identity.

- `.github/dev-requirements.txt` covers the pinned pytest and ruff tools plus
  transitives, with environment markers for platform-specific dependencies. It is
  used on Ubuntu Python 3.11–3.14, Windows Python 3.11 and local macOS.
- `.github/litellm-requirements.in` holds the integration-only direct pin.
  `.github/litellm-requirements.txt` locks it together with the development tools
  for the Ubuntu x86-64 / Python 3.11 integration job. It is not a general-purpose
  LiteLLM dependency policy or a runtime requirement of KRY.
- `.github/crypto-requirements.in` selects the tested optional cryptography and
  liboqs wrapper versions. Its complete `.txt` lock is used by the separate
  Ubuntu/Python 3.11 and 3.13 `optional-crypto` jobs; core dependencies remain empty.
- `.github/build-requirements.txt` is the separate existing release-build lock.
  The publishing job installs only that lock and builds with isolation disabled.

## Updating the locks

Keep pytest and ruff direct pins in `pyproject.toml`; do not edit their copies in
the lock files. Change the integration pin in its `.in` file. After a dependency
update, regenerate the locks from the repository root (generated with uv 0.12.1):

```bash
uv pip compile pyproject.toml --extra dev --universal --python-version 3.11 --only-binary=:all: --generate-hashes --no-annotate --no-header --output-file .github/dev-requirements.txt
uv pip compile pyproject.toml .github/litellm-requirements.in --extra dev --python-platform x86_64-unknown-linux-gnu --python-version 3.11 --only-binary=:all: --generate-hashes --no-annotate --no-header --output-file .github/litellm-requirements.txt
uv pip compile pyproject.toml .github/crypto-requirements.in --extra dev --python-platform x86_64-unknown-linux-gnu --python-version 3.11 --only-binary=:all: --generate-hashes --no-annotate --no-header --output-file .github/crypto-requirements.txt
python scripts/check_dependency_locks.py
```

The compiler preserves compatible existing transitive pins. To update a particular
transitive dependency, add `--upgrade-package PACKAGE` to the relevant command and
review the resulting version and hash changes. The lock compiler is a maintainer
tool; CI does not download or execute it. Do not paste replacement hashes from a
failed installation without verifying the intended PyPI release.

`check_dependency_locks.py` checks direct-pin synchronization and lock syntax
offline before tool installation. pip enforces the hashes and complete dependency
closure during installation; the sync check does not replace pip's resolver.
Dependabot changes to `pyproject.toml` require these generated locks to be refreshed
before CI passes. A lock update must pass the existing Python/OS matrix and the
mock-response integration job. No credentials or paid provider calls are needed.

For a fresh local development environment:

```bash
python -m venv .venv
# Activate .venv using your platform's activation command, then:
python scripts/check_dependency_locks.py
python -m pip install --require-hashes --only-binary=:all: -r .github/dev-requirements.txt
python -m pip install --no-deps --no-build-isolation -e .
python -m pip check
python -m pytest tests/ -q
```

The release verifier creates its own temporary environment with the same dev lock.
Release tooling changes must retain the local-source install tests, the full release
gate, and the separation between testing and publishing credentials.

References: [pip secure installs](https://pip.pypa.io/en/stable/topics/secure-installs/)
and [uv lock compilation](https://docs.astral.sh/uv/pip/compile/).

## Optional crypto job

The `optional-crypto` matrix covers Python 3.11 and 3.13, including their different
JSON parser recursion behavior. Each job builds liboqs 0.16.0 from the
[official release archive](https://codeload.github.com/open-quantum-safe/liboqs/tar.gz/refs/tags/0.16.0),
checking SHA-256
`162d5b510518ee5f285f82fa1f16402a885176e818bf1b1a4c3c91c9a2f01eae`
before extraction. This pins the downloaded bytes, not a separately authenticated
release signature. The native build enables only ML-DSA-44, ML-DSA-65 and ML-DSA-87,
with OpenSSL integration disabled and an explicit temporary installation prefix.
The compiler, CMake, Python and pip supplied by the runner remain bootstrap trust.

Before wrapper import, `scripts/kry_optional_crypto_check.py` requires the selected
native file to exist and load, then checks the wrapper loaded that exact path,
reports its SHA-256 and verifies its version and enabled algorithms. The pinned
wrapper otherwise attempts an automatic source installation on load failure.
`PYOQS_VERSION=disabled-by-kry-ci` makes that fallback reject the version before
cloning; this behavior must be rechecked when changing the wrapper pin. The test
subprocess inherits the same prefix and fallback guard.

The job performs real sign/verify and altered-message rejection for all three
parameter sets, then runs both TEE verifier test modules, the low-severity audit
regressions and the entire `kry_pqc/` suite. A JUnit report containing cases from every required module with zero skips,
failures or errors is required; dependency absence or an omitted module cannot
turn the job green. The reviewed minimum is 220 cases, rather than an exact count;
additional tests may grow the suite. Lowering that minimum requires reviewing an
intentional collection change.
It uploads only the test report. Keys are temporary test data; no provider credentials,
paid inference, live enclave deployment or external financial evidence are used.
Nitro coverage includes a retained genuine attestation fixture plus constructed
cases; SNP coverage uses constructed reports and certificate chains, not a new
genuine EPYC attestation. These checks do not constitute FIPS validation, independent key custody, hardware
attestation of the CI runner, or a guarantee of cryptographic superiority.

The ordinary release gate remains the minimal dependency profile. Its optional
skips and the doctor's external-evidence warning retain their original meaning.
