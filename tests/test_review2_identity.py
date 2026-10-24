"""Identity boundary regressions using constructed importer output, no live proofs."""
import importlib.util
import json
from pathlib import Path

import pytest


def _load(name):
    path = Path(__file__).resolve().parents[1] / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_NOTARY = "04a1b2c3d4e5f6" * 4
_KW = dict(expect_server="openrouter.ai", event_type="cache_hit",
           avoided_model="gh/claude-opus-4.8", served_model=None,
           tokens_saved=None, require_status=200, dry_run=False, expect_notary=_NOTARY)


def _presentation(gid, serial=1):
    body = {"data": {"id": gid, "native_tokens_prompt": 10,
                     "native_tokens_completion": 5000, "total_cost": 0}}
    return {"verified": True, "server_name": "openrouter.ai", "notary_key": _NOTARY,
            "time": serial, "recv": "HTTP/1.1 200 OK\r\n\r\n" + json.dumps(body)}


@pytest.mark.parametrize("gid", ["gen/7", "gen 7", "gen\t7", "gen\u00a07", "", 7, True, ["7"]])
@pytest.mark.parametrize("dry_run", [False, True])
def test_tlsn_invalid_id_refused_before_any_write(gid, dry_run):
    from kry import kry_mint as km
    result = _load("kry_tlsn_verify").run(_presentation(gid), **{**_KW, "dry_run": dry_run})
    assert result["verdict"] == "REJECTED"
    assert "generation_id" in " ".join(result["errors"])
    assert not km._MINT_LOG_PATH.exists()


@pytest.mark.parametrize("gid", ["gen-7", 'gen"7', r"gen\7", "測定7"])
@pytest.mark.parametrize("prior", [False, True])
def test_tlsn_decoded_id_credited_exactly_once(gid, prior):
    from kry import kry_mint as km
    if prior:
        original = km.mint("cache_hit", 5000, detail=f"route /openrouter:{gid}",
                           evidence="original", avoided_model=_KW["avoided_model"])
    mod = _load("kry_tlsn_verify")
    first = mod.run(_presentation(gid), **_KW)
    second = mod.run(_presentation(gid, 2), **_KW)
    assert first["verdict"] == "OK"
    if prior:
        assert first["minted"]["supersedes"] == original.receipt_id
    assert second["verdict"] == ("ALREADY_UPGRADED" if prior else "ALREADY_MINTED")
    assert km.veracity_breakdown()["total_kry"] == 5000
    assert km.verify_chain()[0]


@pytest.mark.parametrize("name", ["_find_t1_receipt_for_gen", "_find_fresh_t2_receipt_for_gen",
                                 "_find_measurement_receipt_for_tee", "_find_fresh_tee_receipt_for_measurement"])
@pytest.mark.parametrize("identifier", [None, "", "bad/id", "bad id", 7, True])
def test_private_lookup_invalid_id_is_not_a_miss(name, identifier):
    from kry import kry_mint as km
    with pytest.raises(ValueError, match="_id"):
        getattr(km, name)(identifier)


@pytest.mark.parametrize("gid", [None, "", "bad/id", "bad id", 7])
def test_tlsn_promotion_invalid_id_is_not_a_miss(gid):
    from kry import kry_mint as km
    with pytest.raises(ValueError, match="generation_id"):
        km.promote_to_tlsn(gid, "synthetic-proof")


def test_tlsn_routing_lookup_uses_decoded_json():
    from kry import kry_mint as km
    gid = 'gen"7'
    km.mint("cache_hit", 5000, detail=f"route /openrouter:{gid}", evidence="original",
            avoided_model=_KW["avoided_model"])
    result = _load("kry_tlsn_verify").run(_presentation(gid), **{**_KW, "avoided_model": None})
    assert result["verdict"] == "OK"
    assert result["avoided_model"]["source"] == "routing-log"
    assert result["minted"]["mode"] == "tier_upgrade"


def test_missing_identity_reports_evidence_gap_without_relaxing_grade():
    mod = _load("kry_reconcile")
    receipt = {"receipt_id": "KRY-1", "metered_tokens": [10, 20]}
    provider = {"id": "msg-1", "tokens_prompt": 10, "tokens_completion": 20}
    result = mod.reconcile([receipt], [provider])
    assert result["verdict"] == "DISCREPANCY" and result["matched"] == 0
    assert result["matching_basis"] == "request_id"
    assert result["unmatched_receipts"][0]["reason"] == "MISSING_RECEIPT_ID"
    assert result["token_consistency"]["matched"] == 1
    assert result["token_consistency"]["establishes_request_identity"] is False


