"""Raw review regressions and the Annex D document-byte boundary."""
from __future__ import annotations

import hashlib
import importlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

_ROOT = Path(os.environ.get("KRY_REVIEW_ROOT", Path(__file__).resolve().parents[1])).resolve()
_SOURCE = Path(__file__).resolve().parents[1]
_LIMIT = 16 * 1024 * 1024
_CASE_FILES = sorted(path for path in (_SOURCE / "vectors/hardening").rglob("*.json")
                     if path.stem.startswith("review_") or "_ts_presence_" in path.stem)
_CASES = [json.loads(path.read_text()) for path in _CASE_FILES]


def test_adopted_vector_layout_preserves_sources():
    seeds = [json.loads(line) for line in (_SOURCE / "vectors/hardening/review_cases.ndjson").read_text().splitlines()]
    adopted = [(path, case) for path, case in zip(_CASE_FILES, _CASES) if case["id"].startswith("review_")]
    assert len(adopted) == len(seeds)
    assert {case["id"]: case for _, case in adopted} == {case["id"]: case for case in seeds}
    for path, case in adopted:
        att = json.loads(case["input_raw_text"])
        profile = ("anchor" if "input_anchor" in case else
                   "action" if isinstance(att, dict) and att.get("kind") == "kry_action_attestation" else "savings")
        assert path.parent == _SOURCE / "vectors/hardening" / profile


