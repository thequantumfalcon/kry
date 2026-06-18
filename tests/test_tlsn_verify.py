"""T2 (tlsn_attested): mint from a verified TLSNotary presentation.

The Rust verifier (attestation_verify) is the cryptographic root of trust; these
tests pin the KRY-side glue that turns its ALREADY-VERIFIED output into a T2 mint:

  - a verified 200 with provider usage mints a tlsn_attested receipt and LIFTS the
    veracity_floor (the new tier counts as externally anchored)
  - the receipt is tamper-evident exactly like every current receipt (the tier is
    bound into the hash, so a forged downgrade/upgrade breaks the chain)
  - fail-closed: not-verified, wrong server, and non-200 are REFUSED with no mint
  - replaying the SAME presentation does not double-mint (evidence binding + decay)
  - a credits-style body (no token counts) mints nothing without an explicit basis
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "kry_tlsn_verify.py"


def _load():
    spec = importlib.util.spec_from_file_location("kry_tlsn_verify_standalone", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    import kry.kry_token as kt
    import kry.kry_mint as km
    log = tmp_path / "mint.jsonl"
    monkeypatch.setattr(km, "_MINT_LOG_PATH", log)
    monkeypatch.setattr(kt, "_LEDGER_PATH", tmp_path / "ledger.json")
    monkeypatch.setattr(km, "_DECAY_STATE_PATH", tmp_path / "decay.json")
    km._RECEIPT_COUNTER = 0
    km._CHAIN_TIP = "0" * 64
    km._evidence_mints = {}
    km._decay_loaded = True
    kt._ledger_instance = kt.KRYLedger()
    return kt, km, log


def _presentation(*, verified=True, server="openrouter.ai", status=200,
                  prompt=120, completion=300, gen_id="gen-abc", cost=0.0,
                  model=None, body=None):
    if body is None:
        model_field = f'"model":"{model}",' if model else ""
        body = ('{"data":{"id":"%s",%s"native_tokens_prompt":%d,'
                '"native_tokens_completion":%d,"total_cost":%s,'
                '"provider_name":"DeepInfra"}}'
                % (gen_id, model_field, prompt, completion, cost))
    recv = (f"HTTP/1.1 {status} OK\r\n"
            "Date: Fri, 05 Jun 2026 03:39:15 GMT\r\n"
            "Content-Type: application/json\r\n"
            "Server: cloudflare\r\n\r\n" + body)
    return {
        "verified": verified,
        "server_name": server,
        "recv": recv,
        "sent": "GET /api/v1/generation?id=gen-abc HTTP/1.1\r\nHost: openrouter.ai\r\n\r\n",
        "notary_key": "04a1b2c3d4e5f6" * 4,
        "time": 1780554000,
    }


def test_verified_200_mints_t2_and_lifts_floor(isolated):
    kt, km, log = isolated
    mod = _load()
    # seed a self_reported cache hit so the floor starts at 0 and has room to lift
    km.mint("cache_hit", 1000, "c", evidence="seed", avoided_model="gh/claude-opus-4.8")
    assert km.veracity_breakdown()["veracity_floor"] == 0.0

    res = mod.run(_presentation(), expect_server="openrouter.ai",
                  event_type="short_circuit", avoided_model="gh/claude-opus-4.8",
                  served_model=None, tokens_saved=None, require_status=200,
                  dry_run=False)

    assert res["verdict"] == "OK"
    assert res["attested_tokens"] == {"prompt": 120, "completion": 300}
    assert res["minted"]["evidence_tier"] == "tlsn_attested"
    assert res["minted"]["kry_minted"] > 0
    assert res["veracity_floor"]["after"] > res["veracity_floor"]["before"]

    # the chain still verifies and the new tier is counted as externally anchored
    assert km.verify_chain()[0]
    vb = km.veracity_breakdown()
    assert vb["by_tier"].get("tlsn_attested", 0) > 0
    assert vb["externally_anchored_kry"] > 0


def test_t2_receipt_is_tamper_evident(isolated):
    kt, km, log = isolated
    mod = _load()
    import json
    res = mod.run(_presentation(), expect_server="openrouter.ai",
                  event_type="short_circuit", avoided_model="gh/claude-opus-4.8",
                  served_model=None, tokens_saved=None, require_status=200,
                  dry_run=False)
    assert res["minted"]["evidence_tier"] == "tlsn_attested"
    assert km.verify_chain()[0]

    # forge a tier downgrade in place → the receipt hash (which binds the tier) breaks
    lines = log.read_text(encoding="utf-8").splitlines()
    rec = json.loads(lines[-1])
    assert rec["hash_version"] == 5   # current mint format (v5: +language-neutral integer block)
    rec["evidence_tier"] = "self_reported"
    lines[-1] = json.dumps(rec)
    log.write_text("\n".join(lines) + "\n", encoding="utf-8")
    ok, errs = km.verify_chain()
    assert not ok
    assert any("receipt_hash mismatch" in e for e in errs)


def test_not_verified_is_refused(isolated):
    kt, km, log = isolated
    mod = _load()
    res = mod.run(_presentation(verified=False), expect_server="openrouter.ai",
                  event_type="short_circuit", avoided_model="gh/claude-opus-4.8",
                  served_model=None, tokens_saved=None, require_status=200,
                  dry_run=False)
    assert res["verdict"] == "REJECTED"
    assert any("verified" in e for e in res["errors"])
    assert km.chain_summary()["receipts"] == 0   # nothing minted


def test_wrong_server_is_refused(isolated):
    kt, km, log = isolated
    mod = _load()
    res = mod.run(_presentation(server="evil.example.com"),
                  expect_server="openrouter.ai", event_type="short_circuit",
                  avoided_model="gh/claude-opus-4.8", served_model=None,
                  tokens_saved=None, require_status=200, dry_run=False)
    assert res["verdict"] == "REJECTED"
    assert any("expected" in e for e in res["errors"])
    assert km.chain_summary()["receipts"] == 0


def test_non_200_is_refused(isolated):
    kt, km, log = isolated
    mod = _load()
    res = mod.run(_presentation(status=401, body='{"error":{"code":401}}'),
                  expect_server="openrouter.ai", event_type="short_circuit",
                  avoided_model="gh/claude-opus-4.8", served_model=None,
                  tokens_saved=None, require_status=200, dry_run=False)
    assert res["verdict"] == "REJECTED"
    assert any("status" in e for e in res["errors"])
    assert km.chain_summary()["receipts"] == 0


def test_notarized_body_rejects_nonstandard_json_constants(isolated):
    kt, km, log = isolated
    mod = _load()
    pres = _presentation(body='{"data":{"native_tokens_prompt":1,"native_tokens_completion":NaN}}')

    res = mod.run(pres, expect_server="openrouter.ai", event_type="short_circuit",
                  avoided_model="gh/claude-opus-4.8", served_model=None,
                  tokens_saved=None, require_status=200, dry_run=False)

    assert res["verdict"] == "REJECTED"
    assert any("non-standard JSON constant NaN" in e for e in res["errors"])
    assert km.chain_summary()["receipts"] == 0


def test_provider_token_counts_must_be_json_integers(isolated):
    kt, km, log = isolated
    mod = _load()
    pres = _presentation(body='{"data":{"native_tokens_prompt":true,"native_tokens_completion":3}}')

    res = mod.run(pres, expect_server="openrouter.ai", event_type="short_circuit",
                  avoided_model="gh/claude-opus-4.8", served_model=None,
                  tokens_saved=None, require_status=200, dry_run=False)

    assert res["verdict"] == "REJECTED"
    assert any("provider prompt token count must be a non-negative JSON integer" in e
               for e in res["errors"])
    assert km.chain_summary()["receipts"] == 0


def test_non_finite_cli_basis_refuses(isolated):
    kt, km, log = isolated
    mod = _load()

    res = mod.run(_presentation(), expect_server="openrouter.ai",
                  event_type="short_circuit", avoided_model="gh/claude-opus-4.8",
                  served_model=None, tokens_saved=float("nan"), require_status=200,
                  dry_run=False)

    assert res["verdict"] == "NO_BASIS"
    assert km.chain_summary()["receipts"] == 0


def test_presentation_json_boundary_rejects_nonstandard_constants():
    mod = _load()

    with pytest.raises(ValueError, match="non-standard JSON constant NaN"):
        mod._json_loads('{"verified":true,"server_name":"openrouter.ai","recv":NaN}')


def test_replay_does_not_double_mint(isolated):
    kt, km, log = isolated
    mod = _load()
    pres = _presentation()
    kw = dict(expect_server="openrouter.ai", event_type="short_circuit",
              avoided_model="gh/claude-opus-4.8", served_model=None,
              tokens_saved=None, require_status=200, dry_run=False)
    first = mod.run(pres, **kw)
    assert first["minted"]["kry_minted"] > 0
    floor_after_first = km.veracity_breakdown()["veracity_floor"]

    second = mod.run(pres, **kw)   # identical presentation → same evidence binding
    # decay collapses the repeat: either no receipt, or a dust receipt that does not
    # raise the floor above the first mint's level
    assert second["verdict"] in ("NOT_MINTED", "OK")
    assert km.veracity_breakdown()["veracity_floor"] <= floor_after_first + 1e-9


def test_dry_run_mints_nothing(isolated):
    kt, km, log = isolated
    mod = _load()
    res = mod.run(_presentation(), expect_server="openrouter.ai",
                  event_type="short_circuit", avoided_model="gh/claude-opus-4.8",
                  served_model=None, tokens_saved=None, require_status=200,
                  dry_run=True)
    assert res["verdict"] == "OK"
    assert res["minted"] is None
    assert km.chain_summary()["receipts"] == 0


def test_served_model_from_attested_body(isolated):
    """served_model defaults to the model named in the notarized body (attested)."""
    kt, km, log = isolated
    mod = _load()
    pres = _presentation(model="nvidia/nemotron-3-super-120b-a12b:free")
    res = mod.run(pres, expect_server="openrouter.ai", event_type="short_circuit",
                  avoided_model="gh/claude-opus-4.8", served_model=None,
                  tokens_saved=None, require_status=200, dry_run=False)
    assert res["served_model"]["value"] == "nvidia/nemotron-3-super-120b-a12b:free"
    assert res["served_model"]["source"] == "attested-body"


def test_avoided_model_from_routing_log_upgrades_not_double_credits(isolated):
    """When the HOST already minted this gen id as a T1 displacement, T2 must UPGRADE that
    receipt's tier — NOT credit the saving a second time (docs/KRY_T2_FINDINGS_REPORT.md §7b,
    option iii). avoided_model still resolves from the recorded routing decision; the mint is
    a net-zero tier promotion; total supply is unchanged; the value re-tiers to tlsn_attested."""
    kt, km, log = isolated
    mod = _load()
    # the host minted a routing receipt for this generation (T1 provider_metered)
    t1 = km.mint("short_circuit", 100, "routed via OR /openrouter:gen-routed-1", evidence="r1",
                 avoided_model="gh/claude-opus-4.8", evidence_tier=km.TIER_PROVIDER_METERED,
                 metered_tokens=[10, 20])
    before = km.veracity_breakdown()
    assert before["by_tier"].get("provider_metered", 0) > 0
    assert before["tlsn_attested_fraction"] == 0.0

    pres = _presentation(gen_id="gen-routed-1", model="some/cheap:free")
    res = mod.run(pres, expect_server="openrouter.ai", event_type="short_circuit",
                  avoided_model=None, served_model=None,   # no CLI — must come from the log
                  tokens_saved=None, require_status=200, dry_run=False)
    assert res["avoided_model"]["value"] == "gh/claude-opus-4.8"
    assert res["avoided_model"]["source"] == "routing-log"

    # net-zero tier UPGRADE, not a second credit
    assert res["minted"]["mode"] == "tier_upgrade"
    assert res["minted"]["supersedes"] == t1.receipt_id
    assert res["minted"]["evidence_tier"] == "tlsn_attested"
    assert res["minted"]["kry_re_tiered"] == pytest.approx(t1.kry_minted)

    after = km.veracity_breakdown()
    # THE FIX: total supply is unchanged — the saving was credited exactly once at T1
    assert after["total_kry"] == pytest.approx(before["total_kry"])
    # the value moved provider_metered -> tlsn_attested (binary floor unchanged: both anchored)
    assert after["by_tier"].get("provider_metered", 0) == pytest.approx(0.0, abs=1e-9)
    assert after["by_tier"]["tlsn_attested"] == pytest.approx(t1.kry_minted)
    assert after["tlsn_attested_fraction"] > 0.0
    assert after["veracity_floor"] == pytest.approx(before["veracity_floor"])
    assert km.verify_chain()[0]

    # idempotent: a re-run does not stack a second promotion
    res2 = mod.run(pres, expect_server="openrouter.ai", event_type="short_circuit",
                   avoided_model=None, served_model=None, tokens_saved=None,
                   require_status=200, dry_run=False)
    assert res2["verdict"] == "ALREADY_UPGRADED"
    assert "minted" not in res2
    assert km.veracity_breakdown()["total_kry"] == pytest.approx(before["total_kry"])


def test_no_routing_and_no_cli_refuses_to_mint(isolated):
    """No recorded routing decision and no --avoided-model → REFUSE (the counterfactual is
    never invented, and value_multiplier(None)=1.0 would silently credit full value)."""
    kt, km, log = isolated
    mod = _load()
    res = mod.run(_presentation(gen_id="gen-unknown", model="x/y:free"),
                  expect_server="openrouter.ai", event_type="short_circuit",
                  avoided_model=None, served_model=None, tokens_saved=None,
                  require_status=200, dry_run=False)
    assert res["verdict"] == "NO_DISPLACEMENT_CONTEXT"
    assert res["avoided_model"]["value"] is None
    assert "minted" not in res
    assert km.chain_summary()["receipts"] == 0   # nothing minted


_PRES_NOTARY = "04a1b2c3d4e5f6" * 4   # the notary_key _presentation() stamps


def test_pinned_notary_match_mints(isolated):
    """A presentation notarized by the PINNED notary mints normally (case/0x-insensitive)."""
    kt, km, log = isolated
    mod = _load()
    res = mod.run(_presentation(), expect_server="openrouter.ai", event_type="short_circuit",
                  avoided_model="gh/claude-opus-4.8", served_model=None, tokens_saved=None,
                  require_status=200, dry_run=False,
                  expect_notary="0x" + _PRES_NOTARY.upper())   # normalization handles 0x + case
    assert res["verdict"] == "OK"
    assert res["notary_pinned"] is True
    assert res["minted"]["evidence_tier"] == "tlsn_attested"


def test_pinned_notary_mismatch_is_refused(isolated):
    """A presentation notarized by a DIFFERENT notary than pinned is REFUSED — nothing minted."""
    kt, km, log = isolated
    mod = _load()
    res = mod.run(_presentation(), expect_server="openrouter.ai", event_type="short_circuit",
                  avoided_model="gh/claude-opus-4.8", served_model=None, tokens_saved=None,
                  require_status=200, dry_run=False,
                  expect_notary="dead" * 16)
    assert res["verdict"] == "REJECTED"
    assert any("UNPINNED notary" in e for e in res["errors"])
    assert km.chain_summary()["receipts"] == 0


def test_pinned_notary_but_presentation_has_none_is_refused(isolated):
    """Pinning a notary while the presentation carries no notary_key is REFUSED (fail-closed)."""
    kt, km, log = isolated
    mod = _load()
    pres = _presentation()
    pres.pop("notary_key")
    res = mod.run(pres, expect_server="openrouter.ai", event_type="short_circuit",
                  avoided_model="gh/claude-opus-4.8", served_model=None, tokens_saved=None,
                  require_status=200, dry_run=False, expect_notary=_PRES_NOTARY)
    assert res["verdict"] == "REJECTED"
    assert any("no notary_key" in e for e in res["errors"])
    assert km.chain_summary()["receipts"] == 0


def test_no_pin_is_unchanged(isolated):
    """Without --notary-key the path is byte-for-byte the prior behavior: any verified
    presentation mints, and notary_pinned is False."""
    kt, km, log = isolated
    mod = _load()
    res = mod.run(_presentation(), expect_server="openrouter.ai", event_type="short_circuit",
                  avoided_model="gh/claude-opus-4.8", served_model=None, tokens_saved=None,
                  require_status=200, dry_run=False)   # expect_notary defaults to None
    assert res["verdict"] == "OK"
    assert res["notary_pinned"] is False
    assert res["minted"]["evidence_tier"] == "tlsn_attested"


def test_credits_body_no_tokens_needs_explicit_basis(isolated):
    kt, km, log = isolated
    mod = _load()
    # /api/v1/credits attests dollars, not per-request tokens → no completion count
    credits = _presentation(body='{"data":{"total_credits":10.0,"total_usage":3.2}}')
    res = mod.run(credits, expect_server="openrouter.ai", event_type="short_circuit",
                  avoided_model="gh/claude-opus-4.8", served_model=None,
                  tokens_saved=None, require_status=200, dry_run=False)
    assert res["verdict"] == "NO_BASIS"
    assert km.chain_summary()["receipts"] == 0

    # with an explicit basis it mints
    res2 = mod.run(credits, expect_server="openrouter.ai", event_type="short_circuit",
                   avoided_model="gh/claude-opus-4.8", served_model=None,
                   tokens_saved=500.0, require_status=200, dry_run=False)
    assert res2["verdict"] == "OK"
    assert res2["minted"]["evidence_tier"] == "tlsn_attested"
