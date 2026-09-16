# Verify your first kry receipt

About five minutes. You need Python 3.11 or newer; kry has no other dependencies. No repository
checkout, account, API key or network access is needed after installing.

## 1. Install

```bash
python3 -m pip install kry-attest
```

This installs the `kry` library and two commands, `kry-verify` and `kry-try`. kry-attest 0.1.4 is the
first release published on PyPI and the first that includes the commands. In a checkout of this
repository, `python3 -m pip install .` installs the same commands.

## 2. Make a demo receipt

```bash
kry-try
```

`kry-try` records two made-up efficiency events in a temporary ledger, hash-chains them, writes the
public attestation to `kry-demo-attestation.json` in the current directory, and verifies it. The
temporary ledger is deleted when it finishes. Output (paths shortened):

```text
kry-try: SYNTHETIC demo data. These events are made up; this is not a savings claim.
temporary ledger: /tmp/kry_try_xxxxxxxx (deleted on exit)

1. Mint two self-reported efficiency events into a hash-chained ledger
2. Build the public attestation (hashes and totals only, no prompts or responses)
   wrote 2 receipts, 1,480.00 KRY -> /path/to/kry-demo-attestation.json
3. Verify it the way a stranger would: a separate program that imports nothing from kry

KRY external verification — attestation
  receipts:        2 (recomputed from links)
  total_kry:       1480.0 (recomputed from links, not the declared field)
  veracity_floor:  0.0 (fraction anchored by more than self-report — external OR operator-run; witnesses the event, NOT the magnitude)
  price basis:     $25.0/M frontier, as of 2026-09-15 (magnitude recomputed from the public price table)
  VERDICT: VALID — integrity + conservation + magnitude (where checkable) hold; trust surface honest (read veracity_floor for what is operator-asserted).
```

The attestation is the only file kept. It holds hashes and totals, not prompts, responses or keys.

## 3. Verify it yourself

```bash
kry-verify kry-demo-attestation.json
```

`kry-verify` is the stranger's check. It uses only the Python standard library and imports nothing
from the `kry` package, so it does not rely on the code that produced the receipt. It prints the same
report and exits with status 0 for VALID.

## 4. Change a number and watch it fail

Open `kry-demo-attestation.json`, find the first `"kry_minted"` value (`1000.0`), change it to `1001.0`,
save, and run `kry-verify` again:

```text
KRY external verification — attestation
  receipts:        2 (recomputed from links)
  total_kry:       1481.0 (recomputed from links, not the declared field)
  veracity_floor:  0.0 (fraction anchored by more than self-report — external OR operator-run; witnesses the event, NOT the magnitude)
  price basis:     $25.0/M frontier, as of 2026-09-15 (magnitude recomputed from the public price table)
  VERDICT: INVALID
    - seq 1: chain link broken — receipt inserted/removed/altered
    - total_kry mismatch: declared 1480.0, chain sums to 1481.0
    - usd_equivalent mismatch: declared 0.037, links imply 0.037025
    - attestation_hash mismatch — public metadata may have been altered
    - veracity by_tier mismatch: declared {'self_reported': 1480.0}, links imply {'self_reported': 1481.0}
    - self_reported_kry mismatch
```

The exit status is 1. A single edited number breaks the hash chain, the totals and the attestation's
own hash at once.

## 5. Check it in a browser

Paste the original file's contents into the [browser verifier](https://thequantumfalcon.github.io/kry/).
It runs a second, independent implementation (JavaScript) entirely in the page; nothing is uploaded.

## What this proves, and what it does not

- **Proven:** the receipts form an unbroken hash chain, the declared totals equal the sum of the
  receipts, each receipt's magnitude is consistent with the public price table, and the declared trust
  surface matches the receipts.
- **Not proven:** that the events happened. Both demo events are self-reported, so `veracity_floor` is
  0.0: nothing anchors them beyond the operator's word. See [`CLAIMS_BOUNDARY.md`](CLAIMS_BOUNDARY.md)
  for what kry can and cannot claim.

## Next

To produce receipts from your own gateway logs, clone the repository and follow the
[README Quickstart](../README.md#quickstart), starting with `scripts/kry_savings_report.py`.

If your logs carry provider prompt-cache usage — Anthropic's `cache_read_input_tokens` and
`cache_creation_input_tokens`, or OpenAI's `input_tokens_details` (`prompt_tokens_details` on Chat
Completions) — the report also shows a separate prompt-cache block: the discount the
provider gave, at dated list prices. That figure is reported only. It is not minted, not attested, and
never added to the savings a receipt carries.
