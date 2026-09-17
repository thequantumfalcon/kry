"""Report-only valuation of provider prompt caching (Stage A of docs/PROMPT_CACHE_PLAN.md).

Values the input-side discount a provider gave on cached prompt tokens, from the provider's own usage
fields at dated list prices. This module never mints, never writes the chain and never feeds the
published multiplier set, so nothing here can change a receipt or a verifier verdict. The figure is
self-reported by construction.

Anthropic reports cache reads and writes outside `input_tokens`: total input is
`input_tokens + cache_read_input_tokens + cache_creation_input_tokens`. A record is valued only when
its model id is in the table below and nothing in its usage changes the price (batch or priority
tier, fast mode, US-only inference, or input above 200K tokens on a model with no stated long-context
rate); every other record is counted, never guessed.

OpenAI (GPT-5.6 and later) reports them INSIDE `input_tokens`, as
`input_tokens_details.cached_tokens` and `.cache_write_tokens` (`prompt_tokens` and
`prompt_tokens_details` on Chat Completions), so the ordinary tokens are the remainder. Its records
are valued at the standard tier's short-context prices; any other stated `service_tier` is excluded.
"""
from __future__ import annotations

from decimal import Decimal

PRICE_BASIS_AS_OF = "2026-09-16"   # the newest `as_of` below

# Provider pages the prices and rules below were copied from, each saved and hashed on its `as_of`.
PRICE_SOURCES = (
    {"url": "https://platform.claude.com/docs/en/about-claude/pricing", "as_of": "2026-09-15",
     "sha256": "880bdf86b7b7a3dc963c8e356ea88d2b87c1e8e57640a46d7e61d9854e5634c9"},
    {"url": "https://platform.claude.com/docs/en/docs/build-with-claude/prompt-caching", "as_of": "2026-09-15",
     "sha256": "4a05b04857a90dae03f7133c87913e0751202ce7bf00544e333b1c4af18e4c88"},
    {"url": "https://platform.claude.com/docs/en/api/service-tiers", "as_of": "2026-09-15",
     "sha256": "90c3b90ed23f0d722d77a0d25f6b6c6ef3edf5ec613aec037186c7152e9b4120"},
    {"url": "https://platform.claude.com/docs/en/build-with-claude/fast-mode", "as_of": "2026-09-15",
     "sha256": "e2cfb7c1c457383747e798ada2284b199865f1693fcedb7f678cb856aad5f4de"},
    {"url": "https://platform.claude.com/docs/en/manage-claude/data-residency", "as_of": "2026-09-15",
     "sha256": "a0643e6606db0e95444ee5ea83e5434ff36649d7ed87aa7a122c86ebc53c9487"},
    {"url": "https://platform.claude.com/docs/en/about-claude/models/overview", "as_of": "2026-09-15",
     "sha256": "2ec2d36b82cb5bf264c4da7822c328702ef4946c6d55a758dadd7d537f3bb4cf"},
    {"url": "https://platform.openai.com/docs/pricing", "as_of": "2026-09-16",
     "sha256": "4be434db67b922e1937e27cab5eea30cf6f6d52ead603684c535704908859a6c"},
    {"url": "https://platform.openai.com/docs/guides/prompt-caching", "as_of": "2026-09-16",
     "sha256": "81f040fbcf1c30c05fe36c72052e3a6bafba5ceefe4ab93ea06dafee74fd5731"},
)

LABEL = ("provider prompt-cache discount, computed from reported usage at list price; "
         "not minted, not attested, self-reported")

# $ per million tokens from the pricing page's model table (standard tier, global inference):
# (base input, 5-minute cache write, 1-hour cache write, cache read, output). Keyed by exact API
# model id; ids not confirmed on the models overview page are deliberately absent.
_ANTHROPIC_PRICES: dict[str, tuple[str, str, str, str, str]] = {
    "claude-fable-5-1":  ("10", "12.50", "20", "0.25", "50"),
    "claude-fable-5":    ("10", "12.50", "20", "1", "50"),
    "claude-opus-5":     ("5", "6.25", "10", "0.50", "25"),
    "claude-opus-4-8":   ("5", "6.25", "10", "0.50", "25"),
    "claude-opus-4-7":   ("5", "6.25", "10", "0.50", "25"),
    "claude-opus-4-6":   ("5", "6.25", "10", "0.50", "25"),
    "claude-opus-4-5":   ("5", "6.25", "10", "0.50", "25"),
    "claude-sonnet-5":   ("2", "2.50", "4", "0.20", "10"),
    "claude-sonnet-4-6": ("3", "3.75", "6", "0.30", "15"),
    "claude-sonnet-4-5": ("3", "3.75", "6", "0.30", "15"),
    "claude-haiku-4-5":  ("1", "1.25", "2", "0.10", "5"),
}
_ALIASES = {"claude-haiku-4-5-20251001": "claude-haiku-4-5"}

