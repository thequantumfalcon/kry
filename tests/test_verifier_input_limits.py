"""Cross-implementation boundaries for JSON container depth and integer literal length."""
from __future__ import annotations

import json
import struct
import subprocess
import sys

import pytest

import kry.kry_attest as ka
from test_verifier_hardening import _NODE, _ROOT, _canon, _js, _receipt, _sha, kav, kv
from test_verifier_hardening_d import _action, _action_cli, _js_cli


def _nested_raw(levels, shape="mixed"):
    raw = "0"
    for level in range(levels):
        raw = '{"x":' + raw + '}' if shape == "object" or (shape == "mixed" and level % 2) else '[' + raw + ']'
    return raw


def _savings_raw(metadata, canonical_metadata=None):
    att = _receipt()
    att.update(metadata="LIMIT_MARKER", attestation_hash="")
    template = _canon(att)
    raw = template.replace('"LIMIT_MARKER"', metadata)
    canonical = template.replace('"LIMIT_MARKER"', metadata if canonical_metadata is None else canonical_metadata)
    return raw.replace('"attestation_hash":""', '"attestation_hash":"' + _sha(canonical) + '"')


def _action_raw(tool):
    att = _action(tool="LIMIT_MARKER")
    link = att["links"][0]
    payload = {"action_hash_version": 1, "tool": link["tool"], "args_commit": link["args_commit"],
               "result_commit": link["result_commit"], "status": link["status"],
               "ts": struct.pack(">d", float(link["ts"])).hex(), "agent_id": link["agent_id"],
               "evidence_tier": link["evidence_tier"], "server_evidence_commit": link["server_evidence_commit"]}
    link["receipt_hash"] = _sha(_canon(payload).replace('"LIMIT_MARKER"', tool))
    link["chain_hash"] = att["chain_tip"] = _sha("0" * 64 + ":" + link["receipt_hash"])
    return _canon(att).replace('"LIMIT_MARKER"', tool)


def _api_result(fn, *args):
    try:
        return fn(*args)
    except Exception as exc:
        return False, ["RAISED " + type(exc).__name__]


def _check_raw(raw, expected, tmp_path, *, action=False):
    parses = {}
    decoded = None
    for name, module in (("stranger", kv), ("action", kav), ("package", ka)):
        try:
            decoded = module._json_loads(raw)
            parses[name] = "PARSED"
        except ValueError:
            parses[name] = "PARSE_ERROR"
        except Exception as exc:
            parses[name] = "RAISED " + type(exc).__name__
    results = {"js": _js([raw], tmp_path)[0]["verdict"]}
    if action:
        result = _api_result(kav.verify_action_attestation, decoded)
        results["python"] = "PARSE_ERROR" if parses["action"] == "PARSE_ERROR" else ("VALID" if result[0] else "INVALID")
    else:
        result = _api_result(kv.verify_attestation, decoded)
        results["stranger"] = "PARSE_ERROR" if parses["stranger"] == "PARSE_ERROR" else ("VALID" if result[0] else "INVALID")
        ok, errors = _api_result(ka.verify_attestation, raw)
        results["package"] = "VALID" if ok else ("PARSE_ERROR" if any(e.startswith("invalid JSON") for e in errors) else "INVALID")
    assert results == dict.fromkeys(results, expected), (results, parses)
    assert parses == dict.fromkeys(parses, "PARSED" if expected == "VALID" else "PARSE_ERROR")


@pytest.mark.parametrize("shape", ["array", "object", "mixed"])
@pytest.mark.parametrize("depth", [63, 64, 65])
def test_savings_json_depth_boundary(depth, shape, tmp_path):
    # The root object counts as one container; metadata supplies the remaining levels.
    raw = _savings_raw(_nested_raw(depth - 1, shape))
    _check_raw(raw, "VALID" if depth <= 64 else "PARSE_ERROR", tmp_path)


@pytest.mark.parametrize("depth", [3000, 5000])
def test_deep_json_is_a_parse_error_without_recursion_escape(depth, tmp_path):
    _check_raw(_savings_raw(_nested_raw(depth - 1)), "PARSE_ERROR", tmp_path)


