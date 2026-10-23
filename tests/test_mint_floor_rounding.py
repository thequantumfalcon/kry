"""The local veracity floor uses the public attestation's rounded operands."""
import pytest

from kry import kry_attest, kry_mint


@pytest.mark.parametrize("anchored_tokens,self_tokens,expected_floor", [
    (48.68161, 80.36254, 0.3773),
    (84.8875, 59.76195, 0.5869),
    (31.2684, 7.42264, 0.8081),
])
def test_local_floor_matches_public_contract(anchored_tokens, self_tokens, expected_floor):
    assert kry_mint.mint("continuity_capsule", anchored_tokens, evidence="anchored",
                         evidence_tier="provider_metered", metered_tokens=[1, 1]) is not None
    assert kry_mint.mint("continuity_capsule", self_tokens, evidence="self") is not None
    assert kry_mint.verify_chain()[0]

    breakdown = kry_mint.veracity_breakdown()
    assert breakdown["veracity_floor"] == expected_floor
    assert breakdown["veracity_floor"] == round(
        breakdown["anchored_kry"] / breakdown["total_kry"], 4)
    attestation = kry_attest.build_attestation()
    assert attestation.veracity["veracity_floor"] == breakdown["veracity_floor"]
    assert kry_attest.verify_attestation(attestation.to_public_json())[0]
