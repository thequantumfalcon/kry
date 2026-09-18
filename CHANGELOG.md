# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **The LiteLLM callback mints receipts for auto-router savings** —
  `scripts/kry_litellm_callback.py` minted only response-cache hits.
  - A request LiteLLM's auto-router sends to a cheaper model now mints a `short_circuit` receipt, the
    event the savings report mints for a displacement. The avoided model is the router's
    `savings_baseline_model`; the served model is the one that ran; tokens are output tokens.
  - Nothing is minted for the router's internal calls, when the baseline served the request, or
    when the route is not cheaper at kry's published prices.
  - Receipts are `self_reported`: the counterfactual is the router configuration. LiteLLM's
    `autorouter_savings` and `saved_cache_cost` figures are recorded as context, not evidence.
  - The routing decision's `signals` and keyword fields quote the caller's prompt and are never
    copied.
  - Tests: `tests/test_litellm_callback.py` (7 new).

- **The prompt-cache report values OpenAI calls** (`docs/PROMPT_CACHE_PLAN.md` A4) — the report-only
  `prompt_cache` block priced Anthropic models only, so an OpenAI call was counted as unpriced.
  - GPT-5.6 and later are priced from the sealed pricing page at the standard tier's short-context
    rates: cached input at 0.1x and cache writes at 1.25x the base input rate.
  - OpenAI reports cache tokens inside `input_tokens` (`input_tokens_details.cached_tokens` and
    `.cache_write_tokens`, or the `prompt_tokens` pair on Chat Completions), the opposite of
    Anthropic; the ordinary tokens are the remainder. Both request shapes are read.
  - A record stating any other `service_tier` (`flex`, `fast`, `priority`, `ultrafast`, `auto`,
    `batch`) is excluded and counted. Batch result bodies carry no marker, so a batch export must be
    tagged `service_tier: "batch"`; it bills at 50%, so an untagged batch record would be valued at
    twice its true saving.
  - Long-context rates are exactly 2x the short-context ones and the page states no threshold for
    them, so such a call's saving is understated, never overstated. The `gpt-daybreak-*-latest`
    aliases stay unpriced because the page repoints them at new models.
  - Each price source now records its own `as_of`; `PRICE_BASIS_AS_OF` is the newest.
  - Still report-only: nothing here mints, attests, or changes a verifier verdict.
  - The artifact privacy gate accepts OpenAI's usage shape: its documented `service_tier` values and
    the `input_tokens_details` / `prompt_tokens_details` / `output_tokens_details` objects, which carry
    token counts only. Free text under any of those names, or smuggled inside one of the objects, is
    still rejected, and no rejected value is echoed.
  - Tests: `tests/test_prompt_cache.py` (10 new, including the guide's own worked example) and
    `tests/test_artifact_privacy.py` (7 new, fixtures taken from a real response).

- **Batch-billed traffic is excluded from the prompt-cache block** — a Batch API call bills at 50%,
  so valuing one at standard rates reports twice the true saving. The report now recognises a batch
  record by the `custom_id` key both providers put on batch result rows (or an explicit `batch: true`)
  and tags it so the existing price-modifier rule excludes and counts it. A record that looks
  batch-billed but claims another tier is read as batch: the two readings disagree and only one of
  them can overstate. `--batch` marks a whole log, for an export that kept no per-row marker.
  - A raw batch result row nests the call under `response.body`, so it carries no model to price and
    was already skipped; a test pins that it can never reach the priced count.
  - Tests: `tests/test_savings_report_prompt_cache.py` (4 new).

### Documentation

- **Six spec ambiguities pinned to what the verifiers already do** (`docs/SPEC_REVIEW_2026_09_17.md`) —
  each was checked against the code first; no verifier changed and no verdict moves.
  - §3 now states which profile a document belongs to: `kind` of `kry_action_attestation` is verified
    under §4, everything else under §3, dispatching on the document rather than on the `kind` wrapper a
    vector file adds around it.
  - §3.4 states that the pre-v4 rules are **live, not vestigial**: `hash_version <= 3`, or an absent
    field meaning `1`, is legacy and verified with the §3.3 formula. Only a version above 7, or a
    non-integer, is unrecognized — and such a link is INVALID, its declared `chain_hash` becomes the
    next link's `prev`, and it is excluded from every derivation.
  - §3.4 states that duplicate hash-bound `receipt_id`s are INVALID for every verifier, not only one
    claiming the optional overlay profile.
  - §3.8 states that the anchor resolves to the **first** link whose `seq` equals `count`, since `seq`
    is bound by no hash and is not required to be unique.
  - §3.7 defines `position` as the link's index in `links`, not its `seq`.

### Changed

- **Current provider models are valued at their list price** — the output price table in
  `src/kry/kry_token.py` matched model ids by substring against class entries dated 2026-06-03, so
  Sonnet 5 and Sonnet 4.6 minted at the $7.50 Sonnet estimate and Fable 5.1 and Haiku 4.5 at the
  $1.25 floor. That under-credited savings; it never over-credited them.
  - Exact provider ids now come first, at the list prices checked on 2026-09-15: Fable 5 and 5.1
    $50, Sonnet 5 $10, Sonnet 4.6 and 4.5 $15, Haiku 4.5 $5. Opus 5 already matched the $25 entry.
  - Every older entry is unchanged, so receipts minted at the older prices still verify. Every
    multiplier legal in 0.1.5 stays legal, and 0.5 stays illegal.
  - The published set in `vectors/primitives/legal_multipliers.json` grows from 30 to 40 values,
    regenerated from the reference; the standalone verifier's copy and the browser page's inlined list
    match it. No other conformance vector changes.
  - Each price now records its own date; `PRICE_BASIS_AS_OF` is the newest one.
  - Tests: `tests/test_output_price_refresh.py`.

### Fixed

- **A current-version link can no longer skip the magnitude checks by omitting its inputs** — the
  magnitude check exempted any link declaring neither `tokens_saved` nor `earn_rate`, on the grounds
  that a legacy receipt exposes no inputs. The exemption was unbounded, so a modern link could omit
  them, skip both the published-rate and the published-multiplier check, and mint an arbitrary
  `kry_minted` that still verified. From `hash_version` 4 the economic block is bound into
  `chain_hash`, so the exemption now stops there: a v4+ link that omits either input is INVALID.
  Fixed in all three implementations (`scripts/kry_verify.py`, `src/kry/kry_attest.py`,
  `verifiers/js/verify.mjs`).
  - Every link in the corpus already declares both inputs, so no existing vector or verdict changes.
  - `SPEC.md` §3.4.1 now states the version bound. §3.5 now states that ANCHORED tiers are exactly the
    enumerated set, so an unknown tier string counts toward `total_kry` but never toward
    `anchored_kry`: it cannot claim a `veracity_floor` of `1.0`. The reference verifiers already
    behaved that way; only the spec text was permissive.
  - New vectors: `savings/adversarial/magnitude_inputs_omitted` and
    `savings/adversarial/unknown_tier_claims_anchored`, both INVALID. The corpus grows from 46 to 48.
  - Both gaps were found by writing a fresh verifier from `SPEC.md` and the corpus alone, with no
    access to the reference implementation, and recording every point where the text left a choice.
  - That review is recorded in `docs/SPEC_REVIEW_2026_09_17.md`: 25 points where the text leaves a
    choice, of which these two were verdict-affecting and are fixed here, eight remain open, and the
    corpus corrected none of them. It also names three blind spots in the corpus itself.

- **The two verifiers rounded differently, and could reject each other's documents** — SPEC's
  `round(x, n)` is round-half-even on the exact binary value, which is what Python's `round()` does.
  `verifiers/js/verify.mjs` rounded by scaling (`Math.round(x * 1e4) / 1e4`); the multiply injects its
  own error, so roughly **4% of five-decimal magnitudes** rounded to a different value — for example
  `2122.59595` gave `2122.5959` in Python and `2122.596` in JS. That gap is 1e-4, four decades above
  the 1e-9 comparison tolerance, so each verifier would have called some of the other's valid
  attestations INVALID. It affects `total_kry`, `usd_equivalent`, every `by_tier` value, `anchored_kry`
  and `veracity_floor`.
  - JS now expands the exact decimal value and rounds it half-even. Checked against the reference on
    91,997 values, including dyadic values that hit the genuine half-even tie: 0 divergences. The
    project's differential fuzz (20,000 cases) also reports 0.
  - Neither the corpus nor the fuzzer had ever produced such a value, so nothing caught this.
    `tests/test_js_rounding_parity.py` pins it: 350 of its 2,097 cases fail against the previous code.
  - `SPEC.md` now states the rounding rule and warns against rounding by scaling.

- **The JS corpus runner gives the same verdicts on a CRLF checkout** — `verifiers/js/cli.mjs`
  re-extracted each vector's raw input with a regex that required `,\n` before `"expected"`. On CRLF
  files it fell back to re-serializing the input, which respells numbers such as `1.0`, so four
  valid vectors came back INVALID. The regex now accepts `\r\n`; the browser page's demo loader
  shares the same extraction and the same fix. No vector or verdict changes.
  - Tests: `tests/test_js_vectors_crlf.py` (fail on the previous runner, pass now).

## [0.1.5] - 2026-09-15

### Added

- **The savings report shows the provider prompt-cache discount** — `scripts/kry_savings_report.py`
  never read provider cache fields, so prompt caching, often the largest saving in agentic workloads,
  reported as zero. The report now carries a separate `prompt_cache` block (JSON) and text section.
  - It values Anthropic usage (`cache_read_input_tokens`, `cache_creation_input_tokens` and its
    5-minute and 1-hour split) at list prices from a dated table in the new
    `src/kry/kry_prompt_cache.py`. Every price source is recorded with the SHA-256 of the page it was
    copied from.
  - It is labeled "not minted, not attested, self-reported" and is never added to `saved_kry`,
    `efficiency_ratio` or veracity. The block leaves every minted figure unchanged, and a test checks
    that.
  - These are excluded and counted:
    - model ids not in the table
    - batch or priority tier, fast mode, and US-only inference
    - input above 200K tokens on the 4.5 models, for which the pricing page states no long-context
      rate. A record that names its `context_window`, as an organization usage report row does, is
      judged by that field instead.
  - Writes without a TTL split are priced at the 1-hour rate, so the saving is never overstated. A
    cache that is written but never read shows as a negative saving.
  - It reproduces the provider's worked example: 40,000 Opus 5 cache-read tokens cost $0.02 instead of
    $0.20.
  - Plan and candidate spec: `docs/PROMPT_CACHE_PLAN.md`, `docs/SPEC_V1_4_PROMPT_CACHE.md`. Tests:
    `tests/test_prompt_cache.py`, `tests/test_savings_report_prompt_cache.py`. 41 tests.

### Changed

- **The report's SPEND uses real prices for provider model ids** — `spend_cost` charges any model
  without a `SPEND_RATES` prefix at the Opus rate. That overstated Sonnet 5 by 2.5x and understated the
  Fable models by half. The report now uses `SPEND_RATES` for gateway ids and the dated list output
  price for exact provider model ids. Any other model is counted in `unpriced_spend_calls` and left out
  of SPEND. `spend_cost` itself is unchanged, so ledger routing charges still treat unknown models at
  the frontier rate, and the report's paid/free classification still follows it.

### Fixed

- **Anthropic prompt counts include cache reads and writes** — `scripts/kry_reconcile.py` and the
  savings report's `normalize` read Anthropic `input_tokens` as the whole prompt, but it excludes cache
  reads and writes. A `provider_metered` receipt on a cached request therefore carried and was checked
  against the uncached input only. Both now use
  `input_tokens + cache_read_input_tokens + cache_creation_input_tokens`. Receipts minted with the old
  count still reconcile and are reported in `matched_legacy_uncached_prompt`. OpenAI-style prompt counts
  already include cached tokens and are unchanged. Aggregate mode cannot tell old receipts from new
  ones, so a window holding receipts minted with the old count is checked more loosely; the reconciler's
  docstring says to reconcile such windows per request. `tests/test_reconcile_anthropic_cache.py`, plus an
  end-to-end mint-and-reconcile test in `tests/test_savings_report_prompt_cache.py`. 6 tests.
- **Bundles accept provider usage as the provider reports it** — the bundle privacy gate allows only
  documented usage-log and provider-export fields.
  - It rejected `cache_creation`, `service_tier`, `speed` and `inference_geo`, which provider usage
    objects carry, so a real usage log could not be bundled.
  - Those fields are now accepted, along with `context_window` from organization usage reports.
  - `cache_creation` may hold only token counts. The four text fields accept only their documented
    values or null, so they cannot carry other text.
  - Tests: `tests/test_artifact_privacy.py`.

### Documentation

- `docs/PROMPT_CACHE_PLAN.md` now says Stage A shipped in 0.1.5 and Stage B is not implemented; it
  still said nothing was implemented.
- The README's real-world validation limit names the one real reconciliation (n=52 free-tier calls,
  token counts only) instead of saying every result is on synthetic or internal data.

