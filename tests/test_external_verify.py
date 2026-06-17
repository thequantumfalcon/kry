"""Falsifier #1: a STRANGER can verify a KRY settlement from public artifacts
alone — no package runtime, no live host.

The standalone verifier is scripts/kry_verify.py (stdlib-only). These tests:
  - build REAL attestation + settlement artifacts via the kry package,
  - hand them to the standalone verifier (loaded by path, not as a package import),
  - confirm it AGREES with the package and CATCHES every tamper class,
  - confirm the script depends on nothing in `kernel.*`/`kry.*` (the whole point).
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

_VERIFIER = Path(__file__).resolve().parents[1] / "scripts" / "kry_verify.py"


def _load_verifier():
    spec = importlib.util.spec_from_file_location("kry_verify_standalone", _VERIFIER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    import kry.kry_token as kt
    import kry.kry_mint as km
    import kry.kry_attest as ka
    import kry.kry_settlement as ks
    log = tmp_path / "mint.jsonl"
    monkeypatch.setattr(km, "_MINT_LOG_PATH", log)
    monkeypatch.setattr(ka, "_MINT_LOG_PATH", log)
    monkeypatch.setattr(kt, "_LEDGER_PATH", tmp_path / "ledger.json")
    monkeypatch.setattr(km, "_DECAY_STATE_PATH", tmp_path / "decay.json")
    monkeypatch.setattr(ks, "_REGISTRY_PATH", tmp_path / "reg.jsonl")
    km._RECEIPT_COUNTER = 0
    km._CHAIN_TIP = "0" * 64
    km._evidence_mints = {}
    km._decay_loaded = True
    kt._ledger_instance = kt.KRYLedger()
    return kt, km, ka, ks, log


def test_verifier_imports_no_kernel():
    """The falsifier's load-bearing constraint: zero package dependency."""
    src = _VERIFIER.read_text(encoding="utf-8")
    assert "import kernel" not in src and "from kernel" not in src
    assert "import kry" not in src and "from kry" not in src


def test_stranger_agrees_with_package(isolated):
    kt, km, ka, ks, log = isolated
    for i in range(10):
        km.mint("cache_hit", 1000, f"q{i}", evidence=f"u{i}",
                avoided_model="gh/claude-opus-4.8")
    att = json.loads(ka.build_attestation(log).to_public_json())

    v = _load_verifier()
    ok, errs = v.verify_attestation(att)
    assert ok, errs
    # the package agrees
    assert ka.verify_attestation(json.dumps(att))[0]


def test_stranger_accepts_netted_paid_displacement(isolated):
    """A cheaper-PAID (OpenRouter) displacement mints provider_metered with magnitude
    = the NET saving vm(avoided) - vm(served), a difference of two public prices. The
    F2 verifier must ACCEPT it (legal_multipliers includes pairwise differences), not
    flag it as a non-public price — the regression the net-saving change could cause."""
    kt, km, ka, ks, log = isolated
    km.mint("short_circuit", 1000, "disp/or/deepseek-v4-pro/openrouter:gen-x",
            evidence="e", avoided_model="gh/claude-opus-4.8",
            served_model="or/deepseek/deepseek-v4-pro",
            evidence_tier=km.TIER_PROVIDER_METERED, metered_tokens=[10, 20])
    att = json.loads(ka.build_attestation(log).to_public_json())
    assert att["links"][0]["metered_tokens"] == [10, 20]
    assert isinstance(att["links"][0]["ts"], float)
    v = _load_verifier()
    ok, errs = v.verify_attestation(att)
    assert ok, errs


def test_stranger_rejects_provider_metered_without_timestamp(isolated):
    kt, km, ka, ks, log = isolated
    km.mint("short_circuit", 1000, "disp/or/deepseek-v4-pro/openrouter:gen-x",
            evidence="e", avoided_model="gh/claude-opus-4.8",
            served_model="or/deepseek/deepseek-v4-pro",
            evidence_tier=km.TIER_PROVIDER_METERED, metered_tokens=[10, 20])
    att = json.loads(ka.build_attestation(log).to_public_json())
    del att["links"][0]["ts"]
    v = _load_verifier()
    att["attestation_hash"] = v._attestation_hash(att)

    ok, errs = v.verify_attestation(att)

    assert not ok
    assert any("provider_metered link missing numeric ts" in e for e in errs)


