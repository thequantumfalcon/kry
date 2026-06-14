"""T2 (tee_attested): mint from an attested-hardware (AWS Nitro) measurement.

Two layers, mirroring how scripts/kry_tee_verify.py is built:

  GLUE (pure stdlib) — run() consumes an ALREADY-VERIFIED attestation (exactly as
  test_tlsn_verify mocks the Rust verifier's `verified:true`) and mints:
    - a verified measurement mints a tee_attested receipt and LIFTS the veracity_floor
    - the receipt is tamper-evident (the tier is bound into the hash)
    - fail-closed: not-verified / no basis / no avoided-model / no measurement-id REFUSED
    - replaying the SAME attestation does not double-mint (evidence binding + decay)
    - a prior self_reported/holdout measurement receipt is UPGRADED, not re-credited

  CRYPTO (needs `cryptography`) — verify_attestation does the REAL work: it round-trips a
  freshly-signed Nitro COSE_Sign1 (ES384 + an X.509 chain to a pinned test root) and
  proves it fails closed on a bad signature, the wrong root, expiry, and a missing lib.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "kry_tee_verify.py"
_REF_TS = 1780531200.0   # 2026-06-04T08:00:00Z — a fixed reference time for fixtures


def _load():
    spec = importlib.util.spec_from_file_location("kry_tee_verify_standalone", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ── GLUE: a pre-verified attestation dict (no crypto) ────────────────────────────

def _verified_att(*, measurement_id="meas-1", tokens_saved=300.0,
                  avoided="gh/claude-opus-4.8", served=None, with_measurement=True,
                  verified=True, doc_sha="d" * 64, module_id="i-0abc-enc0"):
    m = None
    if with_measurement:
        m = {"measurement_id": measurement_id, "tokens_saved": tokens_saved}
        if avoided is not None:
            m["avoided_model"] = avoided
        if served is not None:
            m["served_model"] = served
    return {
        "verified": verified,
        "errors": [] if verified else ["attestation not verified (test)"],
        "measurement": m,
        "module_id": module_id,
        "timestamp": int(_REF_TS * 1000),
        "nonce": "11" * 16,
        "pcr0": "ab" * 48,
        "doc_sha256": doc_sha,
    }


_KW = dict(event_type="short_circuit", avoided_model=None, served_model=None,
           tokens_saved=None, measurement_id=None, dry_run=False)


def test_verified_mints_tee_and_lifts_floor():
    import kry.kry_mint as km
    mod = _load()
    # seed a self_reported cache hit so the floor starts at 0 with room to lift
    km.mint("cache_hit", 1000, "c", evidence="seed", avoided_model="gh/claude-opus-4.8")
    assert km.veracity_breakdown()["veracity_floor"] == 0.0

    res = mod.run(_verified_att(), **_KW)
    assert res["verdict"] == "OK"
    assert res["minted"]["evidence_tier"] == "tee_attested"
    assert res["minted"]["mode"] == "fresh_mint"
    assert res["minted"]["kry_minted"] > 0
    assert res["veracity_floor"]["after"] > res["veracity_floor"]["before"]

    assert km.verify_chain()[0]
    vb = km.veracity_breakdown()
    assert vb["by_tier"].get("tee_attested", 0) > 0
    assert vb["tee_attested_fraction"] > 0
    assert vb["externally_anchored_kry"] > 0


def test_tee_receipt_is_tamper_evident(tmp_path):
    import kry.kry_mint as km
    mod = _load()
    res = mod.run(_verified_att(), **_KW)
    assert res["minted"]["evidence_tier"] == "tee_attested"
    assert km.verify_chain()[0]

    lines = km._MINT_LOG_PATH.read_text(encoding="utf-8").splitlines()
    rec = json.loads(lines[-1])
    assert rec["hash_version"] == 4   # current mint format (v4: +public-block chain bind)
    rec["evidence_tier"] = "self_reported"          # forge a tier downgrade in place
    lines[-1] = json.dumps(rec)
    km._MINT_LOG_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    ok, errs = km.verify_chain()
    assert not ok
    assert any("receipt_hash mismatch" in e for e in errs)


def test_not_verified_is_refused():
    import kry.kry_mint as km
    mod = _load()
    res = mod.run(_verified_att(verified=False), **_KW)
    assert res["verdict"] == "REJECTED"
    assert km.chain_summary()["receipts"] == 0


def test_no_basis_refuses():
    import kry.kry_mint as km
    mod = _load()
    res = mod.run(_verified_att(tokens_saved=0.0), **_KW)
    assert res["verdict"] == "NO_BASIS"
    assert km.chain_summary()["receipts"] == 0


def test_non_finite_measurement_basis_refuses():
    import kry.kry_mint as km
    mod = _load()

    res = mod.run(_verified_att(tokens_saved=float("nan")), **_KW)
    assert res["verdict"] == "NO_BASIS"
    assert km.chain_summary()["receipts"] == 0

    res = mod.run(_verified_att(), **{**_KW, "tokens_saved": float("inf")})
    assert res["verdict"] == "NO_BASIS"
    assert km.chain_summary()["receipts"] == 0


def test_measurement_json_boundary_rejects_nonstandard_constants():
    mod = _load()
    raw = b'{"measurement_id":"meas-bad","tokens_saved":NaN,"avoided_model":"x"}'

    assert mod._parse_measurement(raw) is None


def test_no_displacement_context_refuses():
    import kry.kry_mint as km
    mod = _load()
    res = mod.run(_verified_att(avoided=None), **_KW)
    assert res["verdict"] == "NO_DISPLACEMENT_CONTEXT"
    assert res["avoided_model"]["value"] is None
    assert km.chain_summary()["receipts"] == 0


def test_no_measurement_id_refuses():
    import kry.kry_mint as km
    mod = _load()
    att = _verified_att()
    att["measurement"].pop("measurement_id")
    res = mod.run(att, **_KW)
    assert res["verdict"] == "NO_MEASUREMENT_ID"
    assert km.chain_summary()["receipts"] == 0


def test_replay_does_not_double_mint():
    import kry.kry_mint as km
    mod = _load()
    att = _verified_att()      # identical doc_sha256 → identical evidence binding
    first = mod.run(att, **_KW)
    assert first["minted"]["kry_minted"] > 0
    floor1 = km.veracity_breakdown()["veracity_floor"]
    second = mod.run(att, **_KW)
    assert second["verdict"] in ("NOT_MINTED", "OK")
    assert km.veracity_breakdown()["veracity_floor"] <= floor1 + 1e-9


def test_dry_run_mints_nothing():
    import kry.kry_mint as km
    mod = _load()
    res = mod.run(_verified_att(), **{**_KW, "dry_run": True})
    assert res["verdict"] == "OK"
    assert res["minted"] is None
    assert km.chain_summary()["receipts"] == 0


def test_cli_override_avoided_model_when_measurement_omits_it():
    import kry.kry_mint as km
    mod = _load()
    res = mod.run(_verified_att(avoided=None), **{**_KW, "avoided_model": "gh/claude-opus-4.8"})
    assert res["verdict"] == "OK"
    assert res["avoided_model"]["source"] == "cli"
    assert res["minted"]["evidence_tier"] == "tee_attested"
    assert km.verify_chain()[0]


def test_promotion_upgrades_self_reported_not_double_credits():
    """A prior self_reported measurement receipt is UPGRADED to tee_attested (net-zero),
    NOT credited again — and because self_reported is unanchored, the floor RISES."""
    import kry.kry_mint as km
    mod = _load()
    # the operator first self-reported this measurement's saving
    prior = km.mint("short_circuit", 200, "holdout run /measurement:meas-up", evidence="m1",
                    avoided_model="gh/claude-opus-4.8", evidence_tier=km.TIER_SELF_REPORTED)
    before = km.veracity_breakdown()
    assert before["by_tier"].get("self_reported", 0) > 0
    assert before["veracity_floor"] == 0.0

    res = mod.run(_verified_att(measurement_id="meas-up"), **_KW)
    assert res["minted"]["mode"] == "tier_upgrade"
    assert res["minted"]["supersedes"] == prior.receipt_id
    assert res["minted"]["evidence_tier"] == "tee_attested"
    assert res["minted"]["kry_re_tiered"] == pytest.approx(prior.kry_minted)

    after = km.veracity_breakdown()
    assert after["total_kry"] == pytest.approx(before["total_kry"])      # no new value
    assert after["by_tier"].get("self_reported", 0) == pytest.approx(0.0, abs=1e-9)
    assert after["by_tier"]["tee_attested"] == pytest.approx(prior.kry_minted)
    assert after["veracity_floor"] > before["veracity_floor"]            # self-report -> anchored
    assert after["tee_attested_fraction"] > 0
    assert km.verify_chain()[0]

    # idempotent: re-run does not stack a second promotion
    res2 = mod.run(_verified_att(measurement_id="meas-up"), **_KW)
    assert res2["verdict"] == "ALREADY_UPGRADED"
    assert "minted" not in res2
    assert km.veracity_breakdown()["total_kry"] == pytest.approx(before["total_kry"])


def test_promotion_of_holdout_keeps_floor_but_strengthens_subtier():
    """Promoting an already-anchored holdout_validated receipt keeps the binary floor
    (both anchored) but moves the value to the stronger tee sub-tier."""
    import kry.kry_mint as km
    mod = _load()
    prior = km.mint("short_circuit", 200, "holdout /measurement:meas-h", evidence="mh",
                    avoided_model="gh/claude-opus-4.8", evidence_tier=km.TIER_HOLDOUT_VALIDATED)
    before = km.veracity_breakdown()
    assert before["veracity_floor"] == 1.0          # holdout is anchored
    assert before["tee_attested_fraction"] == 0.0

    res = mod.run(_verified_att(measurement_id="meas-h"), **_KW)
    assert res["minted"]["mode"] == "tier_upgrade"
    after = km.veracity_breakdown()
    assert after["veracity_floor"] == pytest.approx(before["veracity_floor"])   # unchanged
    assert after["by_tier"].get("holdout_validated", 0) == pytest.approx(0.0, abs=1e-9)
    assert after["tee_attested_fraction"] == pytest.approx(1.0)
    assert after["total_kry"] == pytest.approx(before["total_kry"])
    assert km.verify_chain()[0]
    _ = prior


# ── CRYPTO: real Nitro COSE round-trip (needs `cryptography`) ────────────────────

crypto = pytest.importorskip("cryptography")


def _cbor_enc(obj):
    """Minimal CBOR encoder for building test attestation docs (int/bytes/str/list/map/None)."""
    mod = _load()
    if obj is None:
        return b"\xf6"
    if isinstance(obj, bool):
        return b"\xf5" if obj else b"\xf4"
    if isinstance(obj, int):
        if obj >= 0:
            return mod._cbor_head(0, obj)
        return mod._cbor_head(1, -1 - obj)
    if isinstance(obj, (bytes, bytearray)):
        return mod._cbor_head(2, len(obj)) + bytes(obj)
    if isinstance(obj, str):
        b = obj.encode()
        return mod._cbor_head(3, len(b)) + b
    if isinstance(obj, list):
        out = mod._cbor_head(4, len(obj))
        for it in obj:
            out += _cbor_enc(it)
        return out
    if isinstance(obj, dict):
        out = mod._cbor_head(5, len(obj))
        for k, v in obj.items():
            out += _cbor_enc(k) + _cbor_enc(v)
        return out
    raise TypeError(type(obj))


def _build_nitro_doc(measurement, *, ref_ts=_REF_TS, pcr0=b"\xab" * 48,
                     nonce=b"\x11" * 16, with_intermediate=True, tamper_sig=False):
    """Build a signed Nitro COSE_Sign1 + return (cose_bytes, root_der). Uses a fresh
    test CA (root [-> intermediate] -> leaf); the leaf signs the COSE Sig_structure."""
    import datetime
    from cryptography import x509
    from cryptography.x509.oid import NameOID
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
    mod = _load()
    utc = datetime.timezone.utc
    nb = datetime.datetime.fromtimestamp(ref_ts - 86400, utc)
    na = datetime.datetime.fromtimestamp(ref_ts + 86400, utc)

    def _name(cn):
        return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])

    def _cert(subj_name, subj_key, issuer_name, issuer_key, ca):
        b = (x509.CertificateBuilder().subject_name(subj_name).issuer_name(issuer_name)
             .public_key(subj_key.public_key()).serial_number(x509.random_serial_number())
             .not_valid_before(nb).not_valid_after(na)
             .add_extension(x509.BasicConstraints(ca=ca, path_length=None), critical=True))
        return b.sign(issuer_key, hashes.SHA384())

    root_key = ec.generate_private_key(ec.SECP384R1())
    root = _cert(_name("test-root"), root_key, _name("test-root"), root_key, ca=True)
    root_der = root.public_bytes(serialization.Encoding.DER)

    cabundle = [root_der]
    leaf_issuer_name, leaf_issuer_key = _name("test-root"), root_key
    if with_intermediate:
        int_key = ec.generate_private_key(ec.SECP384R1())
        inter = _cert(_name("test-int"), int_key, _name("test-root"), root_key, ca=True)
        cabundle.append(inter.public_bytes(serialization.Encoding.DER))
        leaf_issuer_name, leaf_issuer_key = _name("test-int"), int_key

    leaf_key = ec.generate_private_key(ec.SECP384R1())
    leaf = _cert(_name("test-enclave"), leaf_key, leaf_issuer_name, leaf_issuer_key, ca=False)
    leaf_der = leaf.public_bytes(serialization.Encoding.DER)

    payload = {
        "module_id": "i-0test-enc0",
        "timestamp": int(ref_ts * 1000),
        "digest": "SHA384",
        "pcrs": {0: pcr0},
        "certificate": leaf_der,
        "cabundle": cabundle,
        "user_data": json.dumps(measurement).encode(),
        "nonce": nonce,
        "public_key": None,
    }
    protected = _cbor_enc({1: mod.COSE_ALG_ES384})
    payload_b = _cbor_enc(payload)
    sig_structure = mod._cbor_encode(["Signature1", protected, b"", payload_b])
    der_sig = leaf_key.sign(sig_structure, ec.ECDSA(hashes.SHA384()))
    r, s = decode_dss_signature(der_sig)
    sig = r.to_bytes(48, "big") + s.to_bytes(48, "big")
    if tamper_sig:
        sig = sig[:-1] + bytes([sig[-1] ^ 0xFF])
    cose = _cbor_enc([protected, {}, payload_b, sig])
    return cose, root_der


_MEAS = {"measurement_id": "meas-crypto", "tokens_saved": 420.0,
         "avoided_model": "gh/claude-opus-4.8"}


def test_real_attestation_verifies_and_round_trips():
    mod = _load()
    cose, root_der = _build_nitro_doc(_MEAS)
    att = mod.verify_attestation(cose, root_cert_der=root_der, now=_REF_TS, max_age_s=3600)
    assert att["verified"] is True, att["errors"]
    assert att["measurement"]["measurement_id"] == "meas-crypto"
    assert att["pcr0"] == ("ab" * 48)
    # and it mints through run()
    import kry.kry_mint as km
    res = mod.run(att, **_KW)
    assert res["minted"]["evidence_tier"] == "tee_attested"
    assert km.verify_chain()[0]


def test_bad_signature_fails_closed():
    mod = _load()
    cose, root_der = _build_nitro_doc(_MEAS, tamper_sig=True)
    att = mod.verify_attestation(cose, root_cert_der=root_der, now=_REF_TS, max_age_s=3600)
    assert att["verified"] is False
    assert any("signature does not verify" in e for e in att["errors"])


def test_wrong_root_fails_closed():
    mod = _load()
    cose, _root_der = _build_nitro_doc(_MEAS)
    other_cose, other_root = _build_nitro_doc(_MEAS)   # a different, untrusted root
    att = mod.verify_attestation(cose, root_cert_der=other_root, now=_REF_TS, max_age_s=3600)
    assert att["verified"] is False
    assert any("does not terminate at the pinned" in e for e in att["errors"])
    _ = other_cose


def test_expired_certificate_fails_closed():
    mod = _load()
    cose, root_der = _build_nitro_doc(_MEAS)
    far_future = _REF_TS + 86400 * 30           # beyond the certs' not_after
    att = mod.verify_attestation(cose, root_cert_der=root_der, now=far_future, max_age_s=1e12)
    assert att["verified"] is False
    assert any("validity window" in e for e in att["errors"])


def test_stale_attestation_fails_closed():
    mod = _load()
    cose, root_der = _build_nitro_doc(_MEAS)
    # now is within cert validity but long after the attestation timestamp
    att = mod.verify_attestation(cose, root_cert_der=root_der, now=_REF_TS + 80000, max_age_s=3600)
    assert att["verified"] is False
    assert any("stale" in e for e in att["errors"])


def test_pcr0_pin_mismatch_fails_closed():
    mod = _load()
    cose, root_der = _build_nitro_doc(_MEAS, pcr0=b"\xcd" * 48)
    att = mod.verify_attestation(cose, root_cert_der=root_der, now=_REF_TS, max_age_s=3600,
                                 expected_pcr0="ab" * 48)
    assert att["verified"] is False
    assert any("PCR0" in e for e in att["errors"])


# ── CRYPTO: real AWS-hardware attestation (Nitro PoC regression fixture) ──────────
# A GENUINE AWS-signed Nitro attestation captured from a c6i.xlarge enclave (poc/nitro,
# 2026-06-06). The synthetic _build_nitro_doc above emits DEFINITE-length CBOR, but real
# AWS NSM serializes the attestation document with INDEFINITE-length maps/strings/arrays
# (0xbf/0x5f/0x9f/0x7f closed by 0xff) — the path the decoder originally missed, so the
# live PoC failed where the unit tests passed. This fixture locks the indefinite-length
# support and the AWS root pin against regression. Timestamp is read from the doc so
# freshness is deterministic (no wall-clock dependence).

_FIXTURES = Path(__file__).parent / "fixtures"


def _real_attestation():
    mod = _load()
    doc = (_FIXTURES / "nitro_real_attestation.bin").read_bytes()
    root_der, root_sha = mod._load_root_der(str(_FIXTURES / "nitro_real_root.pem"))
    cose, _ = mod._cbor_decode(doc)
    payload, _ = mod._cbor_decode(bytes(cose[2]))
    near = payload["timestamp"] / 1000.0 + 5
    return mod, doc, root_der, root_sha, near


def test_real_aws_nitro_attestation_verifies_indefinite_cbor():
    mod, doc, root_der, _root_sha, near = _real_attestation()
    assert b"\xbf" in doc           # the genuine doc really uses an indefinite-length map
    att = mod.verify_attestation(doc, root_cert_der=root_der, now=near, max_age_s=3600)
    assert att["verified"] is True, att["errors"]
    assert att["measurement"]["measurement_id"] == "poc-nitro-001"
    assert att["measurement"]["avoided_model"] == "gh/claude-opus-4.8"


def test_real_root_matches_pinned_fingerprint():
    # the captured AWS root is byte-identical to the fingerprint the verifier pins
    mod, _doc, _root_der, root_sha, _near = _real_attestation()
    assert root_sha.lower() == mod.AWS_NITRO_ROOT_G1_DER_SHA256.lower()


def test_real_attestation_tamper_fails_closed():
    mod, doc, root_der, _root_sha, near = _real_attestation()
    t = bytearray(doc)
    t[-1] ^= 0x01                   # flip a signature byte on the REAL document
    att = mod.verify_attestation(bytes(t), root_cert_der=root_der, now=near, max_age_s=3600)
    assert att["verified"] is False
    assert any("signature does not verify" in e for e in att["errors"])


def test_root_fingerprint_is_der_sha256_format_independent(tmp_path):
    """--root pin is sha256(DER) — a PEM and the DER it decodes to share one fingerprint."""
    import hashlib
    from cryptography import x509
    from cryptography.x509.oid import NameOID
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    import datetime
    mod = _load()
    k = ec.generate_private_key(ec.SECP384R1())
    n = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "r")])
    utc = datetime.timezone.utc
    cert = (x509.CertificateBuilder().subject_name(n).issuer_name(n).public_key(k.public_key())
            .serial_number(1).not_valid_before(datetime.datetime.fromtimestamp(_REF_TS - 1, utc))
            .not_valid_after(datetime.datetime.fromtimestamp(_REF_TS + 1, utc))
            .sign(k, hashes.SHA384()))
    der = cert.public_bytes(serialization.Encoding.DER)
    pem = cert.public_bytes(serialization.Encoding.PEM)
    der_f, pem_f = tmp_path / "r.der", tmp_path / "r.pem"
    der_f.write_bytes(der)
    pem_f.write_bytes(pem)
    want = hashlib.sha256(der).hexdigest()
    assert mod._load_root_der(str(der_f)) == (der, want)
    assert mod._load_root_der(str(pem_f))[1] == want      # PEM yields the same fingerprint


def test_missing_cryptography_fails_closed(monkeypatch):
    """Without the audited lib, verification REFUSES — never a silent skip."""
    import sys
    mod = _load()
    cose, root_der = _build_nitro_doc(_MEAS)
    monkeypatch.setitem(sys.modules, "cryptography", None)
    att = mod.verify_attestation(cose, root_cert_der=root_der, now=_REF_TS, max_age_s=3600)
    assert att["verified"] is False
    assert any("cryptography" in e for e in att["errors"])