def _load(name):
    spec = importlib.util.spec_from_file_location(name, _ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def verifiers():
    sys.path.insert(0, str(_ROOT / "src"))
    return _load("kry_verify"), _load("kry_action_verify"), importlib.import_module("kry.kry_attest")


def _python_verdict(vector, verifiers):
    savings, action, _ = verifiers
    try:
        att = savings._json_loads(vector["input_raw_text"])
    except ValueError:
        return "PARSE_ERROR"
    is_action = isinstance(att, dict) and att.get("kind") == "kry_action_attestation"
    fn = action.verify_action_attestation if is_action else savings.verify_attestation
    ok = fn(att)[0]
    if "input_anchor" in vector:
        try:
            anchor = savings._json_loads(json.dumps(vector["input_anchor"]))
        except ValueError:
            return "INVALID"
        fn = action.verify_against_anchor if is_action else savings.verify_attestation_against_anchor
        ok = fn(att, anchor)[0] and ok
    return "VALID" if ok else "INVALID"


@pytest.fixture(scope="module")
def javascript_cases(tmp_path_factory):
    directory = tmp_path_factory.mktemp("review-js")
    runner = directory / "verify.mjs"
    runner.write_text(
        f'import {{ verdict, verdictWithAnchor, setMultipliers }} from {json.dumps((_ROOT / "verifiers/js/verify.mjs").as_uri())};\n'
        'import { readFileSync } from "node:fs";\n'
        'setMultipliers(JSON.parse(readFileSync(process.argv[3], "utf8")).multipliers);\n'
        'const cases = JSON.parse(readFileSync(process.argv[2], "utf8"));\n'
        'console.log(JSON.stringify(cases.map(c => c.anchor === undefined ? verdict(c.raw) : verdictWithAnchor(c.raw, c.anchor))));\n')
    path = directory / "inputs.json"
    inputs = [{"raw": c["input_raw_text"], **({"anchor": json.dumps(c["input_anchor"])} if "input_anchor" in c else {})}
              for c in _CASES]
    path.write_text(json.dumps(inputs))
    result = subprocess.run(["node", str(runner), str(path), str(_ROOT / "vectors/primitives/legal_multipliers.json")],
                            capture_output=True, text=True, check=True, timeout=60)
    return dict(zip((c["id"] for c in _CASES), json.loads(result.stdout)))


@pytest.mark.parametrize("vector", _CASES, ids=lambda c: c["id"])
def test_reviewed_raw_vector(vector, verifiers, javascript_cases):
    """Each expectation is fixed independently of the verifier being exercised."""
    expected = vector["expected"]["verdict"]
    observed = {"python": _python_verdict(vector, verifiers), "js": javascript_cases[vector["id"]]}
    decoded = json.loads(vector["input_raw_text"])
    if isinstance(decoded, dict) and decoded.get("kind") != "kry_action_attestation" and "input_anchor" not in vector:
        ok, errors = verifiers[2].verify_attestation(vector["input_raw_text"])
        observed["package"] = "VALID" if ok else ("PARSE_ERROR" if any(e.startswith("invalid JSON") for e in errors) else "INVALID")
    assert observed == dict.fromkeys(observed, expected), observed


def _sample(kind):
    return json.loads((_ROOT / f"vectors/{kind}/valid/single_self_reported.json").read_text())["input"]


def _canon(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _sized_attestation(kind, size, unicode):
    att = _sample(kind)
    if unicode:
        att["metadata"] = "é😀"
        if kind == "savings":
            att["attestation_hash"] = ""
            att["attestation_hash"] = hashlib.sha256(_canon(att).encode()).hexdigest()
    raw = json.dumps(att, separators=(",", ":"), ensure_ascii=False)
    return raw + " " * (size - len(raw.encode("utf-8")))


def _anchor(kind):
    att = _sample(kind)
    return ({"schema": "kry_chain_anchor/v1", "count": att["links"][0]["seq"], "tip": att["chain_head"]}
            if kind == "savings" else {"kind": "kry_action_anchor", "count": len(att["links"]), "chain_tip": att["chain_tip"]})


def _cli(kind, language):
    if language == "js":
        return ["node", str(_ROOT / "verifiers/js/cli.mjs")]
    script = "kry_verify.py" if kind == "savings" else "kry_action_verify.py"
    return [sys.executable, str(_ROOT / "scripts" / script)]


def _js_api(tmp_path, raw, anchor=None):
    (tmp_path / "raw.json").write_text(raw, encoding="utf-8")
    if anchor is not None:
        (tmp_path / "anchor.json").write_text(anchor, encoding="utf-8")
    runner = tmp_path / "size.mjs"
    runner.write_text(
        f'import {{ verdict, verdictWithAnchor, setMultipliers }} from {json.dumps((_ROOT / "verifiers/js/verify.mjs").as_uri())};\n'
        'import { readFileSync } from "node:fs";\n'
        'setMultipliers(JSON.parse(readFileSync(process.argv[3], "utf8")).multipliers);\n'
        'const raw = readFileSync(process.argv[2], "utf8");\n'
        'console.log(process.argv[4] ? verdictWithAnchor(raw, readFileSync(process.argv[4], "utf8")) : verdict(raw));\n')
    args = ["node", str(runner), str(tmp_path / "raw.json"), str(_ROOT / "vectors/primitives/legal_multipliers.json")]
    if anchor is not None:
        args.append(str(tmp_path / "anchor.json"))
    return subprocess.run(args, capture_output=True, text=True, check=True, timeout=30).stdout.strip()


@pytest.mark.parametrize("kind", ["savings", "action"])
@pytest.mark.parametrize("delta", [-1, 0, 1])
@pytest.mark.parametrize("unicode", [False, True])
def test_raw_utf8_document_size_boundary(kind, delta, unicode, tmp_path, verifiers):
    raw = _sized_attestation(kind, _LIMIT + delta, unicode)
    assert len(raw.encode("utf-8")) == _LIMIT + delta
    expected = "PARSE_ERROR" if delta > 0 else "VALID"
    observed = {"python": _python_verdict({"input_raw_text": raw}, verifiers), "js": _js_api(tmp_path, raw)}
    if kind == "savings":
        ok, errors = verifiers[2].verify_attestation(raw)
        observed["package"] = "VALID" if ok else ("PARSE_ERROR" if any(e.startswith("invalid JSON") for e in errors) else "INVALID")
    assert observed == dict.fromkeys(observed, expected), observed


@pytest.mark.parametrize("kind", ["savings", "action"])
@pytest.mark.parametrize("delta", [0, 1])
@pytest.mark.parametrize("stdin", [False, True])
def test_document_size_limit_on_file_and_stdin(kind, delta, stdin, tmp_path):
    raw = _sized_attestation(kind, _LIMIT + delta, True)
    path = tmp_path / "attestation.json"
    path.write_text(raw, encoding="utf-8")
    expected = "PARSE_ERROR" if delta else "VALID"
    for language in ("python", "js"):
        result = subprocess.run(_cli(kind, language) + (["-"] if stdin else [str(path)]),
                                input=raw if stdin else None, capture_output=True, text=True, timeout=30)
        assert result.returncode == (1 if delta else 0) and f"VERDICT: {expected}" in result.stdout, (language, result.stdout, result.stderr)


@pytest.mark.parametrize("kind", ["savings", "action"])
@pytest.mark.parametrize("delta", [-1, 0, 1])
def test_anchor_document_size_boundary(kind, delta, tmp_path):
    raw = _canon(_sample(kind))
    anchor = _canon(_anchor(kind))
    anchor += " " * (_LIMIT + delta - len(anchor.encode("utf-8")))
    expected = "PARSE_ERROR" if delta > 0 else "VALID"
    assert _js_api(tmp_path, raw, anchor) == expected
    for language in ("python", "js"):
        args = [str(tmp_path / "raw.json"), "--anchor", str(tmp_path / "anchor.json")]
        result = subprocess.run(_cli(kind, language) + args, capture_output=True, text=True, timeout=30)
        assert result.returncode == (1 if delta > 0 else 0) and f"VERDICT: {expected}" in result.stdout, (language, result.stdout, result.stderr)


@pytest.mark.parametrize("target", ["attestation", "anchor"])
def test_size_limit_applies_to_raw_corpus_inputs(target, tmp_path):
    category = "hardening/savings" if target == "attestation" else "hardening/anchor"
    (tmp_path / category).mkdir(parents=True)
    (tmp_path / "primitives").mkdir()
    for name in ("canon_f64", "canonical_json", "legal_multipliers"):
        shutil.copyfile(_ROOT / "vectors/primitives" / f"{name}.json", tmp_path / "primitives" / f"{name}.json")
    raw = _sized_attestation("savings", _LIMIT + 1, False) if target == "attestation" else _canon(_sample("savings"))
    vector = {"id": "size_limit", "input_raw_text": raw, "expected": {"verdict": "PARSE_ERROR"}}
    text = json.dumps(vector)
    if target == "anchor":
        anchor = _canon(_anchor("savings"))
        # The whitespace belongs to the anchor document slice, after its opening value token.
        anchor = anchor[:1] + " " * (_LIMIT + 1 - len(anchor.encode())) + anchor[1:]
        text = '{"input_anchor":' + anchor + ',' + text[1:]
    (tmp_path / category / "size_limit.json").write_text(text)
    (tmp_path / "manifest.json").write_text(json.dumps({"vectors": [{"category": category, "id": "size_limit"}]}))
    result = subprocess.run(_cli("savings", "js") + ["--vectors", str(tmp_path)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("kind", ["savings", "action"])
@pytest.mark.parametrize("delta", [0, 1])
def test_anchor_size_limit_on_stdin(kind, delta, tmp_path):
    path = tmp_path / "attestation.json"
    path.write_text(_canon(_sample(kind)))
    anchor = _canon(_anchor(kind))
    anchor += " " * (_LIMIT + delta - len(anchor.encode()))
    expected = "PARSE_ERROR" if delta else "VALID"
    for language in ("python", "js"):
        result = subprocess.run(_cli(kind, language) + [str(path), "--anchor", "-"], input=anchor,
                                capture_output=True, text=True, timeout=30)
        assert result.returncode == (1 if delta else 0) and f"VERDICT: {expected}" in result.stdout, (language, result.stdout, result.stderr)


@pytest.mark.parametrize("kind", ["savings", "action"])
def test_decoded_anchor_has_no_raw_document_byte_limit(kind, tmp_path, verifiers):
    """Decoded inputs retain structural limits; original wire bytes are unavailable."""
    att, anchor = _sample(kind), _anchor(kind)
    anchor["metadata"] = "x" * (_LIMIT + 1)
    fn = verifiers[0].verify_attestation_against_anchor if kind == "savings" else verifiers[1].verify_against_anchor
    assert fn(att, anchor) == (True, [])
    path = tmp_path / "decoded.json"
    path.write_text(json.dumps({"attestation": _canon(att), "anchor": anchor}))
    runner = tmp_path / "decoded.mjs"
    runner.write_text(
        f'import {{ anchorErrors, parse }} from {json.dumps((_ROOT / "verifiers/js/verify.mjs").as_uri())};\n'
        'import { readFileSync } from "node:fs";\n'
        'const value = JSON.parse(readFileSync(process.argv[2], "utf8"));\n'
        'console.log(JSON.stringify(anchorErrors(parse(value.attestation), value.anchor)));\n')
    result = subprocess.run(["node", str(runner), str(path)], capture_output=True, text=True, check=True, timeout=30)
    assert json.loads(result.stdout) == []
