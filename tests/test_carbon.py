"""kry_carbon — the avoided-emissions ESTIMATE denomination.

Closes a real coverage gap surfaced by the capability audit: kry_carbon shipped
untested. These pin the arithmetic and, importantly, that it labels itself an
ESTIMATE (not a certified credit) — the honest boundary must not silently drift.
"""
from __future__ import annotations

import kry.kry_carbon as kc


def test_zero_in_zero_out():
    assert kc.kry_to_energy_kwh(0) == 0.0
    s = kc.carbon_statement(0)
    assert s["energy_kwh_avoided"] == 0.0 and s["co2_grams_avoided"] == 0.0


def test_co2_is_energy_times_grid_intensity():
    k = 1_000_000.0
    kwh = kc.kry_to_energy_kwh(k)
    assert abs(kwh - k * kc._JOULES_PER_TOKEN / kc._J_PER_KWH) < 1e-12
    assert abs(kc.kry_to_co2_grams(k) - kwh * kc._GRID_CO2_G_PER_KWH) < 1e-9


def test_monotonic_in_kry():
    assert kc.carbon_statement(2_000_000)["co2_grams_avoided"] > \
           kc.carbon_statement(1_000_000)["co2_grams_avoided"]


def test_statement_labels_itself_an_estimate_not_a_credit():
    s = kc.carbon_statement(500_000)
    assert "ESTIMATE" in s["status"]
    assert isinstance(s["to_certify_requires"], list) and s["to_certify_requires"]
    assert "mint chain" in s["audit_trail"]
    # basis is disclosed (the two estimate constants), so the number is reproducible
    assert s["basis"]["joules_per_token"] == kc._JOULES_PER_TOKEN
    assert s["basis"]["grid_co2_g_per_kwh"] == kc._GRID_CO2_G_PER_KWH
