"""KRY Carbon — second denomination: avoided inference → avoided energy → avoided CO2.

The novel, legal, globally-relevant edge: KRY measures frontier-equivalent tokens
AVOIDED (via cache/compression/routing). Avoided inference = avoided datacenter
ENERGY = avoided CO2 emissions. Avoided emissions are the basis of a real,
regulated, tradeable instrument (carbon credits / white certificates / ISO 50001).

This gives KRY a SECOND denomination that bridges to an actual market — and it
directly addresses the global datacenter/energy problem (less compute = less load).

    1 KRY  =  1 frontier-equiv output token avoided
           →  E_token joules avoided   (GPU forward-pass + datacenter overhead)
           →  kWh avoided              (J / 3.6e6)
           →  g CO2 avoided            (kWh × grid carbon intensity)

## HONEST LEGAL BOUNDARY (read before any external claim)

This computes an avoided-emissions ESTIMATE. It is NOT a certified carbon credit.
To become a SELLABLE credit, the standard requires, and this module does NOT yet
provide:
  - a documented baseline (what WOULD have been emitted) — ISO 50001 audit
  - ADDITIONALITY (the saving wouldn't have happened anyway)
  - third-party verification (Verra / Gold Standard / a registered verifier)
  - registry issuance + serialization (no double-counting across registries)

What this module DOES provide that those verifiers need: a tamper-evident,
hash-chained audit trail (the KRY mint chain) of every avoidance event with its
dated basis. That is the substrate a verifier audits — we build the measurement
+ provenance honestly and label it an estimate until certified. No overclaiming.

## Constants (documented estimates, all env-configurable)

Energy per avoided frontier output token (J): published frontier-inference
estimates vary widely (~0.3–4 J/token at the GPU; datacenter PUE ~1.4–1.6 adds
overhead). Default 2.0 J/token is a labeled mid estimate, NOT a measurement of
any specific provider. Set KRY_JOULES_PER_TOKEN to your own measured figure.

Grid carbon intensity (g CO2/kWh): global avg ~400–480; varies hugely by region
(hydro/nuclear ~30–80, coal ~700+). Default 400. Set KRY_GRID_CO2_G_PER_KWH.
"""
from __future__ import annotations

import os

# 1 KRY = 1 frontier-equivalent output token avoided (matches kry_token).
_JOULES_PER_TOKEN = float(os.environ.get("KRY_JOULES_PER_TOKEN", "2.0"))   # labeled estimate
_GRID_CO2_G_PER_KWH = float(os.environ.get("KRY_GRID_CO2_G_PER_KWH", "400.0"))
_J_PER_KWH = 3.6e6


def kry_to_energy_kwh(kry: float) -> float:
    """Avoided energy (kWh) for `kry` frontier-equivalent tokens avoided."""
    return (kry * _JOULES_PER_TOKEN) / _J_PER_KWH


def kry_to_co2_grams(kry: float) -> float:
    """Avoided CO2 (grams) — kWh avoided × grid carbon intensity."""
    return kry_to_energy_kwh(kry) * _GRID_CO2_G_PER_KWH


def carbon_statement(total_kry_avoided: float) -> dict:
    """Avoided-emissions ESTIMATE for a KRY total. Clearly labeled, verifiable
    via the mint chain, NOT a certified credit (see module docstring)."""
    kwh = kry_to_energy_kwh(total_kry_avoided)
    co2_g = kry_to_co2_grams(total_kry_avoided)
    return {
        "kry_avoided": round(total_kry_avoided, 2),
        "energy_kwh_avoided": round(kwh, 6),
        "co2_grams_avoided": round(co2_g, 4),
        "co2_kg_avoided": round(co2_g / 1000.0, 6),
        "basis": {
            "joules_per_token": _JOULES_PER_TOKEN,
            "grid_co2_g_per_kwh": _GRID_CO2_G_PER_KWH,
            # Empirical ceiling (2026-06-04 transcript dig): the provider returns
            # usage.inference_geo = "not_available", so the datacenter — and thus
            # the real grid carbon intensity — is STRUCTURALLY unknowable per
            # inference, not merely unmeasured. The CO2 figure is therefore a
            # global-average estimate with an irreducible regional uncertainty
            # (hydro/nuclear ~30–80 vs coal ~700+ g/kWh ⇒ up to ~10–20×), the same
            # honest-ceiling discipline as veracity_floor. No geo, no certification.
            "grid_intensity_known": False,
            "grid_uncertainty_note": "provider redacts inference_geo; CO2 is global-avg estimate, ±~10-20x by region",
        },
        "status": "ESTIMATE — not a certified carbon credit (grid region unknowable: inference_geo redacted)",
        "to_certify_requires": [
            "documented baseline (ISO 50001 audit)",
            "additionality proof",
            "third-party verification (Verra/Gold Standard)",
            "registry issuance + serialization",
        ],
        "audit_trail": "KRY mint chain (hash-chained, dated) — the substrate a verifier audits",
    }


def from_mint_chain() -> dict:
    """Carbon statement computed from the live KRY mint chain (dated, tamper-evident)."""
    try:
        from kry.kry_mint import retained_dollars_dated
        # Energy/CO2 scales with COMPUTE avoided, not dollars: a free-tier call
        # still burns datacenter energy, so use raw tokens_saved (not the
        # edge-weighted kry_minted, which is 0 for free-tier avoidance).
        total_tokens = retained_dollars_dated().get("total_tokens_saved", 0.0)
    except Exception:
        total_tokens = 0.0
    out = carbon_statement(total_tokens)
    out["source"] = "kry_mint chain (verifiable)"
    return out
