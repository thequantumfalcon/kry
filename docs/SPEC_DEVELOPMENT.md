# KRY-SPEC development sheet

The working plan for evolving [`SPEC.md`](../SPEC.md): what shipped in each revision, what is
a candidate for the next one, the acceptance bar every change must clear, and what was
considered and rejected. Companion to [`CLAIMS_BOUNDARY.md`](CLAIMS_BOUNDARY.md) (what may be
claimed today) — this file is about what the *wire contract* becomes next.

## Ground rules (every spec change must clear all of these)

1. **Additive or profiled.** A change is either version-dispatched (a new `hash_version` /
   field that legacy verifiers fail closed on) or an optional **profile** (like §3.7): named,
   self-contained, claimable. Existing vectors and verdicts never change meaning.
2. **Vectors or it didn't happen.** A normative rule ships with reference-generated vectors
   (`vectors/generate.py` — expected verdicts computed by the shipping implementation, never
   hand-written) in the same PR. No prose-only normativity.
3. **Two implementations agree.** The Python reference and `verifiers/js` must both pass the
   full corpus before merge (the `conformance-vectors` CI job enforces this).
4. **Fail-closed is the default posture.** A verifier that does not understand a version or
   does not claim a profile refuses; it never guesses or silently skips.
5. **The overlay tripwire stands.** The promotion overlay has produced four HIGH findings
   across audit rounds. A fifth overlay HIGH means the *design* is the problem and the
   feature is removed (spec revision deletes §3.7) — profiling it does not soften that.

## Shipped

| Rev | Date | Content |
|---|---|---|
| v1.0 | 2026-07-04 | First normative spec: canonicalization, `canon_f64`, savings v4–v7 chain + magnitude + tier schema + veracity + verdict, action profile. Overlay + anchor deferred. |
| v1.1 | 2026-07-21 | §3.7 promotion overlay: informative → optional **profile**, five invariants + outcome guard, `vectors/savings/overlay/` (1 valid, 4 adversarial). Non-profile verifiers fail closed on `supersedes`. Both bundled implementations pass 33/33. |
| v1.2 | 2026-07-21 | §3.8 **chain-head anchor profile**: the published `{count, tip}` anchor as a second optional profile — `vectors/savings/anchor/` (anchored-valid; trailing truncation, which verifies VALID standalone and only the anchor catches; retroactive re-mint). Anchor vectors carry `input_anchor` as a second verifier input; `verifiers/js/cli.mjs` takes an anchor path. Both implementations pass 36/36. (The truncation hole was independently confirmed by the same design appearing in the author's `nomos-kernel` ledger — sidecar head file, "a prefix of a valid chain is valid" rationale.) |

## Next revision

No revision is currently scheduled. §3.7 and §3.8 closed both items v1.0 deferred; the
remaining candidates below are **design decisions awaiting a real external driver** (an
independent implementer or user asking for them) — per the ground rules, none should be
promoted to spec text just because the machinery is easy.

## Attestation-surface candidates (design decisions — not started)

Each of these changes what an attestation *says*, so each needs deliberate design review
before any code. Sources: the author's `nomos-kernel` (governance kernel — force/warrant
vocabulary, calibration honesty) — adapted, never imported as dependencies.

1. **`veracity_floor` reasons enumeration.** Alongside the scalar floor, an enumerated,
   machine-readable list of *why* the floor is where it is ("N links self-reported; anchor
   unpublished; T1 witness operator-supplied"). Turns an opaque number into an auditable
   justification — the floor's honesty argument, made legible to the stranger it exists for.
   Wire impact: additive optional field in `veracity`; verifier re-derives and compares like
   the floor itself. Risk: S. Blocked on: field vocabulary design.
2. **Optional falsifier field.** `falsifier: {statement, status: untested|survived|failed}` on
   an attestation — the cheapest observation that would disprove the savings claim, and
   whether it has been run. Grades a receipt from *assertion* to *falsifiable-and-tested*.
   Must stay optional (mandatory would break simple receipts). Risk: M (scope-creep hazard —
   adopt only with a concrete first user).
3. **Per-field provenance kinds.** Tag fields `machine_measured | operator_reported |
   derived`, and add the self-evidence rule (a claim may not cite its own artifact hash as
   its evidence). Would let `veracity_floor` be *computed from tags* rather than from tier
   membership alone. Risk: M — partially redundant with the tier ladder; needs a worked
   example showing what tags catch that tiers don't, else reject.
4. **Minimum-n reporting rule.** A normative line (CLAIMS_BOUNDARY + report tooling): derived
   aggregate metrics below n observations are reported as `insufficient_data`, never as a
   number. kry already practices this culturally (the n=52 scope labels); this would make it
   a rule with a chosen n. Blocked on: choosing n per metric honestly rather than decoratively.

## Process disciplines (adopted as norms, not code)

From the author's `Regurgitate` protocol — adopted in [`../CONTRIBUTING.md`](../CONTRIBUTING.md)
as evidence-work norms, deliberately *not* as enforcement machinery (kry publishes its
evidence; a private ledger engine would fight the stdlib, public-by-default posture):

- **Seal before reading:** an evidence doc cites the sha256 of the raw run artifact it
  interprets, recorded before analysis.
- **Literal note before interpretation:** transcribe what the artifact says (counts, ranges)
  with no verdict, as its own section, before any "this shows…" sentence.
- **Claim-mutation log:** when a headline claim changes scope ("saves X" → "saves X under Y"),
  the old→new wording is logged verbatim where the claim lives.
- **Separation invariant** (now stated in CLAIMS_BOUNDARY): the thing evaluated must be
  external to the logic evaluating it — the reason the stranger verifier imports nothing from
  the package and the JS verifier reads only `SPEC.md` + `vectors/`.

## Considered and rejected

- **Runtime LLM review chambers / tribunals** (nomos-kernel): network, nondeterminism, and an
  API dependency — all three break stdlib-offline-deterministic. kry's audits are a human/CI
  discipline, not a runtime component.
- **Action interception / grants / hooks** (nomos-kernel): kry attests *after* the fact; a
  gating kernel is a different product. Out of scope permanently.
- **pydantic / click / any dependency import**: any adopted data shape is hand-rolled stdlib.
- **Regurgitate's numeric constants and ledger engine**: the mechanics transfer as norms; the
  numbers were calibrated for a research loop, and an enforcement engine would be process
  tooling the repo doesn't need.
- **Re-scaffolding under new names** what kry already has: kill gates, claims boundary,
  pre-registered readiness rubric, adversarial audit rounds. Renaming is churn, not signal.
