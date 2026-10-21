"""Synthetic prospective native projection links, not provider authenticity."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import kry_anthropic_projection as native  # noqa: E402
import kry_usage_link as linking  # noqa: E402
import kry_verify  # noqa: E402

MODEL = "claude-haiku-4-5-20251001"
ACCOUNT = hashlib.sha256(b"synthetic-account-a").hexdigest()


def _inputs(index=0):
    response = {
        "id": f"msg_constructed_{index}", "type": "message", "role": "assistant",
        "model": MODEL, "content": [{"type": "text", "text": "Constructed response only"}],
        "stop_reason": "end_turn", "stop_sequence": None,
        "usage": {"input_tokens": 2000, "output_tokens": 400,
                  "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0,
                  "service_tier": "standard", "speed": "standard"},
    }
    acquisition = {
        "schema": "kry_anthropic_acquisition/v1", "endpoint": "/v1/messages",
        "http_status": 200, "requested_model": MODEL,
        "request_id": f"req_constructed_{index}", "observed_at_utc": "2026-10-21T00:00:00Z",
        "account_scope_sha256": ACCOUNT,
    }
    return response, acquisition


def _project(response, acquisition):
    return native.project_anthropic_response(
        json.dumps(response).encode(), json.dumps(acquisition).encode())


def _usage(provider):
    return dict(copy.deepcopy(provider), event_type="short_circuit",
                served_model=MODEL, avoided_model="gh/claude-opus-4.8")


def _case(count=1):
    from kry.kry_attest import build_attestation
    from kry.kry_mint import mint

    inputs = {"usage_rows": [], "provider_rows": [], "links": []}
    for index in range(count):
        provider = _project(*_inputs(index))["provider_row"]
        usage = _usage(provider)
        prepared = linking.prepare_link(usage, provider)
        receipt = mint(**prepared["mint_arguments"])
        assert receipt is not None
        inputs["usage_rows"].append(usage)
        inputs["provider_rows"].append(provider)
        inputs["links"].append({"usage_index": index, "provider_index": index,
                                "receipt_id": receipt.receipt_id,
                                "report_row": prepared["report_row"]})
    inputs["attestation"] = json.loads(build_attestation().to_public_json())
    return inputs


def test_projection_mint_and_opening_preserve_t0_and_claim_limits():
    response, acquisition = _inputs()
    projection = _project(response, acquisition)
    expected = {
        "identity": {"provider": "anthropic", "account_scope_sha256": ACCOUNT,
                     "namespace": "http_request_id",
                     "request_id_sha256": hashlib.sha256(b"req_constructed_0").hexdigest()},
        "model": MODEL, "service_tier": "standard",
        "usage": {"prompt_tokens": 2000, "completion_tokens": 400},
    }
    assert projection["provider_row"] == expected
    assert projection["projection"]["http_request_id_sha256"] == hashlib.sha256(
        b"req_constructed_0").hexdigest()
    assert projection["projection"]["message_id_sha256"] == hashlib.sha256(
        b"msg_constructed_0").hexdigest()
    assert projection["native_projection_consistent"] is True
    assert projection["receipt_binding_verified"] is False
    assert projection["observed_usage"] == response["usage"]
    inputs = _case()
    assert kry_verify.verify_attestation(inputs["attestation"])[0] is True
    result = linking.assess_links(**inputs)
    assert result["linked_rows"] == 1
    # Existing output-price benchmark: 400 * (25 - 5) / 25; not a charge.
    assert result["estimated_kry"] == 320
    assert result["normalization_verified"] is False
    assert result["excluded_attestation_receipts"] == 0
    for report in (projection, result):
        assert report["source_truth_authenticated"] is False
        assert report["actual_charge_usd"] is None
        assert report["external_verified_savings"] is False
    receipt = inputs["attestation"]["links"][0]
    assert receipt["hash_version"] == 7
    assert receipt["evidence_tier"] == "self_reported"
    assert receipt.get("metered_tokens") is None
    assert inputs["attestation"]["veracity"]["veracity_floor"] == 0


@pytest.mark.parametrize("field", ["input_tokens", "output_tokens"])
def test_changed_native_count_cannot_match_unchanged_usage(field):
    response, acquisition = _inputs()
    usage = _usage(_project(response, acquisition)["provider_row"])
    response["usage"][field] += 1
    changed = _project(response, acquisition)["provider_row"]
    with pytest.raises(ValueError, match="token_mismatch"):
        linking.prepare_link(usage, changed)


def test_reprojected_input_count_cannot_reopen_prior_receipt():
    inputs = _case()
    response, acquisition = _inputs()
    response["usage"]["input_tokens"] += 1
    provider = _project(response, acquisition)["provider_row"]
    usage = _usage(provider)
    inputs["provider_rows"][0], inputs["usage_rows"][0] = provider, usage
    inputs["links"][0]["report_row"] = linking.prepare_link(usage, provider)["report_row"]
    with pytest.raises(ValueError, match="receipt_opening_mismatch"):
        linking.assess_links(**inputs)


def test_equal_token_native_requests_cannot_exchange_provider_rows():
    inputs = _case(2)
    first, second = inputs["provider_rows"]
    assert first["usage"] == second["usage"]
    assert first["identity"]["request_id_sha256"] != second["identity"]["request_id_sha256"]
    inputs["provider_rows"].reverse()
    with pytest.raises(ValueError, match="identity_mismatch"):
        linking.assess_links(**inputs)


def test_equal_token_native_requests_cannot_exchange_receipts():
    inputs = _case(2)
    inputs["links"][0]["receipt_id"], inputs["links"][1]["receipt_id"] = (
        inputs["links"][1]["receipt_id"], inputs["links"][0]["receipt_id"])
    with pytest.raises(ValueError, match="receipt_opening_mismatch"):
        linking.assess_links(**inputs)


def test_acquisition_account_change_cannot_match_original_usage():
    response, acquisition = _inputs()
    usage = _usage(_project(response, acquisition)["provider_row"])
    acquisition["account_scope_sha256"] = hashlib.sha256(b"synthetic-account-b").hexdigest()
    changed = _project(response, acquisition)["provider_row"]
    with pytest.raises(ValueError, match="identity_mismatch"):
        linking.prepare_link(usage, changed)


def test_reprojected_account_cannot_reopen_prior_receipt():
    inputs = _case()
    response, acquisition = _inputs()
    acquisition["account_scope_sha256"] = hashlib.sha256(b"synthetic-account-b").hexdigest()
    provider = _project(response, acquisition)["provider_row"]
    usage = _usage(provider)
    inputs["provider_rows"][0], inputs["usage_rows"][0] = provider, usage
    inputs["links"][0]["report_row"] = linking.prepare_link(usage, provider)["report_row"]
    with pytest.raises(ValueError, match="receipt_opening_mismatch"):
        linking.assess_links(**inputs)


def test_unknown_account_has_no_eligible_provider_row():
    response, acquisition = _inputs()
    acquisition["account_scope_sha256"] = None
    result = _project(response, acquisition)
    assert result["native_projection_consistent"] is True
    assert result["provider_row"] is None
    assert result["source_truth_authenticated"] is False
    assert result["receipt_binding_verified"] is False
    with pytest.raises(ValueError, match="unsupported_row_profile"):
        linking.prepare_link(_usage(_project(*_inputs())["provider_row"]), result["provider_row"])


def test_native_bytes_are_not_added_to_existing_receipt_commitment():
    inputs = _case()
    response, acquisition = _inputs()
    original = _project(response, acquisition)
    response["content"][0]["text"] = "Different constructed text, same normalized usage"
    changed = _project(response, acquisition)
    assert changed["inputs"] != original["inputs"]
    assert changed["provider_row"] == original["provider_row"]
    assert changed["receipt_binding_verified"] is False
    inputs["provider_rows"][0] = changed["provider_row"]
    result = linking.assess_links(**inputs)
    assert result["supported"] is True
    assert result["normalization_verified"] is False
    assert result["source_truth_authenticated"] is False


def test_projection_does_not_retrofit_id_only_receipt():
    from kry.kry_attest import build_attestation
    from kry.kry_mint import mint

    provider = _project(*_inputs())["provider_row"]
    usage = _usage(provider)
    prepared = linking.prepare_link(usage, provider)
    receipt = mint(**dict(prepared["mint_arguments"], evidence="synthetic-old-id-only"))
    assert receipt is not None
    attestation = json.loads(build_attestation().to_public_json())
    assert kry_verify.verify_attestation(attestation)[0] is True
    with pytest.raises(ValueError, match="receipt_opening_mismatch"):
        linking.assess_links(attestation, [usage], [provider], [{
            "usage_index": 0, "provider_index": 0, "receipt_id": receipt.receipt_id,
            "report_row": prepared["report_row"],
        }])
