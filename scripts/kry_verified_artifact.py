#!/usr/bin/env python3
"""KRY verified-savings artifact gate.

This is the smallest package a buyer, reviewer, or operator can inspect without
letting polished internal evidence masquerade as external validation. It composes
existing tools:

  - kry_savings_report.analyze          savings math from the usage log
  - kry_verify.verify_attestation       stranger-verifier over the public proof
  - kry_research_grade.assess           provider-export reconciliation
  - kry_capabilities.readiness_label    mechanical readiness label

The output is JSON with explicit product, science, external-review, and kill gates.
Missing real-provider data, missing real-corpus declaration, or missing buyer/legal
review blocks the external claim; it does not disappear behind a green check.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import math
import shutil
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(_ROOT / "src"))

import kry_research_grade  # noqa: E402
import kry_reconcile       # noqa: E402
import kry_savings_report  # noqa: E402
import kry_verify          # noqa: E402
from kry_artifact_io import (  # noqa: E402
    _artifact_compare_body,
    _artifact_hash,
    _hash_file,
    _json_canonical,
    _json_pretty,
    _load_json,
    _resolve_path,
)
from kry_artifact_privacy import (  # noqa: E402
    PROVIDER_EXPORT_PRIVATE_KEY_FRAGMENTS as PROVIDER_EXPORT_PRIVATE_KEY_FRAGMENTS,
    PROVIDER_EXPORT_PRIVATE_KEYS as PROVIDER_EXPORT_PRIVATE_KEYS,
    PROVIDER_EXPORT_PUBLIC_TOKEN_KEYS as PROVIDER_EXPORT_PUBLIC_TOKEN_KEYS,
    PRIVATE_STRING_VALUE_RE as PRIVATE_STRING_VALUE_RE,
    USAGE_LOG_PUBLIC_TOKEN_KEYS as USAGE_LOG_PUBLIC_TOKEN_KEYS,
    _json_key_label as _json_key_label,
    _private_key_errors as _private_key_errors,
    _provider_export_privacy_errors as _provider_export_privacy_errors,
    _public_packet_json_privacy_errors as _public_packet_json_privacy_errors,
    _review_evidence_file_privacy_errors as _review_evidence_file_privacy_errors,
    _review_evidence_privacy_errors as _review_evidence_privacy_errors,
    _usage_log_privacy_errors as _usage_log_privacy_errors,
)
from kry import kry_capabilities  # noqa: E402

T1_MANIFEST_SCHEMA = "kry_t1_reconciliation_manifest/v1"
PROVIDER_EXPORT_MANIFEST_SCHEMA = "kry_provider_export_manifest/v1"
TOOL_MANIFEST_SCHEMA = "kry_tool_manifest/v1"
REVIEW_BASIS_SCHEMA = "kry_review_basis/v1"
REVIEWER_CHECKLIST_SCHEMA = "kry_reviewer_checklist/v1"
CLAIM_EVIDENCE_MANIFEST_SCHEMA = "kry_claim_evidence_manifest/v1"
VALIDATION_PLAN_SCHEMA = "kry_validation_plan/v1"
FINOPS_REPORT_SCHEMA = "kry_finops_report/v1"
MAX_EXTERNAL_AGGREGATE_TOLERANCE_PCT = 2.0
BUYER_MATERIALITY_AVOIDABLE_SPEND_PCT_BAR = 10.0
BUYER_MATERIALITY_MONTHLY_SAVINGS_USD_BAR = 5000.0
REQUIRED_VALIDATION_KILL_CRITERIA = (
    "provider reconciliation discrepancy",
    "independent agreement below bar",
    "missing outside review, buyer feedback, or legal review",
    "quality or SLO regression in counted savings",
    "buyer materiality or reliance threshold not met",
    "private data exposure in public packet",
    "invalid, revoked, or voided mint discovered after publication",
)
REVIEW_BASIS_INPUTS = (
    "usage_log",
    "attestation",
    "provider_export",
    "provider_export_manifest",
    "corpus_manifest",
    "t1_manifest",
    "tool_manifest",
)
ARTIFACT_PATH_INPUTS = (
    "usage_log",
    "attestation",
    "mint_log",
    "t1_manifest",
    "provider_export",
    "provider_export_manifest",
    "corpus_manifest",
    "outside_review",
    "buyer_feedback",
    "legal_review",
)
PUBLIC_PACKET_JSON_INPUTS = (
    "attestation",
    "t1_manifest",
    "provider_export_manifest",
    "corpus_manifest",
)
TOOLCHAIN_FILES = (
    "scripts/kry_verified_artifact.py",
    "scripts/kry_artifact_io.py",
    "scripts/kry_artifact_privacy.py",
    "scripts/kry_doctor.py",
    "scripts/kry_finops_report.py",
    "scripts/kry_savings_report.py",
    "scripts/kry_verify.py",
    "scripts/kry_research_grade.py",
    "scripts/kry_reconcile.py",
    "src/kry/kry_mint.py",
    "src/kry/kry_attest.py",
    "src/kry/kry_baseline.py",
    "src/kry/kry_capabilities.py",
    "src/kry/kry_token.py",
)
PRIVATE_PACKET_NAME_FRAGMENTS = (
    "mint_log",
    "kry_mint_log",
)
PRIVATE_PACKET_NAMES = (
    "kry_data",
    "mint.jsonl",
    "ledger.json",
    "decay.json",
)


def _is_bundled_sample_usage_log(path: str | Path, base_dir: str | Path | None = None) -> bool:
    candidate = _hash_file(path, base_dir)
    sample = _hash_file(_ROOT / "examples" / "sample_usage_log.jsonl")
    return bool(candidate and sample and candidate["sha256"] == sample["sha256"])


def _tool_manifest() -> dict:
    files = []
    for rel in TOOLCHAIN_FILES:
        path = _ROOT / rel
        data = path.read_bytes()
        files.append({
            "path": rel,
            "sha256": hashlib.sha256(data).hexdigest(),
            "bytes": len(data),
        })
    manifest = {
        "schema": TOOL_MANIFEST_SCHEMA,
        "files": files,
    }
    payload = _json_canonical(manifest)
    manifest["sha256"] = hashlib.sha256(payload.encode()).hexdigest()
    return manifest


def write_t1_manifest(
    mint_log: str | Path,
    out_path: str | Path,
    *,
    since: float | None = None,
    until: float | None = None,
    base_dir: str | Path | None = None,
) -> str:
    """Write the minimal shareable T1 receipt set needed for provider reconciliation."""
    source = _resolve_path(mint_log, base_dir)
    if source is None or not source.exists():
        raise FileNotFoundError(f"mint log not found: {mint_log}")
    receipts = []
    for rec in kry_reconcile.load_t1_receipts(str(source), since=since, until=until):
        metered = rec.get("metered_tokens") or [0, 0]
        if len(metered) < 2:
            raise ValueError(f"T1 receipt {rec.get('receipt_id')} has invalid metered_tokens")
        row = {
            "receipt_id": rec.get("receipt_id"),
            "evidence_tier": "provider_metered",
            "receipt_hash": rec.get("receipt_hash"),
            "chain_hash": rec.get("chain_hash"),
            "metered_tokens": [int(metered[0]), int(metered[1])],
        }
        if rec.get("ts") is not None:
            row["ts"] = rec.get("ts")
        receipts.append(row)
    if not receipts:
        raise ValueError("T1 manifest requires at least one provider_metered receipt")
    data = {
        "schema": T1_MANIFEST_SCHEMA,
        "source_mint_log_sha256": _hash_file(str(source))["sha256"],
        "receipt_count": len(receipts),
        "receipts": receipts,
    }
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(_json_pretty(data), encoding="utf-8")
    return str(out)


def _input_sha(input_hashes: dict, key: str) -> str | None:
    meta = input_hashes.get(key)
    if not meta:
        return None
    return meta.get("sha256")


def _review_basis(
    input_hashes: dict,
    *,
    corpus: str,
    mode: str,
    tolerance: int,
    tolerance_pct: float,
    since: float | None,
    until: float | None,
    replay_pass_rate: float,
) -> dict:
    config_errors = _artifact_config_errors(
        tolerance=tolerance,
        tolerance_pct=tolerance_pct,
        since=since,
        until=until,
        replay_pass_rate=replay_pass_rate,
    )
    if config_errors:
        raise ValueError("artifact numeric config invalid: " + "; ".join(config_errors))
    data = {
        "schema": REVIEW_BASIS_SCHEMA,
        "inputs": {
            f"{key}_sha256": _input_sha(input_hashes, key)
            for key in REVIEW_BASIS_INPUTS
        },
        "config": {
            "corpus": str(corpus),
            "mode": str(mode),
            "tolerance": int(tolerance),
            "tolerance_pct": float(tolerance_pct),
            "since": None if since is None else float(since),
            "until": None if until is None else float(until),
            "replay_pass_rate": float(replay_pass_rate),
        },
    }
    payload = _json_canonical(data)
    data["sha256"] = hashlib.sha256(payload.encode()).hexdigest()
    return data


def _reviewer_checklist(base_inputs: dict, *,
                        artifact_path: str = "artifact.json",
                        artifact_hash: str | None = None) -> dict:
    return {
        "schema": REVIEWER_CHECKLIST_SCHEMA,
        "artifact": {
            "path": artifact_path,
            "artifact_hash": artifact_hash,
        },
        "basis": {
            "tool_manifest_sha256": base_inputs.get("tool_manifest_sha256"),
            "review_basis_sha256": base_inputs.get("review_basis_sha256"),
        },
        "verify_command": f"python3 scripts/kry_verified_artifact.py --verify-artifact {artifact_path}",
        "doctor_command": f"python3 scripts/kry_doctor.py --artifact {artifact_path}",
        "derived_artifacts": [
            {
                "file": "finops_report.md",
                "schema": FINOPS_REPORT_SCHEMA,
                "source": artifact_path,
            },
        ],
        "required_evidence": [
            {"file": "provider_export_manifest.json", "schema": PROVIDER_EXPORT_MANIFEST_SCHEMA},
            {"file": "corpus_manifest.json", "schema": "kry_corpus_manifest/v1"},
            {"file": "outside_review.json", "schema": "kry_external_evidence/v1", "kind": "outside_review"},
            {"file": "buyer_feedback.json", "schema": "kry_external_evidence/v1", "kind": "buyer_feedback"},
            {"file": "legal_review.json", "schema": "kry_external_evidence/v1", "kind": "legal_review"},
        ],
        "buyer_local_privacy_boundary": _bullet_items(_BUYER_LOCAL_PRIVACY_BOUNDARY),
        "buyer_local_evidence_gates": _bullet_items(_BUYER_LOCAL_EVIDENCE_GATES),
        "buyer_threshold_context_fields": _bullet_items(_BUYER_THRESHOLD_CONTEXT),
        "buyer_materiality_threshold": {
            "avoidable_spend_pct_min": BUYER_MATERIALITY_AVOIDABLE_SPEND_PCT_BAR,
            "plausible_monthly_savings_usd_min": BUYER_MATERIALITY_MONTHLY_SAVINGS_USD_BAR,
            "rule": "at least one threshold must be met",
        },
        "required_kill_criteria": list(REQUIRED_VALIDATION_KILL_CRITERIA),
        "legal_claim_checks": _bullet_items(_LEGAL_CLAIM_CHECKS),
        "review_steps": [
            "Run verify_command and require ok=true.",
            "Run doctor_command and require fail=0 before handing the packet to a reviewer.",
            "Confirm artifact.claim_evidence_manifest has one entry for every artifact.claim_register claim.",
            "Confirm artifact.corpus_manifest.summary.validation_plan uses kry_validation_plan/v1.",
            "Confirm artifact.claim_register.external_verified_savings has status=allowed before any external savings claim.",
            "Confirm artifact.claim_register.tradeable_token has status=forbidden.",
            "Confirm template schemas were replaced by live evidence schemas only after real review or provenance happened.",
            "Confirm finops_report.md was regenerated from artifact.json and does not override artifact.claim_register.",
        ],
        "claim_checks": [
            {"claim_id": "external_verified_savings", "required_status_for_external_claim": "allowed"},
            {"claim_id": "production_ready", "required_status_for_public_claim": "allowed"},
            {"claim_id": "tradeable_token", "required_status": "forbidden"},
        ],
    }


def _artifact_binding_errors(data: dict, input_hashes: dict,
                             required_inputs: tuple[str, ...]) -> list[str]:
    bindings = data.get("artifact_inputs")
    if not isinstance(bindings, dict):
        return ["artifact_inputs object missing"]
    errors: list[str] = []
    for key in required_inputs:
        expected = _input_sha(input_hashes, key)
        field = f"{key}_sha256"
        if not expected:
            errors.append(f"{key} hash unavailable")
        elif bindings.get(field) != expected:
            errors.append(f"{field} mismatch")
    return errors


def _date_ordinal(value) -> int | None:
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00")).date().toordinal()
        except ValueError:
            return None
    return None


def _date_epoch(value) -> float | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    try:
        return dt.timestamp()
    except (OverflowError, OSError, ValueError):
        return None


def _latest_date_label(*values) -> str | None:
    parsed = []
    for value in values:
        epoch = _date_epoch(value)
        if epoch is not None:
            parsed.append((epoch, str(value)))
    if not parsed:
        return None
    return max(parsed, key=lambda item: item[0])[1]


def _date_errors(value, *, not_before: str | None = None) -> list[str]:
    if value is None or (isinstance(value, str) and not value.strip()):
        return ["date missing"]
    actual = _date_ordinal(value)
    if actual is None:
        return ["date must be ISO-8601"]
    now = datetime.now(timezone.utc)
    actual_epoch = _date_epoch(value)
    if actual_epoch is not None and actual_epoch > now.timestamp():
        return ["date must not be in the future"]
    if not_before is not None:
        required_epoch = _date_epoch(not_before)
        if actual_epoch is not None and required_epoch is not None and actual_epoch < required_epoch:
            return [f"date must be on or after reviewed evidence date {not_before}"]
    return []


def _text_errors(value, field: str) -> list[str]:
    if not isinstance(value, str) or not value.strip():
        return [f"{field} missing"]
    if "TODO" in value.upper():
        return [f"{field} must replace TODO placeholder"]
    return []


_GENERIC_PLACEHOLDER_TEXT = {
    "n/a",
    "na",
    "none",
    "unknown",
    "tbd",
    "to be determined",
    "not applicable",
    "unspecified",
}


def _concrete_text_errors(value, field: str) -> list[str]:
    errors = _text_errors(value, field)
    if errors:
        return errors
    text = value.strip()
    if any(ord(ch) < 32 for ch in text):
        return [f"{field} must be a single-line concrete value with no control characters"]
    if " / " in text:
        return [f"{field} must be a concrete value, not a placeholder option list"]
    if text.lower().strip(".:") in _GENERIC_PLACEHOLDER_TEXT:
        return [f"{field} must be a concrete value, not a generic placeholder"]
    return []


def _text_list_errors(value, field: str) -> list[str]:
    if not isinstance(value, list) or not value:
        return [f"{field} must be a non-empty list of strings"]
    errors: list[str] = []
    for idx, item in enumerate(value):
        errors.extend(_text_errors(item, f"{field}[{idx}]"))
    return errors


def _concrete_text_list_errors(value, field: str) -> list[str]:
    if not isinstance(value, list) or not value:
        return [f"{field} must be a non-empty list of strings"]
    errors: list[str] = []
    for idx, item in enumerate(value):
        errors.extend(_concrete_text_errors(item, f"{field}[{idx}]"))
    return errors


def _kill_criteria_errors(value, field: str) -> list[str]:
    errors = _text_list_errors(value, field)
    if not isinstance(value, list):
        return errors
    entries = {item.strip() for item in value if isinstance(item, str)}
    for criterion in REQUIRED_VALIDATION_KILL_CRITERIA:
        if criterion not in entries:
            errors.append(f"{field} must include {criterion}")
    return errors


def _is_nonnegative_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _sha256_hex_errors(value, field: str) -> list[str]:
    if not isinstance(value, str) or not value.strip():
        return [f"{field} missing"]
    text = value.strip()
    if len(text) != 64 or any(ch not in "0123456789abcdef" for ch in text):
        return [f"{field} must be 64 lowercase hex characters"]
    return []


def _evidence_record_errors(data: dict) -> list[str]:
    errors: list[str] = []
    errors.extend(_concrete_text_errors(data.get("evidence_source"), "evidence_source"))
    errors.extend(_concrete_text_errors(data.get("evidence_reference"), "evidence_reference"))
    return errors


_REQUIRED_REVIEWED_CLAIMS = {
    "outside_review": {"external_verified_savings"},
    "buyer_feedback": {"external_verified_savings"},
    "legal_review": {"external_verified_savings", "tradeable_token"},
}

_BUYER_LOCAL_GATE_FIELDS = (
    "proof_required_reader_named",
    "provider_or_bill_data_named",
    "request_or_gateway_logs_named",
    "baseline_accepted",
    "authority_named",
    "quality_or_slo_named",
    "materiality_named",
    "seven_day_window_or_data_supplied",
)

_BUYER_THRESHOLD_CONTEXT_FIELDS = (
    "proof_required_reader",
    "provider_or_bill_data_source",
    "request_or_gateway_metadata_source",
    "baseline_reference",
    "authority_basis",
    "quality_or_slo_boundary",
    "materiality_basis",
    "sample_window",
)

_OUTSIDE_REVIEW_CHECK_FIELDS = (
    "verify_artifact_command_run",
    "doctor_command_run",
    "claim_register_checked",
    "claim_evidence_manifest_checked",
    "finops_report_checked",
    "hash_bindings_checked",
    "template_schema_absent",
    "no_private_packet_material",
    "revocation_or_void_status_checked",
)

_OUTSIDE_REVIEW_OUTPUT_TRUE_FIELDS = (
    "verify_artifact_ok",
    "finops_report_rendered",
    "claim_register_external_verified_savings_allowed",
    "claim_register_tradeable_token_forbidden",
    "claim_evidence_manifest_complete",
    "no_invalid_revoked_or_voided_mints_known",
)

_OUTSIDE_REVIEW_OUTPUT_ZERO_FIELDS = (
    "verify_artifact_error_count",
    "doctor_fail_count",
)

_LEGAL_CLAIM_CHECK_FIELDS = (
    "external_claim_text_checked",
    "retained_dollars_language_checked",
    "credit_settlement_language_checked",
    "routing_permission_language_checked",
    "carbon_language_checked",
    "tradeable_token_disclaimer_checked",
    "non_transferable_scope_checked",
    "legal_limitations_recorded",
)

_KNOWN_CLAIM_IDS = {
    "internal_efficiency_artifact",
    "external_verified_savings",
    "provider_reconciled",
    "real_corpus_validated",
    "research_grade_readiness",
    "production_ready",
    "external_review_complete",
    "tradeable_token",
}


def _reviewed_claims_errors(data: dict, kind: str) -> list[str]:
    errors = _text_list_errors(data.get("reviewed_claims"), "reviewed_claims")
    claims = data.get("reviewed_claims")
    if isinstance(claims, list):
        seen = set()
        for idx, item in enumerate(claims):
            if not isinstance(item, str):
                continue
            claim_id = item.strip()
            seen.add(claim_id)
            if claim_id and "TODO" not in claim_id.upper() and claim_id not in _KNOWN_CLAIM_IDS:
                errors.append(f"reviewed_claims[{idx}] unknown claim id {claim_id}")
        for claim_id in sorted(_REQUIRED_REVIEWED_CLAIMS.get(kind, set())):
            if claim_id not in seen:
                errors.append(f"reviewed_claims must include {claim_id}")
    return errors


def _buyer_local_gate_errors(data: dict) -> list[str]:
    gates = data.get("buyer_local_evidence_gates")
    if not isinstance(gates, dict):
        return ["buyer_local_evidence_gates object missing"]
    errors = []
    for field in _BUYER_LOCAL_GATE_FIELDS:
        if gates.get(field) is not True:
            errors.append(f"buyer_local_evidence_gates.{field} must be true")
    return errors


def _buyer_threshold_context_errors(data: dict) -> list[str]:
    context = data.get("buyer_threshold_context")
    if not isinstance(context, dict):
        return ["buyer_threshold_context object missing"]
    errors = []
    for field in _BUYER_THRESHOLD_CONTEXT_FIELDS:
        errors.extend(_concrete_text_errors(context.get(field), f"buyer_threshold_context.{field}"))
    return errors


def _buyer_materiality_errors(data: dict) -> list[str]:
    materiality = data.get("buyer_materiality")
    if not isinstance(materiality, dict):
        return ["buyer_materiality object missing"]
    errors: list[str] = []

    def optional_number(field: str) -> float | None:
        if field not in materiality:
            return None
        field_errors = _finite_number_errors(
            materiality.get(field),
            f"buyer_materiality.{field}",
            minimum=0,
        )
        errors.extend(field_errors)
        if field_errors:
            return None
        return float(materiality[field])

    avoidable_pct = optional_number("avoidable_spend_pct")
    monthly_usd = optional_number("plausible_monthly_savings_usd")
    if errors:
        return errors
    if avoidable_pct is None and monthly_usd is None:
        return ["buyer_materiality must include avoidable_spend_pct or plausible_monthly_savings_usd"]
    if not (
        (avoidable_pct is not None and avoidable_pct >= BUYER_MATERIALITY_AVOIDABLE_SPEND_PCT_BAR)
        or (monthly_usd is not None and monthly_usd >= BUYER_MATERIALITY_MONTHLY_SAVINGS_USD_BAR)
    ):
        errors.append(
            "buyer_materiality must meet avoidable_spend_pct >= "
            f"{BUYER_MATERIALITY_AVOIDABLE_SPEND_PCT_BAR:g} or plausible_monthly_savings_usd >= "
            f"{BUYER_MATERIALITY_MONTHLY_SAVINGS_USD_BAR:g}"
        )
    return errors


def _outside_review_check_errors(data: dict) -> list[str]:
    checks = data.get("reviewer_artifact_checks")
    if not isinstance(checks, dict):
        return ["reviewer_artifact_checks object missing"]
    errors = []
    for field in _OUTSIDE_REVIEW_CHECK_FIELDS:
        if checks.get(field) is not True:
            errors.append(f"reviewer_artifact_checks.{field} must be true")
    return errors


def _outside_review_output_errors(data: dict) -> list[str]:
    outputs = data.get("reviewer_command_outputs")
    if not isinstance(outputs, dict):
        return ["reviewer_command_outputs object missing"]
    errors = []
    for field in _OUTSIDE_REVIEW_OUTPUT_TRUE_FIELDS:
        if outputs.get(field) is not True:
            errors.append(f"reviewer_command_outputs.{field} must be true")
    for field in _OUTSIDE_REVIEW_OUTPUT_ZERO_FIELDS:
        value = outputs.get(field)
        if not _is_nonnegative_int(value) or value != 0:
            errors.append(f"reviewer_command_outputs.{field} must be 0")
    return errors


def _legal_claim_check_errors(data: dict) -> list[str]:
    checks = data.get("legal_claim_checks")
    if not isinstance(checks, dict):
        return ["legal_claim_checks object missing"]
    errors = []
    for field in _LEGAL_CLAIM_CHECK_FIELDS:
        if checks.get(field) is not True:
            errors.append(f"legal_claim_checks.{field} must be true")
    return errors


def _review_evidence_summary(data: dict | None, kind: str) -> dict:
    source = data if isinstance(data, dict) else {}
    return {
        "schema": source.get("schema"),
        "kind": source.get("kind", kind),
        "date": source.get("date"),
        "verdict": source.get("verdict"),
        "evidence_source": source.get("evidence_source"),
        "evidence_reference": source.get("evidence_reference"),
        "reviewer": source.get("reviewer"),
        "reviewer_artifact_checks": source.get("reviewer_artifact_checks"),
        "reviewer_command_outputs": source.get("reviewer_command_outputs"),
        "buyer": source.get("buyer"),
        "buyer_role": source.get("buyer_role"),
        "buyer_local_evidence_gates": source.get("buyer_local_evidence_gates"),
        "buyer_threshold_context": source.get("buyer_threshold_context"),
        "buyer_materiality": source.get("buyer_materiality"),
        "external_claim_allowed": source.get("external_claim_allowed"),
        "tradeable_token_disclaimed": source.get("tradeable_token_disclaimed"),
        "legal_claim_checks": source.get("legal_claim_checks"),
        "legal_limitations": source.get("legal_limitations"),
        "reviewed_claims": source.get("reviewed_claims"),
    }


def _review_evidence(path: str | Path | None, kind: str, input_hashes: dict,
                     base_dir: str | Path | None = None,
                     *,
                     not_before_date: str | None = None) -> dict:
    file_meta = _hash_file(path, base_dir)
    if file_meta is None:
        return {
            "ok": False,
            "file": None,
            "errors": ["evidence file missing"],
            "summary": _review_evidence_summary(None, kind),
        }
    try:
        data = _load_json(path, base_dir)
    except Exception as exc:
        return {
            "ok": False,
            "file": file_meta,
            "errors": [f"evidence JSON unreadable: {exc}"],
            "summary": _review_evidence_summary(None, kind),
        }
    if not isinstance(data, dict):
        return {
            "ok": False,
            "file": file_meta,
            "errors": ["evidence JSON must be an object"],
            "summary": _review_evidence_summary(None, kind),
        }

    required_inputs = [
        "usage_log",
        "attestation",
        "provider_export",
        "corpus_manifest",
        "tool_manifest",
        "review_basis",
    ]
    if _input_sha(input_hashes, "t1_manifest"):
        required_inputs.append("t1_manifest")
    if _input_sha(input_hashes, "provider_export_manifest"):
        required_inputs.append("provider_export_manifest")
    errors = _artifact_binding_errors(data, input_hashes, required_inputs)
    if data.get("schema") != "kry_external_evidence/v1":
        errors.append("schema must be kry_external_evidence/v1")
    if data.get("kind") != kind:
        errors.append(f"kind must be {kind}")
    errors.extend(_review_evidence_privacy_errors(data, kind))
    errors.extend(_date_errors(data.get("date"), not_before=not_before_date))
    errors.extend(_evidence_record_errors(data))
    errors.extend(_reviewed_claims_errors(data, kind))

    verdict = str(data.get("verdict", "")).strip().lower()
    if kind == "outside_review":
        if verdict not in {"pass", "verified", "accepted"}:
            errors.append("outside_review verdict must be pass/verified/accepted")
        errors.extend(_concrete_text_errors(data.get("reviewer"), "reviewer"))
        if data.get("independent") is not True:
            errors.append("independent must be true")
        errors.extend(_outside_review_check_errors(data))
        errors.extend(_outside_review_output_errors(data))
    elif kind == "buyer_feedback":
        if verdict not in {"qualified_interest", "pilot", "paid_trial", "pass", "accepted"}:
            errors.append("buyer_feedback verdict must be qualified_interest/pilot/paid_trial/pass/accepted")
        errors.extend(_concrete_text_errors(data.get("buyer"), "buyer"))
        errors.extend(_concrete_text_errors(data.get("buyer_role"), "buyer_role"))
        errors.extend(_buyer_local_gate_errors(data))
        errors.extend(_buyer_threshold_context_errors(data))
        errors.extend(_buyer_materiality_errors(data))
    elif kind == "legal_review":
        if verdict not in {"approved", "approved_with_limits", "pass"}:
            errors.append("legal_review verdict must be approved/approved_with_limits/pass")
        errors.extend(_concrete_text_errors(data.get("reviewer"), "reviewer"))
        if data.get("external_claim_allowed") is not True:
            errors.append("external_claim_allowed must be true")
        if data.get("tradeable_token_disclaimed") is not True:
            errors.append("tradeable_token_disclaimed must be true")
        errors.extend(_concrete_text_list_errors(data.get("legal_limitations"), "legal_limitations"))
        errors.extend(_legal_claim_check_errors(data))
    else:
        errors.append(f"unknown evidence kind {kind}")

    summary = _review_evidence_summary(data, kind)
    return {"ok": not errors, "file": file_meta, "errors": errors, "summary": summary}


def _normalized_channel_value(value) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return " ".join(value.strip().lower().split())


def _review_channel_separation_errors(review: dict) -> list[str]:
    errors: list[str] = []
    summaries = {
        kind: (block.get("summary") if isinstance(block, dict) else None) or {}
        for kind, block in review.items()
    }

    seen_refs: dict[str, str] = {}
    for kind, summary in summaries.items():
        ref = _normalized_channel_value(summary.get("evidence_reference"))
        if not ref:
            continue
        if ref in seen_refs:
            errors.append(
                f"review evidence channels must use distinct evidence_reference values: "
                f"{seen_refs[ref]} and {kind} match"
            )
        else:
            seen_refs[ref] = kind

    actors = {
        "outside_review.reviewer": summaries.get("outside_review", {}).get("reviewer"),
        "buyer_feedback.buyer": summaries.get("buyer_feedback", {}).get("buyer"),
        "legal_review.reviewer": summaries.get("legal_review", {}).get("reviewer"),
    }
    seen_actors: dict[str, str] = {}
    for label, value in actors.items():
        actor = _normalized_channel_value(value)
        if not actor:
            continue
        if actor in seen_actors:
            errors.append(
                f"review evidence channels must name distinct actors: "
                f"{seen_actors[actor]} and {label} match"
            )
        else:
            seen_actors[actor] = label
    return errors


def _corpus_manifest(path: str | Path | None, input_hashes: dict, savings: dict,
                     base_dir: str | Path | None = None) -> dict:
    file_meta = _hash_file(path, base_dir)
    if file_meta is None:
        return {"ok": False, "file": None, "errors": ["corpus manifest missing"], "summary": None}
    try:
        data = _load_json(path, base_dir)
    except Exception as exc:
        return {"ok": False, "file": file_meta, "errors": [f"corpus manifest unreadable: {exc}"], "summary": None}
    if not isinstance(data, dict):
        return {"ok": False, "file": file_meta, "errors": ["corpus manifest must be an object"], "summary": None}

    required_inputs = ("usage_log", "provider_export", "provider_export_manifest", "t1_manifest")
    errors = _artifact_binding_errors(data, input_hashes, required_inputs)
    if data.get("schema") != "kry_corpus_manifest/v1":
        errors.append("schema must be kry_corpus_manifest/v1")
    if data.get("corpus") != "real":
        errors.append("corpus must be real")
    if data.get("non_synthetic") is not True:
        errors.append("non_synthetic must be true")
    errors.extend(_date_errors(data.get("date")))
    errors.extend(_concrete_text_errors(data.get("source"), "source"))
    errors.extend(_concrete_text_errors(data.get("source_reference"), "source_reference"))
    collection_window = data.get("collection_window")
    if not isinstance(collection_window, dict):
        errors.append("collection_window object missing")
    else:
        errors.extend(_collection_window_bound_errors(collection_window))
    count = data.get("record_count")
    if not _is_nonnegative_int(count):
        errors.append("record_count must be a non-negative integer")
    elif count != savings.get("records"):
        errors.append(f"record_count must match normalized records ({savings.get('records')})")
    summary = {
        "schema": data.get("schema"),
        "corpus": data.get("corpus"),
        "date": data.get("date"),
        "source": data.get("source"),
        "source_reference": data.get("source_reference"),
        "record_count": data.get("record_count"),
        "collection_window": data.get("collection_window"),
        "validation_plan": data.get("validation_plan"),
    }
    return {"ok": not errors, "file": file_meta, "errors": errors, "summary": summary}


def _provider_export_rows(raw: object, mode: str) -> list[dict] | None:
    if mode == "per-request":
        rows = kry_reconcile.provider_record_rows(raw)
        for row in rows:
            kry_reconcile.normalize_provider_record(row)
        return rows
    if isinstance(raw, dict):
        if isinstance(raw.get("data"), list):
            rows = raw["data"]
            for row in rows:
                kry_reconcile.normalize_provider_record(row)
            return rows
        if isinstance(raw.get("records"), list):
            rows = raw["records"]
            for row in rows:
                kry_reconcile.normalize_provider_record(row)
            return rows
        if mode == "aggregate":
            kry_reconcile.normalize_provider_record(raw)
            return [raw]
        return []
    if isinstance(raw, list):
        for row in raw:
            kry_reconcile.normalize_provider_record(row)
        return raw
    return None


def _provider_export_record_count(provider_export: str | Path | None,
                                  mode: str,
                                  base_dir: str | Path | None = None) -> int | None:
    if not provider_export:
        return None
    rows = _provider_export_rows(_load_json(provider_export, base_dir), mode)
    return len(rows) if rows is not None else None


def _provider_export_token_total(provider_export: str | Path | None,
                                 mode: str,
                                 base_dir: str | Path | None = None) -> int | None:
    if not provider_export:
        return None
    rows = _provider_export_rows(_load_json(provider_export, base_dir), mode)
    if rows is None:
        return None
    total = 0
    for row in rows:
        prompt, completion = kry_reconcile.normalize_provider_record(row)
        total += prompt + completion
    return total


def _required_window(since: str | None, until: str | None) -> dict:
    if not since or not until:
        raise ValueError("collection window requires both since and until")
    return {"since": since, "until": until}


def _provider_export_manifest_generation_errors(
    *,
    provider: str,
    export_source: str,
    export_reference: str,
    date: str,
    collection_window: dict,
) -> list[str]:
    errors: list[str] = []
    errors.extend(_concrete_text_errors(provider, "provider"))
    errors.extend(_concrete_text_errors(export_source, "export_source"))
    errors.extend(_concrete_text_errors(export_reference, "export_reference"))
    errors.extend(_date_errors(date))
    if not isinstance(collection_window, dict):
        errors.append("collection_window object missing")
    else:
        errors.extend(_collection_window_bound_errors(collection_window))
    return errors


def _corpus_manifest_generation_errors(
    *,
    provider: str,
    source: str,
    source_reference: str,
    date: str,
    collection_window: dict,
) -> list[str]:
    errors: list[str] = []
    errors.extend(_concrete_text_errors(provider, "provider"))
    errors.extend(_concrete_text_errors(source, "source"))
    errors.extend(_concrete_text_errors(source_reference, "source_reference"))
    errors.extend(_date_errors(date))
    if not isinstance(collection_window, dict):
        errors.append("collection_window object missing")
    else:
        errors.extend(_collection_window_bound_errors(collection_window))
    return errors


def _external_aggregate_tolerance_errors(mode: str, tolerance_pct: float) -> list[str]:
    if mode != "aggregate":
        return []
    errors = _finite_number_errors(
        tolerance_pct,
        "tolerance_pct",
        minimum=0,
        maximum=MAX_EXTERNAL_AGGREGATE_TOLERANCE_PCT,
    )
    if errors:
        return errors
    return []


def write_provider_export_manifest(
    out_path: str | Path,
    *,
    provider_export: str,
    t1_manifest: str,
    provider: str,
    export_source: str,
    export_reference: str,
    date: str,
    collection_window: dict,
    mode: str = "per-request",
    base_dir: str | Path | None = None,
) -> dict:
    generation_errors = _provider_export_manifest_generation_errors(
        provider=provider,
        export_source=export_source,
        export_reference=export_reference,
        date=date,
        collection_window=collection_window,
    )
    if generation_errors:
        raise ValueError("provider export manifest evidence fields invalid: " + "; ".join(generation_errors))
    privacy_errors = _provider_export_privacy_errors(provider_export, base_dir)
    if privacy_errors:
        raise ValueError("provider export privacy check failed: " + "; ".join(privacy_errors))
    provider_count = _provider_export_record_count(provider_export, mode, base_dir)
    if provider_count is None:
        raise ValueError("provider export record count unavailable")
    if provider_count <= 0:
        raise ValueError("provider export must contain at least one provider record")
    provider_token_total = _provider_export_token_total(provider_export, mode, base_dir)
    if provider_token_total is None:
        raise ValueError("provider export token total unavailable")
    if provider_token_total <= 0:
        raise ValueError("provider export must contain positive provider token counts")
    manifest = {
        "schema": PROVIDER_EXPORT_MANIFEST_SCHEMA,
        "provider": provider,
        "export_source": export_source,
        "export_reference": export_reference,
        "date": date,
        "non_synthetic": True,
        "reconciliation_mode": mode,
        "provider_record_count": provider_count,
        "collection_window": collection_window,
        "artifact_inputs": {
            "provider_export_sha256": _hash_file(provider_export, base_dir)["sha256"],
            "t1_manifest_sha256": _hash_file(t1_manifest, base_dir)["sha256"],
        },
    }
    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_json_pretty(manifest), encoding="utf-8")
    return manifest


def write_corpus_manifest(
    out_path: str | Path,
    usage_log: str,
    *,
    provider_export: str,
    provider_export_manifest: str,
    t1_manifest: str,
    provider: str,
    source: str,
    source_reference: str,
    date: str,
    collection_window: dict,
    mode: str = "per-request",
    tolerance: int = 0,
    tolerance_pct: float = 5.0,
    min_provider_records: int | None = None,
    min_usage_records: int | None = None,
    min_independent_agreement: float | None = None,
    base_dir: str | Path | None = None,
) -> dict:
    generation_errors = _corpus_manifest_generation_errors(
        provider=provider,
        source=source,
        source_reference=source_reference,
        date=date,
        collection_window=collection_window,
    )
    if not _is_nonnegative_int(tolerance):
        generation_errors.append("tolerance must be a non-negative integer")
    generation_errors.extend(_finite_number_errors(tolerance_pct, "tolerance_pct", minimum=0))
    generation_errors.extend(_external_aggregate_tolerance_errors(mode, tolerance_pct))
    if min_independent_agreement is not None:
        generation_errors.extend(
            _finite_number_errors(
                min_independent_agreement,
                "min_independent_agreement",
                minimum=0,
                maximum=1,
            )
        )
    if generation_errors:
        raise ValueError("corpus manifest evidence fields invalid: " + "; ".join(generation_errors))
    records = kry_savings_report._load_records(str(_resolve_path(usage_log, base_dir)))
    usage_privacy_errors = _usage_log_privacy_errors(records)
    if usage_privacy_errors:
        raise ValueError("usage log privacy check failed: " + "; ".join(usage_privacy_errors))
    provider_privacy_errors = _provider_export_privacy_errors(provider_export, base_dir)
    if provider_privacy_errors:
        raise ValueError("provider export privacy check failed: " + "; ".join(provider_privacy_errors))
    savings = kry_savings_report.analyze(records)
    usage_count = int(savings.get("records") or 0)
    provider_count = _provider_export_record_count(provider_export, mode, base_dir)
    if provider_count is None:
        raise ValueError("provider export record count unavailable")
    min_provider = provider_count if min_provider_records is None else min_provider_records
    min_usage = usage_count if min_usage_records is None else min_usage_records
    min_agreement = (
        kry_capabilities.INDEPENDENT_AGREEMENT_BAR
        if min_independent_agreement is None
        else min_independent_agreement
    )
    manifest = {
        "schema": "kry_corpus_manifest/v1",
        "corpus": "real",
        "date": date,
        "source": source,
        "source_reference": source_reference,
        "non_synthetic": True,
        "record_count": usage_count,
        "collection_window": collection_window,
        "validation_plan": {
            "schema": VALIDATION_PLAN_SCHEMA,
            "registered_date": date,
            "provider": provider,
            "reconciliation_mode": mode,
            "tolerance": int(tolerance),
            "tolerance_pct": float(tolerance_pct),
            "min_provider_records": int(min_provider),
            "min_usage_records": int(min_usage),
            "min_independent_agreement": float(min_agreement),
            "collection_window": collection_window,
            "outside_review_required": True,
            "buyer_feedback_required": True,
            "legal_review_required": True,
            "kill_criteria": list(REQUIRED_VALIDATION_KILL_CRITERIA),
        },
        "artifact_inputs": {
            "usage_log_sha256": _hash_file(usage_log, base_dir)["sha256"],
            "provider_export_sha256": _hash_file(provider_export, base_dir)["sha256"],
            "provider_export_manifest_sha256": _hash_file(provider_export_manifest, base_dir)["sha256"],
            "t1_manifest_sha256": _hash_file(t1_manifest, base_dir)["sha256"],
        },
    }
    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_json_pretty(manifest), encoding="utf-8")
    return manifest


def _window_bound_to_epoch(value) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)) and math.isfinite(float(value)):
        return float(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            number = float(text)
        except ValueError:
            pass
        else:
            return number if math.isfinite(number) else None
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    return None


def _collection_window_errors(window: dict, *, since: float | None, until: float | None) -> list[str]:
    errors: list[str] = []
    for name, expected in (("since", since), ("until", until)):
        if expected is None:
            continue
        actual = _window_bound_to_epoch(window.get(name))
        if actual is None or abs(actual - float(expected)) > 1e-6:
            errors.append(f"collection_window.{name} must match --{name} ({float(expected)})")
    return errors


def _aggregate_reconciliation_window_errors(
    mode: str,
    since: float | None,
    until: float | None,
) -> list[str]:
    if mode != "aggregate":
        return []
    if since is None or until is None:
        return ["aggregate mode requires --since and --until to filter T1 receipts to the billed window"]
    if isinstance(since, bool) or isinstance(until, bool):
        return ["aggregate mode requires numeric --since and --until receipt filters"]
    try:
        start = float(since)
        end = float(until)
    except (TypeError, ValueError):
        return ["aggregate mode requires numeric --since and --until receipt filters"]
    if not math.isfinite(start) or not math.isfinite(end):
        return ["aggregate mode requires finite --since and --until receipt filters"]
    if start >= end:
        return ["aggregate mode requires --since before --until"]
    return []


def _collection_window_bound_errors(window: dict, prefix: str = "collection_window") -> list[str]:
    errors: list[str] = []
    parsed: dict[str, float] = {}
    for name in ("since", "until"):
        actual = _window_bound_to_epoch(window.get(name))
        if actual is None:
            errors.append(f"{prefix}.{name} must be numeric epoch or ISO-8601")
        else:
            parsed[name] = actual
    if set(parsed) == {"since", "until"} and parsed["since"] >= parsed["until"]:
        errors.append(f"{prefix}.since must be before {prefix}.until")
    return errors


def _window_pair(window: dict | None) -> tuple[float | None, float | None]:
    if not isinstance(window, dict):
        return None, None
    return _window_bound_to_epoch(window.get("since")), _window_bound_to_epoch(window.get("until"))


def _collection_windows_aligned(corpus_evidence: dict, provider_evidence: dict) -> tuple[bool, str]:
    corpus = (corpus_evidence.get("summary") or {}).get("collection_window")
    provider = (provider_evidence.get("summary") or {}).get("collection_window")
    c_since, c_until = _window_pair(corpus)
    p_since, p_until = _window_pair(provider)
    if c_since is None or c_until is None or p_since is None or p_until is None:
        return False, "corpus/provider collection windows unavailable"
    if abs(c_since - p_since) > 1e-6 or abs(c_until - p_until) > 1e-6:
        return False, f"corpus window {corpus} vs provider window {provider}"
    return True, "corpus/provider collection windows match"


def _same_window(left: dict | None, right: dict | None) -> bool:
    l_since, l_until = _window_pair(left)
    r_since, r_until = _window_pair(right)
    return (
        l_since is not None and l_until is not None
        and r_since is not None and r_until is not None
        and abs(l_since - r_since) <= 1e-6
        and abs(l_until - r_until) <= 1e-6
    )


def _nonnegative_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0


def _finite_number_errors(value, field: str, *,
                          minimum: float | None = None,
                          maximum: float | None = None) -> list[str]:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return [f"{field} must be a finite number"]
    number = float(value)
    if not math.isfinite(number):
        return [f"{field} must be a finite number"]
    if minimum is not None and number < minimum:
        return [f"{field} must be >= {minimum:g}"]
    if maximum is not None and number > maximum:
        return [f"{field} must be <= {maximum:g}"]
    return []


def _artifact_config_errors(*,
                            tolerance: int,
                            tolerance_pct: float,
                            since: float | None,
                            until: float | None,
                            replay_pass_rate: float) -> list[str]:
    errors: list[str] = []
    if not _is_nonnegative_int(tolerance):
        errors.append("tolerance must be a non-negative integer")
    errors.extend(_finite_number_errors(tolerance_pct, "tolerance_pct", minimum=0))
    errors.extend(_finite_number_errors(replay_pass_rate, "replay_pass_rate", minimum=0, maximum=1))
    for field, value in (("since", since), ("until", until)):
        if value is not None:
            errors.extend(_finite_number_errors(value, field))
    return errors


def _validation_plan(
    corpus_evidence: dict,
    provider_evidence: dict,
    research: dict,
    savings: dict,
    *,
    mode: str,
    tolerance: int,
    tolerance_pct: float,
) -> dict:
    corpus_summary = corpus_evidence.get("summary") or {}
    provider_summary = provider_evidence.get("summary") or {}
    plan = corpus_summary.get("validation_plan")
    if not isinstance(plan, dict):
        return {"ok": False, "errors": ["validation_plan object missing"], "summary": None}

    errors: list[str] = []
    if plan.get("schema") != VALIDATION_PLAN_SCHEMA:
        errors.append(f"validation_plan schema must be {VALIDATION_PLAN_SCHEMA}")
    errors.extend(_date_errors(plan.get("registered_date")))
    registered = _date_ordinal(plan.get("registered_date"))
    registered_epoch = _date_epoch(plan.get("registered_date"))
    for label, value in (
        ("corpus manifest", corpus_summary.get("date")),
        ("provider export manifest", provider_summary.get("date")),
    ):
        actual = _date_ordinal(value)
        if registered is not None and actual is not None and registered > actual:
            errors.append(f"registered_date must be on or before {label} date {value}")

    errors.extend(_text_errors(plan.get("provider"), "validation_plan.provider"))
    provider = provider_summary.get("provider")
    if provider and plan.get("provider") != provider:
        errors.append(f"validation_plan.provider must match provider export manifest provider {provider}")
    if plan.get("reconciliation_mode") != mode:
        errors.append(f"validation_plan.reconciliation_mode must be {mode}")
    if plan.get("tolerance") != tolerance:
        errors.append(f"validation_plan.tolerance must be {tolerance}")
    if not _nonnegative_number(plan.get("tolerance_pct")) or abs(float(plan.get("tolerance_pct")) - float(tolerance_pct)) > 1e-9:
        errors.append(f"validation_plan.tolerance_pct must be {float(tolerance_pct)}")
    errors.extend(_external_aggregate_tolerance_errors(mode, plan.get("tolerance_pct")))

    window = plan.get("collection_window")
    if not isinstance(window, dict):
        errors.append("validation_plan.collection_window object missing")
    else:
        errors.extend(_collection_window_bound_errors(window, "validation_plan.collection_window"))
        window_start = window.get("since")
        start_epoch = _window_bound_to_epoch(window_start)
        if registered_epoch is not None and start_epoch is not None and registered_epoch > start_epoch:
            errors.append(f"registered_date must be on or before collection window start {window_start}")
        if not _same_window(window, corpus_summary.get("collection_window")):
            errors.append("validation_plan.collection_window must match corpus manifest window")
        if not _same_window(window, provider_summary.get("collection_window")):
            errors.append("validation_plan.collection_window must match provider export manifest window")

    min_provider_records = plan.get("min_provider_records")
    provider_count = provider_summary.get("provider_record_count")
    if not _is_nonnegative_int(min_provider_records):
        errors.append("validation_plan.min_provider_records must be a non-negative integer")
    elif _is_nonnegative_int(provider_count) and provider_count < min_provider_records:
        errors.append(f"provider_record_count {provider_count} below validation plan minimum {min_provider_records}")

    min_usage_records = plan.get("min_usage_records")
    if not _is_nonnegative_int(min_usage_records):
        errors.append("validation_plan.min_usage_records must be a non-negative integer")
    elif int(savings.get("records") or 0) < min_usage_records:
        errors.append(f"usage record count {savings.get('records')} below validation plan minimum {min_usage_records}")

    min_agreement = plan.get("min_independent_agreement")
    agreement = research.get("independent_agreement")
    if not _nonnegative_number(min_agreement) or float(min_agreement) > 1:
        errors.append("validation_plan.min_independent_agreement must be between 0 and 1")
    elif agreement is None or float(agreement) < float(min_agreement):
        errors.append(f"independent agreement {agreement} below validation plan minimum {min_agreement}")

    for field in ("outside_review_required", "buyer_feedback_required", "legal_review_required"):
        if plan.get(field) is not True:
            errors.append(f"validation_plan.{field} must be true")
    errors.extend(_kill_criteria_errors(plan.get("kill_criteria"), "validation_plan.kill_criteria"))

    summary = {
        "schema": plan.get("schema"),
        "registered_date": plan.get("registered_date"),
        "provider": plan.get("provider"),
        "reconciliation_mode": plan.get("reconciliation_mode"),
        "collection_window": plan.get("collection_window"),
        "min_provider_records": plan.get("min_provider_records"),
        "min_usage_records": plan.get("min_usage_records"),
        "min_independent_agreement": plan.get("min_independent_agreement"),
        "kill_criteria": plan.get("kill_criteria"),
    }
    return {"ok": not errors, "errors": errors, "summary": summary}


def _provider_export_manifest(path: str | Path | None,
                              input_hashes: dict,
                              provider_export: str | Path | None,
                              mode: str,
                              base_dir: str | Path | None = None,
                              *,
                              since: float | None = None,
                              until: float | None = None) -> dict:
    file_meta = _hash_file(path, base_dir)
    if file_meta is None:
        return {"ok": False, "file": None, "errors": ["provider export manifest missing"], "summary": None}
    try:
        data = _load_json(path, base_dir)
    except Exception as exc:
        return {"ok": False, "file": file_meta, "errors": [f"provider export manifest unreadable: {exc}"], "summary": None}
    if not isinstance(data, dict):
        return {"ok": False, "file": file_meta, "errors": ["provider export manifest must be an object"], "summary": None}

    errors = _artifact_binding_errors(data, input_hashes, ("provider_export", "t1_manifest"))
    if data.get("schema") != PROVIDER_EXPORT_MANIFEST_SCHEMA:
        errors.append(f"schema must be {PROVIDER_EXPORT_MANIFEST_SCHEMA}")
    errors.extend(_concrete_text_errors(data.get("provider"), "provider"))
    errors.extend(_concrete_text_errors(data.get("export_source"), "export_source"))
    errors.extend(_concrete_text_errors(data.get("export_reference"), "export_reference"))
    errors.extend(_date_errors(data.get("date")))
    if data.get("non_synthetic") is not True:
        errors.append("non_synthetic must be true")
    collection_window = data.get("collection_window")
    if not isinstance(collection_window, dict):
        errors.append("collection_window object missing")
    else:
        errors.extend(_collection_window_bound_errors(collection_window))
        errors.extend(_collection_window_errors(collection_window, since=since, until=until))
    if data.get("reconciliation_mode") != mode:
        errors.append(f"reconciliation_mode must be {mode}")
    errors.extend(_provider_export_privacy_errors(provider_export, base_dir))
    count = data.get("provider_record_count")
    if not _is_nonnegative_int(count):
        errors.append("provider_record_count must be a non-negative integer")
    elif count <= 0:
        errors.append("provider_record_count must be greater than 0")
    else:
        try:
            actual_count = _provider_export_record_count(provider_export, mode, base_dir)
        except Exception as exc:
            errors.append(f"provider export count unavailable: {exc}")
            actual_count = None
        if actual_count is not None and count != actual_count:
            errors.append(f"provider_record_count must match provider export ({actual_count})")
    try:
        actual_total = _provider_export_token_total(provider_export, mode, base_dir)
    except Exception as exc:
        errors.append(f"provider export token total unavailable: {exc}")
        actual_total = None
    if actual_total is not None and actual_total <= 0:
        errors.append("provider export token total must be greater than 0")
    summary = {
        "schema": data.get("schema"),
        "provider": data.get("provider"),
        "export_source": data.get("export_source"),
        "export_reference": data.get("export_reference"),
        "date": data.get("date"),
        "reconciliation_mode": data.get("reconciliation_mode"),
        "provider_record_count": data.get("provider_record_count"),
        "provider_token_total": actual_total,
        "collection_window": data.get("collection_window"),
    }
    return {"ok": not errors, "file": file_meta, "errors": errors, "summary": summary}


def _metered_pair(value) -> tuple[list[int] | None, str | None]:
    if not isinstance(value, list) or len(value) != 2:
        return None, "metered_tokens must be [prompt, completion]"
    if not all(isinstance(v, int) and not isinstance(v, bool) for v in value):
        return None, "metered_tokens must be integers"
    prompt, completion = value
    if prompt < 0 or completion < 0:
        return None, "metered_tokens must be non-negative"
    return [prompt, completion], None


def _receipt_ts(value) -> tuple[float | None, str | None]:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None, "ts must be a finite numeric epoch"
    ts = float(value)
    if not math.isfinite(ts):
        return None, "ts must be a finite numeric epoch"
    return ts, None


def _t1_manifest_attestation_binding(path: str | Path | None, attestation: dict | None,
                                     base_dir: str | Path | None = None) -> dict:
    file_meta = _hash_file(path, base_dir)
    if file_meta is None:
        return {"ok": False, "file": None, "errors": ["T1 manifest missing"], "summary": None}
    if not isinstance(attestation, dict):
        return {"ok": False, "file": file_meta, "errors": ["attestation unavailable"], "summary": None}
    try:
        data = _load_json(path, base_dir)
    except Exception as exc:
        return {"ok": False, "file": file_meta, "errors": [f"T1 manifest unreadable: {exc}"], "summary": None}
    if not isinstance(data, dict):
        return {"ok": False, "file": file_meta, "errors": ["T1 manifest must be an object"], "summary": None}

    errors: list[str] = []
    if data.get("schema") != T1_MANIFEST_SCHEMA:
        errors.append(f"schema must be {T1_MANIFEST_SCHEMA}")
    errors.extend(_sha256_hex_errors(data.get("source_mint_log_sha256"), "source_mint_log_sha256"))
    receipts = data.get("receipts")
    if not isinstance(receipts, list):
        receipts = []
        errors.append("receipts must be a list")
    receipt_count = data.get("receipt_count")
    if not _is_nonnegative_int(receipt_count):
        errors.append("receipt_count must be a non-negative integer")
    elif receipt_count != len(receipts):
        errors.append(f"receipt_count must match receipts ({len(receipts)})")

    att_links = attestation.get("links", [])
    if not isinstance(att_links, list):
        att_links = []
        errors.append("attestation links must be a list")
    att_t1 = [
        link for link in att_links
        if link.get("evidence_tier", "self_reported") == "provider_metered"
    ]
    att_by_pair = {
        (link.get("receipt_hash"), link.get("chain_hash")): link
        for link in att_t1
    }
    att_pairs = set(att_by_pair)
    manifest_pairs = []
    for i, rec in enumerate(receipts, 1):
        if not isinstance(rec, dict):
            errors.append(f"receipt {i}: must be an object")
            continue
        pair = (rec.get("receipt_hash"), rec.get("chain_hash"))
        manifest_metered = rec.get("metered_tokens")
        if rec.get("evidence_tier") != "provider_metered":
            errors.append(f"receipt {i}: evidence_tier must be provider_metered")
        manifest_pair, manifest_error = _metered_pair(manifest_metered)
        if manifest_error:
            errors.append(f"receipt {i}: {manifest_error}")
        manifest_ts, manifest_ts_error = _receipt_ts(rec.get("ts"))
        if manifest_ts_error:
            errors.append(f"receipt {i}: {manifest_ts_error}")
        if not pair[0] or not pair[1]:
            errors.append(f"receipt {i}: receipt_hash and chain_hash required")
            continue
        att_link = att_by_pair.get(pair)
        if att_link is None:
            errors.append(f"receipt {i}: not present in provider_metered attestation links")
        else:
            att_metered = att_link.get("metered_tokens")
            att_pair, att_error = _metered_pair(att_metered)
            if att_error:
                errors.append(f"receipt {i}: attestation link {att_error}")
            elif manifest_pair is not None and att_pair != manifest_pair:
                errors.append(f"receipt {i}: metered_tokens differ from attestation link")
            att_ts, att_ts_error = _receipt_ts(att_link.get("ts"))
            if att_ts_error:
                errors.append(f"receipt {i}: attestation link {att_ts_error}")
            elif manifest_ts is not None and abs(att_ts - manifest_ts) > 1e-6:
                errors.append(f"receipt {i}: ts differs from attestation link")
        manifest_pairs.append(pair)
    if len(set(manifest_pairs)) != len(manifest_pairs):
        errors.append("T1 manifest contains duplicate receipt_hash/chain_hash pairs")

    missing = sorted(att_pairs - set(manifest_pairs))
    if missing:
        errors.append(f"T1 manifest omits {len(missing)} provider_metered attestation link(s)")
    summary = {
        "schema": data.get("schema"),
        "source_mint_log_sha256": data.get("source_mint_log_sha256"),
        "manifest_receipts": len(receipts),
        "attestation_provider_metered_links": len(att_t1),
        "matched_links": len(att_pairs & set(manifest_pairs)),
    }
    return {"ok": not errors, "file": file_meta, "errors": errors, "summary": summary}


def _check(name: str, passed: bool, detail: str) -> dict:
    return {"name": name, "pass": bool(passed), "detail": detail}


def _gate(checks: list[dict], *, fail_names: set[str] | None = None) -> dict:
    fail_names = fail_names or set()
    failed = [c["name"] for c in checks if not c["pass"]]
    status = "PASS" if not failed else "FAIL"
    return {"status": status, "failed": failed, "checks": checks, "fail_names": sorted(fail_names)}


def _prefixed_failures(gate: dict, prefix: str) -> list[str]:
    return [f"{prefix}:{name}" for name in gate.get("failed", [])]


def _claim(claim_id: str, statement: str, status: str,
           evidence: list[str], blockers: list[str]) -> dict:
    return {
        "id": claim_id,
        "statement": statement,
        "status": status,
        "evidence": evidence,
        "blockers": blockers,
    }


def _claim_register(
    *,
    product_gate: dict,
    science_gate: dict,
    review_gate: dict,
    kill_gate: dict,
    ship_scope: str,
    production: kry_capabilities.ReadinessReport,
    research: dict,
    corpus_evidence: dict,
    provider_export_evidence: dict,
    validation_plan: dict,
    t1_binding: dict,
    corpus_provider_windows_align: bool,
    corpus: str,
) -> dict:
    product_blockers = _prefixed_failures(product_gate, "product")
    science_blockers = _prefixed_failures(science_gate, "science")
    review_blockers = _prefixed_failures(review_gate, "external_review")
    kill_blockers = [f"kill:{name}" for name in kill_gate.get("triggers", [])]
    external_blockers = product_blockers + science_blockers + review_blockers + kill_blockers
    no_hard_kill = not kill_gate.get("triggers")
    product_ok = product_gate.get("status") == "PASS" and no_hard_kill
    provider_verdict = (research.get("reconcile") or {}).get("verdict")
    t1_count = int(research.get("t1_receipts") or 0)
    t1_bound = t1_binding.get("ok") is True
    provider_ok = provider_verdict == "RECONCILED" and t1_count > 0 and t1_bound
    provider_blockers = []
    if provider_verdict != "RECONCILED" or t1_count <= 0:
        provider_blockers.append(f"provider_reconciliation:{provider_verdict or 'missing'}")
    if not t1_bound:
        provider_blockers.append("science:t1_manifest_matches_attestation")
    real_corpus_ok = (
        corpus == "real"
        and science_gate.get("status") == "PASS"
        and corpus_evidence.get("ok") is True
        and provider_export_evidence.get("ok") is True
        and validation_plan.get("ok") is True
        and corpus_provider_windows_align is True
        and t1_bound
    )
    research_ready = research.get("research_grade_reached") is True
    research_grade_ok = (
        research_ready
        and product_ok
        and science_gate.get("status") == "PASS"
        and no_hard_kill
    )
    research_grade_blockers = []
    if not research_ready:
        research_grade_blockers.extend(list(research.get("reasons") or []))
    if not product_ok:
        research_grade_blockers.extend(product_blockers + kill_blockers)
    if science_gate.get("status") != "PASS":
        research_grade_blockers.extend(science_blockers)
    production_ready = (
        production.label == "production_ready"
        and product_ok
        and science_gate.get("status") == "PASS"
        and review_gate.get("status") == "PASS"
    )

    claims = [
        _claim(
            "internal_efficiency_artifact",
            "The packet may be used as an internal efficiency artifact.",
            "allowed" if product_ok else "blocked",
            ["product_gate", "kill_gate"],
            product_blockers + kill_blockers,
        ),
        _claim(
            "external_verified_savings",
            "The packet may be described as an external verified-savings candidate.",
            "allowed" if ship_scope == "external_verified_savings_candidate" else "blocked",
            ["product_gate", "science_gate", "external_review_gate",
             "legal_review.external_claim_allowed", "kill_gate"],
            external_blockers,
        ),
        _claim(
            "provider_reconciled",
            "Provider-metered T1 receipts reconcile against a non-vacuous provider export.",
            "allowed" if provider_ok else "blocked",
            ["research_assessment.reconcile", "t1_manifest"],
            provider_blockers,
        ),
        _claim(
            "real_corpus_validated",
            "The usage corpus is real, non-synthetic, and provenance-bound.",
            "allowed" if real_corpus_ok else "blocked",
            ["corpus_manifest", "provider_export_manifest", "validation_plan"],
            ([] if real_corpus_ok else science_blockers),
        ),
        _claim(
            "research_grade_readiness",
            "The packet reaches research-grade readiness or better.",
            "allowed" if research_grade_ok else "blocked",
            ["research_assessment.independent_agreement", "readiness_label"],
            ([] if research_grade_ok else research_grade_blockers),
        ),
        _claim(
            "production_ready",
            "The packet reaches production-ready readiness.",
            "allowed" if production_ready else "blocked",
            ["production_readiness_if_claimed", "science_gate", "external_review_gate", "product_gate"],
            (
                [] if production_ready
                else list(production.reasons) + product_blockers + science_blockers + review_blockers + kill_blockers
            ),
        ),
        _claim(
            "external_review_complete",
            "Outside review, buyer feedback, and legal review are complete and bound to this packet.",
            "allowed" if review_gate.get("status") == "PASS" else "blocked",
            ["outside_review_evidence", "buyer_feedback_evidence", "legal_review_evidence", "external_review_gate"],
            review_blockers,
        ),
        _claim(
            "tradeable_token",
            "KRY may be marketed as a tradeable or transferable token.",
            "forbidden",
            ["legal_review.tradeable_token_disclaimed", "project_scope"],
            [],
        ),
    ]
    return {"schema": "kry_claim_register/v1", "claims": claims}


_CLAIM_EVIDENCE_FIELDS = {
    "product_gate": ["/gates/product", "/savings_report", "/attestation_verification"],
    "kill_gate": ["/gates/kill"],
    "science_gate": [
        "/gates/science",
        "/research_assessment",
        "/provider_export_manifest",
        "/corpus_manifest",
        "/t1_manifest_attestation_binding",
    ],
    "external_review_gate": ["/gates/external_review", "/review_evidence"],
    "outside_review_evidence": ["/review_evidence/outside_review"],
    "buyer_feedback_evidence": ["/review_evidence/buyer_feedback"],
    "legal_review_evidence": ["/review_evidence/legal_review"],
    "research_assessment.reconcile": ["/research_assessment/reconcile"],
    "t1_manifest": ["/inputs/t1_manifest", "/t1_manifest_attestation_binding"],
    "corpus_manifest": ["/corpus_manifest", "/inputs/corpus_manifest"],
    "provider_export_manifest": ["/provider_export_manifest", "/inputs/provider_export_manifest"],
    "validation_plan": ["/validation_plan"],
    "research_assessment.independent_agreement": [
        "/research_assessment/independent_agreement",
        "/research_assessment/bar",
    ],
    "readiness_label": ["/production_readiness_if_claimed"],
    "production_readiness_if_claimed": ["/production_readiness_if_claimed"],
    "review_evidence": ["/review_evidence"],
    "legal_review.external_claim_allowed": ["/review_evidence/legal_review/summary/external_claim_allowed"],
    "legal_review.tradeable_token_disclaimed": ["/review_evidence/legal_review/summary/tradeable_token_disclaimed"],
    "project_scope": ["/claim_register"],
}


def _blocker_fields(blocker: str, claim_index: int) -> list[str]:
    if blocker.startswith("product:"):
        return ["/gates/product"]
    if blocker.startswith("science:"):
        return ["/gates/science"]
    if blocker.startswith("external_review:"):
        return ["/gates/external_review"]
    if blocker.startswith("kill:"):
        return ["/gates/kill"]
    if blocker.startswith("provider_reconciliation:"):
        return ["/research_assessment/reconcile"]
    return [f"/claim_register/claims/{claim_index}/blockers"]


def _claim_evidence_manifest(claim_register: dict, *, ship_scope: str,
                             artifact_path: str = "artifact.json",
                             artifact_hash: str | None = None) -> dict:
    claims = claim_register.get("claims") if isinstance(claim_register, dict) else []
    manifest_claims = []
    for index, claim in enumerate(claims if isinstance(claims, list) else []):
        evidence = [
            {
                "id": evidence_id,
                "artifact_fields": _CLAIM_EVIDENCE_FIELDS.get(
                    evidence_id,
                    [f"/claim_register/claims/{index}/evidence"],
                ),
            }
            for evidence_id in claim.get("evidence", [])
        ]
        blockers = [
            {
                "id": blocker,
                "artifact_fields": _blocker_fields(str(blocker), index),
            }
            for blocker in claim.get("blockers", [])
        ]
        manifest_claims.append({
            "id": claim.get("id"),
            "status": claim.get("status"),
            "statement": claim.get("statement"),
            "claim_register_ref": f"/claim_register/claims/{index}",
            "evidence": evidence,
            "blockers": blockers,
        })
    return {
        "schema": CLAIM_EVIDENCE_MANIFEST_SCHEMA,
        "artifact": {"path": artifact_path, "artifact_hash": artifact_hash},
        "verify_command": f"python3 scripts/kry_verified_artifact.py --verify-artifact {artifact_path}",
        "ship_scope": ship_scope,
        "claims": manifest_claims,
    }


def _json_pointer_exists(data: object, pointer: str) -> bool:
    if pointer == "":
        return True
    if not isinstance(pointer, str) or not pointer.startswith("/"):
        return False
    current = data
    for raw_part in pointer.split("/")[1:]:
        part = raw_part.replace("~1", "/").replace("~0", "~")
        if isinstance(current, dict):
            if part not in current:
                return False
            current = current[part]
        elif isinstance(current, list):
            if not part.isdigit():
                return False
            index = int(part)
            if index >= len(current):
                return False
            current = current[index]
        else:
            return False
    return True


def _claim_evidence_manifest_errors(artifact: dict) -> list[str]:
    manifest = artifact.get("claim_evidence_manifest")
    register = artifact.get("claim_register")
    if not isinstance(manifest, dict):
        return ["claim_evidence_manifest object missing"]
    errors: list[str] = []
    if manifest.get("schema") != CLAIM_EVIDENCE_MANIFEST_SCHEMA:
        errors.append(f"claim_evidence_manifest schema must be {CLAIM_EVIDENCE_MANIFEST_SCHEMA}")
    artifact_ref = manifest.get("artifact")
    if not isinstance(artifact_ref, dict) or artifact_ref.get("path") != "artifact.json":
        errors.append("claim_evidence_manifest artifact path mismatch")
    if not isinstance(artifact_ref, dict) or artifact_ref.get("artifact_hash") != artifact.get("artifact_hash"):
        errors.append("claim_evidence_manifest artifact hash mismatch")
    if manifest.get("verify_command") != "python3 scripts/kry_verified_artifact.py --verify-artifact artifact.json":
        errors.append("claim_evidence_manifest verify_command mismatch")
    if manifest.get("ship_scope") != artifact.get("ship_scope"):
        errors.append("claim_evidence_manifest ship_scope mismatch")
    if not isinstance(register, dict) or not isinstance(register.get("claims"), list):
        errors.append("claim_register claims unavailable")
        return errors
    manifest_claims = manifest.get("claims")
    if not isinstance(manifest_claims, list):
        errors.append("claim_evidence_manifest claims must be a list")
        return errors
    register_claims = register["claims"]
    if len(manifest_claims) != len(register_claims):
        errors.append("claim_evidence_manifest claim count mismatch")
    for index, claim in enumerate(register_claims):
        if index >= len(manifest_claims):
            errors.append(f"claim_evidence_manifest missing claim {claim.get('id')}")
            continue
        manifest_claim = manifest_claims[index]
        if not isinstance(manifest_claim, dict):
            errors.append(f"claim_evidence_manifest claim {index} must be an object")
            continue
        expected_ref = f"/claim_register/claims/{index}"
        for field in ("id", "status", "statement"):
            if manifest_claim.get(field) != claim.get(field):
                errors.append(f"claim_evidence_manifest {claim.get('id')} {field} mismatch")
        if manifest_claim.get("claim_register_ref") != expected_ref:
            errors.append(f"claim_evidence_manifest {claim.get('id')} claim_register_ref mismatch")

        for list_name in ("evidence", "blockers"):
            register_ids = [str(value) for value in claim.get(list_name, [])]
            manifest_items = manifest_claim.get(list_name)
            if not isinstance(manifest_items, list):
                errors.append(f"claim_evidence_manifest {claim.get('id')} {list_name} must be a list")
                continue
            manifest_ids = [
                str(item.get("id")) if isinstance(item, dict) else None
                for item in manifest_items
            ]
            if manifest_ids != register_ids:
                errors.append(f"claim_evidence_manifest {claim.get('id')} {list_name} ids mismatch")
            for item in manifest_items:
                if not isinstance(item, dict):
                    errors.append(f"claim_evidence_manifest {claim.get('id')} {list_name} item must be an object")
                    continue
                fields = item.get("artifact_fields")
                if not isinstance(fields, list) or not fields:
                    errors.append(f"claim_evidence_manifest {claim.get('id')} {list_name}:{item.get('id')} fields missing")
                    continue
                for pointer in fields:
                    if not _json_pointer_exists(artifact, pointer):
                        errors.append(
                            f"claim_evidence_manifest {claim.get('id')} "
                            f"{list_name}:{item.get('id')} field missing: {pointer}"
                        )
    return errors


def _artifact_claims_by_id(artifact: dict) -> dict:
    register = artifact.get("claim_register")
    claims = register.get("claims") if isinstance(register, dict) else []
    return {
        claim.get("id"): claim
        for claim in claims
        if isinstance(claim, dict)
    }


def _externally_claimable_artifact(artifact: dict) -> bool:
    if artifact.get("ship_scope") == "external_verified_savings_candidate":
        return True
    claim_allowed = artifact.get("claim_allowed")
    if isinstance(claim_allowed, dict) and claim_allowed.get("external_verified_savings") is True:
        return True
    external_claim = _artifact_claims_by_id(artifact).get("external_verified_savings")
    return isinstance(external_claim, dict) and external_claim.get("status") == "allowed"


def _command_input_portability_errors(artifact_path: Path, artifact: dict) -> list[str]:
    if not _externally_claimable_artifact(artifact):
        return []
    command_inputs = artifact.get("command_inputs")
    if not isinstance(command_inputs, dict):
        return []
    errors: list[str] = []
    packet_dir = artifact_path.parent.resolve()
    for key in ARTIFACT_PATH_INPUTS:
        value = command_inputs.get(key)
        if value is None:
            continue
        if key == "mint_log":
            errors.append("command_inputs.mint_log must be absent for external candidates; use t1_manifest")
            continue
        if not isinstance(value, str) or not value:
            errors.append(f"command_inputs.{key} must be a relative packet path")
            continue
        input_path = Path(value)
        if input_path.is_absolute():
            errors.append(f"command_inputs.{key} must be relative, got absolute path")
            continue
        resolved = (packet_dir / input_path).resolve()
        try:
            resolved.relative_to(packet_dir)
        except ValueError:
            errors.append(f"command_inputs.{key} escapes artifact directory")
    return errors


def _command_input_containment_errors(artifact_path: Path, command_inputs: object) -> list[str]:
    """UNGATED path-containment for verification. Every command_inputs file MUST be a relative
    path that stays inside the artifact's bundle directory — enforced for ALL artifacts (NOT just
    externally-claimable) and BEFORE any read, so verifying an UNTRUSTED artifact cannot be tricked
    into reading out-of-bundle files (/etc/..., ../secret). Operator-side BUILD is unaffected: it
    resolves real CLI paths, never an artifact's self-declared inputs."""
    errors: list[str] = []
    if not isinstance(command_inputs, dict):
        return errors
    bundle = artifact_path.parent.resolve()
    for key in ARTIFACT_PATH_INPUTS:
        value = command_inputs.get(key)
        if value is None:
            continue
        if not isinstance(value, str) or not value:
            errors.append(f"command_inputs.{key} must be a relative bundle path")
            continue
        if Path(value).is_absolute():
            errors.append(f"command_inputs.{key}: absolute path not allowed — must stay in the bundle")
            continue
        resolved = (bundle / value).resolve()
        try:
            resolved.relative_to(bundle)
        except ValueError:
            errors.append(f"command_inputs.{key}: path escapes the bundle directory ({value!r})")
    return errors


