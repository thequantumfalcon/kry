"""Synthetic SNP reports exercise signature inspection and the separate mint gate."""
from __future__ import annotations

import datetime
import hashlib
import importlib.util
import json
import struct
from pathlib import Path

import pytest

pytest.importorskip("cryptography")
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, utils
from cryptography.x509.oid import NameOID

_ROOT = Path(__file__).resolve().parents[1]
_KW = dict(event_type="cache_hit", avoided_model=None, served_model=None,
           tokens_saved=None, measurement_id=None, dry_run=False)


@pytest.fixture
def snp():
    spec = importlib.util.spec_from_file_location("snp_security", _ROOT / "scripts/kry_snp_verify.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def signed_report():
    """A separately generated fake chain must never become AMD-backed mint evidence."""
    def build(*, curve=None, policy=0x30000, version=2):
        root_key = ec.generate_private_key(ec.SECP384R1())
        issuer_key = ec.generate_private_key(ec.SECP384R1())
        leaf_key = ec.generate_private_key(curve or ec.SECP384R1())
        now = datetime.datetime.now(datetime.timezone.utc)

        def cert(subject, issuer, key, issuer_key, ca):
            def name(value):
                return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, value)])
            return (x509.CertificateBuilder().subject_name(name(subject)).issuer_name(name(issuer))
                    .public_key(key.public_key()).serial_number(x509.random_serial_number())
                    .not_valid_before(now - datetime.timedelta(days=1))
                    .not_valid_after(now + datetime.timedelta(days=1))
                    .add_extension(x509.BasicConstraints(ca=ca, path_length=None), critical=True)
                    .sign(issuer_key, hashes.SHA384()).public_bytes(serialization.Encoding.DER))

        ark = cert("synthetic ARK", "synthetic ARK", root_key, root_key, True)
        ask = cert("synthetic ASK", "synthetic ARK", issuer_key, root_key, True)
        leaf = cert("synthetic VCEK", "synthetic ASK", leaf_key, issuer_key, False)
        measurement = json.dumps({"measurement_id": "run-7", "tokens_saved": 5000,
                                  "avoided_model": "gh/claude-opus-4.8"}).encode()
        report = bytearray(1184)
        struct.pack_into("<I", report, 0, version)
        struct.pack_into("<Q", report, 8, policy)
        struct.pack_into("<I", report, 0x34, 1)
        report[0x50:0x90] = hashlib.sha512(measurement).digest()
        report[0x90:0xC0] = b"\x33" * 48
        report[0x1A0:0x1E0] = b"\x55" * 64
        r, s = utils.decode_dss_signature(leaf_key.sign(bytes(report[:0x2A0]), ec.ECDSA(hashes.SHA384())))
        report[0x2A0:0x2E8] = r.to_bytes(72, "little")
        report[0x2E8:0x330] = s.to_bytes(72, "little")
        return bytes(report), dict(vcek_der=leaf, ask_der=ask, ark_der=ark, measurement_json=measurement)
    return build


def test_custom_root_inspection_is_not_mint_permission(snp, signed_report):
    from kry import kry_mint
    report, kwargs = signed_report()
    att = snp.verify_report(report, **kwargs)
    assert att["verified"] is True
    assert att.get("mint_eligible") is False
    assert snp.run(att, **_KW)["verdict"] == "POLICY_REQUIRED"
    assert not kry_mint._MINT_LOG_PATH.exists()


def test_verified_only_output_cannot_mint(snp):
    from kry import kry_mint
    att = {"verified": True, "report_sha256": "a" * 64,
           "parsed_measurement": {"measurement_id": "run-7", "tokens_saved": 5000,
                                  "avoided_model": "gh/claude-opus-4.8"}}
    assert snp.run(att, **_KW)["verdict"] == "POLICY_REQUIRED"
    assert not kry_mint._MINT_LOG_PATH.exists()


@pytest.mark.parametrize("offset", [0x2A0 + 48, 0x2E8 + 48, 0x330, 1183])
def test_signature_padding_is_rejected(snp, signed_report, offset):
    report, kwargs = signed_report()
    altered = bytearray(report)
    altered[offset] = 1
    att = snp.verify_report(bytes(altered), **kwargs)
    assert att["verified"] is False
    assert any("padding" in error for error in att["errors"])


def test_p256_leaf_cannot_claim_p384(snp, signed_report):
    report, kwargs = signed_report(curve=ec.SECP256R1())
    att = snp.verify_report(report, **kwargs)
    assert att["verified"] is False
    assert any("P-384" in error for error in att["errors"])


def test_statement_digest_is_separate_from_raw_report(snp, signed_report):
    report, kwargs = signed_report()
    att = snp.verify_report(report, **kwargs)
    assert att.get("statement_sha256") == hashlib.sha256(report[:0x2A0]).hexdigest()
    assert att["report_sha256"] == hashlib.sha256(report).hexdigest()
    altered_signature = dict(att, report_sha256="f" * 64)
    assert snp._evidence_binding(att) == snp._evidence_binding(altered_signature)


