"""Producer output must satisfy the independent SPEC derivations and anchor profile."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import random
import shutil
import subprocess

import pytest

from kry import kry_attest as ka, kry_mint as km

ROOT = Path(__file__).resolve().parents[1]


def _stranger():
    spec = importlib.util.spec_from_file_location("producer_stranger", ROOT / "scripts/kry_verify.py")
    verifier = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(verifier)
    return verifier


def _check(att, anchor=None):
    text = att.to_public_json()
    ok, errors = ka.verify_attestation(text)
    assert ok, errors
    verifier = _stranger()
    data = json.loads(text)
    ok, errors = verifier.verify_attestation(data)
    assert ok, errors
    if anchor is not None:
        ok, errors = verifier.verify_attestation_against_anchor(data, anchor)
        assert ok, errors
    return {"text": text, "anchor": anchor}


def _check_js(cases):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required for the independent JS verifier")
    result = subprocess.run(
        [node, "--input-type=module", "-e", """
import {readFileSync} from 'node:fs';
import {explain} from './verifiers/js/verify.mjs';
const cases = JSON.parse(readFileSync(0, 'utf8'));
console.log(JSON.stringify(cases.map(({text, anchor}) => explain(text, anchor))));
"""], input=json.dumps(cases), text=True, capture_output=True, cwd=ROOT, check=True,
    )
    outcomes = json.loads(result.stdout)
    assert len(outcomes) == len(cases)
    assert all(outcome["verdict"] == "VALID" for outcome in outcomes), outcomes


def test_producer_uses_declared_total_for_usd():
    assert km.mint("feed_bag_deposit", 6041, evidence="deposit") is not None
    assert km.mint("compression", 2864, evidence="compression") is not None
    att = ka.build_attestation()
    assert att.total_kry == 5947.1
    assert att.usd_equivalent == 0.148678
    _check_js([_check(att)])


@pytest.mark.parametrize("anchored_tokens,self_tokens,expected_total,expected_floor", [
    (48.68161, 80.36254, 12.9044, 0.3773),
    (84.8875, 59.76195, 14.4649, 0.5869),
    (31.2684, 7.42264, 3.8691, 0.8081),
])
def test_producer_floor_uses_rounded_operands(anchored_tokens, self_tokens, expected_total,
                                           expected_floor):
    assert km.mint("continuity_capsule", anchored_tokens, evidence="metered", evidence_tier="provider_metered",
                   metered_tokens=[1, 1]) is not None
    assert km.mint("continuity_capsule", self_tokens, evidence="self") is not None
    att = ka.build_attestation()
    assert att.total_kry == expected_total
    assert att.veracity["veracity_floor"] == expected_floor
    _check_js([_check(att)])


@pytest.mark.parametrize("blank", ["\n", " \t\n", "\n\n"])
def test_blank_lines_preserve_public_anchor_consistency(blank):
    for i in range(3):
        assert km.mint("cache_hit", 100 + i, evidence=f"receipt-{i}") is not None
    anchor = km.export_chain_anchor()
    original = km._MINT_LOG_PATH.read_text(encoding="utf-8")
    spaced = blank + blank.join(original.splitlines(keepends=True)) + blank
    km._MINT_LOG_PATH.write_text(spaced, encoding="utf-8")
    ok, errors = km.verify_chain_against_anchor(anchor)
    assert ok, errors
    att = ka.build_attestation()
    assert [link["seq"] for link in att.links] == [1, 2, 3]
    _check_js([_check(att, anchor)])
    assert km._MINT_LOG_PATH.read_text(encoding="utf-8") == spaced


@pytest.mark.parametrize("seed", [22, 106, 2026])
def test_generated_producer_prefixes_verify(seed):
    rng = random.Random(seed)
    cases = [_check(ka.build_attestation())]
    events = ("cache_hit", "compression", "feed_bag_deposit", "continuity_capsule")
    for i in range(40):
        event = rng.choice(events)
        tokens = 1 + rng.randint(1, 10000) / rng.choice((1, 10, 100, 100000))
        tier = rng.choice(("self_reported", "provider_metered"))
        assert km.mint(event, tokens, evidence=f"{seed}-{i}", evidence_tier=tier,
                       metered_tokens=[i + 1, 1] if tier == "provider_metered" else None) is not None
        cases.append(_check(ka.build_attestation()))
    _check_js(cases)