def _packet_surface_errors(artifact_path: Path, artifact: dict) -> list[str]:
    if not _externally_claimable_artifact(artifact):
        return []
    errors: list[str] = []
    if artifact_path.name != "artifact.json":
        errors.append("externally claimable packet artifact must be named artifact.json")
    packet_dir = artifact_path.parent.resolve()
    expected_files = {artifact_path.name, "reviewer_checklist.json", "finops_report.md"}
    command_inputs = artifact.get("command_inputs")
    if isinstance(command_inputs, dict):
        for key in ARTIFACT_PATH_INPUTS:
            value = command_inputs.get(key)
            if key == "mint_log" or not isinstance(value, str) or not value:
                continue
            input_path = Path(value)
            if input_path.is_absolute():
                continue
            resolved = (packet_dir / input_path).resolve()
            try:
                expected_files.add(resolved.relative_to(packet_dir).as_posix())
            except ValueError:
                continue
    expected_dirs = {
        parent.as_posix()
        for rel in expected_files
        for parent in Path(rel).parents
        if parent.as_posix() not in (".", "")
    }
    private_files = []
    for candidate in artifact_path.parent.rglob("*"):
        rel = candidate.relative_to(artifact_path.parent).as_posix()
        name = candidate.name.lower()
        if candidate.is_symlink():
            errors.append(f"packet contains symlink: {rel}")
            continue
        if name in PRIVATE_PACKET_NAMES or any(fragment in name for fragment in PRIVATE_PACKET_NAME_FRAGMENTS):
            private_files.append(rel)
        if candidate.is_dir():
            if rel not in expected_dirs:
                errors.append(f"packet contains unbound directory: {rel}")
            continue
        if not candidate.is_file():
            errors.append(f"packet contains non-regular entry: {rel}")
            continue
        if rel not in expected_files:
            errors.append(f"packet contains unbound file: {rel}")
    if private_files:
        errors.append("private ledger/mint-log material present in packet: " + ", ".join(sorted(private_files)))
    if isinstance(command_inputs, dict):
        for key in PUBLIC_PACKET_JSON_INPUTS:
            value = command_inputs.get(key)
            if not isinstance(value, str) or not value:
                continue
            input_path = Path(value)
            if input_path.is_absolute():
                continue
            resolved = (packet_dir / input_path).resolve()
            try:
                resolved.relative_to(packet_dir)
            except ValueError:
                continue
            errors.extend(_public_packet_json_privacy_errors(resolved, key))

    report_path = artifact_path.parent / "finops_report.md"
    if not report_path.is_file():
        errors.append("finops_report.md missing from packet; bundle mode should generate it")
    else:
        try:
            actual_report = report_path.read_text(encoding="utf-8")
            expected_report = _render_finops_report(artifact_path)
            if not expected_report.endswith("\n"):
                expected_report += "\n"
        except Exception as exc:
            errors.append(f"cannot verify finops_report.md: {exc}")
        else:
            if actual_report != expected_report:
                errors.append("finops_report.md does not match artifact.json")

    checklist_path = artifact_path.parent / "reviewer_checklist.json"
    if not checklist_path.is_file():
        errors.append("reviewer_checklist.json missing from packet; bundle mode should generate it")
        return errors
    try:
        actual = _load_json(checklist_path)
    except Exception as exc:
        errors.append(f"cannot read reviewer_checklist.json: {exc}")
        return errors
    review_basis = artifact.get("review_basis") if isinstance(artifact, dict) else {}
    if not isinstance(review_basis, dict):
        review_basis = {}
    base_inputs = dict(review_basis.get("inputs") or {})
    base_inputs["review_basis_sha256"] = review_basis.get("sha256")
    expected = _reviewer_checklist(
        base_inputs,
        artifact_path=artifact_path.name,
        artifact_hash=artifact.get("artifact_hash"),
    )
    if actual != expected:
        errors.append("reviewer_checklist.json does not match artifact.json")
    return errors


