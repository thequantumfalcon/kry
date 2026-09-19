"""Cross-language regressions built from the public block specification, including raw wire text."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import random
import shutil
import struct
import subprocess
import sys
from pathlib import Path

import pytest

from kry.kry_attest import verify_attestation

_ROOT = Path(__file__).resolve().parents[1]
_NODE = shutil.which("node")


def _load(name):
    spec = importlib.util.spec_from_file_location(name, _ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


kv = _load("kry_verify")
kav = _load("kry_action_verify")


def _canon(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def _seal(att):
    att["attestation_hash"] = ""
    att["attestation_hash"] = _sha(_canon(att))
    return _canon(att)


def _receipt(value=1000.0, version=7):
    att = json.loads((_ROOT / "vectors/savings/valid/single_self_reported.json").read_text())["input"]
    link = att["links"][0]
    link.update(tokens_saved=value, kry_minted=value, earn_rate=1.0, hash_version=version)
    block = {k: link.get(k) for k in ("hash_version", "tokens_saved", "ts", "evidence_tier",
                                     "metered_tokens", "kry_minted", "earn_rate")}
    if version >= 5:
        for key in ("tokens_saved", "ts", "kry_minted", "earn_rate"):
            block[key] = struct.pack(">d", float(link[key])).hex()
    if version >= 6:
        block["receipt_id"] = link["receipt_id"]
    if version >= 7:
        block["event_type"] = link["event_type"]
    link["chain_hash"] = _sha("0" * 64 + ":" + link["receipt_hash"] + ":" + _canon(block))
    total = round(value, 4)
    att.update(chain_head=link["chain_hash"], total_kry=total, usd_equivalent=round(total * .000025, 6))
    att["veracity"] = {"by_tier": {"self_reported": total}, "anchored_kry": 0.0,
                       "self_reported_kry": total, "veracity_floor": 0.0}
    _seal(att)
    return att


def _python_verdict(raw):
    try:
        att = kv._json_loads(raw)
    except ValueError:
        return "PARSE_ERROR"
    verifier = kav.verify_action_attestation if att.get("kind") == "kry_action_attestation" else kv.verify_attestation
    return "VALID" if verifier(att)[0] else "INVALID"


def _js(raws, tmp_path):
    if _NODE is None:
        pytest.skip("node is not installed")
    inputs = tmp_path / "inputs.json"
    inputs.write_text(json.dumps(raws), encoding="utf-8")
    runner = tmp_path / "verify.mjs"
    runner.write_text(
        f'import {{ explain, setMultipliers }} from {json.dumps((_ROOT / "verifiers/js/verify.mjs").as_uri())};\n'
        'import { readFileSync } from "node:fs";\n'
        'setMultipliers(JSON.parse(readFileSync(process.argv[3], "utf8")).multipliers);\n'
        'console.log(JSON.stringify(JSON.parse(readFileSync(process.argv[2], "utf8")).map(raw => explain(raw))));\n',
        encoding="utf-8")
    out = subprocess.run([_NODE, str(runner), str(inputs), str(_ROOT / "vectors/primitives/legal_multipliers.json")],
                         capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


@pytest.mark.parametrize("value", [0.0, .03125, .09375, .0078125, .0234375, 2122.59595,
                                    312.5, 937.5, 902680278377.107, 1e21, 1e100, 1e308])
def test_receipt_rounding_matches_all_verifiers(value, tmp_path):
    att = _receipt(value)
    raw = _canon(att)
    assert kv.verify_attestation(att)[0]
    assert verify_attestation(raw)[0]
    assert _js([raw], tmp_path)[0]["verdict"] == "VALID"


def test_rounded_inflation_is_rejected(tmp_path):
    att = _receipt(902680278377.107)
    inflated = 902680278377.1072
    att["total_kry"] = inflated
    att["usd_equivalent"] = round(inflated * .000025, 6)
    att["veracity"]["by_tier"]["self_reported"] = inflated
    att["veracity"]["self_reported_kry"] = inflated
    raw = _seal(att)
    assert not kv.verify_attestation(att)[0]
    assert not verify_attestation(raw)[0]
    assert _js([raw], tmp_path)[0]["verdict"] == "INVALID"


@pytest.mark.parametrize("version", [4, 5, 6, 7])
def test_float_respellings_use_the_same_hash(version, tmp_path):
    raw = _canon(_receipt(version=version))
    raws = [raw.replace('"tokens_saved":1000.0', '"tokens_saved":' + spelling)
            for spelling in ("1e3", "1000.00", "1.000e+03")]
    assert all(_python_verdict(r) == "VALID" and verify_attestation(r)[0] for r in raws)
    assert all(r["verdict"] == "VALID" for r in _js(raws, tmp_path))


def test_literal_preserving_hash_is_not_canonical(tmp_path):
    att = _receipt()
    att["attestation_hash"] = ""
    raw = _canon(att).replace('"tokens_saved":1000.0', '"tokens_saved":1e3')
    raw = raw.replace('"attestation_hash":""', '"attestation_hash":"' + _sha(raw) + '"')
    assert _python_verdict(raw) == "INVALID"
    assert not verify_attestation(raw)[0]
    assert _js([raw], tmp_path)[0]["verdict"] == "INVALID"


@pytest.mark.parametrize("field", ["veracity_floor", "anchored_kry", "self_reported_kry"])
@pytest.mark.parametrize("value", ["1.0", None, True, [], {}])
def test_trust_summaries_require_numbers(field, value, tmp_path):
    att = _receipt()
    att["veracity"][field] = value
    raw = _seal(att)
    assert not kv.verify_attestation(att)[0]
    assert not verify_attestation(raw)[0]
    assert _js([raw], tmp_path)[0]["verdict"] == "INVALID"


@pytest.mark.parametrize("wire", ['"a\tb"', '"a\\uZZZZ"', '"a\\q"', '"unterminated'])
def test_malformed_strings_are_parse_errors(wire, tmp_path):
    att = _receipt()
    att["note"] = "a\x00"
    raw = _seal(att).replace('"a\\u0000"', wire)
    assert _python_verdict(raw) == "PARSE_ERROR"
    assert not verify_attestation(raw)[0]
    assert _js([raw], tmp_path)[0]["verdict"] == "PARSE_ERROR"


def test_overflowing_token_exponent_is_a_parse_error(tmp_path):
    att = _receipt()
    raw = _canon(att).replace('"tokens_saved":1000.0', '"tokens_saved":1e309')
    assert _python_verdict(raw) == "PARSE_ERROR"
    assert not verify_attestation(raw)[0]
    assert _js([raw], tmp_path)[0]["verdict"] == "PARSE_ERROR"
    att["links"][0]["tokens_saved"] = 10 ** 400
    assert not kv.verify_attestation(att)[0]  # dict API must not crash on arbitrary-size integers
    raw = _seal(att)
    assert not verify_attestation(raw)[0]
    assert _js([raw], tmp_path)[0]["verdict"] == "INVALID"


def test_unicode_keys_and_integer_negative_zero(tmp_path):
    att = _receipt()
    att["extra"] = {"\ue000": 0, "\U00010000": 1}
    raw = _seal(att).replace(':0,', ':-0,')
    assert _python_verdict(raw) == "VALID"
    assert verify_attestation(raw)[0]
    assert _js([raw], tmp_path)[0]["verdict"] == "VALID"


def test_action_profile_cannot_verify_as_savings(tmp_path):
    att = _receipt()
    att["kind"] = "kry_action_attestation"
    raw = _seal(att)
    assert not kv.verify_attestation(att)[0]
    assert not verify_attestation(raw)[0]
    assert _js([raw], tmp_path)[0]["verdict"] == "INVALID"


def test_unknown_version_does_not_contribute_to_totals(tmp_path):
    att = _receipt(version=8)
    att.update(total_kry=0.0, usd_equivalent=0.0, event_type_counts={})
    att["veracity"].update(by_tier={}, self_reported_kry=0.0)
    raw = _seal(att)
    ok, errors = kv.verify_attestation(att)
    assert not ok and len(errors) == 1 and "hash_version" in errors[0]
    js = _js([raw], tmp_path)[0]
    assert js["verdict"] == "INVALID"
    assert len(js["reasons"]) == 1 and "hash_version" in js["reasons"][0]


def test_canonical_float_spelling_across_binary64_ranges(tmp_path):
    att = _receipt()
    rng = random.Random(20260919)
    values = [-0.0, 1e-7, 1e-4, 1e15, 1e16, 1e20, 1e23, 5e-324]
    for _ in range(1000):
        value = struct.unpack(">d", rng.getrandbits(64).to_bytes(8, "big"))[0]
        if math.isfinite(value):
            values.append(value)
    att["numeric_metadata"] = values + [2 ** 100, -(2 ** 100)]
    raw = _seal(att)
    assert _python_verdict(raw) == "VALID"
    assert verify_attestation(raw)[0]
    assert _js([raw], tmp_path)[0]["verdict"] == "VALID"


@pytest.mark.parametrize("path", sorted((_ROOT / "vectors/hardening").glob("*.json")), ids=lambda p: p.stem)
def test_hardening_vectors_in_python(path):
    vector = json.loads(path.read_text(encoding="utf-8"))
    raw, expected = vector["input_raw_text"], vector["expected"]["verdict"]
    assert _python_verdict(raw) == expected
    # The package API implements savings only and must refuse declared action documents.
    assert verify_attestation(raw)[0] == (expected == "VALID")


@pytest.mark.parametrize("vector_id", ["overflowing_tokens", "invalid_unicode_escape", "unescaped_tab",
                                        "veracity_floor_string", "valid_v7"])
@pytest.mark.parametrize("stdio_encoding", ["utf-8", "cp1252"])
def test_stranger_cli_reports_vector_verdict(vector_id, stdio_encoding, tmp_path, monkeypatch):
    monkeypatch.setenv("PYTHONIOENCODING", stdio_encoding)
    vector = json.loads((_ROOT / "vectors/hardening" / f"{vector_id}.json").read_text(encoding="utf-8"))
    path = tmp_path / "attestation.json"
    path.write_text(vector["input_raw_text"], encoding="utf-8")
    result = subprocess.run([sys.executable, str(_ROOT / "scripts/kry_verify.py"), str(path)],
                            capture_output=True)
    expected = vector["expected"]["verdict"]
    assert result.returncode == (0 if expected == "VALID" else 1)
    # Verdicts are ASCII in either console encoding; surrounding prose need not be UTF-8.
    assert f"VERDICT: {expected}".encode("ascii") in result.stdout
    assert result.stderr == b""


def test_stranger_cli_distinguishes_unreadable_file_from_json_error(tmp_path, capsys):
    assert kv.main([str(tmp_path / "missing.json")]) == 1
    output = capsys.readouterr().out
    assert "VERDICT: INVALID" in output
    assert "attestation unreadable:" in output
