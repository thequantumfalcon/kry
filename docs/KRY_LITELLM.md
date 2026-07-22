# kry × LiteLLM — attest your proxy's cache savings

Turns every LiteLLM response-cache hit into a kry `cache_hit` receipt, so the proxy's
claimed savings become a tamper-evident, stranger-verifiable ledger instead of a
dashboard number. One file, no new dependencies: `scripts/kry_litellm_callback.py`
imports only the kry stdlib core; LiteLLM is imported lazily (the extractor is fully
testable without it).

## The honest evidence boundary — read first

- Every receipt minted here is tier **`self_reported` (T0)**: the cache-hit signal comes
  from the operator's own gateway, so the attestation's `veracity_floor` correctly stays
  `0.0` until stronger tiers back it. This integration proves your ledger of claimed hits
  is **intact and honestly priced**; it cannot prove the hits happened. That is kry's
  integrity-vs-veracity line working as designed — publish a chain anchor
  (`scripts/kry_chain_anchor.py`) to also make retroactive edits/truncation detectable.
- `cache_hit` is **LiteLLM's response cache**, not provider-side prompt caching
  (`cached_tokens`) — prompt-cache savings need a separate signal.
- `response_cost` is LiteLLM's **price-table estimate**; it is recorded in the receipt
  detail as context, never used as evidence.

## Setup

LiteLLM proxy (`litellm_config.yaml`), with the script on `PYTHONPATH`:

```yaml
litellm_settings:
  callbacks: kry_litellm_callback.kry_logger
```

Or in Python:

```python
import litellm
from kry_litellm_callback import KryLiteLLMLogger
litellm.callbacks = [KryLiteLLMLogger()]
```

Each response-cache hit mints one receipt (`tokens_saved` = the cached response's total
tokens — the size of the avoided call; `avoided_model` = the requested model; the LiteLLM
call id is the evidence key, minted at most once per process). Anything malformed or
missing fails closed: no receipt, never a guessed one, and the logging path never raises
into your serving traffic.

## Attest and hand it to a stranger

```bash
python3 - <<'EOF'
from kry import kry_attest
print(kry_attest.build_attestation().to_public_json())
EOF
# → attestation.json; then anyone, offline, with no trust in you:
python3 scripts/kry_verify.py attestation.json
node verifiers/js/cli.mjs attestation.json          # independent implementation
```

The verifier re-derives the chain, the price arithmetic, and the `veracity_floor` — and
the floor will honestly read `0.0` for a pure cache-hit ledger. That is the feature, not
a bug: the number states exactly how much still rests on your word, instead of hiding it
behind a green checkmark. See `docs/CLAIMS_BOUNDARY.md` for what you may claim on top.