@pytest.mark.parametrize("depth", [63, 64, 65])
def test_action_json_depth_boundary_in_a_hash_bound_value(depth, tmp_path):
    # Root, links array, and link object account for three containers above the tool value.
    _check_raw(_action_raw(_nested_raw(depth - 3)), "VALID" if depth <= 64 else "PARSE_ERROR",
               tmp_path, action=True)


@pytest.mark.parametrize("digits, negative", [(1, False), (401, False), (640, False), (640, True),
                                               (641, False), (641, True), (4301, False), (5000, True)])
def test_integer_literal_digit_boundary(digits, negative, tmp_path):
    number = ("-" if negative else "") + "9" * digits
    _check_raw(_savings_raw(number), "VALID" if digits <= 640 else "PARSE_ERROR", tmp_path)


@pytest.mark.parametrize("digits", [640, 641])
def test_action_integer_boundary_in_a_hash_bound_value(digits, tmp_path):
    _check_raw(_action_raw("9" * digits), "VALID" if digits <= 640 else "PARSE_ERROR", tmp_path, action=True)


@pytest.mark.parametrize("number", ["0", "-0"])
def test_integer_zero_is_within_the_limit(number, tmp_path):
    _check_raw(_savings_raw(number, canonical_metadata="0"), "VALID", tmp_path)


@pytest.mark.parametrize("action", [False, True])
def test_strings_do_not_consume_depth_or_integer_digits(action, tmp_path):
    text = _canon('[{"quoted": "\\\\\\\""}]' * 3000 + "9" * 5000)
    raw = _action_raw(text) if action else _savings_raw(text)
    _check_raw(raw, "VALID", tmp_path, action=action)


def _oversized_value(kind):
    if kind == "integer":
        return 10 ** 640
    value = 0
    for _ in range(64):
        value = [value]
    return value


def _assert_limit_rejection(result):
    assert not result[0] and any("limit" in error.lower() for error in result[1]), result


@pytest.mark.parametrize("kind", ["depth", "integer"])
def test_decoded_verifier_apis_reject_limits_before_hashing(kind):
    savings, action = _receipt(), _action()
    savings["metadata"] = action["metadata"] = _oversized_value(kind)
    for fn, att in ((kv.verify_attestation, savings), (kav.verify_action_attestation, action)):
        _assert_limit_rejection(_api_result(fn, att))


@pytest.mark.parametrize("kind", ["depth", "integer"])
@pytest.mark.parametrize("argument", ["attestation", "anchor"])
def test_decoded_anchor_apis_check_limits_in_both_inputs(kind, argument):
    for fn, att, anchor in (
        (kv.verify_attestation_against_anchor, _receipt(),
         {"schema": "kry_chain_anchor/v1", "count": 0, "tip": "0" * 64}),
        (kav.verify_against_anchor, _action(),
         {"kind": "kry_action_anchor", "count": 0, "chain_tip": "0" * 64}),
    ):
        target = att if argument == "attestation" else anchor
        target["metadata"] = _oversized_value(kind)
        _assert_limit_rejection(_api_result(fn, att, anchor))


def _savings_cli(path, *extra):
    return subprocess.run([sys.executable, str(_ROOT / "scripts/kry_verify.py"), str(path), *extra],
                          capture_output=True, text=True, encoding="utf-8", errors="replace")


def _assert_cli_verdict(result, verdict):
    assert result.returncode == 1 and f"VERDICT: {verdict}" in result.stdout, result.stdout + result.stderr
    assert "Traceback" not in result.stderr and "RangeError" not in result.stderr


@pytest.mark.parametrize("kind", ["depth", "integer"])
def test_cli_limit_failures_are_parse_errors(kind, tmp_path):
    metadata = _nested_raw(64) if kind == "depth" else "9" * 641
    for action in (False, True):
        raw = _action_raw(metadata) if action else _savings_raw(metadata)
        path = tmp_path / "attestation.json"
        path.write_text(raw, encoding="utf-8")
        result = _action_cli(tmp_path, raw) if action else _savings_cli(path)
        _assert_cli_verdict(result, "PARSE_ERROR")
        _assert_cli_verdict(_js_cli(path), "PARSE_ERROR")


