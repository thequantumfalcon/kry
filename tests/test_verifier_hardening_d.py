"""Hostile-input regressions for savings, actions, and the wire-format CLI boundaries."""
from __future__ import annotations

import json
import struct
import subprocess
import sys

import pytest

from kry.kry_attest import verify_attestation
from test_verifier_hardening import (
    _NODE, _ROOT, _all_verdicts, _canon, _js, _receipt, _rechain, _seal, _sha, kav, kv,
)


def _savings_verdicts(att, tmp_path):
    _seal(att)
    return _all_verdicts(att, tmp_path)


def _two_links(version=7):
    att = _receipt(version=version)
    second = dict(att["links"][0], seq=att["links"][0]["seq"] + 1,
                  receipt_id="KRY-second", receipt_hash="cd" * 32)
    att["links"].append(second)
    return _rechain(att)


@pytest.mark.parametrize("tail", [5, [], {}, "x", None, True])
def test_non_object_last_savings_link_is_invalid(tail, tmp_path):
    att = _two_links()
    att["links"].append(tail)
    att["receipts"] = 3
    assert _savings_verdicts(att, tmp_path) == {"INVALID"}


@pytest.mark.parametrize("field", ["receipt_id", "supersedes"])
@pytest.mark.parametrize("value", [0, 7, 0.0, 7.5, False, True, [], ["x"], {}, {"a": 1}])
@pytest.mark.parametrize("version", [4, 5, 6, 7])
def test_non_string_savings_identifiers_are_invalid(field, value, version, tmp_path):
    # SPEC 3.2 field types apply independently of whether the hash version binds the field.
    # Re-hash the malformed value: rejection must come from its type, not a stale chain hash.
    att = _two_links(version)
    att["links"][1].update({field: value})
    assert _savings_verdicts(_rechain(att), tmp_path) == {"INVALID"}


@pytest.mark.parametrize("value", [7, ["KRY-target"], {"id": "KRY-target"}])
def test_non_string_promotion_target_cannot_claim_an_overlay(value, tmp_path):
    att = _two_links()
    att["links"][0]["receipt_id"] = value
    att["links"][1].update(supersedes=value, evidence_tier="tlsn_attested",
                            kry_minted=0.0, tokens_saved=0.0)
    _rechain(att)
    moved = att["links"][0]["kry_minted"]
    att["veracity"].update(by_tier={"self_reported": 0.0, "tlsn_attested": moved},
                            anchored_kry=moved, self_reported_kry=0.0, veracity_floor=1.0)
    assert _savings_verdicts(att, tmp_path) == {"INVALID"}


@pytest.mark.parametrize("present", [False, True])
def test_absent_or_null_supersedes_preserves_an_honest_chain(present, tmp_path):
    att = _receipt()
    if present:
        att["links"][0]["supersedes"] = None
    else:
        att["links"][0].pop("supersedes", None)
    assert _savings_verdicts(_rechain(att), tmp_path) == {"VALID"}


@pytest.mark.parametrize("sign", [1, -1])
def test_overflowing_by_tier_integer_is_invalid(sign, tmp_path):
    att = _receipt()
    att["veracity"]["by_tier"]["self_reported"] = sign * 10 ** 400
    assert _savings_verdicts(att, tmp_path) == {"INVALID"}


def test_unpaired_surrogate_never_hashes_as_its_replacement(tmp_path):
    att = _receipt()
    att["links"][0]["receipt_hash"] = "\ufffd"
    _rechain(att)
    assert _savings_verdicts(att, tmp_path) == {"VALID"}
    att["links"][0]["receipt_hash"] = "\ud800"
    assert _savings_verdicts(att, tmp_path) == {"INVALID"}


def test_stranger_cli_survives_an_overflowing_link_value(tmp_path, capsys):
    att = _two_links()
    att["links"][1]["kry_minted"] = 10 ** 400
    path = tmp_path / "attestation.json"
    path.write_text(_seal(att), encoding="utf-8")
    assert kv.main([str(path)]) == 1
    assert "VERDICT: INVALID" in capsys.readouterr().out


def test_registry_unpaired_surrogate_is_tampered():
    ok, errors = kv.verify_registry([{"party": "\ud800", "amount": 1.0, "entry_hash": "a" * 64}])
    assert not ok and errors


