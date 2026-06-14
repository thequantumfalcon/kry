#!/usr/bin/env python3
"""KRY external shadow-emitter — read-only, digest-only measured-row emitter.

Turns ONE external LLM request/response (captured out-of-band, e.g. from a LiteLLM
`CustomLogger.async_log_success_event` callback) into ONE validated, privacy-safe
measured row: the sealed base schema `kry_live_shadow_first_batch_row/v1` +
the external annex `EXTERNAL_EMITTER_SCHEMA_ANNEX_2026_06_11.md`.

DISCIPLINE (do not break):
- DIGEST-ONLY: never store raw prompt/response/messages/outputs. The privacy gate
  rejects any forbidden field; only *_digest fields carry content provenance.
- NET-NOT-GROSS: a row carries positive measured value ONLY if it passes P0
  (cheap/rescue correct AND frontier correct, OR a deployable validator pass) and
  is not a false-accept.
- MEASURE-ONLY: this is instrumentation, NOT evidence of savings. The self-test
  uses MOCK numbers and claims nothing.
- STDLIB ONLY: no third-party imports here. The LiteLLM binding is a thin adapter
  the customer wires up (see `litellm_adapter_example` below) which only calls
  emit_row() from inside the callback — litellm never enters this module.

Run the self-test (no providers, no network):  python3 scripts/kry_shadow_emitter.py
"""
from __future__ import annotations
import hashlib
import json

SCHEMA = "kry_live_shadow_first_batch_row/v1"
ANNEX_VERSION = "kry_external_emitter_annex/v1"

# Privacy gate — any of these present in a saved row blocks it (base schema law).
FORBIDDEN_RAW = (
    "intent", "outcome", "prompt", "response", "messages", "wall_why",
    "cheap_output", "rescue_output", "frontier_output",
)
CORRECTNESS_SOURCES = ("deployable", "reviewed", "frontier_holdout", "none")
MEASUREMENT_CLASSES = ("frontier_holdout", "deployable_validated",
                       "excluded_with_reason", "population_only", "invalid")
COST_SOURCES = ("provider_receipt", "local_meter", "pricing_table",
                "review_estimate", "none")