@pytest.mark.parametrize("kind", ["depth", "integer"])
def test_overlimit_anchor_is_invalid_second_input(kind, tmp_path):
    metadata = _nested_raw(64) if kind == "depth" else "9" * 641
    for action in (False, True):
        att = _action() if action else _receipt()
        anchor = ({"kind": "kry_action_anchor", "count": 0, "chain_tip": "0" * 64} if action else
                  {"schema": "kry_chain_anchor/v1", "count": 0, "tip": "0" * 64})
        anchor["metadata"] = "LIMIT_MARKER"
        anchor_path = tmp_path / "anchor.json"
        anchor_path.write_text(_canon(anchor).replace('"LIMIT_MARKER"', metadata), encoding="utf-8")
        path = tmp_path / "attestation.json"
        path.write_text(_canon(att), encoding="utf-8")
        result = (_action_cli(tmp_path, _canon(att), "--anchor", str(anchor_path)) if action else
                  _savings_cli(path, "--anchor", str(anchor_path)))
        _assert_cli_verdict(result, "INVALID")
        if not action:
            _assert_cli_verdict(_js_cli(path, str(anchor_path)), "INVALID")


@pytest.mark.skipif(_NODE is None, reason="node is not installed")
@pytest.mark.parametrize("form", ["object", "map"])
@pytest.mark.parametrize("kind, size, expected", [("depth", 64, "VALID"), ("depth", 65, "INVALID"),
                                                  ("integer", 640, "VALID"), ("integer", 641, "INVALID")])
def test_js_decoded_anchors_obey_input_limits(form, kind, size, expected, tmp_path):
    att_path = tmp_path / "attestation.json"
    att_path.write_text(_canon(_receipt()), encoding="utf-8")
    runner = tmp_path / "anchor-limits.mjs"
    runner.write_text(
        f'import {{ verdictWithAnchor, anchorErrors, parse, Num, setMultipliers }} from {_canon((_ROOT / "verifiers/js/verify.mjs").as_uri())};\n'
        'import { readFileSync } from "node:fs";\n'
        'const [attPath, multiplierPath, form, kind, sizeText] = process.argv.slice(2);\n'
        'const raw = readFileSync(attPath, "utf8"), size = Number(sizeText);\n'
        'setMultipliers(JSON.parse(readFileSync(multiplierPath, "utf8")).multipliers);\n'
        'let metadata = kind === "integer" ? new Num("9".repeat(size)) : 0;\n'
        'if (kind === "depth") for (let i = 0; i < size - 1; i++) metadata = i % 2 ? {x: metadata} : [metadata];\n'
        'const fields = {schema: "kry_chain_anchor/v1", count: form === "map" ? new Num("0") : 0, tip: "0".repeat(64), metadata};\n'
        'const anchor = form === "map" ? new Map(Object.entries(fields)) : fields;\n'
        'console.log(JSON.stringify({verdict: verdictWithAnchor(raw, anchor), errors: anchorErrors(parse(raw), anchor)}));\n',
        encoding="utf-8")
    result = subprocess.run([_NODE, str(runner), str(att_path), str(_ROOT / "vectors/primitives/legal_multipliers.json"),
                             form, kind, str(size)], capture_output=True, text=True, check=True)
    checked = json.loads(result.stdout)
    assert checked["verdict"] == expected, checked
    if expected == "VALID":
        assert checked["errors"] == []
    else:
        assert any("limit" in error.lower() for error in checked["errors"]), checked


@pytest.mark.skipif(_NODE is None, reason="node is not installed")
@pytest.mark.parametrize("count", [0, 1])
def test_js_direct_anchor_api_rejects_non_objects_and_missing_links(count, tmp_path):
    runner = tmp_path / "anchor-shapes.mjs"
    runner.write_text(
        f'import {{ anchorErrors }} from {_canon((_ROOT / "verifiers/js/verify.mjs").as_uri())};\n'
        'const anchor = {schema: "kry_chain_anchor/v1", count: Number(process.argv[2]), tip: "0".repeat(64)};\n'
        'const inputs = [null, [], 1, "x", true, {}, new Map()];\n'
        'console.log(JSON.stringify(inputs.map(att => {\n'
        '  try { return {errors: anchorErrors(att, anchor)}; } catch (error) { return {raised: error.name}; }\n'
        '})));\n', encoding="utf-8")
    result = subprocess.run([_NODE, str(runner), str(count)], capture_output=True, text=True, check=True)
    checked = json.loads(result.stdout)
    assert all(isinstance(item.get("errors"), list) and item["errors"] for item in checked), checked