# OpenAI, $ per million tokens, standard tier, SHORT-context columns of the pricing page:
# (base input, cached input, cache write, output). GPT-5.6 and later only: the guide states the
# 1.25x write / 0.1x read multipliers for those models, while earlier models have model-dependent
# cached-input rates and no published cache-write price. The `gpt-daybreak-*-latest` aliases are
# deliberately absent: the page says they are repointed at new models, so an id would not fix a price.
# Long-context rates are exactly 2x these, so a long-context call's saving is understated, never
# overstated — the page states no threshold at which they apply.
_OPENAI_PRICES: dict[str, tuple[str, str, str, str]] = {
    "gpt-6-astra":   ("10", "1", "12.50", "50"),
    "gpt-5.6-sol":   ("4", "0.40", "5", "20"),
    "gpt-5.6-terra": ("2", "0.20", "2.50", "12"),
    "gpt-5.6-luna":  ("0.20", "0.02", "0.25", "1.20"),
    "gpt-5.6-cyber": ("12.50", "1.25", "15.625", "75"),
}
# Values a response may carry for the tier that bills at these prices. Every other documented value
# (`flex`, `fast`, `priority`, `ultrafast`) bills differently and is excluded; so is `auto`, which
# names a project setting rather than the tier actually used. An absent field counts as standard,
# the same convention Stage A uses for Anthropic. The Batch API bills at 50% and its result bodies
# carry no marker at all, so a batch export must be tagged `service_tier: "batch"` to be excluded.
_OPENAI_STANDARD_TIERS = frozenset({"default"})

_MILLION = Decimal(1_000_000)
_TOKEN_FIELDS = ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")

# The pricing page states that 4.6-generation and later models bill the full 1M context at standard
# rates. It gives no long-context rate for these ids, so their input above 200K tokens is left out.
_NO_STATED_LONG_CONTEXT_RATE = frozenset({"claude-opus-4-5", "claude-sonnet-4-5", "claude-haiku-4-5"})
_LONG_CONTEXT_TOKENS = 200_000


def _beyond_stated_context(usage: dict, counts: dict) -> bool:
    """Whether usage may exceed the 200K tokens priced by the table. An organization usage report row
    names its context window and sums many requests, so that field decides; a single call is judged by
    its own input tokens."""
    if "context_window" in usage:
        return usage["context_window"] != "0-200k"
    return sum(counts.values()) > _LONG_CONTEXT_TOKENS


def _model_key(model: object) -> str | None:
    if not isinstance(model, str):
        return None
    key = _ALIASES.get(model, model)
    return key if key in _ANTHROPIC_PRICES else None


def output_price_usd_per_m(model: object) -> Decimal | None:
    """List output price for an exact Anthropic model id, or None when the model is not in the table."""
    key = _model_key(model)
    return Decimal(_ANTHROPIC_PRICES[key][4]) if key else None


def _count(value: object) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return None


def _openai_key(model: object) -> str | None:
    return model if isinstance(model, str) and model in _OPENAI_PRICES else None


def _value_openai(key: str, usage: object) -> dict:
    """Value one OpenAI call. `input_tokens` INCLUDES the cached and written tokens, the opposite of
    Anthropic: the guide's own cost function computes `input - cached - cache_write` as the ordinary
    tokens. Writes carry one price (1.25x base), so they are reported in the 1.25x class."""
    if not isinstance(usage, dict):
        return {"status": "malformed", "model": key}
    tier = usage.get("service_tier")
    if tier is not None and tier not in _OPENAI_STANDARD_TIERS:
        return {"status": "modifier_excluded", "model": key}
    total = _count(usage.get("input_tokens", usage.get("prompt_tokens")))
    details = usage.get("input_tokens_details", usage.get("prompt_tokens_details", {}))
    if total is None or not isinstance(details, dict):
        return {"status": "malformed", "model": key}
    cached, write = _count(details.get("cached_tokens", 0)), _count(details.get("cache_write_tokens", 0))
    if cached is None or write is None or cached + write > total:
        return {"status": "malformed", "model": key}
    base, price_cached, price_write, _output = (Decimal(p) for p in _OPENAI_PRICES[key])
    uncached = total - cached - write
    without = total * base / _MILLION
    actual = (uncached * base + cached * price_cached + write * price_write) / _MILLION
    return {
        "status": "priced", "model": key,
        "input": uncached, "cache_read": cached, "cache_write_5m": write, "cache_write_1h": 0,
        "ttl_assumed_1h": False,
        "without_caching_usd": without, "actual_usd": actual, "saving_usd": without - actual,
    }


