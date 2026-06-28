"""Adversarial regressions for the action-receipt layer (kry_action).

Every test that matters here is a FORGERY the chain + the stranger verifier must
catch, or an honesty property (content-sealing, tier coercion, floor re-derivation)
that must hold. Mirrors the savings layer's test_hardening / test_external_verify
discipline: the point is not that the happy path works, but that the failure paths
are caught.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest

from kry import kry_action

# Import the STANDALONE stranger verifier the way a third party would (it must not
# import any part of the kry package — asserted in test_verifier_is_independent).
import importlib.util

_VERIFIER_PATH = Path(__file__).resolve().parents[1] / "scripts" / "kry_action_verify.py"
_spec = importlib.util.spec_from_file_location("kry_action_verify", _VERIFIER_PATH)
verifier = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(verifier)


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setenv("KRY_DATA_DIR", str(tmp_path / "kry_data"))
    kry_action.reset_state_for_tests()
    yield
    kry_action.reset_state_for_tests()


def _record_three():
    kry_action.record("search", {"q": "x"}, result={"hits": 3}, agent_id="a")
    kry_action.record(
        "trade", {"qty": 5}, result={"filled": True},
        evidence_tier=kry_action.TIER_SERVER_WITNESSED,
        server_evidence={"sig": "abc"}, agent_id="a")
    kry_action.record("email", {"to": "z"}, result=None, has_result=False,
                      status="error", agent_id="a")


# ── happy path ────────────────────────────────────────────────────────────────

def test_happy_path_chain_and_verifier_agree():
    _record_three()
    ok, errors = kry_action.verify_action_chain()
    assert ok, errors
    att = kry_action.build_action_attestation()
    assert att["action_count"] == 3
    v_ok, v_errors, _ = verifier.verify_action_attestation(att)
    assert v_ok, v_errors


def test_veracity_floor_one_server_witnessed_of_three():
    _record_three()
    att = kry_action.build_action_attestation()
    # 1 of 3 actions externally witnessed -> floor 0.3333
    assert att["veracity"]["veracity_floor"] == pytest.approx(1 / 3, abs=1e-4)
    assert att["veracity"]["anchored_actions"] == 1


# ── tamper detection ──────────────────────────────────────────────────────────

def test_tampering_an_arg_commit_is_caught():
    _record_three()
    att = kry_action.build_action_attestation()
    att["links"][0]["args_commit"] = "f" * 64  # rewrite a committed argument
    ok, errors, _ = verifier.verify_action_attestation(att)
    assert not ok
    assert any("receipt_hash mismatch" in e for e in errors)


def test_tampering_a_tool_name_is_caught():
    _record_three()
    att = kry_action.build_action_attestation()
    att["links"][2]["tool"] = "wire_transfer"  # relabel the action after the fact
    ok, errors, _ = verifier.verify_action_attestation(att)
    assert not ok


def test_tampering_status_is_caught():
    _record_three()
    att = kry_action.build_action_attestation()
    att["links"][2]["status"] = "ok"  # hide a failure
    ok, errors, _ = verifier.verify_action_attestation(att)
    assert not ok


def test_reordering_links_is_caught():
    _record_three()
    att = kry_action.build_action_attestation()
    att["links"][0], att["links"][1] = att["links"][1], att["links"][0]
    ok, errors, _ = verifier.verify_action_attestation(att)
    assert not ok
    assert any("chain_hash mismatch" in e for e in errors)


def test_dropping_a_link_is_caught():
    _record_three()
    att = kry_action.build_action_attestation()
    del att["links"][1]
    att["action_count"] = 2
    ok, errors, _ = verifier.verify_action_attestation(att)
    assert not ok


def test_inserting_a_forged_link_is_caught():
    _record_three()
    att = kry_action.build_action_attestation()
    forged = dict(att["links"][0])
    forged["receipt_id"] = "act-99"
    att["links"].insert(1, forged)
    att["action_count"] = 4
    ok, errors, _ = verifier.verify_action_attestation(att)
    assert not ok


def test_duplicate_receipt_id_is_caught():
    _record_three()
    att = kry_action.build_action_attestation()
    att["links"][1]["receipt_id"] = att["links"][0]["receipt_id"]
    ok, errors, _ = verifier.verify_action_attestation(att)
    assert not ok
    assert any("duplicate receipt_id" in e for e in errors)


# ── tier honesty ──────────────────────────────────────────────────────────────

def test_minter_refuses_anchored_tier_without_witness():
    with pytest.raises(ValueError, match="requires server_evidence_commit"):
        kry_action.record("trade", {"qty": 1},
                          evidence_tier=kry_action.TIER_SERVER_WITNESSED)


def test_verifier_coerces_forged_anchored_tier_to_self_reported():
    # Forge a chain whose link CLAIMS server_witnessed but carries no witness, re-mint
    # it cleanly from genesis so integrity passes, then confirm the floor is NOT inflated.
    kry_action.record("search", {"q": "x"}, result={"hits": 1}, agent_id="a")
    att = kry_action.build_action_attestation()
    link = att["links"][0]
    link["evidence_tier"] = "attested"          # lie about the tier
    link["server_evidence_commit"] = None       # ...with no witness
    # re-mint so receipt_hash/chain_hash are internally consistent
    payload = verifier._receipt_payload(link)
    rh = hashlib.sha256(verifier._canon(payload).encode()).hexdigest()
    ch = hashlib.sha256(f"{'0'*64}:{rh}".encode()).hexdigest()
    link["receipt_hash"], link["chain_hash"] = rh, ch
    att["chain_tip"] = ch
    att["veracity"]["veracity_floor"] = 0.0     # an HONEST attestation already coerces it

    ok, errors, warnings = verifier.verify_action_attestation(att)
    assert ok, errors  # integrity holds...
    assert any("coerced to self_reported" in w for w in warnings)  # ...but it's not credited


def test_overclaimed_veracity_floor_is_rejected():
    kry_action.record("search", {"q": "x"}, result={"hits": 1})  # T0 -> floor 0.0
    att = kry_action.build_action_attestation()
    att["veracity"]["veracity_floor"] = 1.0  # claim full external backing
    ok, errors, _ = verifier.verify_action_attestation(att)
    assert not ok
    assert any("veracity_floor mismatch" in e for e in errors)


# ── content-sealing ───────────────────────────────────────────────────────────

def test_no_raw_content_in_attestation():
    secret_arg = "PROJECT BLUEBIRD launch code 4471"
    secret_result = "approved by board member jane.doe@corp.com"
    kry_action.record("approve", {"memo": secret_arg}, result={"note": secret_result})
    att = kry_action.build_action_attestation()
    blob = json.dumps(att)
    assert secret_arg not in blob
    assert secret_result not in blob
    kry_action.assert_no_content_leak(att, [secret_arg, secret_result])


def test_commit_proves_membership_without_revealing():
    raw = {"memo": "PROJECT BLUEBIRD"}
    kry_action.record("approve", raw, result={"ok": True})
    att = kry_action.build_action_attestation()
    # A holder of the raw args recomputes the commitment and matches the receipt.
    assert kry_action.commit(raw) == att["links"][0]["args_commit"]
    # A different value does not.
    assert kry_action.commit({"memo": "PROJECT REDBIRD"}) != att["links"][0]["args_commit"]


# ── anchor / re-mint ──────────────────────────────────────────────────────────

def test_anchor_catches_remint_with_dropped_action():
    _record_three()
    anchor = kry_action.export_anchor()
    assert anchor["count"] == 3

    # Re-mint a 2-action chain (drop the failed email), cleanly from genesis.
    att = kry_action.build_action_attestation()
    links = [link for link in att["links"] if link["tool"] != "email"]
    prev = "0" * 64
    for link in links:
        payload = verifier._receipt_payload(link)
        rh = hashlib.sha256(verifier._canon(payload).encode()).hexdigest()
        ch = hashlib.sha256(f"{prev}:{rh}".encode()).hexdigest()
        link["receipt_hash"], link["chain_hash"] = rh, ch
        prev = ch
    att["links"], att["action_count"], att["chain_tip"] = links, len(links), prev
    att["veracity"] = kry_action._veracity(links)  # keep the re-mint fully consistent

    # Integrity alone passes (a re-mint is internally consistent — the known limit)...
    ok, _, _ = verifier.verify_action_attestation(att)
    assert ok
    # ...but the published anchor catches it.
    a_ok, a_errors = verifier.verify_against_anchor(att, anchor)
    assert not a_ok
    assert any("DROPPED" in e or "RE-MINTED" in e for e in a_errors)


def test_anchor_matches_an_untouched_log():
    _record_three()
    anchor = kry_action.export_anchor()
    att = kry_action.build_action_attestation()
    a_ok, a_errors = verifier.verify_against_anchor(att, anchor)
    assert a_ok, a_errors


# ── determinism / cross-language preimage ─────────────────────────────────────

def test_receipt_hash_is_deterministic_for_fixed_inputs():
    r1 = kry_action.ActionReceipt.create(
        "act-1", "tool", kry_action.commit({"a": 1}), "0" * 64,
        result_commit=kry_action.commit({"b": 2}), status="ok",
        ts=1_700_000_000.5, agent_id="a")
    r2 = kry_action.ActionReceipt.create(
        "act-1", "tool", kry_action.commit({"a": 1}), "0" * 64,
        result_commit=kry_action.commit({"b": 2}), status="ok",
        ts=1_700_000_000.5, agent_id="a")
    assert r1.receipt_hash == r2.receipt_hash
    assert r1.chain_hash == r2.chain_hash


def test_canon_f64_is_ieee754_big_endian():
    # Pin the language-neutral float encoding (a Rust/JS/Go verifier must match this).
    assert kry_action._canon_f64(1.0) == "3ff0000000000000"
    assert kry_action._canon_f64(0.0) == "0000000000000000"
    assert verifier._canon_f64(1.0) == kry_action._canon_f64(1.0)


# ── cross-process state recovery ──────────────────────────────────────────────

def test_new_process_appends_to_existing_chain():
    kry_action.record("a", {"x": 1}, result={"ok": True})
    tip_after_one = kry_action.chain_tip()[1]
    # Simulate a fresh process: drop in-memory state, re-read from the log.
    kry_action.reset_state_for_tests()
    r2 = kry_action.record("b", {"y": 2}, result={"ok": True})
    # The second receipt must chain onto the first (not restart from genesis).
    assert r2.chain_hash != tip_after_one
    ok, errors = kry_action.verify_action_chain()
    assert ok, errors
    assert kry_action.chain_tip()[0] == 2


# ── the verifier must be a genuine STRANGER ───────────────────────────────────

def test_verifier_is_independent_of_the_package():
    src = _VERIFIER_PATH.read_text()
    # It re-implements the checks from spec; it must not import the package under test.
    assert not re.search(r"^\s*(from|import)\s+kry\b", src, re.MULTILINE), \
        "kry_action_verify must not import the kry package — it is the stranger's check"