def _near(a: float | None, b: float | None, *, tol: float = 0.02) -> bool:
    if a is None or b is None:
        return False
    return math.isfinite(a) and math.isfinite(b) and abs(a - b) <= tol


def _savings_event_counts(savings: dict) -> dict:
    by_kind = savings.get("by_kind", {})
    expected = {
        "cache_hit": int(by_kind.get("cache_hit") or 0),
        "short_circuit": int(by_kind.get("displacement") or 0),
    }
    return {k: v for k, v in expected.items() if v}


def _attestation_verification(attestation_path: str | None,
                              base_dir: str | Path | None = None) -> tuple[dict | None, dict]:
    if not attestation_path:
        return None, {"ok": False, "errors": ["attestation path missing"]}
    try:
        att = _load_json(attestation_path, base_dir)
    except Exception as exc:
        return None, {"ok": False, "errors": [f"attestation unreadable: {exc}"]}
    ok, errors = kry_verify.verify_attestation(att)
    return att, {"ok": ok, "errors": errors}


def _research_assessment(
    mint_log: str | None,
    t1_manifest: str | None,
    provider_export: str | None,
    *,
    mode: str,
    tolerance: int,
    tolerance_pct: float,
    since: float | None,
    until: float | None,
    replay_pass_rate: float,
    base_dir: str | Path | None = None,
) -> dict:
    if not provider_export:
        grade = kry_capabilities.readiness_label(
            replay_pass_rate=replay_pass_rate,
            independent_agreement=None,
            real_corpus_validated=False,
        )
        return {
            "provider_export": None,
            "mint_log": _hash_file(mint_log, base_dir),
            "t1_manifest": _hash_file(t1_manifest, base_dir),
            "reconciliation_source": "t1_manifest" if t1_manifest else "mint_log" if mint_log else None,
            "independent_agreement": None,
            "bar": kry_capabilities.INDEPENDENT_AGREEMENT_BAR,
            "readiness_label": grade.label,
            "reasons": grade.reasons,
            "reconcile": {"verdict": "NO_ORACLE", "reconciled_fraction": None},
        }
    reconciliation_source = t1_manifest or mint_log
    source_kind = "t1_manifest" if t1_manifest else "mint_log" if mint_log else None
    resolved_source = _resolve_path(reconciliation_source, base_dir)
    if not resolved_source or not resolved_source.exists():
        return {
            "provider_export": _hash_file(provider_export, base_dir),
            "mint_log": None,
            "t1_manifest": _hash_file(t1_manifest, base_dir),
            "reconciliation_source": source_kind,
            "independent_agreement": None,
            "bar": kry_capabilities.INDEPENDENT_AGREEMENT_BAR,
            "readiness_label": "internally_consistent",
            "reasons": ["provider export supplied, but T1 reconciliation source is missing"],
            "reconcile": {"verdict": "RECONCILIATION_SOURCE_MISSING", "reconciled_fraction": None},
        }
    try:
        export = _load_json(provider_export, base_dir)
        assessed = kry_research_grade.assess(
            str(resolved_source),
            provider_export=export,
            mode=mode,
            tolerance=tolerance,
            tolerance_pct=tolerance_pct,
            since=since,
            until=until,
            replay_pass_rate=replay_pass_rate,
        )
    except Exception as exc:
        return {
            "provider_export": _hash_file(provider_export, base_dir),
            "mint_log": _hash_file(mint_log, base_dir),
            "t1_manifest": _hash_file(t1_manifest, base_dir),
            "reconciliation_source": source_kind,
            "independent_agreement": None,
            "bar": kry_capabilities.INDEPENDENT_AGREEMENT_BAR,
            "readiness_label": "internally_consistent",
            "reasons": [f"research assessment failed: {exc}"],
            "reconcile": {"verdict": "ASSESSMENT_ERROR", "reconciled_fraction": None},
        }
    assessed["provider_export"] = _hash_file(provider_export, base_dir)
    assessed["mint_log"] = _hash_file(mint_log, base_dir)
    assessed["t1_manifest"] = _hash_file(t1_manifest, base_dir)
    assessed["reconciliation_source"] = source_kind
    return assessed


