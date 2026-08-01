# AGENTS.md — instructions for AI coding agents

This repository sells reproducibility and honest claims. An agent that produces plausible-looking
work here does more damage than one that produces none, because the whole artifact is an argument
that unverified numbers should not be trusted. Read this before editing.

## What kry is

A zero-dependency, pure-stdlib Python library for tamper-evident, stranger-verifiable LLM
cost-savings receipts. The spine of the design is one distinction, and every claim in the repo is
organized around it:

- **integrity** — the ledger was not edited. Provable, and proved (SHA-256 hash chain).
- **magnitude** — the dollar figure follows from the events and a dated public price table.
  Provable, and proved (recomputed by the verifier).
- **veracity** — the saving actually happened. **Not provable.** A cache hit is a call that never
  reached a provider, so nothing outside the operator's runtime witnessed it. This is disclosed as
  an explicit number, `veracity_floor`, not hidden behind a checkmark.

If you find yourself writing a sentence that blurs those three, stop and rewrite it.

## Non-negotiable rules

1. **Stdlib only** in `src/kry/` and `scripts/`. No third-party imports, ever. `pytest` and `ruff`
   are dev tools, pinned in `pyproject.toml`. The `tee`/`pqc` extras are opt-in and must never leak
   into the core.
2. **No AI attribution anywhere** — not in code, comments, docs, commit messages, PR bodies, or
   release notes. `scripts/check_attribution.py` enforces this via a git hook, a CI workflow, and
   the release gate. Do not work around it.
3. **`docs/CLAIMS_BOUNDARY.md` is authoritative.** It states what the repo may cause a reader to
   believe. Where any README line, docstring, or demo reads stronger than the boundary, the
   boundary wins — fix the prose, not the boundary.
4. **Never commit runtime data.** `kry_data/` and the operator artifacts listed in `.gitignore`
   (provider exports, `prompts.jsonl`, gateway traffic, generated packets) are real private data.
5. **Say "the host system."** The private system that integrates kry is never named — not its
   module paths, feature flags, classifier names, or commit SHAs.
   `tests/test_public_claims.py` fails CI on a leak.
6. **Label measured vs projected.** "Tested on synthetic data" is not "validated on real traffic,"
   and the README must never blur them.

## Build and test

```bash
python3 -m pip install -e ".[dev]"
python3 -m pytest tests/ -q                          # the suite
ruff check src/ scripts/ tests/ examples/ lab/       # must stay clean
node verifiers/js/cli.mjs --vectors vectors          # second implementation vs the corpus
PYTHONPATH=src python3 vectors/generate.py           # regenerate vectors; must be byte-idempotent
python3 scripts/kry_release_verify.py                # the single release gate — run before pushing
```

## Spec changes

`SPEC.md` is normative and versioned. `docs/SPEC_DEVELOPMENT.md` holds the ground rules; the ones
that bite:

- **Additive or profiled.** Existing vectors and verdicts never change meaning.
- **Vectors or it didn't happen** — and note this is necessary, not sufficient. A rule whose
  vectors all exercise one branch is unpinned in the branches they miss. That is exactly how two
  verifiers silently disagreed for weeks about absent envelope keys.
- **Both implementations agree.** The Python reference and `verifiers/js` must pass the full corpus.
- **Fail closed** is the default posture everywhere.
- **Ordering trap:** `vectors/generate.py` derives every expected verdict *from the reference*. Fix
  the reference first, then regenerate — otherwise the new vector pins the wrong verdict.

There are **three** verify implementations, not two: `scripts/kry_verify.py` (the stranger replica,
which deliberately imports nothing from the package), `verifiers/js/verify.mjs`, and
`kry_attest.verify_attestation`. A rule change lands in all three or in none.

## Style

Match the surrounding code. This codebase annotates non-obvious constraints inline with a short tag
(`# R14 fix:`, `# HOLE #27:`, `# IMPORT-PURITY:`). Comments explain the *constraint* — why the code
must be this way — never the editing process. Surgical diffs: touch only what the task requires,
and do not reformat adjacent code.

## Security boundaries

`SECURITY.md` states the threat model: the adversary is a **false savings claim**, not a remote
attacker. The disclosed limits in `src/kry/kry_capabilities.py` (`per_event_counterfactual_proof`,
`source_truth_of_self_report`, `sybil_resistant_identity`, `real_world_validated_savings`) are
datasheet disclosures, not defects — do not "fix" them, and do not report them as vulnerabilities.

The promotion overlay (`kry_mint._apply_promotion_overlay`) has produced four HIGH findings across
audit rounds and carries a pre-committed tripwire: a fifth means the design is wrong and SPEC §3.7
gets deleted rather than patched again. Treat its five invariants and outcome guard as load-bearing.

## What not to do

- Do not try to move SC3/SC4/SC5/SC7 with code. They need a third-party timestamp, external
  adversaries, a real counterparty, and real traffic respectively. More verifier code moves none of
  them, and `tests/test_capabilities.py` structurally refuses to let code buy the top readiness rung.
- Do not add a dependency to the core to make something easier.
- Do not weaken a check to make a test pass.

See `CONTRIBUTING.md` for the contribution workflow and the AI-assisted-contributions policy.