def test_deep_measurement_returns_parse_refusal(snp):
    assert snp._parse_measurement(b"[" * 2000 + b"0" + b"]" * 2000) is None


def test_oversized_measurement_returns_parse_refusal(snp):
    assert snp._parse_measurement(b'{"x":"' + b"x" * (16 * 1024 * 1024) + b'"}') is None


def test_bad_pem_has_controlled_cli_failure(snp, tmp_path, capsys):
    pem = tmp_path / "bad.pem"
    pem.write_bytes(b"-----BEGIN CERTIFICATE-----\nA\n-----END CERTIFICATE-----\n")
    argv = [str(pem), "--vcek", str(pem), "--ask", str(pem), "--ark", str(pem),
            "--measurement-json", str(pem), "--dry-run"]
    assert snp.main(argv) == 1
    assert "REJECTED" in capsys.readouterr().out


def _policy():
    # Deliberately synthetic minima exercise arithmetic, not a recommended security floor.
    return {"schema": "kry_snp_policy/v1", "product": "Milan", "report_version": 2,
            "measurement": "33" * 48, "min_tcb": {"bootloader": 1, "tee": 0, "snp": 2, "microcode": 3}}


@pytest.fixture
def policy_inputs(snp):
    """Isolated policy unit: a synthetic leaf, NOT a valid chain to the public AMD root."""
    now = datetime.datetime.now(datetime.timezone.utc)
    key = ec.generate_private_key(ec.SECP384R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "policy unit fixture")])
    extensions = {"1": b"\x02\x01\x00", "2": b"\x16\x08Milan-B0", "4": b"\x55" * 64,
                  "3.1": b"\x02\x01\x01", "3.2": b"\x02\x01\x00",
                  "3.3": b"\x02\x01\x02", "3.8": b"\x02\x01\x03"}

    def leaf(values):
        builder = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
                   .serial_number(1).not_valid_before(now - datetime.timedelta(days=1))
                   .not_valid_after(now + datetime.timedelta(days=1)))
        for oid, value in values.items():
            builder = builder.add_extension(x509.UnrecognizedExtension(
                x509.ObjectIdentifier("1.3.6.1.4.1.3704.1." + oid), value), critical=False)
        return builder.sign(key, hashes.SHA384())

    report = bytearray(1184)
    struct.pack_into("<I", report, 0, 2)
    struct.pack_into("<Q", report, 8, 0x30000)
    struct.pack_into("<I", report, 0x34, 1)
    for offset in (0x38, 0x180, 0x1E0, 0x1F0):
        struct.pack_into("<Q", report, offset, 1 | (2 << 48) | (3 << 56))
    report[0x90:0xC0] = b"\x33" * 48
    report[0x1A0:0x1E0] = b"\x55" * 64
    ark = (_ROOT / "tests/fixtures/amd/Milan-ark.der").read_bytes()
    return report, extensions, leaf, ark


def test_policy_unit_accepts_matching_fields_without_bypassing_signature_gate(snp, policy_inputs):
    report, extensions, leaf, ark = policy_inputs
    cert = leaf(extensions)
    assert snp._mint_policy_errors(bytes(report), snp._report_fields(report), cert, ark, _policy()) == []
    # This leaf/report is not signed by AMD: full verification must STILL refuse it.
    result = snp.verify_report(bytes(report), vcek_der=cert.public_bytes(serialization.Encoding.DER),
                             ask_der=ark, ark_der=ark, measurement_json=b"{}", policy=_policy())
    assert result["verified"] is False
    assert result["mint_eligible"] is False


@pytest.mark.parametrize("offset,value,reason", [
    (8, 0xB0000, "DEBUG"), (0x180, 0, "reported TCB"), (0x38, 0, "current TCB"),
    (0x1E0, 0, "committed TCB"), (0x1F0, 0, "launch TCB"),
    (0x188, 1, "reserved"), (0x1F8, 1, "reserved"), (0x30, 1, "VMPL0"),
    (0x48, 4, "flags"),
])
def test_mint_policy_rejects_unsafe_report_fields(snp, policy_inputs, offset, value, reason):
    report, extensions, leaf, ark = policy_inputs
    struct.pack_into("<Q", report, offset, value)
    errors = snp._mint_policy_errors(bytes(report), snp._report_fields(report), leaf(extensions), ark, _policy())
    assert any(reason in error for error in errors)


@pytest.mark.parametrize("field,value", [
    ("report_version", 3), ("report_version", True), ("measurement", ""),
    ("measurement", "ff" * 48), ("product", "Turin"), ("min_tcb", {}),
    ("min_tcb", {"bootloader": 0, "tee": 0, "snp": 0, "microcode": 0}),
])
def test_incomplete_or_unsupported_policy_cannot_mint(snp, policy_inputs, field, value):
    report, extensions, leaf, ark = policy_inputs
    policy = dict(_policy(), **{field: value})
    assert snp._mint_policy_errors(bytes(report), snp._report_fields(report), leaf(extensions), ark, policy)