def build_artifact(
    usage_log: str,
    *,
    attestation: str | None,
    mint_log: str | None = None,
    t1_manifest: str | None = None,
    provider_export: str | None = None,
    provider_export_manifest: str | None = None,
    corpus: str = "synthetic",
    corpus_manifest: str | None = None,
    outside_review: str | None = None,
    buyer_feedback: str | None = None,
    legal_review: str | None = None,
    mode: str = "per-request",
    tolerance: int = 0,
    tolerance_pct: float = 5.0,
    since: float | None = None,
    until: float | None = None,
    replay_pass_rate: float = 1.0,
    base_dir: str | Path | None = None,
) -> dict:
    tool_manifest = _tool_manifest()
    input_hashes = {
        "usage_log": _hash_file(usage_log, base_dir),
        "attestation": _hash_file(attestation, base_dir),
        "mint_log": _hash_file(mint_log, base_dir),
        "t1_manifest": _hash_file(t1_manifest, base_dir),
        "provider_export": _hash_file(provider_export, base_dir),
        "provider_export_manifest": _hash_file(provider_export_manifest, base_dir),
        "corpus_manifest": _hash_file(corpus_manifest, base_dir),
        "tool_manifest": {"sha256": tool_manifest["sha256"]},
    }
    review_basis = _review_basis(
        input_hashes,
        corpus=corpus,
        mode=mode,
        tolerance=tolerance,
        tolerance_pct=tolerance_pct,
        since=since,
        until=until,
        replay_pass_rate=replay_pass_rate,
    )
    input_hashes["review_basis"] = {"sha256": review_basis["sha256"]}
    records = kry_savings_report._load_records(str(_resolve_path(usage_log, base_dir)))
    savings = kry_savings_report.analyze(records)
    usage_log_privacy_errors = _usage_log_privacy_errors(records)
    att, att_verify = _attestation_verification(attestation, base_dir)
    research = _research_assessment(
        mint_log,
        t1_manifest,
        provider_export,
        mode=mode,
        tolerance=tolerance,
        tolerance_pct=tolerance_pct,
        since=since,
        until=until,
        replay_pass_rate=replay_pass_rate,
        base_dir=base_dir,
    )

    att_total = float(att.get("total_kry")) if isinstance(att, dict) and att.get("total_kry") is not None else None
    att_floor = None
    if isinstance(att, dict) and isinstance(att.get("veracity"), dict):
        att_floor = att["veracity"].get("veracity_floor")
    product_checks = [
        _check("usage_log_has_records", savings["records"] > 0, f"{savings['records']} normalized records"),
        _check("positive_savings", savings["saved_kry"] > 0, f"{savings['saved_kry']} KRY saved"),
        _check("attestation_verifies", att_verify["ok"], "; ".join(att_verify["errors"]) or "stdlib verifier accepted"),
        _check("attestation_matches_report_total", _near(att_total, savings["saved_kry"]),
               f"attestation total {att_total} vs report {savings['saved_kry']}"),
        _check("attestation_matches_report_floor", _near(att_floor, savings["veracity"]["veracity_floor"], tol=0.0001),
               f"attestation floor {att_floor} vs report {savings['veracity']['veracity_floor']}"),
        _check("attestation_matches_report_event_counts",
               isinstance(att, dict) and att.get("event_type_counts") == _savings_event_counts(savings),
               f"attestation {att.get('event_type_counts') if isinstance(att, dict) else None} "
               f"vs report {_savings_event_counts(savings)}"),
    ]

    agreement = research.get("independent_agreement")
    bar = research.get("bar", kry_capabilities.INDEPENDENT_AGREEMENT_BAR)
    agreement_pass = agreement is not None and agreement >= bar
    t1_count = int(research.get("t1_receipts") or 0)
    t1_manifest_supplied = input_hashes["t1_manifest"] is not None
    t1_binding = _t1_manifest_attestation_binding(t1_manifest, att, base_dir)
    provider_export_evidence = _provider_export_manifest(
        provider_export_manifest,
        input_hashes,
        provider_export,
        mode,
        base_dir,
        since=since,
        until=until,
    )
    corpus_evidence = _corpus_manifest(corpus_manifest, input_hashes, savings, base_dir)
    windows_ok, windows_detail = _collection_windows_aligned(corpus_evidence, provider_export_evidence)
    aggregate_window_errors = _aggregate_reconciliation_window_errors(mode, since, until)
    aggregate_tolerance_errors = _external_aggregate_tolerance_errors(mode, tolerance_pct)
    validation_plan = _validation_plan(
        corpus_evidence,
        provider_export_evidence,
        research,
        savings,
        mode=mode,
        tolerance=tolerance,
        tolerance_pct=tolerance_pct,
    )
    real_corpus = (
        corpus == "real"
        and corpus_evidence["ok"] is True
        and validation_plan["ok"] is True
        and provider_export_evidence["ok"] is True
        and windows_ok is True
        and t1_binding["ok"] is True
        and not usage_log_privacy_errors
    )
    production = kry_capabilities.readiness_label(
        replay_pass_rate=replay_pass_rate,
        independent_agreement=agreement,
        real_corpus_validated=real_corpus,
        audit_clean=None,
    )
    bundled_sample_declared_real = corpus == "real" and _is_bundled_sample_usage_log(usage_log, base_dir)
    science_checks = [
        _check("corpus_declared_real", corpus == "real", f"corpus={corpus}"),
        _check(
            "real_corpus_not_bundled_sample",
            not bundled_sample_declared_real,
            (
                "examples/sample_usage_log.jsonl is synthetic and cannot support --corpus real"
                if bundled_sample_declared_real
                else "usage log is not the bundled synthetic sample"
            ),
        ),
        _check("usage_log_public_packet_safe", not usage_log_privacy_errors,
               "; ".join(usage_log_privacy_errors) or "usage log excludes private prompt/message/body fields"),
        _check("corpus_manifest_valid", corpus_evidence["ok"],
               "; ".join(corpus_evidence["errors"]) or "real corpus manifest valid"),
        _check("validation_plan_valid", validation_plan["ok"],
               "; ".join(validation_plan["errors"]) or "pre-registered validation plan valid"),
        _check("provider_export_supplied", provider_export is not None, "provider export present" if provider_export else "missing"),
        _check("provider_export_manifest_valid", provider_export_evidence["ok"],
               "; ".join(provider_export_evidence["errors"]) or "provider export provenance valid"),
        _check("corpus_provider_windows_align", windows_ok, windows_detail),
        _check("aggregate_reconciliation_window_applied", not aggregate_window_errors,
               "; ".join(aggregate_window_errors) or "aggregate reconciliation is window-filtered or not aggregate mode"),
        _check("aggregate_reconciliation_tolerance_within_threshold", not aggregate_tolerance_errors,
               "; ".join(aggregate_tolerance_errors) or "aggregate reconciliation tolerance is within the <=2% threshold"),
        _check("t1_manifest_supplied", t1_manifest_supplied,
               "shareable T1 manifest present" if t1_manifest_supplied else "missing shareable T1 manifest"),
        _check("t1_manifest_matches_attestation", t1_binding["ok"],
               "; ".join(t1_binding["errors"]) or "T1 manifest covers provider_metered attestation links"),
        _check("provider_oracle_non_vacuous", t1_count > 0, f"T1 receipts={t1_count}"),
        _check("independent_agreement_at_bar", agreement_pass,
               f"agreement={agreement} bar={bar}"),
        _check("readiness_can_reach_production", production.label == "production_ready",
               f"readiness={production.label}"),
    ]

    review_not_before = _latest_date_label(
        (corpus_evidence.get("summary") or {}).get("date"),
        (provider_export_evidence.get("summary") or {}).get("date"),
    )
    review = {
        "outside_review": _review_evidence(
            outside_review, "outside_review", input_hashes, base_dir,
            not_before_date=review_not_before,
        ),
        "buyer_feedback": _review_evidence(
            buyer_feedback, "buyer_feedback", input_hashes, base_dir,
            not_before_date=review_not_before,
        ),
        "legal_review": _review_evidence(
            legal_review, "legal_review", input_hashes, base_dir,
            not_before_date=review_not_before,
        ),
    }
    review_channel_errors = _review_channel_separation_errors(review)
    review_checks = [
        _check("outside_review_valid", review["outside_review"]["ok"],
               "; ".join(review["outside_review"]["errors"]) or "outside review evidence valid"),
        _check("buyer_feedback_valid", review["buyer_feedback"]["ok"],
               "; ".join(review["buyer_feedback"]["errors"]) or "buyer feedback evidence valid"),
        _check("legal_review_valid", review["legal_review"]["ok"],
               "; ".join(review["legal_review"]["errors"]) or "legal/claims evidence valid"),
        _check("review_channels_distinct", not review_channel_errors,
               "; ".join(review_channel_errors) or "review, buyer, and legal channels are distinct"),
    ]

    hard_kills: list[str] = []
    if not att_verify["ok"]:
        hard_kills.append("attestation_failed")
    if savings["saved_kry"] <= 0:
        hard_kills.append("no_positive_savings")
    verdict = research.get("reconcile", {}).get("verdict")
    if verdict in {"DISCREPANCY", "ASSESSMENT_ERROR", "MINT_LOG_MISSING", "RECONCILIATION_SOURCE_MISSING"}:
        hard_kills.append(f"reconciliation_{verdict.lower()}")
    if t1_manifest_supplied and not t1_binding["ok"]:
        hard_kills.append("t1_manifest_not_attested")
    if agreement is not None and agreement < bar:
        hard_kills.append("independent_agreement_below_bar")

    external_blockers = []
    for gate_name, checks in {
        "product": product_checks,
        "science": science_checks,
        "external_review": review_checks,
    }.items():
        external_blockers.extend(f"{gate_name}:{c['name']}" for c in checks if not c["pass"])
    external_blockers.extend(f"kill:{name}" for name in hard_kills)

    product_gate = _gate(product_checks)
    science_gate = _gate(science_checks)
    review_gate = _gate(review_checks)
    kill_gate = {
        "status": "TRIGGERED" if hard_kills else "CLEAR",
        "triggers": hard_kills,
    }
    if hard_kills or product_gate["status"] != "PASS":
        ship_scope = "do_not_ship"
    elif science_gate["status"] == "PASS" and review_gate["status"] == "PASS":
        ship_scope = "external_verified_savings_candidate"
    else:
        ship_scope = "internal_or_demo_only"
    claim_register = _claim_register(
        product_gate=product_gate,
        science_gate=science_gate,
        review_gate=review_gate,
        kill_gate=kill_gate,
        ship_scope=ship_scope,
        production=production,
        research=research,
        corpus_evidence=corpus_evidence,
        provider_export_evidence=provider_export_evidence,
        validation_plan=validation_plan,
        t1_binding=t1_binding,
        corpus_provider_windows_align=windows_ok,
        corpus=corpus,
    )
    claim_allowed = {
        "internal_efficiency_artifact": (
            next(c for c in claim_register["claims"] if c["id"] == "internal_efficiency_artifact")["status"] == "allowed"
        ),
        "external_verified_savings": (
            next(c for c in claim_register["claims"] if c["id"] == "external_verified_savings")["status"] == "allowed"
        ),
    }

    artifact = {
        "schema": "kry_verified_savings_artifact/v1",
        "artifact_hash": "",
        "command_inputs": {
            "usage_log": usage_log,
            "attestation": attestation,
            "mint_log": mint_log,
            "t1_manifest": t1_manifest,
            "provider_export": provider_export,
            "provider_export_manifest": provider_export_manifest,
            "corpus": corpus,
            "corpus_manifest": corpus_manifest,
            "outside_review": outside_review,
            "buyer_feedback": buyer_feedback,
            "legal_review": legal_review,
            "mode": mode,
            "tolerance": tolerance,
            "tolerance_pct": tolerance_pct,
            "since": since,
            "until": until,
            "replay_pass_rate": replay_pass_rate,
        },
        "inputs": {**input_hashes, "corpus": corpus},
        "savings_report": savings,
        "attestation_verification": att_verify,
        "research_assessment": research,
        "t1_manifest_attestation_binding": t1_binding,
        "provider_export_manifest": provider_export_evidence,
        "tool_manifest": tool_manifest,
        "production_readiness_if_claimed": {
            "label": production.label,
            "reasons": production.reasons,
            "audit_clean": production.audit_clean,
        },
        "corpus_manifest": corpus_evidence,
        "validation_plan": validation_plan,
        "review_basis": review_basis,
        "review_evidence": review,
        "gates": {
            "product": product_gate,
            "science": science_gate,
            "external_review": review_gate,
            "kill": kill_gate,
        },
        "external_blockers": external_blockers,
        "ship_scope": ship_scope,
        "claim_register": claim_register,
        "claim_evidence_manifest": _claim_evidence_manifest(claim_register, ship_scope=ship_scope),
        "claim_allowed": claim_allowed,
    }
    artifact["artifact_hash"] = _artifact_hash(artifact)
    artifact["claim_evidence_manifest"]["artifact"]["artifact_hash"] = artifact["artifact_hash"]
    return artifact


