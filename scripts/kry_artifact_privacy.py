"""Privacy-boundary checks for KRY verified artifact packets."""

from __future__ import annotations

import re
from pathlib import Path

from kry_artifact_io import _load_json


PROVIDER_EXPORT_PUBLIC_TOKEN_KEYS = {
    "prompt_tokens",
    "tokens_prompt",
    "input_tokens",
    "completion_tokens",
    "tokens_completion",
    "output_tokens",
    "total_tokens",
    "metered_tokens",
}
USAGE_LOG_PUBLIC_TOKEN_KEYS = PROVIDER_EXPORT_PUBLIC_TOKEN_KEYS | {
    "native_tokens_prompt",
    "native_tokens_completion",
}
PROVIDER_EXPORT_PRIVATE_KEYS = {
    "prompt",
    "completion",
    "input",
    "output",
    "message",
    "messages",
    "content",
    "body",
    "request",
    "response",
    "request_body",
    "response_body",
    "raw_request",
    "raw_response",
    "raw_body",
}
PROVIDER_EXPORT_PRIVATE_KEY_FRAGMENTS = (
    "prompt_text",
    "completion_text",
    "input_text",
    "output_text",
    "message_content",
    "request_body",
    "response_body",
    "raw_request",
    "raw_response",
    "raw_body",
    "request_payload",
    "response_payload",
)
PRIVATE_STRING_VALUE_RE = re.compile(
    r"(?:"
    r"[\"'](?:prompt|completion|input|output|message|messages|content|body|request|response|"
    r"request_body|response_body|raw_request|raw_response|raw_body)[\"']\s*:"
    r"|"
    r"\b(?:prompt|completion|input|output|message|messages|content|request_body|response_body|"
    r"raw_request|raw_response|raw_body|request_payload|response_payload)\s*[:=]"
    r")",
    re.IGNORECASE,
)
# Word-level private markers: matched against the key's "_"-split words, so `prompts`,
# `the_prompt`, `system_prompt`, `message_content`, `conversation` are caught — but generic
# metadata like `request_id` / `request_class` (whose words are request/id/class) are NOT.
PRIVATE_KEY_WORDS = {
    "prompt", "prompts", "completion", "completions", "message", "messages",
    "content", "conversation", "conversations", "chat", "transcript", "dialog",
    "dialogue", "body",
}
# Bounded schemas — usage logs and provider exports must carry ONLY documented fields, so an
# UNRECOGNIZED key holding a string/dict/list value (which could smuggle prompt content under a
# generic name like msg/text/data/note/query) is rejected. Token keys are exempted separately.
USAGE_LOG_ALLOWED_KEYS = {
    "id", "request_id", "requestid", "request_class", "class", "tag", "kind",
    "cache_hit", "cached", "displacement", "holdout", "model", "model_name",
    "avoided_model", "served_model", "usage", "tokens_saved", "evidence_tier",
    "ts", "timestamp", "time", "seq", "saved", "cost", "treated",
} | USAGE_LOG_PUBLIC_TOKEN_KEYS
PROVIDER_EXPORT_ALLOWED_KEYS = {
    "id", "request_id", "requestid", "generation_id", "gen_id", "model", "model_name",
    "provider", "object", "type", "index", "created", "created_at", "ts", "timestamp",
    "finish_reason", "native_finish_reason", "usage", "cost", "total_cost",
} | PROVIDER_EXPORT_PUBLIC_TOKEN_KEYS


def _json_key_label(value) -> str:
    return "_".join(
        part for part in "".join(
            ch.lower() if ch.isalnum() else "_"
            for ch in str(value)
        ).split("_") if part
    )


