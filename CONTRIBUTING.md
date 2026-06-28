# Contributing to KRY

Thanks for your interest. KRY is **open source** under the
[Apache License 2.0](LICENSE.md). Contributions are accepted under the same license — inbound=outbound:
by submitting a contribution you license it under Apache-2.0. This project is governed by our
[Code of Conduct](CODE_OF_CONDUCT.md).

KRY values one thing above all: **claims you can prove to a stranger who doesn't trust you.** The
rules below exist to protect that.

## Non-negotiable rules

1. **Stdlib only.** No third-party dependencies in `src/kry/` or `scripts/`. The whole point is a
 zero-dependency package a stranger can audit. (Optional verifier tiers may use *audited* crypto
 behind the `tee` extra — never hand-rolled crypto.)
2. **The suite stays green and `ruff` stays clean.**
 ```bash
 PYTHONPATH=src python -m pytest tests/ -q # must pass
 ruff check src/ scripts/ tests/ examples/ lab/ # must be clean
 ```
3. **Capability honesty is enforced, not aspirational.** Anything marked `implemented` in
 `kry_capabilities.py` must resolve to real code **and** tests — `verify_capabilities()` checks
 it in CI. Out-of-scope things are `not_guaranteed` *disclosures*, not stubs.
4. **Label every claim: measured vs. speculative.** "Tested on synthetic data" ≠ "validated on real
 traffic." Don't call conditional results established. `integrity ≠ veracity` — never hide a
 `veracity_floor = 0.0`.
5. **Never commit runtime data.** `KRY_DATA_DIR` (default `./kry_data`) is gitignored; tests isolate
 it via `tests/conftest.py`.
6. **No AI attribution in any artifact.** No `Co-Authored-By`, no "Generated with …", no AI credit
 in commits, docs, or comments. This is enforced by `.githooks/` and
 `.github/workflows/no-ai-attribution.yml` — it will block a violating commit.

## Development setup

```bash
git clone https://github.com/thequantumfalcon/kry.git
cd kry
python -m pip install -e ".[dev]" # pytest + ruff only; the package itself is zero-dep
PYTHONPATH=src python -m pytest tests/ -q
python examples/try_kry.py # 30s end-to-end: earn → mint → attest → verify → carbon
```
Optional crypto tiers: `pip install -e ".[tee]"` (adds `cryptography` for the `tee_attested` tier).

## Workflow

- Branch off `main` (`feature/*`, `fix/*`, `docs/*`). **`main` is canonical.**
- Keep changes **surgical** — touch only what the task requires; don't reformat or refactor adjacent
 code. Diff noise is a bug.
- Open a PR. The [PR template](.github/PULL_REQUEST_TEMPLATE.md) checklist is the bar. CI must pass:
 lint, tests, the reproducibility smoke (`lab/reproduce.sh`), the packet workflow, and the
 release-gate verifier.
- Confirm before destructive or shared-state actions (force-push, history rewrite, releases).

## Reporting bugs & vulnerabilities

- Bugs → an Issue using the [bug report template](.github/ISSUE_TEMPLATE/bug_report.yml).
- Security issues → **never a public issue** — follow [SECURITY.md](SECURITY.md).

New here? Start with [`README.md`](README.md).