def _hash_action_link(link, prev):
    payload = {"action_hash_version": 1, "tool": link["tool"], "args_commit": link["args_commit"],
               "result_commit": link["result_commit"], "status": link["status"],
               "ts": struct.pack(">d", float(link["ts"])).hex(), "agent_id": link["agent_id"],
               "evidence_tier": link["evidence_tier"],
               "server_evidence_commit": link["server_evidence_commit"]}
    link["receipt_hash"] = _sha(_canon(payload))
    link["chain_hash"] = _sha(prev + ":" + link["receipt_hash"])
    return link["chain_hash"]


def _action(**fields):
    att = json.loads((_ROOT / "vectors/action/valid/single_self_reported.json").read_text())["input"]
    att["links"][0].update(fields)
    att["chain_tip"] = _hash_action_link(att["links"][0], "0" * 64)
    return att


def _action_cli(tmp_path, raw, *extra):
    path = tmp_path / "action.json"
    path.write_bytes(raw if isinstance(raw, bytes) else raw.encode("utf-8"))
    return subprocess.run([sys.executable, str(_ROOT / "scripts/kry_action_verify.py"), str(path), *extra],
                          capture_output=True, text=True, encoding="utf-8", errors="replace")


@pytest.mark.parametrize("doc", [[], "x", 1, None, True])
def test_non_object_documents_yield_invalid(doc, tmp_path):
    raw = _canon(doc)
    assert not kv.verify_attestation(doc)[0]
    assert not verify_attestation(raw)[0]
    assert not kav.verify_action_attestation(doc)[0]
    assert _js([raw], tmp_path)[0]["verdict"] == "INVALID"


@pytest.mark.parametrize("tier", [["server_witnessed"], {"server_witnessed": 1}, 1])
def test_action_non_string_tier_cannot_raise_the_floor(tier, tmp_path):
    for floor, expected in ((0.0, "VALID"), (1.0, "INVALID")):
        att = _action(evidence_tier=tier, server_evidence_commit="ab" * 32)
        att["veracity"] = {"veracity_floor": floor}
        assert kav.verify_action_attestation(att)[0] == (expected == "VALID")
        assert _js([_canon(att)], tmp_path)[0]["verdict"] == expected


@pytest.mark.parametrize("raw", ['{"kind":"kry_action_attestation","x":NaN}', '{"x":1e999}', '{"a":',
                                 b'{"kind":"kry_action_attestation","note":"\xff"}'])
def test_action_cli_reports_parse_errors(raw, tmp_path):
    result = _action_cli(tmp_path, raw)
    assert result.returncode == 1 and "VERDICT: PARSE_ERROR" in result.stdout, result.stdout + result.stderr
    assert "Traceback" not in result.stderr


def test_action_cli_refuses_a_non_object_document(tmp_path):
    result = _action_cli(tmp_path, "[]")
    assert result.returncode == 1 and "VERDICT: INVALID" in result.stdout, result.stdout + result.stderr
    assert "Traceback" not in result.stderr


@pytest.mark.parametrize("veracity", [1, "x", [1], None])
def test_action_cli_survives_non_object_veracity(veracity, tmp_path):
    att = _action()
    att["veracity"] = veracity
    raw = _canon(att)
    result = _action_cli(tmp_path, raw)
    assert "Traceback" not in result.stderr, result.stderr
    expected = _js([raw], tmp_path)[0]["verdict"]
    assert f"VERDICT: {expected}" in result.stdout
    assert result.returncode == (0 if expected == "VALID" else 1)


@pytest.mark.parametrize("anchor", ['{"kind":', '{"kind":"kry_action_anchor","count":NaN}', "[]", "null",
                                    '{"kind":"kry_action_anchor","count":true,"chain_tip":"{tip}"}', None])
def test_action_cli_anchor_failures_are_invalid(anchor, tmp_path):
    att = _action()
    anchor_path = tmp_path / "anchor.json"
    if anchor is not None:
        anchor_path.write_text(anchor.replace("{tip}", att["chain_tip"]), encoding="utf-8")
    result = _action_cli(tmp_path, _canon(att), "--anchor", str(anchor_path))
    assert result.returncode == 1 and "VERDICT: INVALID" in result.stdout, result.stdout + result.stderr
    assert "Traceback" not in result.stderr


def test_action_anchor_check_needs_a_links_list():
    att = _action()
    published = {"kind": "kry_action_anchor", "count": 1, "chain_tip": att["chain_tip"]}
    assert kav.verify_against_anchor(att, published) == (True, [])
    att["links"] = {"0": att["links"][0]}
    assert not kav.verify_against_anchor(att, published)[0]


_SPELLED_NUMBERS = ["1700000001.0", "1_700_000_001", " 1700000001 ", "1.700000001e9", True, False]