## [0.1.4] - 2026-09-14

### Added

- **`pip install kry-attest` installs two commands** — `kry-verify` runs the stdlib stranger verifier
  (`scripts/kry_verify.py`). It ships in the wheel as its own top-level module, `kry_verify`, so it
  still imports nothing from `kry`, and a new test checks that. `kry-try` mints a throwaway receipt chain
  from two synthetic, self-reported events, attests it, and verifies it in a separate process. Until
  now the wheel carried only the `kry` library, so an installed user had nothing to run without a
  checkout. An editable install loads `kry_verify` from the checkout. `tests/test_build_backend.py`
  installs both the wheel and an editable checkout into fresh virtual environments and runs both
  commands, including a check that an edited number verifies INVALID. `tests/test_try_demo.py` covers
  the checkout path and the demo's refusal to run once the ledger modules are already imported. 8 tests.
- **PyPI publishing** — the Release workflow now builds an sdist alongside the wheel, and a new `pypi`
  job runs after the GitHub Release. It uploads only a wheel and sdist whose SHA-256 values match the
  release's `checksums.txt`, through PyPI trusted publishing (no API token), from a `pypi` environment
  that requires approval. The GitHub Release also carries the sdist.

- **The JS CLI verifies a single attestation against the published multipliers** —
  `node verifiers/js/cli.mjs <attestation.json>`, the command the README gives outsiders, never
  loaded `vectors/primitives/legal_multipliers.json`, so its magnitude check fell back to `{1.0}`. An
  attestation minted from `examples/sample_usage_log.jsonl` verified VALID with
  `scripts/kry_verify.py` and INVALID with the JS CLI. Single-file mode now loads the set from the
  checkout, as `--vectors` does, and warns on stderr when the file is absent.
  `tests/test_js_cli.py` checks both cases and skips where node is not installed. 2 tests.