def verify_artifact_file(path: str | Path, *, require_packet_surfaces: bool = True,
                         trust_local_inputs: bool = False) -> dict:
    artifact_path = Path(path)
    try:
        saved = _load_json(artifact_path)
    except Exception as exc:
        return {"ok": False, "errors": [f"artifact unreadable: {exc}"]}
    if not isinstance(saved, dict):
        return {"ok": False, "errors": ["artifact JSON must be an object"]}
    errors: list[str] = []
    if saved.get("schema") != "kry_verified_savings_artifact/v1":
        errors.append("schema mismatch")
    expected_hash = _artifact_hash(saved)
    if saved.get("artifact_hash") != expected_hash:
        errors.append("artifact_hash mismatch")
    cmd = saved.get("command_inputs")
    if not isinstance(cmd, dict):
        errors.append("command_inputs missing")
        return {"ok": False, "errors": errors}
    errors.extend(_command_input_portability_errors(artifact_path, saved))
    if require_packet_surfaces:
        errors.extend(_packet_surface_errors(artifact_path, saved))
    # Path containment BEFORE any rebuild read: by default an artifact being verified is UNTRUSTED,
    # so its command_inputs may not point outside its own bundle (absolute or ../) — fail closed so
    # the read never happens (closes arbitrary-file-read via `kry_doctor --artifact`). The operator
    # re-verifying their OWN non-bundled packet opts in with trust_local_inputs=True.
    if not trust_local_inputs:
        containment = _command_input_containment_errors(artifact_path, cmd)
        if containment:
            return {"ok": False, "errors": errors + containment}
    try:
        rebuilt = build_artifact(
            cmd["usage_log"],
            attestation=cmd.get("attestation"),
            mint_log=cmd.get("mint_log"),
            t1_manifest=cmd.get("t1_manifest"),
            provider_export=cmd.get("provider_export"),
            provider_export_manifest=cmd.get("provider_export_manifest"),
            corpus=cmd.get("corpus", "synthetic"),
            corpus_manifest=cmd.get("corpus_manifest"),
            outside_review=cmd.get("outside_review"),
            buyer_feedback=cmd.get("buyer_feedback"),
            legal_review=cmd.get("legal_review"),
            mode=cmd.get("mode", "per-request"),
            tolerance=int(cmd.get("tolerance", 0)),
            tolerance_pct=float(cmd.get("tolerance_pct", 5.0)),
            since=cmd.get("since"),
            until=cmd.get("until"),
            replay_pass_rate=float(cmd.get("replay_pass_rate", 1.0)),
            base_dir=artifact_path.parent,
        )
    except Exception as exc:
        errors.append(f"rebuild failed: {exc}")
        return {"ok": False, "errors": errors}
    if _artifact_compare_body(saved) != _artifact_compare_body(rebuilt):
        errors.append("artifact body does not match recomputed gates")
    errors.extend(_claim_evidence_manifest_errors(saved))
    return {
        "ok": not errors,
        "errors": errors,
        "artifact_hash": saved.get("artifact_hash"),
        "recomputed_artifact_hash": rebuilt.get("artifact_hash"),
        "ship_scope": rebuilt.get("ship_scope"),
        "claim_allowed": rebuilt.get("claim_allowed"),
        "claim_register": rebuilt.get("claim_register"),
    }


