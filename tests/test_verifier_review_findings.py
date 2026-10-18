"""Review regressions for action witness presence and direct anchor API boundaries."""
from __future__ import annotations

import pytest

from test_verifier_hardening import _canon, _js, _receipt, kav, kv
from test_verifier_hardening_d import _action


@pytest.mark.parametrize("tier", ["server_witnessed", "attested"])
@pytest.mark.parametrize("witness", [0, 0.0, [], {}, False, None, ""])
@pytest.mark.parametrize("floor, expected", [(0.0, "VALID"), (1.0, "INVALID")])
def test_action_falsey_witness_does_not_raise_the_floor(tier, witness, floor, expected, tmp_path):
    # Presence follows the reference's Python truthiness, including empty containers and zero.
    att = _action(evidence_tier=tier, server_evidence_commit=witness)
    att["veracity"] = {"veracity_floor": floor}
    assert kav.verify_action_attestation(att)[0] == (expected == "VALID")
    assert _js([_canon(att)], tmp_path)[0]["verdict"] == expected


@pytest.mark.parametrize("tier", ["server_witnessed", "attested"])
@pytest.mark.parametrize("witness", ["ab" * 32, 1, 1.0, True, [0], {"proof": None}])
@pytest.mark.parametrize("floor, expected", [(1.0, "VALID"), (0.0, "INVALID")])
def test_action_truthy_witness_preserves_the_floor(tier, witness, floor, expected, tmp_path):
    att = _action(evidence_tier=tier, server_evidence_commit=witness)
    att["veracity"] = {"veracity_floor": floor}
    assert kav.verify_action_attestation(att)[0] == (expected == "VALID")
    assert _js([_canon(att)], tmp_path)[0]["verdict"] == expected


@pytest.mark.parametrize("tip_fields", [
    pytest.param({}, id="missing"),
    pytest.param({"chain_tip": None}, id="null"),
    pytest.param({"chain_tip": 0}, id="integer"),
    pytest.param({"chain_tip": []}, id="array"),
    pytest.param({"chain_tip": {}}, id="object"),
    pytest.param({"chain_tip": True}, id="boolean"),
    pytest.param({"chain_tip": ""}, id="empty"),
    pytest.param({"chain_tip": "bad"}, id="short"),
    pytest.param({"chain_tip": "g" * 64}, id="nonhex"),
])
def test_action_anchor_rejects_a_matching_malformed_tip(tip_fields):
    # Equal missing/malformed values do not establish a published chain commitment.
    link = {"chain_hash": tip_fields["chain_tip"]} if tip_fields else {}
    anchor = {"kind": "kry_action_anchor", "count": 1, **tip_fields}
    ok, errors = kav.verify_against_anchor({"links": [link]}, anchor)
    assert not ok and errors


@pytest.mark.parametrize("count", [0, 1])
def test_action_anchor_accepts_an_existing_chain_prefix(count):
    att = _action()
    tip = att["links"][count - 1]["chain_hash"] if count else "0" * 64
    anchor = {"kind": "kry_action_anchor", "count": count, "chain_tip": tip}
    assert kav.verify_against_anchor(att, anchor) == (True, [])


@pytest.mark.parametrize("count", [0, 1])
@pytest.mark.parametrize("att", [[], "x", 1, None, True])
def test_savings_anchor_rejects_non_object_attestations(att, count):
    anchor = {"schema": "kry_chain_anchor/v1", "count": count, "tip": "0" * 64}
    ok, errors = kv.verify_attestation_against_anchor(att, anchor)
    assert not ok and errors


@pytest.mark.parametrize("count", [0, 1])
@pytest.mark.parametrize("fields", [
    pytest.param({}, id="missing"),
    pytest.param({"links": None}, id="null"),
    pytest.param({"links": {}}, id="object"),
    pytest.param({"links": "x"}, id="string"),
    pytest.param({"links": 1}, id="integer"),
])
def test_savings_anchor_requires_a_links_list(fields, count):
    anchor = {"schema": "kry_chain_anchor/v1", "count": count, "tip": "0" * 64}
    ok, errors = kv.verify_attestation_against_anchor(fields, anchor)
    assert not ok and errors


@pytest.mark.parametrize("genesis", [False, True])
def test_savings_anchor_accepts_an_existing_chain_prefix(genesis):
    att = _receipt()
    link = att["links"][0]
    anchor = {"schema": "kry_chain_anchor/v1", "count": 0 if genesis else link["seq"],
              "tip": "0" * 64 if genesis else link["chain_hash"]}
    assert kv.verify_attestation_against_anchor(att, anchor) == (True, [])