### Documentation

- `docs/VERIFY_FIRST_RECEIPT.md` walks a new user through installing, making a demo receipt, verifying
  it, editing a number to see it fail, and checking it in the browser. The README Quickstart links it.
- `docs/AUDIT_2026_06_23.md` no longer names the branch the audit ran on.
- The browser verify page now says its verifier passes the 46-vector KRY-SPEC v1.3 corpus; it still
  said 36 vectors and v1.2.
- `kry_pqc/README.md` no longer points to `kry_pqc/PLAN.md`, which was never in the repository; the
  three designed-but-unbuilt tiers it listed are kept.

## [0.1.3] - 2026-09-14

### Added (distribution surface)

- **LiteLLM integration** (`scripts/kry_litellm_callback.py` + `docs/KRY_LITELLM.md`) — a
  CustomLogger that mints a kry `cache_hit` receipt per LiteLLM response-cache hit
  (`tokens_saved` = the cached response's total tokens; the LiteLLM call id as the
  evidence key, minted once per process). Honest boundary stated everywhere: receipts are
  T0 `self_reported` (the floor stays 0.0 — the gateway is the operator), `cache_hit` is
  LiteLLM's response cache not provider prompt-caching, `response_cost` is recorded as a
  price-table estimate only. Dict-based and stdlib-only (litellm imported lazily); fails
  closed on malformed events and never raises into the serving path. 7 tests.
