"""Evidence identity and fetch-window regressions; all provider calls are stubbed."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from test_verified_artifact import minted_sample as minted_sample

_ROOT = Path(__file__).resolve().parents[1]


def _load(name):
    spec = importlib.util.spec_from_file_location(name, _ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _log(tmp_path, rows):
    path = tmp_path / "receipts.jsonl"
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    return str(path)


def _receipt(gid, ts):
    return {"receipt_id": f"KRY-{gid}", "evidence_tier": "provider_metered",
            "detail": f"displacement/openrouter:{gid}", "ts": ts,
            "metered_tokens": [10, 20]}


def test_fetch_ids_obey_half_open_receipt_window(tmp_path):
    mod = _load("kry_or_fetch")
    rows = [_receipt(f"gen-{i}", i) for i in range(4)]
    rows.append(_receipt("gen-missing-time", None))
    path = _log(tmp_path, rows)
    assert mod.extract_or_ids(path, since=1, until=3) == ["gen-1", "gen-2"]
    assert mod.extract_or_ids(path, since=3) == ["gen-3"]
    assert mod.extract_or_ids(path, until=1) == ["gen-0"]


@pytest.mark.parametrize("bounds", [
    {"since": float("nan")}, {"until": float("inf")}, {"since": True},
    {"since": 3, "until": 3}, {"since": 4, "until": 3},
])
def test_fetch_ids_reject_invalid_window(tmp_path, bounds):
    mod = _load("kry_or_fetch")
    with pytest.raises(ValueError):
        mod.extract_or_ids(_log(tmp_path, [_receipt("gen-1", 1)]), **bounds)


def test_grade_fetch_uses_only_selected_receipt_window(tmp_path, monkeypatch, capsys):
    mod = _load("kry_research_grade")
    path = _log(tmp_path, [_receipt(f"gen-{i}", i) for i in range(4)])
    fetched = []

    def fake_fetch(gid, key):
        fetched.append(gid)
        return {"id": gid, "tokens_prompt": 10, "tokens_completion": 20}

    monkeypatch.setattr(mod.kry_or_fetch, "fetch_generation", fake_fetch)
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    assert mod.main([path, "--fetch", "--since", "1", "--until", "3"]) == 0
    assert fetched == ["gen-1", "gen-2"]
    captured = capsys.readouterr()
    assert "fetched 2/2" in captured.err
    assert "source provenance is unverified" in captured.out


def test_grade_fetch_rejects_bad_window_before_network(tmp_path, monkeypatch, capsys):
    mod = _load("kry_research_grade")
    path = _log(tmp_path, [_receipt("gen-1", 1)])

    def unexpected_fetch(*args, **kwargs):
        pytest.fail("invalid window must not trigger a provider request")

    monkeypatch.setattr(mod.kry_or_fetch, "fetch_generation", unexpected_fetch)
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    assert mod.main([path, "--fetch", "--since", "3", "--until", "1"]) == 2
    assert "since < until" in capsys.readouterr().err


def _provider(gid, prompt=10, completion=20):
    return {"id": gid, "tokens_prompt": prompt, "tokens_completion": completion}


def test_request_identity_precedes_token_counts_and_provider_order():
    mod = _load("kry_reconcile")
    receipts = [_receipt("gen-a", 1), _receipt("gen-b", 2)]
    result = mod.reconcile(receipts, [_provider("gen-b"), _provider("gen-a")])
    assert result["matched"] == 2
    assert result["matching_basis"] == "request_id"
    assert result["verdict"] == "RECONCILED"
    rewritten = mod.reconcile(receipts, [_provider("wrong-b"), _provider("wrong-a")])
    assert rewritten["matched"] == 0
    assert rewritten["verdict"] == "DISCREPANCY"


@pytest.mark.parametrize("mutation", [
    "provider_missing", "receipt_missing", "provider_duplicate", "receipt_duplicate",
    "receipt_conflict", "provider_conflict", "invalid_provider_id", "invalid_digest",
    "tokens_changed",
])
def test_identity_mismatch_never_falls_back_to_equal_counts(mutation):
    mod = _load("kry_reconcile")
    receipts = [_receipt("gen-a", 1)]
    providers = [_provider("gen-a")]
    if mutation == "provider_missing":
        del providers[0]["id"]
    elif mutation == "receipt_missing":
        del receipts[0]["detail"]
    elif mutation == "provider_duplicate":
        providers.append(_provider("gen-a"))
    elif mutation == "receipt_duplicate":
        receipts.append(_receipt("gen-a", 2))
    elif mutation == "receipt_conflict":
        receipts[0]["provider_request_id"] = "gen-b"
    elif mutation == "provider_conflict":
        providers[0]["request_id"] = "gen-b"
    elif mutation == "invalid_provider_id":
        providers[0]["id"] = True
    elif mutation == "invalid_digest":
        receipts[0]["provider_request_id_sha256"] = "not-a-digest"
    elif mutation == "tokens_changed":
        providers[0]["tokens_prompt"] = 11
    result = mod.reconcile(receipts, providers)
    assert result["matching_basis"] == "request_id"
    assert result["verdict"] == "DISCREPANCY"
    assert result["matched"] == 0


def test_legacy_token_consistency_is_labeled_and_cannot_advance_grade(tmp_path):
    mod = _load("kry_research_grade")
    receipt = _receipt("gen-a", 1)
    del receipt["detail"]
    provider = _provider("gen-a")
    del provider["id"]
    result = mod.assess(_log(tmp_path, [receipt]), provider_export=[provider])
    assert result["reconcile"]["verdict"] == "RECONCILED"
    assert result["reconcile"]["matching_basis"] == "token_counts_only"
    assert result["independent_agreement"] == 1.0
    assert result["research_grade_reached"] is False


def test_aggregate_80_percent_coverage_cannot_override_discrepancy(tmp_path):
    mod = _load("kry_research_grade")
    receipt = _receipt("gen-a", 1)
    receipt["metered_tokens"] = [125, 0]
    result = mod.assess(_log(tmp_path, [receipt]), mode="aggregate", since=0, until=2,
                        provider_export={"prompt_tokens": 100, "completion_tokens": 0})
    assert result["independent_agreement"] == .8
    assert result["reconcile"]["verdict"] == "DISCREPANCY"
    assert result["research_grade_reached"] is False


def test_manifest_identity_projection_excludes_raw_identifier(tmp_path):
    import hashlib

    mod = _load("kry_verified_artifact")
    receipt = _receipt("gen-private-reference", 1)
    receipt["receipt_id"] = "KRY-opaque"
    log = _log(tmp_path, [receipt])
    manifest = tmp_path / "t1.json"
    mod.write_t1_manifest(log, manifest)
    raw = manifest.read_text(encoding="utf-8")
    row = json.loads(raw)["receipts"][0]
    assert "gen-private-reference" not in raw
    assert row["provider_request_id_sha256"] == hashlib.sha256(b"gen-private-reference").hexdigest()
    assert "detail" not in row
    assert "provider_request_id" not in row
    assert mod.kry_reconcile.reconcile([row], [_provider("gen-private-reference")])["verdict"] == "RECONCILED"


def test_historical_zero_dollar_bundle_is_pinned_and_exactly_joined():
    import hashlib

    mod = _load("kry_reconcile")
    bundle = _ROOT / "docs/evidence/research_grade"
    expected = {
        "research_grade_mintlog.jsonl": "2257d5e157b0c7f56d0b8bfd42bdd6ac7fbe592087e1132d5f9148e67a261cb2",
        "or_provider_export.json": "739320d7ae52926984e4794f36f0e61cfd907cc1487e7fbe592cae17ae690501",
    }
    for name, digest in expected.items():
        assert hashlib.sha256((bundle / name).read_bytes()).hexdigest() == digest
    receipts = mod.load_t1_receipts(str(bundle / "research_grade_mintlog.jsonl"))
    providers = json.loads((bundle / "or_provider_export.json").read_text(encoding="utf-8"))
    assert len(receipts) == len(providers) == 52
    assert {row["hash_version"] for row in receipts} == {2}
    assert sum(row["kry_minted"] for row in receipts) == 0
    assert sum(row["total_cost"] for row in providers) == 0
    result = mod.reconcile(receipts, list(reversed(providers)))
    assert result["matching_basis"] == "request_id"
    assert result["matched"] == 52
    assert result["reconciled_fraction"] == 1.0
    rewritten = [dict(row, id=f"wrong-{i}") for i, row in enumerate(providers)]
    assert mod.reconcile(receipts, rewritten)["matched"] == 0
    assert mod.reconcile(receipts, providers[10:])["verdict"] == "DISCREPANCY"


def test_artifact_cannot_regrade_identityless_agreement(minted_sample, tmp_path):
    from test_verified_artifact import (
        _claims, _corpus_manifest, _provider_export_manifest, _record_count, _review_files,
    )

    usage_log, att_path, log = minted_sample
    mod = _load("kry_verified_artifact")
    provider = tmp_path / "provider.json"
    provider.write_text(json.dumps([{"prompt_tokens": 2000, "completion_tokens": 400}]), encoding="utf-8")
    manifest = tmp_path / "t1.json"
    mod.write_t1_manifest(str(log), manifest)
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    for row in payload["receipts"]:
        row.pop("provider_request_id_sha256")
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    provider_manifest = _provider_export_manifest(tmp_path, mod, provider, manifest)
    corpus = _corpus_manifest(
        tmp_path, mod, usage_log, provider, record_count=_record_count(usage_log),
        t1_manifest=manifest, provider_export_manifest=provider_manifest,
    )
    outside, buyer, legal = _review_files(
        tmp_path, mod, usage_log, att_path, provider, corpus, manifest, provider_manifest,
    )
    packet = mod.build_artifact(
        str(usage_log), attestation=str(att_path), t1_manifest=str(manifest),
        provider_export=str(provider), provider_export_manifest=str(provider_manifest),
        corpus="real", corpus_manifest=str(corpus), outside_review=str(outside),
        buyer_feedback=str(buyer), legal_review=str(legal),
    )
    assert packet["research_assessment"]["reconcile"]["verdict"] == "RECONCILED"
    assert packet["research_assessment"]["independent_agreement"] == 1.0
    assert packet["research_assessment"]["research_grade_reached"] is False
    assert packet["gates"]["product"]["status"] == "PASS"
    assert packet["gates"]["external_review"]["status"] == "PASS"
    assert packet["production_readiness_if_claimed"]["label"] == "internally_consistent"
    assert "science:research_grade_prerequisites" in packet["external_blockers"]
    assert packet["claim_allowed"]["external_verified_savings"] is False
    assert _claims(packet)["provider_reconciled"]["status"] == "blocked"