def test_stranger_rejects_provider_metered_without_metered_tokens(isolated):
    kt, km, ka, ks, log = isolated
    km.mint("short_circuit", 1000, "disp/or/deepseek-v4-pro/openrouter:gen-x",
            evidence="e", avoided_model="gh/claude-opus-4.8",
            served_model="or/deepseek/deepseek-v4-pro",
            evidence_tier=km.TIER_PROVIDER_METERED, metered_tokens=[10, 20])
    att = json.loads(ka.build_attestation(log).to_public_json())
    del att["links"][0]["metered_tokens"]
    v = _load_verifier()
    att["attestation_hash"] = v._attestation_hash(att)

    ok, errs = v.verify_attestation(att)

    assert not ok
    assert any("provider_metered link missing metered_tokens" in e for e in errs)


def test_stranger_rejects_non_integer_provider_metered_tokens(isolated):
    kt, km, ka, ks, log = isolated
    km.mint("short_circuit", 1000, "disp/or/deepseek-v4-pro/openrouter:gen-x",
            evidence="e", avoided_model="gh/claude-opus-4.8",
            served_model="or/deepseek/deepseek-v4-pro",
            evidence_tier=km.TIER_PROVIDER_METERED, metered_tokens=[10, 20])
    att = json.loads(ka.build_attestation(log).to_public_json())
    v = _load_verifier()
    att["links"][0]["metered_tokens"] = [True, 20]
    att["attestation_hash"] = v._attestation_hash(att)

    ok, errs = v.verify_attestation(att)

    assert not ok
    assert any("provider_metered metered_tokens must be integers" in e for e in errs)


def test_stranger_rejects_extra_provider_metered_tokens(isolated):
    kt, km, ka, ks, log = isolated
    km.mint("short_circuit", 1000, "disp/or/deepseek-v4-pro/openrouter:gen-x",
            evidence="e", avoided_model="gh/claude-opus-4.8",
            served_model="or/deepseek/deepseek-v4-pro",
            evidence_tier=km.TIER_PROVIDER_METERED, metered_tokens=[10, 20])
    att = json.loads(ka.build_attestation(log).to_public_json())
    v = _load_verifier()
    att["links"][0]["metered_tokens"] = [10, 20, 30]
    att["attestation_hash"] = v._attestation_hash(att)

    ok, errs = v.verify_attestation(att)

    assert not ok
    assert any("provider_metered link missing metered_tokens" in e for e in errs)


def test_stranger_catches_tampered_amount(isolated):
    kt, km, ka, ks, log = isolated
    for i in range(5):
        km.mint("cache_hit", 1000, f"q{i}", evidence=f"u{i}",
                avoided_model="gh/claude-opus-4.8")
    att = json.loads(ka.build_attestation(log).to_public_json())
    att["links"][2]["kry_minted"] += 50000   # inflate one link's payout

    v = _load_verifier()
    ok, errs = v.verify_attestation(att)
    assert not ok
    assert any("total_kry mismatch" in e for e in errs)


def test_stranger_catches_forged_veracity_floor(isolated):
    kt, km, ka, ks, log = isolated
    km.mint("cache_hit", 1000, "a", evidence="a", avoided_model="gh/claude-opus-4.8")
    att = json.loads(ka.build_attestation(log).to_public_json())
    att["veracity"]["veracity_floor"] = 0.9   # claim external anchoring we don't have

    v = _load_verifier()
    ok, errs = v.verify_attestation(att)
    assert not ok
    assert any("veracity_floor mismatch" in e for e in errs)


def test_stranger_rejects_stringy_numeric_attestation_without_crashing(isolated):
    kt, km, ka, ks, log = isolated
    km.mint("cache_hit", 1000, "a", evidence="a", avoided_model="gh/claude-opus-4.8")
    att = json.loads(ka.build_attestation(log).to_public_json())
    v = _load_verifier()
    att["links"][0]["kry_minted"] = "NaN"
    att["total_kry"] = "NaN"
    att["attestation_hash"] = v._attestation_hash(att)

    ok, errs = v.verify_attestation(att)

    assert not ok
    assert any("seq 1: kry_minted must be a finite JSON number" in e for e in errs)
    assert any("total_kry must be a finite JSON number" in e for e in errs)