@pytest.mark.parametrize("identity,reason", [("wrong", "NO_MATCHING_PROVIDER_ID"),
                                          ("msg-1", "TOKEN_MISMATCH"),
                                          (None, "INVALID_RECEIPT_ID")])
def test_identity_failure_reason_does_not_become_reconciled(identity, reason):
    mod = _load("kry_reconcile")
    receipt = {"receipt_id": "KRY-1", "provider_request_id": identity, "metered_tokens": [10, 20]}
    result = mod.reconcile([receipt], [{"id": "msg-1", "tokens_prompt": 10, "tokens_completion": 21}])
    assert result["verdict"] == "DISCREPANCY" and result["matched"] == 0
    assert result["unmatched_receipts"][0]["reason"] == reason


def test_explicit_provider_identity_is_retained_privately():
    from kry import kry_mint as km
    receipt = km.mint("short_circuit", 20, evidence="retained-provider-payload",
                      avoided_model="gh/claude-opus-4.8", evidence_tier="provider_metered",
                      metered_tokens=[10, 20], provider_request_id="msg-1")
    assert receipt is not None
    record = json.loads(km._MINT_LOG_PATH.read_text().splitlines()[0])
    assert record["provider_request_id"] == "msg-1"
    result = _load("kry_reconcile").reconcile([record], [
        {"id": "msg-1", "tokens_prompt": 10, "tokens_completion": 20}])
    assert result["verdict"] == "RECONCILED" and result["matching_basis"] == "request_id"
    assert km.verify_chain()[0]


def test_optional_id_does_not_change_legacy_mode_or_public_projection(tmp_path):
    from kry import kry_mint as km
    km.mint("short_circuit", 20, evidence="without-id", evidence_tier="provider_metered",
            metered_tokens=[10, 20])
    first = json.loads(km._MINT_LOG_PATH.read_text().splitlines()[0])
    assert "provider_request_id" not in first
    assert _load("kry_reconcile").reconcile([first], [{
        "tokens_prompt": 10, "tokens_completion": 20}])["matching_basis"] == "token_counts_only"
    km.mint("short_circuit", 20, evidence="with-id", evidence_tier="provider_metered",
            metered_tokens=[10, 20], provider_request_id="msg-private")
    out = tmp_path / "t1-manifest.json"
    _load("kry_verified_artifact").write_t1_manifest(km._MINT_LOG_PATH, out)
    assert "msg-private" not in out.read_text()
    assert "provider_request_id_sha256" in out.read_text()


def test_tlsn_cli_rejects_invalid_id(tmp_path, capsys):
    path = tmp_path / "presentation.json"
    path.write_text(json.dumps(_presentation("gen/7")))
    assert _load("kry_tlsn_verify").main([str(path), "--dry-run"]) == 1
    assert "generation_id" in capsys.readouterr().out


def test_tlsn_equivalent_json_escaping_does_not_hide_receipt():
    from kry import kry_mint as km
    km.mint("cache_hit", 5000, detail="route /openrouter:gen-7", evidence="original")
    km._MINT_LOG_PATH.write_text(km._MINT_LOG_PATH.read_text().replace("gen-7", r"gen-\u0037"))
    assert km.verify_chain()[0]
    result = _load("kry_tlsn_verify").run(_presentation("gen-7"), **_KW)
    assert result["minted"]["mode"] == "tier_upgrade"
    assert km.veracity_breakdown()["total_kry"] == 5000


@pytest.mark.parametrize("identity", ["", " msg-1", "msg-1\n", "msg-\ud800", True, 7, ["msg-1"]])
def test_invalid_provider_identity_does_not_write(identity):
    from kry import kry_mint as km
    assert km.mint("cache_hit", 20, evidence="test", provider_request_id=identity) is None
    assert not km._MINT_LOG_PATH.exists()


@pytest.mark.parametrize("receipt", [False, True])
def test_unpaired_surrogate_identity_is_a_controlled_non_match(receipt):
    mod = _load("kry_reconcile")
    key = "provider_request_id" if receipt else "id"
    assert mod.request_id_digest({key: "msg-\ud800"}, receipt=receipt) is None
