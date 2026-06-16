"""Usage-log privacy boundary — no prompt/response content may ship in a verified packet.

Audit D: the gate was a denylist of exact key spellings, so private content smuggled under an
unlisted key (prompts/the_prompt/msg/text/data/note/query/...) shipped clean. It is now a
word-split private match + an allowlist guard for the bounded usage-log schema.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from kry_artifact_privacy import _usage_log_privacy_errors  # noqa: E402


@pytest.mark.parametrize("rec", [
    {"prompts": "leak"},
    {"the_prompt": "x"},
    {"system_prompt": "x"},
    {"user_prompt": "x"},
    {"message_content": "x"},
    {"msg": "leak"},
    {"text": "leak"},
    {"data": "a private user prompt here"},
    {"note": "the user asked about X"},
    {"query": "secret"},
    {"conversation": [{"role": "user", "content": "x"}]},
])
def test_usage_log_privacy_catches_bypass_keys(rec):
    assert _usage_log_privacy_errors([rec]), f"{rec} leaked through the privacy gate"


def test_usage_log_privacy_passes_legitimate_record():
    legit = {
        "id": "r1", "request_class": "summarize", "cache_hit": True,
        "avoided_model": "gh/claude-opus-4.8", "served_model": "gh/haiku",
        "usage": {"prompt_tokens": 100, "completion_tokens": 50},
        "tokens_saved": 50, "evidence_tier": "self_reported",
    }
    assert _usage_log_privacy_errors([legit]) == []


def test_usage_log_privacy_does_not_false_flag_generic_metadata():
    # request_id / request_class contain the word "request" but are NOT private content.
    rec = {"id": "x", "request_id": "abc", "request_class": "code", "cache_hit": True,
           "avoided_model": "m", "usage": {"total_tokens": 10}}
    assert _usage_log_privacy_errors([rec]) == []