- **Browser verifier → tamper playground** (`verifiers/web/index.html`) — the verify page
  now shows the failing reasons (new `explain()` export in `verify.mjs`), renders the
  declared `veracity_floor` as a labeled meter, and adds two anchor demos that load real
  conformance vectors live: a truncated chain and a genesis re-mint, each VALID standalone
  and caught only by the published anchor (SPEC §3.8), with the story narrated in-page.
  Stale v1.0 reference updated to the 36-vector v1.2 corpus. README links the playground
  from the demo caption; docs table gains the LiteLLM cookbook row.
- **GitHub Pages deployment of the verify surface** (`.github/workflows/pages.yml`) — the
  browser verifier/playground, the JS verifier it imports, the live conformance corpus the
  demos fetch, and SPEC.md, staged into `_site` and published on every push to main
  (digest-pinned actions, harden-runner, minimal permissions). The site is the verify
  surface only, not a repo mirror.

### Changed (SPEC v1.3 — the veracity rules made derivable)

- **All three verifiers now fail closed on absent or loosely matched veracity fields** —
  `scripts/kry_verify.py`, `src/kry/kry_attest.py` and `verifiers/js/verify.mjs` follow SPEC v1.3
  §3.5: a `veracity` key that is absent or not a JSON object is INVALID; each of `by_tier`,
  `anchored_kry`, `self_reported_kry` and `veracity_floor` must be present (an absent one used to
  skip its check); `by_tier` is compared as a map with equal key sets; and every declared-vs-derived
  numeric comparison uses one absolute tolerance of `1e-9`. Both Python verifiers previously
  accepted a `total_kry`, `anchored_kry`, `self_reported_kry` or `veracity_floor` up to `0.01` from
  the derived value; the JS verifier used `1e-9` and `1e-4` for those fields. Missing required
  envelope keys (§3.1) are now reported by name, and an unrecognized `hash_version` fails closed.
  **Compatibility:** an attestation that omits a required field, or whose declared totals differ
  from the derived values by more than `1e-9`, now verifies INVALID where it could previously pass.
  No existing vector changes verdict: the current JS verifier passes all 36 cases of the v0.1.2
  corpus. The corpus grows to 46 cases (10 new `savings/adversarial` vectors).

