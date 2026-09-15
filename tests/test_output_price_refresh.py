"""The output price table values current provider models at list price, additively.

Ground rule 1 (SPEC_DEVELOPMENT.md): existing vectors and verdicts never change meaning. So current
models are added as exact provider ids ahead of the older substring entries, every multiplier legal in
0.1.5 stays legal, and receipts minted at the older prices still verify.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from kry import kry_token as kt

ROOT = Path(__file__).resolve().parents[1]

# The published multiplier set shipped in 0.1.5 (vectors/primitives/legal_multipliers.json at v0.1.5).
_LEGAL_IN_0_1_5 = (
    0.0, 0.002, 0.006, 0.02, 0.022, 0.022000000000000002, 0.024, 0.026, 0.028, 0.044,
    0.044000000000000004, 0.05, 0.1, 0.25, 0.256, 0.276, 0.278, 0.3, 0.35, 0.356, 0.376, 0.378, 0.4,
    0.6, 0.7, 0.95, 0.956, 0.976, 0.978, 1.0,
)


def _legal(value: float, legal: set[float]) -> bool:
    return any(abs(value - m) <= 1e-3 for m in legal)


@pytest.mark.parametrize(("model", "expected"), [
    ("claude-fable-5-1", 1.0),            # $50 output, capped at the $25 frontier
    ("claude-fable-5", 1.0),
    ("claude-opus-5", 1.0),               # $25
    ("claude-sonnet-5", 0.4),             # $10
    ("claude-sonnet-4-6", 0.6),           # $15
    ("claude-sonnet-4-5", 0.6),           # $15
    ("claude-haiku-4-5", 0.2),            # $5
    ("claude-haiku-4-5-20251001", 0.2),   # dated snapshot of the same model
])
def test_current_provider_models_use_their_list_price(model, expected):
    assert kt.value_multiplier(model) == pytest.approx(expected)


@pytest.mark.parametrize(("model", "expected"), [
    ("gh/claude-opus-4.8", 1.0),
    ("or/anthropic/claude-sonnet-4.8", 0.3),   # older substring entries keep their values
    ("gh/claude-haiku", 0.05),
    ("gemini-2.5-pro", 0.0),
    ("some-unlisted-model", 0.05),              # unknown models keep the conservative floor
])
def test_older_entries_and_the_unknown_floor_are_unchanged(model, expected):
    assert kt.value_multiplier(model) == pytest.approx(expected)


def test_every_multiplier_legal_in_0_1_5_stays_legal():
    legal = set(kt.published_multipliers().values())
    missing = [m for m in _LEGAL_IN_0_1_5 if not _legal(m, legal)]
    assert not missing, f"multipliers legal in 0.1.5 are no longer legal: {missing}"


def test_the_adversarial_illegal_multiplier_stays_illegal():
    vector = json.loads((ROOT / "vectors" / "savings" / "adversarial" / "magnitude_illegal_multiplier.json")
                        .read_text(encoding="utf-8"))
    assert vector["expected"]["verdict"] == "INVALID"
    assert not _legal(0.5, set(kt.published_multipliers().values()))


def test_browser_page_inlines_exactly_the_published_multiplier_set():
    page = (ROOT / "verifiers" / "web" / "index.html").read_text(encoding="utf-8")
    match = re.search(r"setMultipliers\(\[([^\]]*)\]\)", page)
    assert match, "verifiers/web/index.html no longer inlines setMultipliers([...])"
    inlined = [float(x) for x in match.group(1).replace("\n", " ").split(",") if x.strip()]
    published = json.loads((ROOT / "vectors" / "primitives" / "legal_multipliers.json")
                           .read_text(encoding="utf-8"))["multipliers"]
    assert inlined == published


def test_every_price_has_a_recorded_source():
    provenance = kt.price_provenance()["models"]
    assert set(provenance) == set(kt._MODEL_OUTPUT_USD_PER_M)
    assert all(entry.get("quality") in ("list", "estimate") and entry.get("source")
               for entry in provenance.values())