@pytest.mark.parametrize(
    ("mutate", "needle"),
    [
        (lambda att: att.update({"receipts": 999}), "receipts mismatch"),
        (lambda att: att.update({"event_type_counts": {"cache_hit": 999}}),
         "event_type_counts mismatch"),
        (lambda att: att.update({"usd_equivalent": 999.0}), "usd_equivalent mismatch"),
        (lambda att: att.update({"chain_valid": False}), "chain_valid is not true"),
    ],
)
def test_stranger_catches_tampered_public_metadata(isolated, mutate, needle):
    """Public aggregate fields are part of the proof surface, not decoration."""
    kt, km, ka, ks, log = isolated
    km.mint("cache_hit", 1000, "a", evidence="a", avoided_model="gh/claude-opus-4.8")
    att = json.loads(ka.build_attestation(log).to_public_json())
    v = _load_verifier()
    mutate(att)
    att["attestation_hash"] = v._attestation_hash(att)

    ok, errs = v.verify_attestation(att)
    assert not ok
    assert any(needle in e for e in errs)


def test_stranger_catches_stale_attestation_hash(isolated):
    kt, km, ka, ks, log = isolated
    km.mint("cache_hit", 1000, "a", evidence="a", avoided_model="gh/claude-opus-4.8")
    att = json.loads(ka.build_attestation(log).to_public_json())
    att["receipts"] = 999  # leave attestation_hash stale

    v = _load_verifier()
    ok, errs = v.verify_attestation(att)
    assert not ok
    assert any("attestation_hash mismatch" in e for e in errs)


def test_stranger_verifies_full_settlement(isolated):
    kt, km, ka, ks, log = isolated
    from kry.kry_settlement import make_offer, verify_and_accept, settle, ReceiverLedger

    for i in range(10):
        km.mint("cache_hit", 1000, f"q{i}", evidence=f"u{i}",
                avoided_model="gh/claude-opus-4.8")        # 10_000 KRY
    att_json = ka.build_attestation(log).to_public_json()

    # A settles 4_000 to B through the package (records to the registry).
    bal = kt.get_ledger().balance
    B = ReceiverLedger(party="B")
    offer = make_offer("A", "B", 4000.0, 40000, now=1000.0)
    grant, _ = verify_and_accept(offer, att_json, now=1001.0)

    def deb(k):
        led = kt.get_ledger()
        amt = min(k, led.balance)
        led.balance -= amt
        led.total_spent += amt
        return amt

    settle(offer, grant, debit_a_fn=deb, receiver=B, a_balance_before=bal)

    # Stranger reads the public artifacts and checks a NEW offer for double-spend.
    att = json.loads(att_json)
    entries = [json.loads(ln) for ln in ks._REGISTRY_PATH.read_text(encoding="utf-8").splitlines() if ln.strip()]
    v = _load_verifier()

    assert v.verify_registry(entries)[0]
    # 5_000 more is fine (10_000 attested − 4_000 settled = 6_000 available)
    ok, _ = v.verify_settlement(att, entries, "A", 5000.0)
    assert ok
    # 7_000 more would exceed the remaining 6_000 → double-spend, must reject
    ok2, errs2 = v.verify_settlement(att, entries, "A", 7000.0)
    assert not ok2
    assert any("double-spend" in e for e in errs2)


def test_verifier_constants_match_source():
    """F2 drift-guard: the verifier's embedded public reference (EARN_RATES, price
    table, multipliers) must stay identical to the package source. EARN_RATES
    mirrors the MINTER's rates (kry_mint) — that is what stamps each receipt."""
    import kry.kry_token as kt
    import kry.kry_mint as km
    v = _load_verifier()
    assert v._EARN_RATES == km._EARN_RATES
    assert v._MODEL_USD_PER_M == kt._MODEL_OUTPUT_USD_PER_M
    assert v._FRONTIER_USD_PER_M == kt.FRONTIER_USD_PER_M_OUTPUT
    assert v.legal_multipliers() == set(kt.published_multipliers().values())


def test_standalone_verifier_rejects_nonstandard_json_constants(tmp_path, capsys):
    v = _load_verifier()
    att_path = tmp_path / "attestation.json"
    att_path.write_text('{"receipts":0,"links":[],"chain_valid":true,"total_kry":NaN}\n', encoding="utf-8")
    registry_path = tmp_path / "registry.jsonl"
    registry_path.write_text('{"party":"A","amount":NaN}\n', encoding="utf-8")

    assert v.main([str(att_path)]) == 1
    out = capsys.readouterr().out

    assert "VERDICT: INVALID" in out
    assert "attestation unreadable: non-standard JSON constant rejected: NaN" in out
    with pytest.raises(ValueError, match="non-standard JSON constant rejected: NaN"):
        v._read_registry(str(registry_path))
    with pytest.raises(ValueError, match="Out of range float values"):
        v._json_dumps({"bad": float("inf")})