def _copy_bundle_file(src: str | Path | None, bundle_dir: Path, name: str) -> str | None:
    if not src:
        return None
    dest = bundle_dir / name
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)
    return name


def _bundle_input_privacy_errors(
    usage_log: str | Path,
    provider_export: str | Path | None = None,
    *,
    attestation: str | Path | None = None,
    t1_manifest: str | Path | None = None,
    provider_export_manifest: str | Path | None = None,
    corpus_manifest: str | Path | None = None,
    outside_review: str | Path | None = None,
    buyer_feedback: str | Path | None = None,
    legal_review: str | Path | None = None,
) -> list[str]:
    errors: list[str] = []
    try:
        records = kry_savings_report._load_records(str(usage_log))
    except Exception as exc:
        errors.append(f"usage log privacy scan unavailable: {exc}")
    else:
        errors.extend(_usage_log_privacy_errors(records))
    errors.extend(_provider_export_privacy_errors(provider_export))
    for label, path in (
        ("attestation", attestation),
        ("t1_manifest", t1_manifest),
        ("provider_export_manifest", provider_export_manifest),
        ("corpus_manifest", corpus_manifest),
    ):
        errors.extend(_public_packet_json_privacy_errors(path, label))
    for kind, path in (
        ("outside_review", outside_review),
        ("buyer_feedback", buyer_feedback),
        ("legal_review", legal_review),
    ):
        errors.extend(_review_evidence_file_privacy_errors(path, kind))
    return errors