### Changed (tooling)

- **The lint rule set is pinned** — `pyproject.toml` sets ruff's `select` to
  `["E4", "E7", "E9", "F"]`, ruff's pre-0.16 default, so a ruff upgrade (0.16 widened the default)
  no longer changes what the lint gate checks. `scripts/kry_release_verify.py` reads the dev tool
  pins from `pyproject.toml` instead of keeping its own copy.
- **The differential fuzz reaches absent-field cases** — `verifiers/diff_fuzz.py` gains envelope
  field-deletion and reseal mutation classes, and CI runs it with a fresh seed per run
  (`KRY_FUZZ_SEED`). The runs are recorded in `docs/evidence/spec_v1_sc2_run.md`.

### Fixed (review hardening)

- **Minting no longer credits an already-credited event again** — `_find_t1_receipt_for_gen` and
  `_find_measurement_receipt_for_tee` (`src/kry/kry_mint.py`) raised on a legitimate zero-value row
  (a free-tier `avoided_model`, every `tier_promotion`), which aborted the scan; the caller read
  that as "no prior receipt" and minted fresh full value. Zero-value rows are now skipped one row at
  a time. `verify_chain` now also rejects two v6+ receipts sharing a hash-bound `receipt_id`, which
  both public verifiers already rejected. Tests: `tests/test_mint_receipt_lookup.py`.
- **Importing a kry module no longer writes to disk** — `_kry_data_dir()` created `kry_data/` at
  import time, so a bare import wrote into the caller's working directory and raised
  `PermissionError` under a read-only one. Writers now create the directory when they first need
  it. Tests: `tests/test_import_hygiene.py`.
- **The wheel and source tarball are publishable** — `build_backend.py` writes the metadata a
  published package needs, ships the license text and `src/kry/py.typed`, and builds a source
  tarball that rebuilds the wheel on its own. Tests: `tests/test_build_backend.py`.
- **Operator artifacts are ignored** — `.gitignore` now covers the provider exports, usage logs,
  prompts, gateway rows and generated packets the docs walk a user through creating at the repo
  root, plus `vectors/.mintwork/`.

### Security (release job split)

- **The release gate no longer runs next to the signing credential** —
  `.github/workflows/release.yml` is split into a read-only `gate` job and a `publish` job that
  `needs: gate`. Only `publish` holds `id-token: write` and runs in the `release` environment, and
  its only package install is the hash-pinned build frontend. Both jobs have timeouts.

### Documentation (specs and contributor guidance)