@pytest.mark.parametrize(
    ("entries", "needle"),
    [
        ([None], "registry entry must be a JSON object"),
        ([{"amount": 1.0, "entry_hash": "0" * 64}], "entry 1: party must be a non-empty string"),
        ([{"party": "A", "entry_hash": "0" * 64}], "entry 1: amount must be a finite JSON number"),
        ([{"party": "A", "amount": True, "entry_hash": "0" * 64}], "entry 1: amount must be a finite JSON number"),
        ([{"party": "A", "amount": float("nan"), "entry_hash": "0" * 64}], "entry 1: amount must be finite"),
        ([{"party": "A", "amount": -1.0, "entry_hash": "0" * 64}], "entry 1: amount must be positive"),
        ([{"party": "A", "amount": 1.0}], "entry 1: entry_hash must be a non-empty string"),
        ([{"party": "A", "amount": 1.0, "entry_hash": "not-a-hash"}],
         "entry 1: entry_hash must be 64 lowercase hex characters"),
    ],
)
def test_stranger_rejects_malformed_registry_entries_without_crashing(entries, needle):
    v = _load_verifier()

    ok, errors = v.verify_registry(entries)

    assert not ok
    assert any(needle in err for err in errors)


@pytest.mark.parametrize(
    ("party", "offer_amount", "needle"),
    [
        ("", 1.0, "settlement party must be a non-empty string"),
        ("A", 0.0, "offer amount must be positive"),
        ("A", -1.0, "offer amount must be positive"),
        ("A", float("nan"), "offer amount must be finite"),
    ],
)
def test_stranger_rejects_invalid_settlement_offer_inputs_without_crashing(party, offer_amount, needle):
    v = _load_verifier()
    att = {
        "receipts": 0,
        "links": [],
        "chain_valid": True,
        "chain_head": "0" * 64,
        "total_kry": 0.0,
        "usd_equivalent": 0.0,
        "event_type_counts": {},
        "veracity": {
            "by_tier": {},
            "externally_anchored_kry": 0.0,
            "self_reported_kry": 0.0,
            "veracity_floor": 0.0,
        },
        "attestation_hash": "",
    }
    att["attestation_hash"] = v._attestation_hash(att)

    ok, errors = v.verify_settlement(att, [], party, offer_amount)

    assert not ok
    assert any(needle in err for err in errors)


def test_imported_verifier_reports_nonfinite_attestation_metadata():
    v = _load_verifier()
    att = {
        "receipts": 0,
        "links": [],
        "chain_valid": True,
        "total_kry": float("nan"),
        "attestation_hash": "0" * 64,
    }

    ok, errors = v.verify_attestation(att)

    assert not ok
    assert any("attestation JSON is not standards-compliant" in err for err in errors)


def test_magnitude_recomputes_and_passes_for_honest_mint(isolated):
    """F2: an honest receipt exposes its inputs and the magnitude checks out."""
    kt, km, ka, ks, log = isolated
    km.mint("cache_hit", 1000, "a", evidence="a", avoided_model="gh/claude-opus-4.8")
    att = json.loads(ka.build_attestation(log).to_public_json())
    link = att["links"][0]
    assert link["tokens_saved"] == 1000 and link["earn_rate"] == 1.0   # inputs exposed
    v = _load_verifier()
    assert v.verify_attestation(att)[0]


def test_magnitude_catches_inflation_that_survives_conservation(isolated):
    """F2 adds detection beyond conservation: inflate a payout AND fix total_kry
    so conservation still holds — only the magnitude check (implied multiplier
    1.5 is not a published price) catches it."""
    kt, km, ka, ks, log = isolated
    km.mint("cache_hit", 1000, "a", evidence="a", avoided_model="gh/claude-opus-4.8")
    att = json.loads(ka.build_attestation(log).to_public_json())
    att["links"][0]["kry_minted"] = 1500.0           # implies multiplier 1.5
    att["total_kry"] = 1500.0                          # keep conservation intact
    att["veracity"]["self_reported_kry"] = 1500.0
    v = _load_verifier()
    ok, errs = v.verify_attestation(att)
    assert not ok
    assert any("non-public price" in e for e in errs)


