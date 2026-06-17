"""T2 (tee_attested) from a self-hosted AMD SEV-SNP attestation report.

Mirrors test_tee_verify.py (Nitro). Two layers, mirroring scripts/kry_snp_verify.py:

  GLUE (pure stdlib) — run() consumes an ALREADY-VERIFIED report dict and mints:
    - a verified report mints a tee_attested receipt and LIFTS the veracity_floor
    - the receipt is tamper-evident (the tier is bound into the hash)
    - fail-closed: not-verified / no basis / no avoided-model / no measurement-id REFUSED
    - replaying the SAME report does not double-mint (evidence binding + decay)
    - a prior self_reported/holdout measurement receipt is UPGRADED, not re-credited
    (run() is shared shape with the Nitro tier — both reuse kry_mint.promote_to_tee.)

  CRYPTO (needs `cryptography`) — verify_report does the REAL work: it round-trips a
  freshly-built 1184-byte report signed by a synthetic VCEK whose cert chains VCEK→ASK→ARK
  to a pinned test ARK, and proves it fails closed on a bad signature, the wrong root, a
  report_data/measurement mismatch, a bad sig_algo, and a missing lib.

  ⚠ The DEFAULT chain here is ECDSA (fast); test_rsa_pss_cert_chain_verifies_like_real_amd
  builds the real-AMD shape (RSA-4096/RSA-PSS ARK/ASK, ECDSA-P384 VCEK) and proves the
  verify_directly_issued_by RSA-PSS path. That closes the code-path gap; the AMD root KEYS
  themselves stay UNPROVEN until a genuine EPYC-node fixture exists (the Nitro tier has a
  real-hardware regression fixture; SNP does not yet).
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import struct
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "kry_snp_verify.py"


def _load():
    spec = importlib.util.spec_from_file_location("kry_snp_verify_standalone", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ── GLUE: a pre-verified report dict (no crypto) ─────────────────────────────────

def _verified_report(*, measurement_id="meas-1", tokens_saved=300.0,
                     avoided="gh/claude-opus-4.8", served=None, with_measurement=True,
                     verified=True, report_sha="d" * 64, chip_id="ab" * 64):
    m = None
    if with_measurement:
        m = {"measurement_id": measurement_id, "tokens_saved": tokens_saved}
        if avoided is not None:
            m["avoided_model"] = avoided
        if served is not None:
            m["served_model"] = served
    return {
        "verified": verified,
        "errors": [] if verified else ["report not verified (test)"],
        "parsed_measurement": m,
        "chip_id": chip_id,
        "measurement": "cd" * 48,
        "report_data": "ef" * 32,
        "report_sha256": report_sha,
    }


_KW = dict(event_type="short_circuit", avoided_model=None, served_model=None,
           tokens_saved=None, measurement_id=None, dry_run=False)


def test_verified_mints_tee_and_lifts_floor():
    import kry.kry_mint as km
    mod = _load()
    km.mint("cache_hit", 1000, "c", evidence="seed", avoided_model="gh/claude-opus-4.8")
    assert km.veracity_breakdown()["veracity_floor"] == 0.0

    res = mod.run(_verified_report(), **_KW)
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


def test_snp_receipt_is_tamper_evident():
    import kry.kry_mint as km
    mod = _load()
    res = mod.run(_verified_report(), **_KW)
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
    res = mod.run(_verified_report(verified=False), **_KW)
    assert res["verdict"] == "REJECTED"
    assert km.chain_summary()["receipts"] == 0


def test_no_basis_refuses():
    import kry.kry_mint as km
    mod = _load()
    res = mod.run(_verified_report(tokens_saved=0.0), **_KW)
    assert res["verdict"] == "NO_BASIS"
    assert km.chain_summary()["receipts"] == 0


def test_non_finite_measurement_basis_refuses():
    import kry.kry_mint as km
    mod = _load()

    res = mod.run(_verified_report(tokens_saved=float("nan")), **_KW)
    assert res["verdict"] == "NO_BASIS"
    assert km.chain_summary()["receipts"] == 0

    res = mod.run(_verified_report(), **{**_KW, "tokens_saved": float("inf")})
    assert res["verdict"] == "NO_BASIS"
    assert km.chain_summary()["receipts"] == 0


def test_measurement_json_boundary_rejects_nonstandard_constants():
    mod = _load()
    raw = b'{"measurement_id":"meas-bad","tokens_saved":NaN,"avoided_model":"x"}'

    assert mod._parse_measurement(raw) is None


def test_no_displacement_context_refuses():
    import kry.kry_mint as km
    mod = _load()
    res = mod.run(_verified_report(avoided=None), **_KW)
    assert res["verdict"] == "NO_DISPLACEMENT_CONTEXT"
    assert res["avoided_model"]["value"] is None
    assert km.chain_summary()["receipts"] == 0


def test_no_measurement_id_refuses():
    import kry.kry_mint as km
    mod = _load()
    rep = _verified_report()
    rep["parsed_measurement"].pop("measurement_id")
    res = mod.run(rep, **_KW)
    assert res["verdict"] == "NO_MEASUREMENT_ID"
    assert km.chain_summary()["receipts"] == 0


def test_replay_does_not_double_mint():
    import kry.kry_mint as km
    mod = _load()
    rep = _verified_report()      # identical report_sha256 → identical evidence binding
    first = mod.run(rep, **_KW)
    assert first["minted"]["kry_minted"] > 0
    floor1 = km.veracity_breakdown()["veracity_floor"]
    second = mod.run(rep, **_KW)
    assert second["verdict"] in ("NOT_MINTED", "OK")
    assert km.veracity_breakdown()["veracity_floor"] <= floor1 + 1e-9


def test_dry_run_mints_nothing():
    import kry.kry_mint as km
    mod = _load()
    res = mod.run(_verified_report(), **{**_KW, "dry_run": True})
    assert res["verdict"] == "OK"
    assert res["minted"] is None
    assert km.chain_summary()["receipts"] == 0


def test_cli_override_avoided_model_when_measurement_omits_it():
    import kry.kry_mint as km
    mod = _load()
    res = mod.run(_verified_report(avoided=None), **{**_KW, "avoided_model": "gh/claude-opus-4.8"})
    assert res["verdict"] == "OK"
    assert res["avoided_model"]["source"] == "cli"
    assert res["minted"]["evidence_tier"] == "tee_attested"
    assert km.verify_chain()[0]


def test_promotion_upgrades_self_reported_not_double_credits():
    import kry.kry_mint as km
    mod = _load()
    prior = km.mint("short_circuit", 200, "holdout /measurement:meas-up", evidence="m1",
                    avoided_model="gh/claude-opus-4.8", evidence_tier=km.TIER_SELF_REPORTED)
    before = km.veracity_breakdown()
    assert before["by_tier"].get("self_reported", 0) > 0
    assert before["veracity_floor"] == 0.0

    res = mod.run(_verified_report(measurement_id="meas-up"), **_KW)
    assert res["minted"]["mode"] == "tier_upgrade"
    assert res["minted"]["supersedes"] == prior.receipt_id
    assert res["minted"]["evidence_tier"] == "tee_attested"
    assert res["minted"]["kry_re_tiered"] == pytest.approx(prior.kry_minted)

    after = km.veracity_breakdown()
    assert after["total_kry"] == pytest.approx(before["total_kry"])      # no new value
    assert after["by_tier"].get("self_reported", 0) == pytest.approx(0.0, abs=1e-9)
    assert after["by_tier"]["tee_attested"] == pytest.approx(prior.kry_minted)
    assert after["veracity_floor"] > before["veracity_floor"]            # self-report -> anchored
    assert km.verify_chain()[0]

    res2 = mod.run(_verified_report(measurement_id="meas-up"), **_KW)
    assert res2["verdict"] == "ALREADY_UPGRADED"
    assert "minted" not in res2
    assert km.veracity_breakdown()["total_kry"] == pytest.approx(before["total_kry"])


# ── CRYPTO: real SEV-SNP report round-trip (needs `cryptography`) ─────────────────

crypto = pytest.importorskip("cryptography")


def _build_snp_report(measurement_json: dict, *, chip_id=b"\x5a" * 64,
                      measurement_field=b"\x33" * 48, version=2, sig_algo=1,
                      tamper_sig=False, bad_report_data=False, expired=False,
                      rsa_roots=False):
    """Build a signed 1184-byte SEV-SNP report + return
    (report_bytes, vcek_der, ask_der, ark_der, measurement_json_bytes).
    Synthetic chain: ARK (self-signed) -> ASK -> VCEK (the leaf that signs the report).

    rsa_roots=True makes ARK/ASK RSA-4096 with RSASSA-PSS-signed certs (the real AMD shape;
    VCEK stays ECDSA-P384 as on real hardware) — exercises the verify_directly_issued_by
    RSA-PSS path that the default (all-ECDSA, fast) chain does not.
    """
    import datetime
    from cryptography import x509
    from cryptography.x509.oid import NameOID
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa
    from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
    mod = _load()
    utc = datetime.timezone.utc
    now = datetime.datetime.now(utc)
    if expired:
        nb, na = now - datetime.timedelta(days=10), now - datetime.timedelta(days=1)
    else:
        nb, na = now - datetime.timedelta(days=1), now + datetime.timedelta(days=3650)

    def _name(cn):
        return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])

    def _cert(subj_name, subj_key, issuer_name, issuer_key, ca):
        builder = (x509.CertificateBuilder().subject_name(subj_name).issuer_name(issuer_name)
                   .public_key(subj_key.public_key()).serial_number(x509.random_serial_number())
                   .not_valid_before(nb).not_valid_after(na)
                   .add_extension(x509.BasicConstraints(ca=ca, path_length=None), critical=True))
        if isinstance(issuer_key, rsa.RSAPrivateKey):
            # real AMD ARK/ASK sign with RSASSA-PSS — the path verify_directly_issued_by must handle
            return builder.sign(issuer_key, hashes.SHA384(),
                                rsa_padding=padding.PSS(mgf=padding.MGF1(hashes.SHA384()),
                                                        salt_length=padding.PSS.MAX_LENGTH))
        return builder.sign(issuer_key, hashes.SHA384())

    if rsa_roots:
        ark_key = rsa.generate_private_key(public_exponent=65537, key_size=4096)
        ask_key = rsa.generate_private_key(public_exponent=65537, key_size=4096)
    else:
        ark_key = ec.generate_private_key(ec.SECP384R1())
        ask_key = ec.generate_private_key(ec.SECP384R1())
    ark = _cert(_name("ARK-test"), ark_key, _name("ARK-test"), ark_key, ca=True)
    ask = _cert(_name("ASK-test"), ask_key, _name("ARK-test"), ark_key, ca=True)
    vcek_key = ec.generate_private_key(ec.SECP384R1())   # VCEK is ECDSA-P384 on real hardware too
    vcek = _cert(_name("VCEK-test"), vcek_key, _name("ASK-test"), ask_key, ca=False)

    def der(c):
        return c.public_bytes(serialization.Encoding.DER)

    meas_bytes = json.dumps(measurement_json).encode()
    report_data = hashlib.sha512(meas_bytes).digest()
    if bad_report_data:
        report_data = b"\x00" * 64

    report = bytearray(mod._REPORT_LEN)
    struct.pack_into("<I", report, mod._OFF_VERSION, version)
    struct.pack_into("<I", report, mod._OFF_SIG_ALGO, sig_algo)
    report[mod._OFF_REPORT_DATA:mod._OFF_REPORT_DATA + 64] = report_data
    report[mod._OFF_MEASUREMENT:mod._OFF_MEASUREMENT + 48] = measurement_field
    report[mod._OFF_CHIP_ID:mod._OFF_CHIP_ID + 64] = chip_id

    # sign report[0:0x2A0] with the VCEK key (ECDSA-P384/SHA-384); place r||s little-endian
    der_sig = vcek_key.sign(bytes(report[:mod._SIGNED_LEN]), ec.ECDSA(hashes.SHA384()))
    r, s = decode_dss_signature(der_sig)
    sig_field = bytearray(512)
    sig_field[0:48] = r.to_bytes(48, "little")
    sig_field[mod._SIG_COMPONENT:mod._SIG_COMPONENT + 48] = s.to_bytes(48, "little")
    if tamper_sig:
        sig_field[0] ^= 0xFF
    report[mod._OFF_SIGNATURE:mod._OFF_SIGNATURE + 512] = sig_field

    return bytes(report), der(vcek), der(ask), der(ark), meas_bytes


_MEAS = {"measurement_id": "meas-snp", "tokens_saved": 420.0,
         "avoided_model": "gh/claude-opus-4.8"}


def test_real_report_verifies_and_round_trips():
    import kry.kry_mint as km
    mod = _load()
    report, vcek, ask, ark, meas = _build_snp_report(_MEAS)
    att = mod.verify_report(report, vcek_der=vcek, ask_der=ask, ark_der=ark, measurement_json=meas)
    assert att["verified"] is True, att["errors"]
    assert att["parsed_measurement"]["measurement_id"] == "meas-snp"
    assert att["measurement"] == ("33" * 48)
    res = mod.run(att, **_KW)
    assert res["minted"]["evidence_tier"] == "tee_attested"
    assert km.verify_chain()[0]


def test_rsa_pss_cert_chain_verifies_like_real_amd():
    """Real AMD ARK/ASK are RSA-4096 with RSASSA-PSS signatures (the fast default chain is
    all-ECDSA). Build a real-AMD-shaped chain — RSA-PSS ARK/ASK, ECDSA-P384 VCEK signing the
    report — and prove the verify_directly_issued_by RSA-PSS path verifies (and fails closed on
    a foreign RSA root). Closes the 'RSA-PSS handled but UNPROVEN' fixture gap without hardware."""
    mod = _load()
    report, vcek, ask, ark, meas = _build_snp_report(_MEAS, rsa_roots=True)
    att = mod.verify_report(report, vcek_der=vcek, ask_der=ask, ark_der=ark, measurement_json=meas)
    assert att["verified"] is True, att["errors"]
    assert att["parsed_measurement"]["measurement_id"] == "meas-snp"
    # a different, untrusted RSA root must not satisfy the pinned-ARK chain
    _r2, _v2, _a2, other_ark, _m2 = _build_snp_report(_MEAS, rsa_roots=True)
    bad = mod.verify_report(report, vcek_der=vcek, ask_der=ask, ark_der=other_ark, measurement_json=meas)
    assert bad["verified"] is False
    assert any("chain broken" in e or "ARK" in e for e in bad["errors"])


def test_bad_signature_fails_closed():
    mod = _load()
    report, vcek, ask, ark, meas = _build_snp_report(_MEAS, tamper_sig=True)
    att = mod.verify_report(report, vcek_der=vcek, ask_der=ask, ark_der=ark, measurement_json=meas)
    assert att["verified"] is False
    assert any("signature does not verify" in e for e in att["errors"])


def test_wrong_ark_fails_closed():
    mod = _load()
    report, vcek, ask, _ark, meas = _build_snp_report(_MEAS)
    _r2, _v2, _a2, other_ark, _m2 = _build_snp_report(_MEAS)   # a different, untrusted root
    att = mod.verify_report(report, vcek_der=vcek, ask_der=ask, ark_der=other_ark, measurement_json=meas)
    assert att["verified"] is False
    assert any("not directly issued by" in e for e in att["errors"])


def test_report_data_mismatch_fails_closed():
    mod = _load()
    report, vcek, ask, ark, meas = _build_snp_report(_MEAS, bad_report_data=True)
    att = mod.verify_report(report, vcek_der=vcek, ask_der=ask, ark_der=ark, measurement_json=meas)
    assert att["verified"] is False
    assert any("report_data does not equal" in e for e in att["errors"])


def test_measurement_substitution_fails_closed():
    """A different measurement JSON (not the one whose SHA-512 is in report_data) is rejected."""
    mod = _load()
    report, vcek, ask, ark, _meas = _build_snp_report(_MEAS)
    other = json.dumps({"measurement_id": "x", "tokens_saved": 1.0,
                        "avoided_model": "y"}).encode()
    att = mod.verify_report(report, vcek_der=vcek, ask_der=ask, ark_der=ark, measurement_json=other)
    assert att["verified"] is False
    assert any("report_data does not equal" in e for e in att["errors"])


def test_bad_sig_algo_fails_closed():
    mod = _load()
    report, vcek, ask, ark, meas = _build_snp_report(_MEAS, sig_algo=0)
    att = mod.verify_report(report, vcek_der=vcek, ask_der=ask, ark_der=ark, measurement_json=meas)
    assert att["verified"] is False
    assert any("unexpected sig_algo" in e for e in att["errors"])


def test_measurement_pin_mismatch_fails_closed():
    mod = _load()
    report, vcek, ask, ark, meas = _build_snp_report(_MEAS, measurement_field=b"\x44" * 48)
    att = mod.verify_report(report, vcek_der=vcek, ask_der=ask, ark_der=ark, measurement_json=meas,
                            expected_measurement="33" * 48)
    assert att["verified"] is False
    assert any("measurement" in e and "pinned" in e for e in att["errors"])


def test_wrong_length_report_fails_closed():
    mod = _load()
    _report, vcek, ask, ark, meas = _build_snp_report(_MEAS)
    att = mod.verify_report(b"\x00" * 100, vcek_der=vcek, ask_der=ask, ark_der=ark,
                            measurement_json=meas)
    assert att["verified"] is False
    assert any("attestation report" in e for e in att["errors"])


def test_expired_cert_fails_closed():
    mod = _load()
    report, vcek, ask, ark, meas = _build_snp_report(_MEAS, expired=True)
    att = mod.verify_report(report, vcek_der=vcek, ask_der=ask, ark_der=ark, measurement_json=meas)
    assert att["verified"] is False
    assert any("validity window" in e for e in att["errors"])


def test_missing_cryptography_fails_closed(monkeypatch):
    """Without the audited lib, verification REFUSES — never a silent skip."""
    import sys
    mod = _load()
    report, vcek, ask, ark, meas = _build_snp_report(_MEAS)
    monkeypatch.setitem(sys.modules, "cryptography", None)
    att = mod.verify_report(report, vcek_der=vcek, ask_der=ask, ark_der=ark, measurement_json=meas)
    assert att["verified"] is False
    assert any("cryptography" in e for e in att["errors"])


def test_ark_sha256_pin_format_independent(tmp_path):
    """--ark pin is sha256(DER): a PEM and the DER it decodes to share one fingerprint."""
    import datetime
    from cryptography import x509
    from cryptography.x509.oid import NameOID
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    mod = _load()
    k = ec.generate_private_key(ec.SECP384R1())
    n = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "ARK")])
    utc = datetime.timezone.utc
    now = datetime.datetime.now(utc)
    cert = (x509.CertificateBuilder().subject_name(n).issuer_name(n).public_key(k.public_key())
            .serial_number(1).not_valid_before(now - datetime.timedelta(days=1))
            .not_valid_after(now + datetime.timedelta(days=1)).sign(k, hashes.SHA384()))
    der = cert.public_bytes(serialization.Encoding.DER)
    pem = cert.public_bytes(serialization.Encoding.PEM)
    der_f, pem_f = tmp_path / "ark.der", tmp_path / "ark.pem"
    der_f.write_bytes(der)
    pem_f.write_bytes(pem)
    assert mod._load_cert_der(str(der_f)) == der
    assert mod._load_cert_der(str(pem_f)) == der          # PEM decodes to the same DER
    assert hashlib.sha256(mod._load_cert_der(str(pem_f))).hexdigest() == hashlib.sha256(der).hexdigest()
