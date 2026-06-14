#!/usr/bin/env python3
"""Fetch OpenRouter per-request usage records for KRY T1 reconciliation (F1).

OpenRouter is the ONE displacement provider that exposes a post-hoc, per-request
usage record: `GET /api/v1/generation?id=<id>` returns the provider's OWN tokenizer
counts (`native_tokens_prompt`/`native_tokens_completion`) for a specific past call.
The host stamps each OpenRouter-served `provider_metered` receipt's `evidence` with
`/openrouter:<id>`. This operator/auditor tool reads those ids from the private mint
log, pulls the provider's record for each (polling for OpenRouter's async flush),
and writes a provider-export JSON that `scripts/kry_reconcile.py --mode per-request`
consumes — turning OpenRouter-served T1 mints from operator-DECLARED into
provider-RECONCILED.

Why this matters: Google (the other live T1 source) exposes NO per-request export
and free-tier usage is unbillable, so Google T1 is only ever aggregate-checkable.
OpenRouter's generation record exists regardless of price — so this works even for
FREE OpenRouter models, giving a genuine per-request external witness at $0.

Needs OPENROUTER_API_KEY (the account credential IS the external root of trust —
the record is only retrievable by the account that made the call). stdlib only;
reads the private mint log directly (never leaves the machine except the GET to
OpenRouter for the account's own records).

Usage:
    python3 scripts/kry_or_fetch.py kry_data/kry_mint_log.jsonl --out or_export.json
    python3 scripts/kry_reconcile.py kry_data/kry_mint_log.jsonl --provider-export or_export.json
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

_GEN_URL = "https://openrouter.ai/api/v1/generation"
_OR_REF = re.compile(r"/openrouter:([^/\s]+)")


def _reject_json_constant(value: str):
    raise ValueError(f"non-standard JSON constant {value} is not allowed")


def _json_loads(raw: str):
    return json.loads(raw, parse_constant=_reject_json_constant)


def _json_dumps(value, **kwargs) -> str:
    return json.dumps(value, allow_nan=False, **kwargs)


def extract_or_ids(mint_log_path: str) -> list[str]:
    """Generation ids from provider_metered receipts whose `detail` carries an
    `/openrouter:<id>` handle (the host stamps it there — `detail` is stored RAW,
    unlike `evidence` which is hashed). De-duplicated, order preserved."""
    ids: list[str] = []
    with open(mint_log_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = _json_loads(line)
            if rec.get("evidence_tier") != "provider_metered":
                continue
            m = _OR_REF.search(str(rec.get("detail") or ""))
            if m:
                ids.append(m.group(1))
    seen: set[str] = set()
    out: list[str] = []
    for i in ids:
        if i not in seen:
            seen.add(i)
            out.append(i)
    return out


def fetch_generation(gen_id: str, key: str, *, retries: int = 4, backoff: float = 0.5,
                     opener=urllib.request.urlopen) -> dict | None:
    """GET one generation record, polling for OpenRouter's async flush.

    Returns the record dict (the API's `data` envelope is unwrapped) once token
    counts are present, or None if never available within `retries` (the record
    may not have flushed yet, or has aged out). `opener` is injectable for tests.
    """
    url = f"{_GEN_URL}?id={urllib.parse.quote(gen_id)}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {key}"})
    for attempt in range(retries):
        try:
            with opener(req, timeout=30) as resp:
                body = _json_loads(resp.read().decode())
            data = body.get("data", body) if isinstance(body, dict) else body
            if isinstance(data, dict) and (
                    data.get("tokens_prompt") is not None
                    or data.get("native_tokens_prompt") is not None):
                return data
        except urllib.error.HTTPError as e:
            if e.code not in (404, 429):       # 404 = not-yet-flushed, 429 = rate
                raise
        except urllib.error.URLError:
            pass
        time.sleep(backoff * (2 ** attempt))
    return None


def to_export_record(gen: dict) -> dict:
    """Shape a generation record for kry_reconcile (its `_norm_provider` reads
    `tokens_prompt`/`tokens_completion`). Prefer the provider's NATIVE tokenizer
    counts; fall back to OpenRouter's normalized counts."""
    p = gen.get("native_tokens_prompt")
    c = gen.get("native_tokens_completion")
    p_field = "native_tokens_prompt"
    c_field = "native_tokens_completion"
    if p is None:
        p = gen.get("tokens_prompt")
        p_field = "tokens_prompt"
    if c is None:
        c = gen.get("tokens_completion")
        c_field = "tokens_completion"
    return {
        "id": gen.get("id"),
        "tokens_prompt": _json_token(p, p_field),
        "tokens_completion": _json_token(c, c_field),
        "native_tokens_prompt": gen.get("native_tokens_prompt"),
        "native_tokens_completion": gen.get("native_tokens_completion"),
        "total_cost": gen.get("total_cost"),
        "provider_name": gen.get("provider_name"),
    }


def _json_token(value, field: str) -> int:
    if value is None:
        return 0
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{field} must be a non-negative JSON integer")
    return value


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Fetch OpenRouter per-request usage for KRY F1 reconciliation")
    p.add_argument("mint_log", help="path to the private mint log (kry_mint_log.jsonl)")
    p.add_argument("--out", default=None,
                   help="write provider-export JSON here (default: stdout)")
    p.add_argument("--api-key", default=None,
                   help="OpenRouter key (default: $OPENROUTER_API_KEY)")
    args = p.parse_args(argv)

    ids = extract_or_ids(args.mint_log)
    if not ids:
        print("No OpenRouter-anchored provider_metered receipts found "
              "(evidence with /openrouter:<id>). Nothing to fetch.", file=sys.stderr)
        records: list[dict] = []
    else:
        key = args.api_key or os.getenv("OPENROUTER_API_KEY", "").strip()
        if not key:
            print("OPENROUTER_API_KEY not set — cannot fetch generation records.",
                  file=sys.stderr)
            return 2
        records = []
        missing: list[str] = []
        for gid in ids:
            gen = fetch_generation(gid, key)
            if gen is None:
                missing.append(gid)
                continue
            records.append(to_export_record(gen))
        print(f"fetched {len(records)}/{len(ids)} OpenRouter generation records "
              f"({len(missing)} unavailable)", file=sys.stderr)
        if missing:
            shown = ", ".join(missing[:10]) + ("…" if len(missing) > 10 else "")
            print(f"  unavailable ids (async-flush delay or expired): {shown}",
                  file=sys.stderr)

    out_json = _json_dumps(records, indent=2)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(out_json + "\n")
        print(f"wrote {len(records)} records -> {args.out}", file=sys.stderr)
    else:
        print(out_json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
