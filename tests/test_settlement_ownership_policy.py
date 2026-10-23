"""An optional operator policy can bind a trusted principal to an enrolled chain.

The principal and enrollment below are constructed trust inputs. This does not
implement authentication or change settlement's default per-party contract.
"""
import importlib.util
import json
from pathlib import Path

import pytest

from kry import kry_attest as ka, kry_mint as km, kry_settlement as ks


@pytest.fixture
def enrolled_policy(tmp_path, monkeypatch):
    # Keep real lease enforcement, isolated from other callers and lab runs.
    monkeypatch.setenv("KRY_SETTLE_LEASE_DIR", str(tmp_path / "policy-leases"))
    spec = importlib.util.spec_from_file_location(
        "ownership_stranger", Path(__file__).resolve().parents[1] / "scripts/kry_verify.py")
    verifier = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(verifier)
    assert km.mint("cache_hit", 1000, evidence="enrolled-A-receipt") is not None
    original = ka.build_attestation().to_public_json()
    # These values stand in for authentication and enrollment performed outside
    # the offer. Neither is accepted from the presented attestation or holder.
    request_context = {"authenticated_principal": "A"}
    enrolled_anchors = {"A": km.export_chain_anchor()}

    def policy(offer, candidate):
        principal = request_context["authenticated_principal"]
        if offer.from_party != principal:
            return "offer party differs from authenticated principal"
        anchor = enrolled_anchors.get(principal)
        if anchor is None:
            return "principal has no enrolled chain"
        valid, _ = verifier.verify_attestation_against_anchor(json.loads(candidate), anchor)
        if not valid:
            return "attestation does not retain the enrolled chain prefix"
        return None

    previous = ks._SETTLEMENT_GUARD
    ks.set_settlement_guard(policy)
    try:
        yield request_context, original, verifier
    finally:
        ks.set_settlement_guard(previous)


@pytest.mark.parametrize("extend", [False, True])
def test_enrolled_principal_can_use_original_or_extended_chain(enrolled_policy, extend):
    _, candidate, _ = enrolled_policy
    if extend:
        assert km.mint("cache_hit", 100, evidence="later-A-receipt") is not None
        candidate = ka.build_attestation().to_public_json()
    offer = ks.make_offer("A", "receiver", 500.0, 100, now=1000.0)
    grant, reason = ks.verify_and_accept(offer, candidate, now=1001.0)
    assert grant is not None and reason == "accepted", reason


@pytest.mark.parametrize("principal,claimed,reason", [
    ("A", "A ", "differs from authenticated principal"),
    ("A", "a", "differs from authenticated principal"),
    ("C", "A", "differs from authenticated principal"),
    ("C", "C", "no enrolled chain"),
])
def test_policy_rejects_aliases_and_other_accounts(enrolled_policy, principal, claimed, reason):
    context, candidate, _ = enrolled_policy
    context["authenticated_principal"] = principal
    offer = ks.make_offer(claimed, "receiver", 500.0, 100, now=1000.0)
    grant, explanation = ks.verify_and_accept(offer, candidate, now=1001.0)
    assert grant is None and reason in explanation
    assert "policy guard" in explanation
    assert not ks._REGISTRY_PATH.exists()
    assert ks._PENDING_RESERVATIONS == {}


def test_self_declared_holder_is_not_an_enrolled_owner(enrolled_policy):
    context, original, verifier = enrolled_policy
    context["authenticated_principal"] = "C"
    candidate = json.loads(original)
    candidate["holder"] = "C"
    candidate["attestation_hash"] = verifier._attestation_hash(candidate)
    text = json.dumps(candidate)
    assert ka.verify_attestation(text)[0]
    offer = ks.make_offer("C", "receiver", 500.0, 100, now=1000.0)
    grant, reason = ks.verify_and_accept(offer, text, now=1001.0)
    assert grant is None and "no enrolled chain" in reason


def test_valid_full_remint_cannot_replace_enrolled_prefix(enrolled_policy, tmp_path, monkeypatch):
    _, original, _ = enrolled_policy
    replacement_log = tmp_path / "replacement" / "mint.jsonl"
    monkeypatch.setattr(km, "_MINT_LOG_PATH", replacement_log)
    assert km.mint("cache_hit", 1000, evidence="replacement-A-receipt") is not None
    candidate = ka.build_attestation(replacement_log).to_public_json()
    assert ka.verify_attestation(candidate)[0]
    assert json.loads(candidate)["chain_head"] != json.loads(original)["chain_head"]
    offer = ks.make_offer("A", "receiver", 500.0, 100, now=1000.0)
    grant, reason = ks.verify_and_accept(offer, candidate, now=1001.0)
    assert grant is None and "does not retain the enrolled chain prefix" in reason