def _private_key_errors(
    value: object,
    *,
    allowed_token_keys: set[str],
    source_label: str,
    allowed_keys: set[str] | None = None,
    path: str = "$",
    limit: int = 8,
) -> list[str]:
    errors: list[str] = []

    def visit(current: object, current_path: str) -> None:
        if len(errors) >= limit:
            return
        if isinstance(current, dict):
            for key, item in current.items():
                key_label = _json_key_label(key)
                child_path = f"{current_path}.{key}"
                key_words = set(key_label.split("_"))
                is_private = key_label not in allowed_token_keys and (
                    key_label in PROVIDER_EXPORT_PRIVATE_KEYS
                    or any(fragment in key_label for fragment in PROVIDER_EXPORT_PRIVATE_KEY_FRAGMENTS)
                    or bool(key_words & PRIVATE_KEY_WORDS)
                )
                if is_private:
                    errors.append(f"{source_label} contains private field {child_path}")
                    if len(errors) >= limit:
                        return
                elif (
                    allowed_keys is not None
                    and key_label not in allowed_keys
                    and key_label not in allowed_token_keys
                    and isinstance(item, (str, dict, list))
                ):
                    errors.append(
                        f"{source_label} has unrecognized field {child_path} that may carry "
                        f"private content (only documented schema fields are allowed)")
                    if len(errors) >= limit:
                        return
                visit(item, child_path)
        elif isinstance(current, list):
            for idx, item in enumerate(current):
                visit(item, f"{current_path}[{idx}]")
                if len(errors) >= limit:
                    return
        elif isinstance(current, str) and PRIVATE_STRING_VALUE_RE.search(current):
            errors.append(f"{source_label} contains private string value {current_path}")

    visit(value, path)
    return errors


def _usage_log_privacy_errors(records: list[dict]) -> list[str]:
    errors = _private_key_errors(
        records,
        allowed_token_keys=USAGE_LOG_PUBLIC_TOKEN_KEYS,
        allowed_keys=USAGE_LOG_ALLOWED_KEYS,
        source_label="usage log",
    )
    if errors:
        return [
            *errors,
            "usage log must exclude prompts, completions, messages, content, and raw request/response bodies",
        ]
    return []


def _provider_export_privacy_errors(
    provider_export: str | Path | None,
    base_dir: str | Path | None = None,
) -> list[str]:
    if not provider_export:
        return []
    try:
        raw = _load_json(provider_export, base_dir)
    except Exception as exc:
        return [f"provider export privacy scan unavailable: {exc}"]
    errors = _private_key_errors(
        raw,
        allowed_token_keys=PROVIDER_EXPORT_PUBLIC_TOKEN_KEYS,
        allowed_keys=PROVIDER_EXPORT_ALLOWED_KEYS,
        source_label="provider export",
    )
    if errors:
        return [
            *errors,
            "provider export must exclude prompts, completions, messages, content, and raw request/response bodies",
        ]
    return []


def _review_evidence_privacy_errors(data: object, kind: str) -> list[str]:
    errors = _private_key_errors(
        data,
        allowed_token_keys=USAGE_LOG_PUBLIC_TOKEN_KEYS,
        source_label=f"{kind} evidence",
    )
    if errors:
        return [
            *errors,
            f"{kind} evidence must exclude prompts, completions, messages, content, and raw request/response bodies",
        ]
    return []


def _review_evidence_file_privacy_errors(path: str | Path | None, kind: str) -> list[str]:
    if not path:
        return []
    try:
        data = _load_json(path)
    except Exception as exc:
        return [f"{kind} evidence privacy scan unavailable: {exc}"]
    return _review_evidence_privacy_errors(data, kind)


def _public_packet_json_privacy_errors(path: str | Path | None, label: str) -> list[str]:
    if not path:
        return []
    try:
        data = _load_json(path)
    except Exception as exc:
        return [f"{label} privacy scan unavailable: {exc}"]
    errors = _private_key_errors(
        data,
        allowed_token_keys=USAGE_LOG_PUBLIC_TOKEN_KEYS,
        source_label=label,
    )
    if errors:
        return [
            *errors,
            f"{label} must exclude prompts, completions, messages, content, and raw request/response bodies",
        ]
    return []