def test_magnitude_catches_nonstandard_earn_rate(isolated):
    kt, km, ka, ks, log = isolated
    km.mint("cache_hit", 1000, "a", evidence="a", avoided_model="gh/claude-opus-4.8")
    att = json.loads(ka.build_attestation(log).to_public_json())
    att["links"][0]["earn_rate"] = 0.9               # cache_hit is published at 1.0
    v = _load_verifier()
    ok, errs = v.verify_attestation(att)
    assert not ok
    assert any("non-standard rate" in e for e in errs)


def test_stranger_catches_tampered_registry(isolated):
    kt, km, ka, ks, log = isolated
    from kry.kry_settlement import _record_settled
    _record_settled("A", 1000.0, "g1")
    _record_settled("A", 2000.0, "g2")
    entries = [json.loads(ln) for ln in ks._REGISTRY_PATH.read_text(encoding="utf-8").splitlines() if ln.strip()]
    entries[0]["amount"] = 1.0   # shrink a recorded settlement to free up balance

    v = _load_verifier()
    ok, errs = v.verify_registry(entries)
    assert not ok
    assert any("tampered" in e for e in errs)


def test_stranger_anchor_catches_remint():
    """P3 (stranger side): a re-mint that passes verify_attestation is caught against a
    PUBLISHED chain-head anchor — the link at seq==count must still match the anchored tip."""
    import hashlib
    v = _load_verifier()
    h1 = hashlib.sha256(b"c1").hexdigest()
    h2 = hashlib.sha256(b"c2").hexdigest()
    att = {"links": [{"seq": 1, "chain_hash": h1}, {"seq": 2, "chain_hash": h2}]}
    anchor = {"schema": "kry_chain_anchor/v1", "count": 2, "tip": h2}
    assert v.verify_attestation_against_anchor(att, anchor)[0] is True
    # operator re-minted -> the link at seq 2 now has a different chain_hash
    forged = {"links": [{"seq": 1, "chain_hash": h1},
                        {"seq": 2, "chain_hash": hashlib.sha256(b"forged").hexdigest()}]}
    ok2, errs2 = v.verify_attestation_against_anchor(forged, anchor)
    assert ok2 is False and any("re-mint detected" in e for e in errs2), errs2
    # truncation: attestation shorter than the anchored count is also caught
    assert v.verify_attestation_against_anchor({"links": [{"seq": 1, "chain_hash": h1}]}, anchor)[0] is False


def test_magnitude_rejects_zero_rate_and_skim():
    """Audit A: a link that DECLARES inputs cannot mint positive KRY from zero tokens/rate,
    and a ~1% multiplier skim is caught (tolerance tightened 0.01 -> 1e-3)."""
    v = _load_verifier()
    assert v._magnitude_errors({"seq": 1, "event_type": "cache_hit", "kry_minted": 1e6,
                                "tokens_saved": 0.0, "earn_rate": 0.0})            # zero-rate bypass
    assert v._magnitude_errors({"seq": 1, "event_type": "cache_hit", "kry_minted": 1000 * 1.0099,
                                "tokens_saved": 1000.0, "earn_rate": 1.0})         # 1% skim
    assert not v._magnitude_errors({"seq": 1, "event_type": "cache_hit", "kry_minted": 1000.0,
                                    "tokens_saved": 1000.0, "earn_rate": 1.0})     # legit mult 1.0
    assert not v._magnitude_errors({"seq": 1, "event_type": "cache_hit", "kry_minted": 1000.0})  # legacy (no inputs)


def test_magnitude_rejects_unknown_event_type_arbitrary_rate():
    """Audit F3: an unknown event_type must use mint's 0.5 fallback rate, not an arbitrary one."""
    v = _load_verifier()
    assert v._magnitude_errors({"seq": 1, "event_type": "weird", "kry_minted": 900.0,
                                "tokens_saved": 1000.0, "earn_rate": 0.9})        # arbitrary -> rejected
    assert not v._magnitude_errors({"seq": 1, "event_type": "weird", "kry_minted": 500.0,
                                    "tokens_saved": 1000.0, "earn_rate": 0.5})    # 0.5 fallback -> ok
