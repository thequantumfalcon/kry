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
- `.github/build-requirements.txt` is the separate existing release-build lock.
  The publishing job installs only that lock and builds with isolation disabled.

## Updating the locks

Keep pytest and ruff direct pins in `pyproject.toml`; do not edit their copies in
the lock files. Change the integration pin in its `.in` file. After a dependency
update, regenerate both locks from the repository root (generated with uv 0.12.1):

```bash
uv pip compile pyproject.toml --extra dev --universal --python-version 3.11 --only-binary=:all: --generate-hashes --no-annotate --no-header --output-file .github/dev-requirements.txt
uv pip compile pyproject.toml .github/litellm-requirements.in --extra dev --python-platform x86_64-unknown-linux-gnu --python-version 3.11 --only-binary=:all: --generate-hashes --no-annotate --no-header --output-file .github/litellm-requirements.txt
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