def value_record(model: object, usage: object) -> dict:
    """Value one provider call. Returns a dict whose `status` is one of: priced, unpriced,
    modifier_excluded, malformed. Priced results carry token classes and exact Decimal dollars."""
    key = _model_key(model)
    if key is None:
        openai_key = _openai_key(model)
        if openai_key is not None:
            return _value_openai(openai_key, usage)
        return {"status": "unpriced", "model": model if isinstance(model, str) else None}
    if not isinstance(usage, dict) or "input_tokens" not in usage:
        return {"status": "malformed", "model": key}
    tier, speed, geo = usage.get("service_tier"), usage.get("speed"), usage.get("inference_geo")
    if (tier is not None and tier != "standard") or speed == "fast" or geo == "us":
        return {"status": "modifier_excluded", "model": key}
    counts = {field: _count(usage.get(field, 0)) for field in _TOKEN_FIELDS}
    if any(value is None for value in counts.values()):
        return {"status": "malformed", "model": key}
    if key in _NO_STATED_LONG_CONTEXT_RATE and _beyond_stated_context(usage, counts):
        return {"status": "modifier_excluded", "model": key}
    write = counts["cache_creation_input_tokens"]

    split = usage.get("cache_creation")
    ttl_assumed = False
    if isinstance(split, dict) and ("ephemeral_5m_input_tokens" in split
                                    or "ephemeral_1h_input_tokens" in split):
        write_5m = _count(split.get("ephemeral_5m_input_tokens", 0))
        write_1h = _count(split.get("ephemeral_1h_input_tokens", 0))
        if write_5m is None or write_1h is None or write_5m + write_1h != write:
            return {"status": "malformed", "model": key}
    else:
        # No TTL split: price every write at the 1-hour rate, the most expensive, so the saving can
        # only be understated.
        write_5m, write_1h, ttl_assumed = 0, write, write > 0

    base, price_5m, price_1h, price_read, _output = (Decimal(p) for p in _ANTHROPIC_PRICES[key])
    uncached, read = counts["input_tokens"], counts["cache_read_input_tokens"]
    without = (uncached + read + write) * base / _MILLION
    actual = (uncached * base + read * price_read + write_5m * price_5m + write_1h * price_1h) / _MILLION
    return {
        "status": "priced", "model": key,
        "input": uncached, "cache_read": read, "cache_write_5m": write_5m, "cache_write_1h": write_1h,
        "ttl_assumed_1h": ttl_assumed,
        "without_caching_usd": without, "actual_usd": actual, "saving_usd": without - actual,
    }


def _usd(value: Decimal) -> float:
    return float(round(value, 6))


def summarize(calls) -> dict:
    """Aggregate (model, usage) pairs for real provider calls into the report's `prompt_cache` block."""
    records = {"priced": 0, "unpriced": 0, "modifier_excluded": 0, "malformed": 0, "ttl_assumed_1h": 0}
    unpriced_models: dict[str, int] = {}
    by_model: dict[str, dict] = {}
    totals = {"without": Decimal(0), "actual": Decimal(0)}
    tokens = {"input": 0, "cache_read": 0, "cache_write_5m": 0, "cache_write_1h": 0}
    for model, usage in calls:
        result = value_record(model, usage)
        records[result["status"]] += 1
        if result["status"] == "unpriced":
            name = result["model"] or "<no model>"
            unpriced_models[name] = unpriced_models.get(name, 0) + 1
        if result["status"] != "priced":
            continue
        records["ttl_assumed_1h"] += result["ttl_assumed_1h"]
        entry = by_model.setdefault(result["model"], {
            "records": 0, "input": 0, "cache_read": 0, "cache_write_5m": 0, "cache_write_1h": 0,
            "without": Decimal(0), "actual": Decimal(0)})
        entry["records"] += 1
        for field in tokens:
            entry[field] += result[field]
            tokens[field] += result[field]
        entry["without"] += result["without_caching_usd"]
        entry["actual"] += result["actual_usd"]
        totals["without"] += result["without_caching_usd"]
        totals["actual"] += result["actual_usd"]

    saving = totals["without"] - totals["actual"]
    return {
        "label": LABEL,
        "price_basis_as_of": PRICE_BASIS_AS_OF,
        "sources": [dict(source) for source in PRICE_SOURCES],
        "records": records,
        "unpriced_models": dict(sorted(unpriced_models.items())),
        "tokens": tokens,
        "without_caching_usd": _usd(totals["without"]),
        "actual_usd": _usd(totals["actual"]),
        "saving_usd": _usd(saving),
        "saving_pct_of_input_cost": float(round(saving / totals["without"], 4)) if totals["without"] else None,
        "by_model": {
            model: {
                "records": e["records"], "input": e["input"], "cache_read": e["cache_read"],
                "cache_write_5m": e["cache_write_5m"], "cache_write_1h": e["cache_write_1h"],
                "without_caching_usd": _usd(e["without"]), "actual_usd": _usd(e["actual"]),
                "saving_usd": _usd(e["without"] - e["actual"]),
            }
            for model, e in sorted(by_model.items())
        },
    }