@pytest.mark.parametrize("suffix,value", [
    ("1", b"\x02\x01\x01"), ("2", b"\x16\x08Genoa-B0"), ("4", b"\x22" * 64),
    ("3.1", b"\x02\x01\x02"), ("3.2", b"\x02\x01\x01"),
    ("3.3", b"\x02\x01\x03"), ("3.8", b"\x02\x02\x01\x00"),
])
def test_vcek_binding_mismatch_cannot_satisfy_policy(snp, policy_inputs, suffix, value):
    report, extensions, leaf, ark = policy_inputs
    cert = leaf(dict(extensions, **{suffix: value}))
    errors = snp._mint_policy_errors(bytes(report), snp._report_fields(report), cert, ark, _policy())
    assert any("VCEK" in error for error in errors)


def test_forged_chain_cannot_be_enabled_with_an_operator_policy(snp, signed_report):
    report, kwargs = signed_report()
    att = snp.verify_report(report, **kwargs, policy=_policy())
    assert att["verified"] is True
    assert att["mint_eligible"] is False
    assert any("recognized AMD root" in error for error in att["policy_errors"])


@pytest.mark.parametrize("family", ["Milan", "Genoa"])
def test_pinned_vendor_roots_match_retained_public_certificates(snp, family):
    der = (_ROOT / f"tests/fixtures/amd/{family}-ark.der").read_bytes()
    cert = x509.load_der_x509_certificate(der)
    cert.verify_directly_issued_by(cert)
    assert hashlib.sha256(der).hexdigest() == snp.AMD_ARK_SHA256[family]


def test_legacy_snp_history_blocks_new_identity_without_changing_old_receipts(snp):
    from kry import kry_mint
    kry_mint.mint("cache_hit", 5000, detail="tee_attested(snp) /measurement:old-id",
                  evidence="old-raw-report-binding", evidence_tier="tee_attested")
    before = kry_mint._MINT_LOG_PATH.read_bytes()
    att = {"verified": True, "mint_eligible": True, "statement_sha256": "a" * 64,
           "parsed_measurement": {"measurement_id": "new-id", "tokens_saved": 5000,
                                  "avoided_model": "gh/claude-opus-4.8"}}
    assert snp.run(att, **_KW)["verdict"] == "MIGRATION_REQUIRED"
    assert kry_mint._MINT_LOG_PATH.read_bytes() == before


def test_nitro_history_does_not_trigger_snp_migration_gate(snp, tmp_path):
    history = tmp_path / "history"
    history.write_text(json.dumps({"detail": "tee_attested(nitro) /measurement:old-id"}) + "\n")
    assert snp._has_legacy_snp_history(history) is False


def test_unbound_statement_marker_does_not_bypass_legacy_guard(snp, tmp_path):
    history = tmp_path / "history"
    history.write_text(json.dumps({"detail": "tee_attested(snp) /snp-statement:" + "a" * 64,
                                  "evidence_hash": "old-evidence"}) + "\n")
    assert snp._has_legacy_snp_history(history) is True


@pytest.mark.parametrize("pinned", [False, True])
@pytest.mark.parametrize("with_policy", [False, True])
def test_cli_inspection_names_the_unsatisfied_hardware_policy(
        snp, signed_report, tmp_path, capsys, pinned, with_policy):
    from kry import kry_mint
    report, inputs = signed_report()
    report_path = tmp_path / "report.bin"
    report_path.write_bytes(report)
    argv = [str(report_path)]
    for flag, key in [("--vcek", "vcek_der"), ("--ask", "ask_der"),
                      ("--ark", "ark_der"), ("--measurement-json", "measurement_json")]:
        path = tmp_path / key
        path.write_bytes(inputs[key])
        argv.extend([flag, str(path)])
    if pinned:
        argv.extend(["--ark-sha256", hashlib.sha256(inputs["ark_der"]).hexdigest()])
    if with_policy:
        policy = tmp_path / "policy.json"
        policy.write_text(json.dumps(_policy()))
        argv.extend(["--policy", str(policy)])

    assert snp.main(argv + ["--dry-run"]) == 0
    output = capsys.readouterr().out
    assert "SIGNATURE INSPECTION ONLY" in output
    assert "KRY SNP signature inspection" in output
    assert output.splitlines()[-1].strip() == (
        "-> dry-run: signature consistent under supplied root; "
        "hardware policy not satisfied; no receipt minted")
    assert snp.main(argv) == 1
    assert "POLICY_REQUIRED" in capsys.readouterr().out
    assert not kry_mint._MINT_LOG_PATH.exists()


def test_dry_run_names_a_preverified_hardware_policy_result(snp, signed_report):
    # Formatting unit only: the synthetic chain does not satisfy hardware policy.
    report, inputs = signed_report()
    att = dict(snp.verify_report(report, **inputs), mint_eligible=True)
    result = snp.run(att, **dict(_KW, dry_run=True))
    assert result["note"] == ("dry-run: signature consistent under supplied root; "
                              "hardware policy satisfied; no receipt minted")
    assert result["minted"] is None