@pytest.mark.parametrize("spelled", _SPELLED_NUMBERS)
@pytest.mark.parametrize("version", [5, 6, 7])
def test_ts_that_only_spells_a_number_breaks_the_chain(spelled, version, tmp_path):
    att = _receipt(version=version)
    att["links"][0]["ts"] = float(spelled)
    _rechain(att)
    att["links"][0]["ts"] = spelled
    assert _savings_verdicts(att, tmp_path) == {"INVALID"}


@pytest.mark.parametrize("spelled", _SPELLED_NUMBERS)
def test_action_ts_that_only_spells_a_number_breaks_the_receipt_hash(spelled, tmp_path):
    att = _action(ts=float(spelled))
    att["links"][0]["ts"] = spelled
    assert not kav.verify_action_attestation(att)[0]
    assert _js([_canon(att)], tmp_path)[0]["verdict"] == "INVALID"


def test_every_canon_f64_uses_the_sentinel_for_non_numbers():
    import kry.kry_action as kax
    import kry.kry_mint as km
    for module, sentinel in ((kv, "nonfinite"), (km, "nonfinite"), (kav, "f" * 16), (kax, "f" * 16)):
        for value in _SPELLED_NUMBERS + ["nan", "inf", None, [], {}, 10 ** 400]:
            assert module._canon_f64(value) == sentinel, (module.__name__, value)
        for value in (1700000001.0, 1700000001, 0, -0.0, 5e-324):
            assert module._canon_f64(value) == struct.pack(">d", float(value)).hex()


def test_boolean_event_type_count_is_invalid(tmp_path):
    att = _receipt()
    att["event_type_counts"] = {"cache_hit": True}
    assert _savings_verdicts(att, tmp_path) == {"INVALID"}
    att["event_type_counts"] = {"cache_hit": 1.0}
    assert len(_savings_verdicts(att, tmp_path)) == 1


@pytest.mark.parametrize("metered, expected", [([100, 50], "VALID"), ([10 ** 400, 1], "VALID"),
                                               ([100.0, 50], "INVALID"), ([1e2, 50], "INVALID"),
                                               ([100, -1], "INVALID"), ([True, 50], "INVALID")])
def test_metered_tokens_are_integer_literals(metered, expected, tmp_path):
    att = _receipt()
    att["links"][0].update(evidence_tier="provider_metered", metered_tokens=metered)
    assert _savings_verdicts(_rechain(att), tmp_path) == {expected}


@pytest.mark.parametrize("field", ["action_hash_version", "action_count"])
def test_action_envelope_integers_are_not_booleans(field, tmp_path):
    att = _action()
    att[field] = True
    assert not kav.verify_action_attestation(att)[0]
    assert _js([_canon(att)], tmp_path)[0]["verdict"] == "INVALID"
    att[field] = 1.0
    assert kav.verify_action_attestation(att)[0] == (_js([_canon(att)], tmp_path)[0]["verdict"] == "VALID")


@pytest.mark.parametrize("count", [1, 2])
def test_action_links_need_a_receipt_id(count, tmp_path):
    att = _action()
    if count == 2:
        second = dict(att["links"][0], ts=att["links"][0]["ts"] + 1.0, receipt_id="ACT-second")
        att["chain_tip"] = _hash_action_link(second, att["links"][0]["chain_hash"])
        att["links"].append(second)
        att["action_count"] = 2
        assert kav.verify_action_attestation(att)[0]
    for link in att["links"]:
        del link["receipt_id"]
    assert not kav.verify_action_attestation(att)[0]
    assert _js([_canon(att)], tmp_path)[0]["verdict"] == "INVALID"


