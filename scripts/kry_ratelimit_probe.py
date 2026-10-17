#!/usr/bin/env python3
"""Capture the REAL Anthropic usage/rate-limit signal — the data, from the API's OWN response headers.

The Anthropic API returns `anthropic-ratelimit-*` headers on every response — the intended, ToS-sanctioned
self-throttle signal. This is what the bridge can read to route/throttle. NOTE: these are the rate limits for the
API KEY used here, which is a DIFFERENT thing from the Claude Code/Claude.ai *subscription* usage banner (that banner
is the chat client's UI for your plan and is not programmatically exposed). For optimizing the BRIDGE's token spend,
the API headers below are the correct, real signal.

  python3 scripts/kry_ratelimit_probe.py   # one 1-token Haiku call; needs ANTHROPIC_API_KEY
"""
import argparse, json, os, sys, urllib.request


def main() -> int:
    # The probe used to run at import time, so --help (or any argument) made the API call.
    argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter).parse_args()
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not key:
        sys.exit("ANTHROPIC_API_KEY not set")
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=json.dumps({"model": "claude-haiku-4-5-20251001", "max_tokens": 1,
                         "messages": [{"role": "user", "content": "hi"}]}).encode(),
        headers={"content-type": "application/json", "x-api-key": key,
                 "anthropic-version": "2023-06-01"})
    with urllib.request.urlopen(req) as r:
        hdrs = {k: v for k, v in r.headers.items()
                if "ratelimit" in k.lower() or k.lower() in ("retry-after", "anthropic-organization-id")}
    print("=== REAL Anthropic usage/rate-limit signal (API response headers, this key) ===")
    for k in sorted(hdrs):
        print(f"  {k}: {hdrs[k]}")
    if not hdrs:
        print("  (no rate-limit headers returned on this response)")
    print("\nThis is the signal the bridge reads to optimize — NOT the chat-client subscription banner.")
    return 0


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):  # a cp1252 Windows console cannot encode the output glyphs
        if hasattr(_stream, "reconfigure"):
            _stream.reconfigure(errors="replace")
    raise SystemExit(main())