def _digest(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def _num(x) -> float:
    return 0.0 if x is None else float(x)


def recompute_verifier_fields(row: dict) -> dict:
    """Recompute the verifier-owned fields from saved fields (the sealed P0/net formulas).

    The verifier recomputes these and does not trust the emitter; we compute the
    same way so a freshly emitted row is internally consistent.
    """
    cfc, crc = row.get("cheap_fast_correct"), row.get("cheap_rescue_correct")
    fc, dvp = row.get("frontier_correct"), row.get("deployable_validator_pass")
    p0 = bool(dvp is True or ((cfc is True or crc is True) and fc is True))
    false_accept = bool(row.get("false_accept", False))
    rescue_cost = (_num(row.get("cheap_fast_cost_usd"))
                   + _num(row.get("cheap_rescue_cost_usd"))
                   + _num(row.get("validator_cost_usd")))
    frontier_baseline = _num(row.get("frontier_holdout_cost_usd"))
    net = frontier_baseline - rescue_cost
    value = net if (p0 and not false_accept) else 0.0
    fout = row.get("frontier_holdout_output_tokens")
    denom = fout if fout else max(_num(row.get("cheap_fast_output_tokens")),
                                  _num(row.get("cheap_rescue_output_tokens")), 1.0)
    return {
        "p0_pass": p0,
        "rescue_path_cost_usd": round(rescue_cost, 8),
        "frontier_baseline_cost_usd": round(frontier_baseline, 8),
        "net_saving_usd": round(net, 8),
        "measured_row_value_usd": round(value, 8),
        "output_token_denominator": denom,
        "net_saving_per_output_token": round(value / denom, 12) if denom else 0.0,
    }


def privacy_gate(row: dict) -> None:
    """Raise if any forbidden raw-content field is present. Digest-only is law."""
    bad = [k for k in row if k in FORBIDDEN_RAW]
    if bad:
        raise ValueError(f"privacy gate: forbidden raw field(s) present: {bad}")


def emit_row(*, frame_id, request_id, measurement_class,
             intent_text=None, outcome_text=None,
             requested_model=None, resolved_backend=None, served_model=None,
             correctness_source="none",
             cheap_fast_correct=None, cheap_rescue_correct=None,
             frontier_correct=None, deployable_validator_pass=None, false_accept=False,
             cheap_fast_cost_usd=None, cheap_rescue_cost_usd=None,
             frontier_holdout_cost_usd=None, validator_cost_usd=None,
             cheap_fast_output_tokens=None, cheap_rescue_output_tokens=None,
             frontier_holdout_output_tokens=None,
             response_cost_usd=None, provider_cost_source="none",
             integration_surface="litellm_custom_logger", shadow_mode="log_then_replay",
             replay_mode=None, latency_class=None, checkable_slice=None,
             deterministic_check_kind=None, deterministic_check_receipt=None,
             output_axis_class=None, output_tokens_capped=None, verbose_output_flag=None,
             partner_or_corpus_id=None, traffic_export_id=None) -> dict:
    """Build ONE validated, digest-only measured row (base schema + annex)."""
    if measurement_class not in MEASUREMENT_CLASSES:
        raise ValueError(f"bad measurement_class: {measurement_class}")
    if correctness_source not in CORRECTNESS_SOURCES:
        raise ValueError(f"bad correctness_source: {correctness_source}")
    if provider_cost_source not in COST_SOURCES:
        raise ValueError(f"bad provider_cost_source: {provider_cost_source}")
    row = {
        "schema": SCHEMA,
        "annex_version": ANNEX_VERSION,
        "frame_id": frame_id,
        "request_id": request_id,
        # content as digests ONLY — never the raw text:
        "intent_digest": _digest(intent_text) if intent_text is not None else None,
        "outcome_digest": _digest(outcome_text) if outcome_text is not None else None,
        "requested_model": requested_model,
        "resolved_backend": resolved_backend,
        "served_model": served_model,
        "measurement_class": measurement_class,
        "cheap_fast_correct": cheap_fast_correct,
        "cheap_rescue_correct": cheap_rescue_correct,
        "frontier_correct": frontier_correct,
        "deployable_validator_pass": deployable_validator_pass,
        "correctness_source": correctness_source,
        "false_accept": bool(false_accept),
        "cheap_fast_cost_usd": cheap_fast_cost_usd,
        "cheap_rescue_cost_usd": cheap_rescue_cost_usd,
        "frontier_holdout_cost_usd": frontier_holdout_cost_usd,
        "validator_cost_usd": validator_cost_usd,
        "cheap_fast_output_tokens": cheap_fast_output_tokens,
        "cheap_rescue_output_tokens": cheap_rescue_output_tokens,
        "frontier_holdout_output_tokens": frontier_holdout_output_tokens,
        "cost_source": provider_cost_source,
        # --- annex ---
        "emitter_type": "kry_shadow_emitter",
        "emitter_version": ANNEX_VERSION,
        "integration_surface": integration_surface,
        "shadow_mode": shadow_mode,
        "replay_mode": replay_mode,
        "latency_class": latency_class,
        "checkable_slice": checkable_slice,
        "deterministic_check_kind": deterministic_check_kind,
        "deterministic_check_receipt_digest": (
            _digest(deterministic_check_receipt) if deterministic_check_receipt is not None else None),
        "output_axis_class": output_axis_class,
        "output_tokens_capped": output_tokens_capped,
        "verbose_output_flag": verbose_output_flag,
        "provider_cost_source": provider_cost_source,
        "response_cost_usd": response_cost_usd,
        "partner_or_corpus_digest": (
            _digest(partner_or_corpus_id) if partner_or_corpus_id is not None else None),
        "traffic_export_digest": _digest(traffic_export_id) if traffic_export_id is not None else None,
    }
    privacy_gate(row)
    row.update(recompute_verifier_fields(row))
    # Content-address the row (the receipt anchor a kry_verify pass binds to):
    row["row_digest"] = _digest(json.dumps({k: row[k] for k in sorted(row)}, default=str))
    return row


# --- LiteLLM binding (documentation only; litellm is NOT imported here) ---------
litellm_adapter_example = '''
# In the CUSTOMER's infra (requires `litellm`); runs out-of-band, never blocks serving:
from litellm.integrations.custom_logger import CustomLogger
from kry_shadow_emitter import emit_row

class KryShadowLogger(CustomLogger):
    async def async_log_success_event(self, kwargs, response_obj, start_time, end_time):
        u = response_obj.get("usage", {})
        run_cheap_shadow_and_check(...)   # offline (log_then_replay) for the executable slice
        row = emit_row(
            frame_id=kwargs["litellm_call_id"], request_id=kwargs["litellm_call_id"],
            measurement_class="deployable_validated",
            requested_model=kwargs.get("model"), served_model=response_obj.get("model"),
            response_cost_usd=kwargs.get("response_cost"), provider_cost_source="provider_receipt",
            frontier_holdout_cost_usd=kwargs.get("response_cost"),       # the served frontier call
            frontier_holdout_output_tokens=u.get("completion_tokens"),
            cheap_fast_correct=check_passed, deployable_validator_pass=check_passed,
            cheap_fast_cost_usd=cheap_shadow_cost, cheap_fast_output_tokens=cheap_tokens,
            correctness_source="deployable", checkable_slice="code_executable",
            deterministic_check_kind="unit_test", deterministic_check_receipt=test_summary,
            latency_class="background")
        append_jsonl(row)   # digest-only; safe to leave the customer boundary
'''


def self_test() -> None:
    """Mock-data self-test — NO provider calls, claims NO measured savings."""
    # 1) deployable-validated PASS: cheap handled it, the executable check passed.
    r = emit_row(
        frame_id="mock-1", request_id="req-1",
        intent_text="write a function that adds two ints",
        outcome_text="def add(a, b): return a + b",
        requested_model="best/code", served_model="frontier/opus",
        measurement_class="deployable_validated", correctness_source="deployable",
        cheap_fast_correct=True, deployable_validator_pass=True,
        cheap_fast_cost_usd=0.0008, validator_cost_usd=0.0,
        frontier_holdout_cost_usd=0.012,            # the avoided (real served) frontier cost
        cheap_fast_output_tokens=40, frontier_holdout_output_tokens=120,
        response_cost_usd=0.012, provider_cost_source="provider_receipt",
        latency_class="background", checkable_slice="code_executable",
        deterministic_check_kind="unit_test", deterministic_check_receipt="3 passed",
        output_axis_class="short_answer", output_tokens_capped=True)
    assert r["p0_pass"] is True
    assert abs(r["measured_row_value_usd"] - (0.012 - 0.0008)) < 1e-9   # net, not gross
    assert "prompt" not in r and "response" not in r
    assert r["intent_digest"] and len(r["intent_digest"]) == 64
    assert len(r["row_digest"]) == 64

    # 2) P0 FAIL: cheap LOOKED right but the frontier counterfactual was wrong -> zero value.
    r2 = emit_row(
        frame_id="mock-2", request_id="req-2",
        measurement_class="frontier_holdout", correctness_source="frontier_holdout",
        cheap_fast_correct=True, frontier_correct=False,
        cheap_fast_cost_usd=0.0008, frontier_holdout_cost_usd=0.012,
        cheap_fast_output_tokens=40, frontier_holdout_output_tokens=120,
        provider_cost_source="provider_receipt")
    assert r2["p0_pass"] is False
    assert r2["measured_row_value_usd"] == 0.0      # no credit unless quality is preserved

    # 3) privacy gate must reject a forbidden raw field.
    raised = False
    try:
        bad = emit_row(frame_id="m3", request_id="r3", measurement_class="population_only")
        bad["response"] = "raw model output"
        privacy_gate(bad)
    except ValueError as e:
        raised = "forbidden" in str(e)
    assert raised, "privacy gate failed to reject a forbidden raw field"

    print("kry_shadow_emitter self-test: PASS (mock data; claims NO measured savings)")
    print("  P0-pass row value (net):  $%.6f" % r["measured_row_value_usd"])
    print("  P0-fail row value (gated): $%.6f" % r2["measured_row_value_usd"])
    print("  privacy gate rejects forbidden raw fields: OK")
    print("  sample digest-only row keys:", ", ".join(sorted(r)[:8]), "...")


if __name__ == "__main__":
    self_test()