@pytest.mark.skipif(_NODE is None, reason="node is not installed")
def test_anchor_count_is_an_exact_integer(tmp_path):
    att = _receipt()
    raw, seq, tip = _canon(att), att["links"][0]["seq"], att["links"][0]["chain_hash"]
    big = _receipt()
    big["links"][0]["seq"] = 2 ** 53
    raw_big = _seal(big)
    anchor = '{"schema":"kry_chain_anchor/v1","count":%s,"tip":"' + tip + '"}'
    cases = [(raw, anchor % seq, "VALID"), (raw, anchor % f"{seq}.0", "INVALID"),
             (raw, anchor % f"{seq}e0", "INVALID"), (raw, '{"schema":"kry_chain_anchor/v1","count":NaN}', "INVALID"),
             (raw, "[]", "INVALID"), (raw_big, anchor % (2 ** 53), "VALID"),
             (raw_big, anchor % (2 ** 53 + 1), "INVALID")]
    inputs = tmp_path / "anchored.json"
    inputs.write_text(json.dumps([[d, a] for d, a, _ in cases]), encoding="utf-8")
    runner = tmp_path / "anchored.mjs"
    runner.write_text(
        f'import {{ verdictWithAnchor, setMultipliers }} from {json.dumps((_ROOT / "verifiers/js/verify.mjs").as_uri())};\n'
        'import { readFileSync } from "node:fs";\n'
        'setMultipliers(JSON.parse(readFileSync(process.argv[3], "utf8")).multipliers);\n'
        'console.log(JSON.stringify(JSON.parse(readFileSync(process.argv[2], "utf8"))'
        '.map(([raw, anchor]) => verdictWithAnchor(raw, anchor))));\n', encoding="utf-8")
    result = subprocess.run([_NODE, str(runner), str(inputs), str(_ROOT / "vectors/primitives/legal_multipliers.json")],
                            capture_output=True, text=True, check=True)
    for (doc, published, expected), js in zip(cases, json.loads(result.stdout)):
        try:
            py = kv.verify_attestation_against_anchor(kv._json_loads(doc), kv._json_loads(published))[0]
        except ValueError:
            py = False
        assert ("VALID" if py else "INVALID", js) == (expected, expected), published


def _corpus_input(name):
    return json.dumps(json.loads((_ROOT / "vectors/savings/valid" / f"{name}.json")
                                 .read_text(encoding="utf-8"))["input"])


def _js_cli(path, *extra):
    if _NODE is None:
        pytest.skip("node is not installed")
    return subprocess.run([_NODE, str(_ROOT / "verifiers/js/cli.mjs"), str(path), *extra],
                          capture_output=True, text=True, encoding="utf-8", errors="replace")


@pytest.mark.parametrize("anchor", ['{"schema":', '{"schema":"kry_chain_anchor/v1","count":NaN,"tip":"x"}', None])
def test_js_cli_reports_a_bad_anchor_as_invalid(anchor, tmp_path):
    att = tmp_path / "att.json"
    att.write_text(_corpus_input("chain_three"), encoding="utf-8")
    anchor_path = tmp_path / "anchor.json"
    if anchor is not None:
        anchor_path.write_text(anchor, encoding="utf-8")
    result = _js_cli(att, str(anchor_path))
    assert result.returncode == 1
    assert "VERDICT: INVALID" in result.stdout, result.stdout + result.stderr


def test_js_cli_reports_an_unreadable_attestation_as_invalid(tmp_path):
    result = _js_cli(tmp_path / "missing.json")
    assert result.returncode == 1
    assert "VERDICT: INVALID" in result.stdout, result.stdout + result.stderr


@pytest.mark.parametrize("raw, expected", [(b"\xff", "PARSE_ERROR"), (b"\xef\xbb\xbf", "PARSE_ERROR"), (b"", "VALID")])
def test_cli_reads_strict_utf8(raw, expected, tmp_path):
    text = _corpus_input("single_self_reported").encode("utf-8")
    if raw == b"\xef\xbb\xbf":
        text = raw + text
    elif raw:
        text = text.replace(b'"sealed_evidence": "', b'"sealed_evidence": "' + raw, 1)
    att = tmp_path / "att.json"
    att.write_bytes(text)
    results = [_js_cli(att), subprocess.run([sys.executable, str(_ROOT / "scripts/kry_verify.py"), str(att)],
                                          capture_output=True, text=True, encoding="utf-8", errors="replace")]
    for result in results:
        assert f"VERDICT: {expected}" in result.stdout, result.stdout + result.stderr
        assert result.returncode == (0 if expected == "VALID" else 1)


def test_js_cli_preserves_anchor_literal_types(tmp_path):
    att_text = _corpus_input("chain_three")
    att = tmp_path / "att.json"
    att.write_text(att_text, encoding="utf-8")
    links = json.loads(att_text)["links"]
    anchor = tmp_path / "anchor.json"
    for count, expected in (("3", "VALID"), ("3.0", "INVALID")):
        anchor.write_text('{"schema":"kry_chain_anchor/v1","count":%s,"tip":"%s"}'
                          % (count, links[-1]["chain_hash"]), encoding="utf-8")
        result = _js_cli(att, str(anchor))
        assert f"VERDICT: {expected}" in result.stdout, (count, result.stdout + result.stderr)
