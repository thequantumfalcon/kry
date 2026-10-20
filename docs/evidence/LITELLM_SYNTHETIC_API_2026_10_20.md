# LiteLLM callback evaluation with synthetic API requests

This is a maintainer-reported transcription of a privately retained run on
2026-10-20. It used real Anthropic responses to invented tasks, with
LiteLLM 1.101.0, httpx 0.28.1 and model `claude-haiku-4-5-20251001`.
The evaluated KRY commit was `d0dbb8644b8b1ae3b1e9a9f34d010d9b617b179c`.

## Evidence provenance

The protocol and executable inputs were frozen before dispatch. The completed
artifact manifest was created **after the original private analysis**. Its seal
is retrospective; it is not an independent timestamp or a prospective results
commitment. The following digests were checked before preparing this public
transcription:

| Private artifact | SHA-256 |
|---|---|
| Protocol | `6caf4077ef02070d1eb474316f8f95ac6e413182c6e55999085ad97ffebed9af` |
| Frozen input manifest | `c14aaac4ef9a51ff2605be7653ba870ca38c6a7855e0b19855d19ac20d9550fa` |
| Baseline raw transport records | `e12994f5085480b554d78a35cd99c5587fc4614266d0bf5eecdf8cb7f8c355c0` |
| KRY raw transport records | `d0fe1c4ef099584ff2ab4afbd10d6802761454cd015fab33c5bee57582f3cbe4` |
| Completed run summary | `933401dd9f061d87b5ab99096885feeed1ce1bab9c84e794530e9409417adbdc` |
| Completed artifact manifest | `3e33a4baf6a7cd652a211376f43ea6047aad17ac97deec9cd0eba1ddb2811b2d` |

The raw responses, provider identifiers, private harness and machine records are
not included here. Digests identify the retained bytes; publishing them alone
does not let a reader independently recompute the live results.

## Literal note

The retained run summary records the window
`2026-10-20T08:11:18.542763+00:00` to
`2026-10-20T08:16:53.338820+00:00`. The baseline completed before the KRY arm.
Each arm had 60 distinct tasks and 40 exact repeated requests, using a fresh
process and local cache. Both arms retained inventory, callback and caller
records; treatment added the KRY callback.

| Observed quantity | Baseline | KRY |
|---|---:|---:|
| Logical requests / callbacks / caller returns | 100 / 100 / 100 | 100 / 100 / 100 |
| HTTP 200 provider responses | 60 | 60 |
| Local cache hits | 40 | 40 |
| KRY receipts | 0 | 40 |
| Input / output tokens | 2,800 / 320 | 2,800 / 320 |
| Exact answers, distinct tasks | 60 / 60 | 60 / 60 |
| Meeting answers in integer format | 10 / 10 | 10 / 10 |
| Truncated responses | 0 | 0 |
| Audit discrepancies | 0 | 0 |
| Response-usage list-price estimate | $0.004400 | $0.004400 |
| Independently observed billed charge | unavailable | unavailable |

All 120 reservations resolved; all responses ended with `end_turn`. No retries
or replacement samples occurred. The retained request IDs and message IDs each
had 120 distinct values. Ordered content and usage had no differences between
arms. The 40 treatment receipts recorded 2,154 tokens, 430.8 KRY and
`veracity_floor: 0.0`. Both unchanged anchored verifiers returned `VALID`.

The estimate uses `(input_tokens × $1 + output_tokens × $5) / 1,000,000`,
the [published standard model rates](https://platform.claude.com/docs/en/about-claude/pricing)
checked on 2026-10-20. The combined estimate is $0.008800; actual charges remain
null. Raw `inference_geo` values were `not_available`, retained literally.

An earlier run scored **50/60 in each arm**, including 0/10 exact meeting
answers: the durations had extra units, Markdown or explanation. Those failures
remain recorded. Only the ten meeting prompt suffixes changed for this run.
The instruction changed verbatim from:

> Return its duration in whole minutes only.

to:

> Return only the integer number of minutes, using digits 0-9. No units, words, Markdown, punctuation, or explanation.

Task facts, expected answers, identifiers, request order and repeats stayed the
same. Both runs used exact equality after stripping outer whitespace only;
there was no numeric extraction or answer repair.

## Interpretation and limits

The retained records support this finite integration observation. The revised
prompt instructions met the separately declared exact-answer target on these
same tasks. This was a revision informed by the earlier failures, not a held-out
quality benchmark. A single sequential pair does not isolate a causal KRY effect.
Both arms made the same number of provider calls and had equal estimated cost.

The receipts are T0 operator assertions. `VALID` checks the supplied chain and
applicable conservation/benchmark arithmetic; it does not independently witness
cache hits or establish paid savings. The deliberately constructed repeat rate
does not measure a customer workload. No independent billing report, customer
benefit, production readiness or superiority over ordinary logs is established.
The repository's [claims boundary](../CLAIMS_BOUNDARY.md) remains unchanged.

The live run exercised normal single-process delivery. Separate regression tests
cover failed appends, uncertain acknowledgements, restart, guard eviction,
concurrent writers and mixed strict/generic replay. They require updated writers
sharing an intact retained log with effective filesystem locks. Atomic recovery
across the receipt log, checkpoint and balance ledger, missed callbacks,
mixed-version writers and large-history throughput remain separate requirements.

## Publicly repeatable offline checks

The repository includes constructed fixtures for the actual LiteLLM adapter,
cache and callback dispatch, plus mint failure/concurrency regressions. Install
the dependencies first; this requires package access or a local wheelhouse:

```bash
python -m pip install -e ".[dev]" "litellm==1.101.0"
```

Then run the constructed tests with the bundled model-cost map. These checks
use no API credentials or paid calls:

```bash
LITELLM_LOCAL_MODEL_COST_MAP=True python -m pytest -q -rs tests/test_litellm_callback.py tests/test_litellm_durable_mint.py tests/test_anthropic_usage.py tests/test_litellm_integration.py tests/test_litellm_anthropic_integration.py
```

For the evaluated source commit, the retained local full-suite result was
1,790 passed / 3 skipped; JavaScript corpus 399 passed / 0 failed. All 12 default
release checks passed; the isolated suite and one-round reproducibility check
each reported 1,786 passed / 5 skipped. Ruff, vector regeneration, attribution
checks and the zero-paced demo passed. Doctor reported 9 pass / 3 warnings /
0 fail; optional cryptography dependencies and missing external evidence
remained disclosed. Test counts depend on installed optional dependencies.

These offline fixtures reproduce the software checks. They do not reproduce
the private live run or upgrade its evidence tier.