def _render_finops_report(artifact_path: Path) -> str:
    report_tool = _ROOT / "scripts" / "kry_finops_report.py"
    spec = importlib.util.spec_from_file_location("kry_finops_report_for_bundle", report_tool)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load scripts/kry_finops_report.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    report = mod.build_report(
        artifact_path,
        display_artifact_path="artifact.json",
        require_packet_surfaces=False,
    )
    if not report.get("ok"):
        errors = "; ".join(report.get("errors") or ["artifact verification failed"])
        raise RuntimeError(f"finops report generation failed: {errors}")
    return mod.render_markdown(report)


def _template_sha(path: str | Path | None) -> str:
    meta = _hash_file(path)
    return meta["sha256"] if meta else "TODO_SHA256"


_BUYER_LOCAL_PRIVACY_BOUNDARY = """- Do not return prompts, completions, message content, raw request bodies, or raw response bodies.
- Return provider-authoritative cost/usage data, request/gateway metadata, hashes, row counts, and gate statuses only."""

_BUYER_LOCAL_EVIDENCE_GATES = """- Named proof-required reader or intended user for the savings evidence.
- Provider bill, provider usage export, or AWS CUR plus gateway/request metadata for the same window.
- Accepted measured/projected baseline before analysis.
- Budget authority, customer authority, procurement authority, board/audit reader, or gainshare authority.
- Seven-day sample target that can reconcile provider-authoritative cost within <=2% after approved exclusions.
- Materiality target: >=10% avoidable spend or >=$5k/month plausible realized savings path."""

_BUYER_THRESHOLD_CONTEXT = """- Concrete proof-required reader or intended user.
- Provider bill, provider usage export, or AWS CUR reference.
- Request/gateway metadata reference without prompts, completions, raw messages, or raw bodies.
- Accepted measured/projected baseline reference.
- Budget, customer, procurement, board, audit, or gainshare authority basis.
- Quality/SLO boundary for usable savings.
- Materiality basis for >=10% avoidable spend or >=$5k/month.
- Seven-day or supplied sample-window reference."""

_LEGAL_CLAIM_CHECKS = """- External claim text matches the claim register and current artifact ship_scope.
- Retained-dollars language is approved or limited by counsel.
- Credit, settlement, and routing-permission wording is approved or limited by counsel.
- Carbon or environmental wording is approved, limited, or explicitly absent.
- Tradeable-token language is disclaimed.
- Non-transferable/internal-use scope is preserved.
- Legal/accounting/tax/security limitations are recorded in the review evidence."""


def _bullet_items(text: str) -> list[str]:
    return [
        line.removeprefix("- ").strip()
        for line in text.splitlines()
        if line.strip()
    ]


def _request_briefs(base_inputs: dict, *, review_basis_sha: str) -> dict[str, str]:
    hash_lines = "\n".join(
        f"- {name}: {value}"
        for name, value in sorted(base_inputs.items())
    )
    verify_steps = (
        "1. Run `python3 scripts/kry_verified_artifact.py --verify-artifact packet/artifact.json`.\n"
        "2. Run `python3 scripts/kry_doctor.py --artifact packet/artifact.json`.\n"
        "3. Reject the request if either command fails, if `ship_scope` is `do_not_ship`, "
        "or if the packet makes claims outside `claim_register`.\n"
    )
    return {
        "provider_export_request.md": f"""# KRY Provider Export Request

Purpose: obtain a real provider usage or billing export for the same window as the KRY usage log.

This request is not evidence by itself. Do not mark the packet externally validated until a real provider export and a completed `kry_provider_export_manifest/v1` are supplied.

Required return files:
- `provider_export.json`: provider usage or billing export for the requested window.
- `provider_export_manifest.json`: completed `kry_provider_export_manifest/v1` replacing the template schema.

Required provenance fields: `provider`, `export_source`, and `export_reference`.

Buyer-local privacy boundary:
{_BUYER_LOCAL_PRIVACY_BOUNDARY}

Buyer-local evidence gates to preserve:
{_BUYER_LOCAL_EVIDENCE_GATES}

Hash bindings to preserve:
{hash_lines}

Review basis SHA-256: {review_basis_sha}
""",
        "external_review_request.md": f"""# KRY Outside Review Request

Purpose: ask an independent reviewer to verify the packet, claim register, gates, and reproduced commands.

This request is not review evidence. Return `outside_review.json` with schema `kry_external_evidence/v1` only after independent verification is complete.

Reviewer steps:
{verify_steps}
Required verdict values for a passing outside review: `verified`, `pass`, or `accepted`.
Required provenance fields: `evidence_source` and `evidence_reference`.
Required claim scope: `reviewed_claims` must include `external_verified_savings`.
Required reviewer check object: `reviewer_artifact_checks` with every verifier/checklist flag set to `true`.
Required command-output object: `reviewer_command_outputs` with verifier/report/claim flags true and error/fail counts set to JSON number `0`.
Required revocation status: set `revocation_or_void_status_checked=true` and `no_invalid_revoked_or_voided_mints_known=true`.

Hash bindings to preserve:
{hash_lines}
""",
        "buyer_feedback_request.md": f"""# KRY Buyer Feedback Request

Purpose: ask a qualified FinOps, platform, or infrastructure buyer whether the verified-savings packet is commercially useful.

This request is not buyer feedback. Return `buyer_feedback.json` with schema `kry_external_evidence/v1` only after actual buyer feedback is obtained.

Buyer review steps:
{verify_steps}
Required verdict values for passing buyer feedback: `qualified_interest`, `pilot`, `paid_trial`, `pass`, or `accepted`.
Required identity/provenance fields: `buyer`, `buyer_role`, `evidence_source`, and `evidence_reference`.
Required claim scope: `reviewed_claims` must include `external_verified_savings`.
Required buyer-local gate object: `buyer_local_evidence_gates` with every threshold flag set to `true`.
Required threshold context object: `buyer_threshold_context` with concrete buyer-local reader, data, baseline, authority, quality, materiality, and sample-window references.
Required materiality object: `buyer_materiality` with `avoidable_spend_pct >= 10` or `plausible_monthly_savings_usd >= 5000`.

Buyer-local privacy boundary:
{_BUYER_LOCAL_PRIVACY_BOUNDARY}

Buyer-local evidence gates to preserve:
{_BUYER_LOCAL_EVIDENCE_GATES}

Hash bindings to preserve:
{hash_lines}
""",
        "legal_review_request.md": f"""# KRY Legal/Claims Review Request

Purpose: ask legal or claims counsel whether the packet wording may be used externally and whether tradeable-token language is disclaimed.

This request is not legal review. Return `legal_review.json` with schema `kry_external_evidence/v1` only after legal/claims review is complete.

Legal review steps:
{verify_steps}
Required passing fields:
- `verdict`: `approved`, `approved_with_limits`, or `pass`
- `external_claim_allowed`: `true`
- `tradeable_token_disclaimed`: `true`
- `legal_claim_checks`: every legal/claims surface flag set to `true`
- `legal_limitations`: non-empty list of approved limits or "none beyond claim register"
- `evidence_source` and `evidence_reference`: non-empty
- `reviewed_claims`: includes `external_verified_savings` and `tradeable_token`

Legal/claims surfaces to preserve:
{_LEGAL_CLAIM_CHECKS}

Hash bindings to preserve:
{hash_lines}
""",
    }


