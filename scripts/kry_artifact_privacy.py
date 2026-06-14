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
                if (
                    key_label not in allowed_token_keys
                    and (
                        key_label in PROVIDER_EXPORT_PRIVATE_KEYS
                        or any(fragment in key_label for fragment in PROVIDER_EXPORT_PRIVATE_KEY_FRAGMENTS)
                    )
                ):
                    errors.append(f"{source_label} contains private field {child_path}")
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