- **Acceptance-gate specs** — `docs/KRY_ADEQUACY_GATE_SPEC.md` and
  `docs/KRY_CORRECTNESS_LAYER_SPEC.md` describe the measurement behind
  `scripts/kry_gate_specificity.py` and `scripts/kry_correctness_layer.py`, with the labeled seed at
  `docs/evidence/adequacy_gate/labeled_seed.jsonl`. `docs/CLAIMS_BOUNDARY.md` records
  correctness-anchored accepted savings as blocked, and `tests/test_public_claims.py` guards tracked
  files against private host identifiers.
- **SPEC.md states its own version** — the header said v1.0 while Annex C documented later
  revisions; it now gives the current version and the first-published date. The "A+" alias is
  retired from the live docs in favour of `production_ready`; dated records keep their wording.
  Annex C and `docs/SPEC_DEVELOPMENT.md` now say v1.3 added ten vectors, not five, and Annex C
  names the rule each one pins (#64).
- **Contributor guidance** — `AGENTS.md`, a contributions policy in `CONTRIBUTING.md`, and
  `.github/release.yml`, which sorts GitHub's generated release notes by PR label.

### Fixed (Windows portability)

- **CLIs no longer crash on a non-UTF-8 console** — on Windows with output piped or
  redirected (cp1252), `print` raised `UnicodeEncodeError` on the report glyphs (`p̂`, `—`,
  the demo's `━` rule lines), so `scripts/kry_verify.py` died before printing a VERDICT and
  `scripts/kry_savings_report.py` and `examples/try_kry.py` failed the same way. Every entry
  point that prints non-ASCII text (56 files) now sets `errors="replace"` on stdout/stderr at
  the top of its `__main__` block: glyphs the console cannot represent print as `?`, UTF-8
  consoles are unchanged, and file writes are untouched (the guard only changes how the two
  console streams encode). `tests/test_console_encoding.py` reproduces the crash on any OS via
  `PYTHONIOENCODING=cp1252` — report → mint → attest → verify, plus the demo — and statically
  requires the guard on every printing entry point; the `test-windows` CI job also runs the whole
  suite on Windows. 3 tests. (Follow-up: the demo test's full-exit assertion now applies only on a
  cp1252 locale. The demo decodes a child's output in the locale encoding, so on a UTF-8 locale the
  forced cp1252 child fails by construction of the test; the first Linux CI run caught this.)
- **Settlement lease lock no longer crashes on Windows under contention** — `_lease_lock`
  (`src/kry/kry_settlement.py`) retried only `FileExistsError`, but on Windows the `O_EXCL`
  create can instead raise `PermissionError` under a multi-process race (consistent with another
  process deleting the lockfile), which killed a settling process:
  `test_acquire_lease_cross_process_atomic` failed 1 in 10 runs on main (3 failures in 13 runs
  overall). On Windows only, `PermissionError` is now retried as contention for up to 2 s of
  consecutive refusals and then re-raised, so an unwritable authority dir still fails; POSIX
  behaviour is unchanged. After the fix the stress test passed 50/50. 3 tests.
- **Absolute-path checks no longer depend on the host OS** — `Path('/etc/hostname').is_absolute()`
  is `False` on Windows, so POSIX-absolute `command_inputs` were classified by where the tool ran:
  `scripts/kry_verified_artifact.py`'s bundle-containment and external-candidate portability checks
  reported the input as escaping instead of absolute, and `scripts/kry_doctor.py` called such an
  artifact packet-shaped on Windows only and named the input as escaping the packet. Each check now
  also treats `PurePosixPath(value).is_absolute()` as absolute; POSIX behaviour is unchanged. Every
  case reproduced 3/3 on Windows before the fix. The containment check rejected the input on both
  OSes throughout, so this is verdict consistency, not a containment hole. Whether the doctor's
  privacy scan should open out-of-packet inputs at all was handled separately; see the doctor input
  containment entry below. 3 new tests; the existing out-of-bundle test now also passes on Windows.
  The same rule now also covers the expected-files set and public-packet privacy scan in
  `_packet_surface_errors` and the doctor's expected-files set (#60): with a packet at a Windows
  volume root, a POSIX-rooted input such as `/etc/x.json` resolved inside the packet and the
  privacy scan read it (reproduced 3/3 on a mounted virtual disk). No pytest reproduces a volume
  root, so this was validated by re-running that probe against the fix.
- **The action verifier no longer reports a valid attestation as tampered on Windows** —
  `scripts/kry_action_verify.py` opened the attestation and anchor JSON without an encoding, so on
  Windows they were decoded as cp1252. A valid attestation saved as raw UTF-8 with a non-ASCII tool
  or agent name then failed with `receipt_hash mismatch — a field was tampered` (3/3 on main; an
  ASCII-escaped copy of the same attestation, and the raw file read in UTF-8 mode, were VALID). Both
  reads now use `encoding="utf-8"`. Attestations kry itself writes are ASCII-escaped and were never
  affected. 2 tests: a static check that every text-mode `open()` in the verifier names an
  encoding, and an end-to-end CLI check (which only discriminates on a non-UTF-8 locale).
- **The lab lease prototype no longer crashes on Windows, and its race test now sees a crash** —
  `lab/hole_d_double_spend.py`'s `_lock` had the same Windows `PermissionError` race as the
  settlement lease lock, and `tests/test_lab_hole_d.py::test_lease_is_atomic_under_race` counted a
  crashed racer thread as a denial, so it kept passing. With the test strengthened to fail when any
  racer raises, main failed 12 of 60 runs on Windows, every failure a `PermissionError`; the old
  test passed through such runs (pytest only warned). `_lock` now uses the same bounded Windows-only
  retry; after the fix the strengthened test failed 0 of 50 runs. The prototype's result that
  exactly one lease wins the race is unchanged. 1 new test; 1 test strengthened.

### Fixed (doctor input containment)

- **`scripts/kry_doctor.py` no longer opens packet inputs outside the packet unless told to** — the
  packet privacy scan ignored `--trust-local-inputs`: for a `command_inputs` path that was absolute
  or escaped the packet with `../`, it opened and parsed that file whether or not the operator had
  vouched for local inputs (reproduced 3/3 on Linux through `run_checks`). Without the flag such
  inputs are now not read, matching the verifier's containment rule, and `packet_privacy_boundary`
  reports them by name as a WARN instead of passing a scan it did not perform; the packet
  portability check already FAILs the same inputs, so the failure count is unchanged. With the flag
  the scan reads them as before. `test_doctor_fails_external_candidate_without_portable_packet`
  previously asserted that scan PASSed (it had read the out-of-packet files); it now asserts the
  WARN. When `usage_log` itself is refused, the inputs that are inside the packet are still
  scanned, so a private field in an in-packet attestation still FAILs, and that FAIL also names the
  inputs that were not read. 2 new tests.

## [0.1.2] - 2026-07-21

### Added (SPEC v1.2 — the chain-head anchor profile)

- **SPEC §3.8: published-anchor verification becomes the second optional profile.** A
  verifier claiming it takes the published `kry_chain_anchor/v1` `{count, tip}` as a second
  input and rejects a chain shorter than the anchor (trailing truncation / rollback) or one
  whose hash at `seq == count` mismatches (retroactive re-mint). New vector category
  `vectors/savings/anchor/` (anchored-valid; truncation — which verifies VALID standalone,
  pinning that chain-walking alone cannot see a dropped tail; re-mint), each carrying
  `input_anchor` alongside `input`, expected verdicts generated from the reference.
  `verifiers/js` implements the profile (`verdictWithAnchor` / `anchorErrors`;
  `cli.mjs <attestation.json> [anchor.json]`); corpus 36/36, both implementations agree.
  This closes the last item v1.0's §3.7 had deferred; `docs/SPEC_DEVELOPMENT.md` moves v1.2
  to shipped with no further revision scheduled.

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

[Unreleased]: https://github.com/thequantumfalcon/kry/compare/v0.1.5...HEAD
[0.1.5]: https://github.com/thequantumfalcon/kry/compare/v0.1.4...v0.1.5
[0.1.4]: https://github.com/thequantumfalcon/kry/compare/v0.1.3...v0.1.4
[0.1.3]: https://github.com/thequantumfalcon/kry/compare/v0.1.2...v0.1.3
[0.1.2]: https://github.com/thequantumfalcon/kry/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/thequantumfalcon/kry/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/thequantumfalcon/kry/releases/tag/v0.1.0