def write_evidence_templates(
    template_dir: str | Path,
    usage_log: str,
    *,
    attestation: str | None = None,
    provider_export: str | None = None,
    provider_export_manifest: str | None = None,
    corpus_manifest: str | None = None,
    t1_manifest: str | None = None,
    corpus: str = "synthetic",
    mode: str = "per-request",
    tolerance: int = 0,
    tolerance_pct: float = 5.0,
    since: float | None = None,
    until: float | None = None,
    replay_pass_rate: float = 1.0,
) -> dict:
    """Write non-passing evidence request templates with current input hashes.

    The templates deliberately use *_template/v1 schemas so they cannot be mistaken
    for completed evidence. Reviewers must fill the requested fields and switch to
    the live schema names documented in KRY_VERIFIED_SAVINGS_ARTIFACT.md.
    """
    out_dir = Path(template_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    usage_sha = _template_sha(usage_log)
    att_sha = _template_sha(attestation)
    provider_sha = _template_sha(provider_export)
    provider_manifest_sha = _template_sha(provider_export_manifest)
    corpus_sha = _template_sha(corpus_manifest)
    t1_sha = _template_sha(t1_manifest)
    tool_manifest = _tool_manifest()
    template_hashes = {
        "usage_log": {"sha256": usage_sha},
        "attestation": {"sha256": att_sha},
        "provider_export": {"sha256": provider_sha},
        "provider_export_manifest": {"sha256": provider_manifest_sha},
        "corpus_manifest": {"sha256": corpus_sha},
        "t1_manifest": {"sha256": t1_sha},
        "tool_manifest": {"sha256": tool_manifest["sha256"]},
    }
    review_basis = _review_basis(
        template_hashes,
        corpus=corpus,
        mode=mode,
        tolerance=tolerance,
        tolerance_pct=tolerance_pct,
        since=since,
        until=until,
        replay_pass_rate=replay_pass_rate,
    )
    base_inputs = {
        "usage_log_sha256": usage_sha,
        "attestation_sha256": att_sha,
        "provider_export_sha256": provider_sha,
        "provider_export_manifest_sha256": provider_manifest_sha,
        "corpus_manifest_sha256": corpus_sha,
        "t1_manifest_sha256": t1_sha,
        "tool_manifest_sha256": tool_manifest["sha256"],
        "review_basis_sha256": review_basis["sha256"],
    }
    templates = {
        "provider_export_manifest.template.json": {
            "schema": "kry_provider_export_manifest_template/v1",
            "instructions": "Replace template schema with kry_provider_export_manifest/v1 after filling real provider-export provenance fields.",
            "provider": "TODO_OpenRouter / OpenAI / Anthropic / Google / other",
            "export_source": "TODO_provider API endpoint / billing export / console export",
            "export_reference": "TODO_export id / report id / console URL / signed note id",
            "date": "TODO_YYYY-MM-DD",
            "non_synthetic": True,
            "reconciliation_mode": "TODO_per-request_or_aggregate",
            "provider_record_count": "TODO_PROVIDER_RECORD_COUNT",
            "collection_window": {"since": "TODO_ISO8601", "until": "TODO_ISO8601"},
            "artifact_inputs": {
                "provider_export_sha256": provider_sha,
                "t1_manifest_sha256": t1_sha,
            },
        },
        "corpus_manifest.template.json": {
            "schema": "kry_corpus_manifest_template/v1",
            "instructions": "Replace template schema with kry_corpus_manifest/v1 after filling real evidence fields.",
            "corpus": "real",
            "date": "TODO_YYYY-MM-DD",
            "source": "TODO_real provider gateway / billing export / production traffic window",
            "source_reference": "TODO_usage export id / log bundle id / report id / signed note id",
            "non_synthetic": True,
            "record_count": "TODO_NORMALIZED_USAGE_RECORD_COUNT",
            "collection_window": {"since": "TODO_ISO8601", "until": "TODO_ISO8601"},
            "validation_plan": {
                "schema": "kry_validation_plan/v1",
                "registered_date": "TODO_YYYY-MM-DD_BEFORE_OR_ON_COLLECTION_START",
                "provider": "TODO_provider_name_matching_provider_export_manifest",
                "reconciliation_mode": mode,
                "tolerance": int(tolerance),
                "tolerance_pct": float(tolerance_pct),
                "min_provider_records": "TODO_MIN_PROVIDER_RECORDS",
                "min_usage_records": "TODO_MIN_NORMALIZED_USAGE_RECORDS",
                "min_independent_agreement": kry_capabilities.INDEPENDENT_AGREEMENT_BAR,
                "collection_window": {"since": "TODO_ISO8601", "until": "TODO_ISO8601"},
                "outside_review_required": True,
                "buyer_feedback_required": True,
                "legal_review_required": True,
                "kill_criteria": list(REQUIRED_VALIDATION_KILL_CRITERIA),
            },
            "artifact_inputs": {
                "usage_log_sha256": usage_sha,
                "provider_export_sha256": provider_sha,
                "provider_export_manifest_sha256": provider_manifest_sha,
                "t1_manifest_sha256": t1_sha,
            },
        },
        "outside_review.template.json": {
            "schema": "kry_external_evidence_template/v1",
            "instructions": "Replace template schema with kry_external_evidence/v1 after an independent reviewer verifies the packet.",
            "kind": "outside_review",
            "date": "TODO_YYYY-MM-DD",
            "evidence_source": "TODO_signed note / issue / email / review packet",
            "evidence_reference": "TODO_URL_OR_FILE_ID_OR_NOTE_ID",
            "reviewer": "TODO_name / org / role",
            "independent": True,
            "verdict": "TODO_verified_or_rejected",
            "reviewer_artifact_checks": {
                field: "TODO_TRUE_OR_FALSE"
                for field in _OUTSIDE_REVIEW_CHECK_FIELDS
            },
            "reviewer_command_outputs": {
                **{
                    field: "TODO_TRUE_OR_FALSE"
                    for field in _OUTSIDE_REVIEW_OUTPUT_TRUE_FIELDS
                },
                **{
                    field: "TODO_ZERO"
                    for field in _OUTSIDE_REVIEW_OUTPUT_ZERO_FIELDS
                },
            },
            "reviewed_claims": ["external_verified_savings"],
            "artifact_inputs": base_inputs,
        },
        "buyer_feedback.template.json": {
            "schema": "kry_external_evidence_template/v1",
            "instructions": "Replace template schema with kry_external_evidence/v1 after buyer feedback is actually obtained.",
            "kind": "buyer_feedback",
            "date": "TODO_YYYY-MM-DD",
            "evidence_source": "TODO_buyer email / call notes / CRM note / LOI / paid trial record",
            "evidence_reference": "TODO_URL_OR_FILE_ID_OR_NOTE_ID",
            "buyer": "TODO_name / organization / counterparty id",
            "buyer_role": "TODO_AI FinOps / platform / infra buyer",
            "verdict": "TODO_qualified_interest_or_rejected",
            "buyer_local_evidence_gates": {
                field: "TODO_TRUE_OR_FALSE"
                for field in _BUYER_LOCAL_GATE_FIELDS
            },
            "buyer_threshold_context": {
                "proof_required_reader": "TODO_finance / customer / procurement / audit / board / gainshare reader",
                "provider_or_bill_data_source": "TODO_provider bill / usage export / AWS CUR reference",
                "request_or_gateway_metadata_source": "TODO_gateway/request metadata reference without prompts or completions",
                "baseline_reference": "TODO_accepted measured/projected baseline reference",
                "authority_basis": "TODO_budget / customer / procurement / board / audit / gainshare authority",
                "quality_or_slo_boundary": "TODO_quality/SLO boundary for usable savings",
                "materiality_basis": "TODO_>=10% avoidable spend or >=$5k/month path",
                "sample_window": "TODO_seven-day or supplied sample window reference",
            },
            "buyer_materiality": {
                "avoidable_spend_pct": "TODO_NUMBER_OR_OMIT",
                "plausible_monthly_savings_usd": "TODO_NUMBER_OR_OMIT",
            },
            "reviewed_claims": ["external_verified_savings"],
            "artifact_inputs": base_inputs,
        },
        "legal_review.template.json": {
            "schema": "kry_external_evidence_template/v1",
            "instructions": "Replace template schema with kry_external_evidence/v1 only after legal/claims review is complete.",
            "kind": "legal_review",
            "date": "TODO_YYYY-MM-DD",
            "evidence_source": "TODO_counsel memo / legal ticket / signed note",
            "evidence_reference": "TODO_URL_OR_FILE_ID_OR_NOTE_ID",
            "reviewer": "TODO_claims counsel / legal reviewer",
            "verdict": "TODO_approved_with_limits_or_rejected",
            "external_claim_allowed": "TODO_TRUE_OR_FALSE",
            "tradeable_token_disclaimed": True,
            "legal_limitations": [
                "TODO_approved limits or none beyond claim register"
            ],
            "legal_claim_checks": {
                field: "TODO_TRUE_OR_FALSE"
                for field in _LEGAL_CLAIM_CHECK_FIELDS
            },
            "reviewed_claims": ["external_verified_savings", "tradeable_token"],
            "artifact_inputs": base_inputs,
        },
    }
    written = []
    for name, data in templates.items():
        path = out_dir / name
        path.write_text(_json_pretty(data), encoding="utf-8")
        written.append(str(path))
    basis_files = []
    for name, data in {
        "tool_manifest.json": tool_manifest,
        "review_basis.json": review_basis,
        "reviewer_checklist.json": _reviewer_checklist(
            base_inputs,
            artifact_path="packet/artifact.json",
        ),
    }.items():
        path = out_dir / name
        path.write_text(_json_pretty(data), encoding="utf-8")
        basis_files.append(str(path))
    request_files = []
    for name, text in _request_briefs(base_inputs, review_basis_sha=review_basis["sha256"]).items():
        path = out_dir / name
        path.write_text(text, encoding="utf-8")
        request_files.append(str(path))
    return {
        "template_dir": str(out_dir),
        "written": written,
        "basis_files": basis_files,
        "request_files": request_files,
        "tool_manifest": tool_manifest,
        "review_basis": review_basis,
    }


def write_bundle(
    bundle_dir: str | Path,
    usage_log: str,
    *,
    attestation: str | None,
    mint_log: str | None = None,
    t1_manifest: str | None = None,
    provider_export: str | None = None,
    provider_export_manifest: str | None = None,
    corpus: str = "synthetic",
    corpus_manifest: str | None = None,
    outside_review: str | None = None,
    buyer_feedback: str | None = None,
    legal_review: str | None = None,
    mode: str = "per-request",
    tolerance: int = 0,
    tolerance_pct: float = 5.0,
    since: float | None = None,
    until: float | None = None,
    replay_pass_rate: float = 1.0,
) -> dict:
    privacy_errors = _bundle_input_privacy_errors(
        usage_log,
        provider_export,
        attestation=attestation,
        t1_manifest=t1_manifest,
        provider_export_manifest=provider_export_manifest,
        corpus_manifest=corpus_manifest,
        outside_review=outside_review,
        buyer_feedback=buyer_feedback,
        legal_review=legal_review,
    )
    if privacy_errors:
        raise ValueError("bundle input privacy check failed: " + "; ".join(privacy_errors))
    bundle = Path(bundle_dir)
    bundle.mkdir(parents=True, exist_ok=True)
    copied = {
        "usage_log": _copy_bundle_file(usage_log, bundle, "usage_log.jsonl"),
        "attestation": _copy_bundle_file(attestation, bundle, "attestation.json"),
        "mint_log": None,
        "t1_manifest": _copy_bundle_file(t1_manifest, bundle, "t1_manifest.json"),
        "provider_export": _copy_bundle_file(provider_export, bundle, "provider_export.json"),
        "provider_export_manifest": _copy_bundle_file(provider_export_manifest, bundle, "provider_export_manifest.json"),
        "corpus_manifest": _copy_bundle_file(corpus_manifest, bundle, "corpus_manifest.json"),
        "outside_review": _copy_bundle_file(outside_review, bundle, "outside_review.json"),
        "buyer_feedback": _copy_bundle_file(buyer_feedback, bundle, "buyer_feedback.json"),
        "legal_review": _copy_bundle_file(legal_review, bundle, "legal_review.json"),
    }
    if copied["t1_manifest"] is None and mint_log:
        write_t1_manifest(mint_log, bundle / "t1_manifest.json", since=since, until=until)
        copied["t1_manifest"] = "t1_manifest.json"
    artifact = build_artifact(
        copied["usage_log"],
        attestation=copied["attestation"],
        mint_log=copied["mint_log"],
        t1_manifest=copied["t1_manifest"],
        provider_export=copied["provider_export"],
        provider_export_manifest=copied["provider_export_manifest"],
        corpus=corpus,
        corpus_manifest=copied["corpus_manifest"],
        outside_review=copied["outside_review"],
        buyer_feedback=copied["buyer_feedback"],
        legal_review=copied["legal_review"],
        mode=mode,
        tolerance=tolerance,
        tolerance_pct=tolerance_pct,
        since=since,
        until=until,
        replay_pass_rate=replay_pass_rate,
        base_dir=bundle,
    )
    (bundle / "artifact.json").write_text(_json_pretty(artifact), encoding="utf-8")
    checklist_inputs = dict(artifact["review_basis"]["inputs"])
    checklist_inputs["review_basis_sha256"] = artifact["review_basis"]["sha256"]
    checklist = _reviewer_checklist(
        checklist_inputs,
        artifact_path="artifact.json",
        artifact_hash=artifact["artifact_hash"],
    )
    (bundle / "reviewer_checklist.json").write_text(_json_pretty(checklist), encoding="utf-8")
    report_text = _render_finops_report(bundle / "artifact.json")
    (bundle / "finops_report.md").write_text(report_text + ("" if report_text.endswith("\n") else "\n"), encoding="utf-8")
    verification = verify_artifact_file(bundle / "artifact.json")
    if not verification.get("ok"):
        errors = "; ".join(verification.get("errors") or ["verification failed"])
        raise ValueError(f"bundle verification failed: {errors}")
    return artifact


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Build/check a KRY verified-savings artifact gate")
    p.add_argument("usage_log", nargs="?", help="JSON/JSONL routing log used for the savings report")
    p.add_argument("--verify-artifact", default=None,
                   help="recompute and verify an existing kry_verified_savings_artifact/v1 JSON")
    p.add_argument("--attestation", default=None, help="public attestation JSON to verify")
    p.add_argument("--mint-log", default=None, help="private mint log for provider reconciliation")
    p.add_argument("--t1-manifest", default=None,
                   help="shareable kry_t1_reconciliation_manifest/v1 for provider reconciliation")
    p.add_argument("--write-t1-manifest", default=None,
                   help="write a shareable T1 manifest from --mint-log, then exit")
    p.add_argument("--write-provider-export-manifest", default=None,
                   help="write kry_provider_export_manifest/v1 for --provider-export and --t1-manifest")
    p.add_argument("--write-corpus-manifest", default=None,
                   help="write kry_corpus_manifest/v1 for the usage/provider/T1 evidence")
    p.add_argument("--provider-export", default=None, help="provider usage/billing export JSON")
    p.add_argument("--provider-export-manifest", default=None,
                   help="structured kry_provider_export_manifest/v1 provenance evidence")
    p.add_argument("--provider", default=None,
                   help="provider name for generated provider/corpus manifests")
    p.add_argument("--export-source", default=None,
                   help="provider export source for generated kry_provider_export_manifest/v1")
    p.add_argument("--export-reference", default=None,
                   help="provider export id/report id/URL/note id for generated kry_provider_export_manifest/v1")
    p.add_argument("--corpus-source", default=None,
                   help="real corpus source for generated kry_corpus_manifest/v1")
    p.add_argument("--corpus-reference", default=None,
                   help="usage export/log bundle/report/note id for generated kry_corpus_manifest/v1")
    p.add_argument("--evidence-date", default=None,
                   help="YYYY-MM-DD date for generated evidence manifests")
    p.add_argument("--window-since", default=None,
                   help="collection window start for generated evidence manifests")
    p.add_argument("--window-until", default=None,
                   help="collection window end for generated evidence manifests")
    p.add_argument("--min-provider-records", type=int, default=None,
                   help="validation-plan minimum provider records; default = generated provider count")
    p.add_argument("--min-usage-records", type=int, default=None,
                   help="validation-plan minimum usage records; default = generated usage count")
    p.add_argument("--min-independent-agreement", type=float, default=None,
                   help="validation-plan independent agreement floor; default = KRY readiness bar")
    p.add_argument("--corpus", choices=["synthetic", "internal", "real"], default="synthetic",
                   help="operator-declared corpus class; only real can pass the science gate")
    p.add_argument("--corpus-manifest", default=None,
                   help="structured kry_corpus_manifest/v1 evidence bound to usage/provider hashes")
    p.add_argument("--outside-review", default=None, help="third-party verification/review evidence file")
    p.add_argument("--buyer-feedback", default=None, help="buyer feedback evidence file")
    p.add_argument("--legal-review", default=None, help="legal/claims review evidence file")
    p.add_argument("--mode", choices=["per-request", "aggregate"], default="per-request")
    p.add_argument("--tolerance", type=int, default=0)
    p.add_argument("--tolerance-pct", type=float, default=5.0)
    p.add_argument("--since", type=float, default=None)
    p.add_argument("--until", type=float, default=None)
    p.add_argument("--replay-pass-rate", type=float, default=1.0)
    p.add_argument("--bundle-dir", default=None,
                   help="copy inputs into a portable bundle and write bundle/artifact.json")
    p.add_argument("--template-dir", default=None,
                   help="write non-passing evidence request templates with current input hashes")
    p.add_argument("--out", default=None, help="write artifact JSON here; stdout if omitted")
    p.add_argument("--trust-local-inputs", action="store_true",
                   help="trust command_inputs that point outside the bundle — only for verifying YOUR OWN local packet")
    args = p.parse_args(argv)

    if args.verify_artifact:
        result = verify_artifact_file(args.verify_artifact, trust_local_inputs=args.trust_local_inputs)
        print(_json_pretty(result), end="")
        return 0 if result.get("ok") else 1
    if args.write_t1_manifest:
        if not args.mint_log:
            p.error("--mint-log is required with --write-t1-manifest")
        try:
            path = write_t1_manifest(
                args.mint_log,
                args.write_t1_manifest,
                since=args.since,
                until=args.until,
            )
        except Exception as exc:
            print(f"T1 manifest generation failed: {exc}", file=sys.stderr)
            return 1
        manifest = _load_json(path)
        print(_json_pretty({
            "schema": T1_MANIFEST_SCHEMA,
            "t1_manifest": _hash_file(path),
            "receipt_count": manifest.get("receipt_count") if isinstance(manifest, dict) else None,
        }), end="")
        return 0

    if args.write_provider_export_manifest or args.write_corpus_manifest:
        if not args.provider_export:
            p.error("--provider-export is required when writing evidence manifests")
        if not args.t1_manifest:
            p.error("--t1-manifest is required when writing evidence manifests")
        if not args.provider:
            p.error("--provider is required when writing evidence manifests")
        if not args.evidence_date:
            p.error("--evidence-date is required when writing evidence manifests")
        if not args.window_since or not args.window_until:
            p.error("--window-since and --window-until are required when writing evidence manifests")
        window = _required_window(args.window_since, args.window_until)
        result = {"schema": "kry_evidence_manifest_generation/v1", "written": []}
        provider_manifest_path = args.provider_export_manifest
        if args.write_provider_export_manifest:
            if not args.export_source:
                p.error("--export-source is required with --write-provider-export-manifest")
            if not args.export_reference:
                p.error("--export-reference is required with --write-provider-export-manifest")
            provider_manifest_path = args.write_provider_export_manifest
            try:
                provider_manifest = write_provider_export_manifest(
                    provider_manifest_path,
                    provider_export=args.provider_export,
                    t1_manifest=args.t1_manifest,
                    provider=args.provider,
                    export_source=args.export_source,
                    export_reference=args.export_reference,
                    date=args.evidence_date,
                    collection_window=window,
                    mode=args.mode,
                )
            except Exception as exc:
                print(f"provider export manifest generation failed: {exc}", file=sys.stderr)
                return 1
            result["provider_export_manifest"] = {
                "path": provider_manifest_path,
                "sha256": _hash_file(provider_manifest_path)["sha256"],
                "provider_record_count": provider_manifest["provider_record_count"],
            }
            result["written"].append(provider_manifest_path)
        if args.write_corpus_manifest:
            if not args.usage_log:
                p.error("usage_log is required with --write-corpus-manifest")
            if not provider_manifest_path:
                p.error("--provider-export-manifest or --write-provider-export-manifest is required with --write-corpus-manifest")
            if not args.corpus_source:
                p.error("--corpus-source is required with --write-corpus-manifest")
            if not args.corpus_reference:
                p.error("--corpus-reference is required with --write-corpus-manifest")
            try:
                corpus_manifest = write_corpus_manifest(
                    args.write_corpus_manifest,
                    args.usage_log,
                    provider_export=args.provider_export,
                    provider_export_manifest=provider_manifest_path,
                    t1_manifest=args.t1_manifest,
                    provider=args.provider,
                    source=args.corpus_source,
                    source_reference=args.corpus_reference,
                    date=args.evidence_date,
                    collection_window=window,
                    mode=args.mode,
                    tolerance=args.tolerance,
                    tolerance_pct=args.tolerance_pct,
                    min_provider_records=args.min_provider_records,
                    min_usage_records=args.min_usage_records,
                    min_independent_agreement=args.min_independent_agreement,
                )
            except Exception as exc:
                print(f"corpus manifest generation failed: {exc}", file=sys.stderr)
                return 1
            result["corpus_manifest"] = {
                "path": args.write_corpus_manifest,
                "sha256": _hash_file(args.write_corpus_manifest)["sha256"],
                "record_count": corpus_manifest["record_count"],
            }
            result["written"].append(args.write_corpus_manifest)
        print(_json_pretty(result), end="")
        return 0

    if not args.usage_log:
        p.error("usage_log is required unless --verify-artifact is used")
    if args.template_dir:
        result = write_evidence_templates(
            args.template_dir,
            args.usage_log,
            attestation=args.attestation,
            provider_export=args.provider_export,
            provider_export_manifest=args.provider_export_manifest,
            corpus_manifest=args.corpus_manifest,
            t1_manifest=args.t1_manifest,
            corpus=args.corpus,
            mode=args.mode,
            tolerance=args.tolerance,
            tolerance_pct=args.tolerance_pct,
            since=args.since,
            until=args.until,
            replay_pass_rate=args.replay_pass_rate,
        )
        print(_json_pretty(result), end="")
        return 0
    if not args.attestation:
        p.error("--attestation is required unless --verify-artifact is used")

    if args.bundle_dir:
        try:
            artifact = write_bundle(
                args.bundle_dir,
                args.usage_log,
                attestation=args.attestation,
                mint_log=args.mint_log,
                t1_manifest=args.t1_manifest,
                provider_export=args.provider_export,
                provider_export_manifest=args.provider_export_manifest,
                corpus=args.corpus,
                corpus_manifest=args.corpus_manifest,
                outside_review=args.outside_review,
                buyer_feedback=args.buyer_feedback,
                legal_review=args.legal_review,
                mode=args.mode,
                tolerance=args.tolerance,
                tolerance_pct=args.tolerance_pct,
                since=args.since,
                until=args.until,
                replay_pass_rate=args.replay_pass_rate,
            )
        except Exception as exc:
            print(f"bundle generation failed: {exc}", file=sys.stderr)
            return 1
    else:
        artifact = build_artifact(
            args.usage_log,
            attestation=args.attestation,
            mint_log=args.mint_log,
            t1_manifest=args.t1_manifest,
            provider_export=args.provider_export,
            provider_export_manifest=args.provider_export_manifest,
            corpus=args.corpus,
            corpus_manifest=args.corpus_manifest,
            outside_review=args.outside_review,
            buyer_feedback=args.buyer_feedback,
            legal_review=args.legal_review,
            mode=args.mode,
            tolerance=args.tolerance,
            tolerance_pct=args.tolerance_pct,
            since=args.since,
            until=args.until,
            replay_pass_rate=args.replay_pass_rate,
        )
    if not args.bundle_dir and artifact["ship_scope"] == "external_verified_savings_candidate":
        print(
            "external verified-savings candidates must be written with --bundle-dir "
            "so relative command_inputs, reviewer_checklist.json, finops_report.md, "
            "and packet privacy checks are generated",
            file=sys.stderr,
        )
        return 1
    text = _json_pretty(artifact)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    else:
        print(text, end="")
    return 0 if artifact["ship_scope"] != "do_not_ship" else 1


if __name__ == "__main__":
    sys.exit(main())
