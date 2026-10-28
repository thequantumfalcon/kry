#!/usr/bin/env python3
"""Mint a KRY T2 receipt from a self-hosted AMD SEV-SNP attestation report.

Companion to scripts/kry_tee_verify.py (AWS Nitro, cloud-only). SEV-SNP is the
self-hostable TEE: on an EPYC node we can run the KRY holdout/measurement inside an
SNP guest and produce a hardware-signed ATTESTATION_REPORT, verified offline against a
recognized AMD root (ARK) and a separately enrolled guest/TCB policy.
See docs/KRY_SNP_POLICY.md. Custom roots support signature inspection only.

The honest claim of `tee_attested` is IDENTICAL to the Nitro tier and must not be
overclaimed (Avoid A1/A8):

    A SEV-SNP attestation proves *"the code that produced this measurement ran inside an
    SNP guest on genuine AMD silicon, measured at launch"* — it does NOT prove a closed
    model provider's OWN inference ran in a TEE, and it does NOT cover runtime behaviour
    AFTER attestation (SNP measures launch state only — see the research doc, finding 3).

    So a KRY `tee_attested` receipt means exactly: "the savings MEASUREMENT (the token
    accounting) ran in attested hardware the operator could not fabricate." It UPGRADES
    self_reported / holdout_validated (operator-trust → hardware-attested). `tlsn_attested`
    remains the only tier that proves the provider call happened.

How trust is established (mirrors kry_tee_verify.verify_attestation):
  - `verify_report()` is the cryptographic boundary. It parses the 1184-byte report
    (stdlib struct — structure parsing is not security-critical; the ECDSA signature is
    what binds the bytes), verifies the ECDSA-P384/SHA-384 signature over report[0:0x2A0]
    under the VCEK leaf certificate, walks VCEK → ASK → ARK (each cert directly issued by
    the next, via the audited library, which handles AMD's RSA-PSS chain), checks validity
    windows, and requires the chain to terminate at EXACTLY the operator-PINNED ARK. The
    asymmetric crypto + X.509 use the audited `cryptography` library; ABSENT it we FAIL
    CLOSED (never mint claiming a verification we did not perform).
  - report_data (64 bytes, signed) binds the report to OUR measurement: it must equal
    SHA-512(the measurement JSON), supplied via --measurement-json. The measurement carries
    {"measurement_id","tokens_saved","avoided_model","served_model"(opt),"event_type"(opt)}.
  - `run()` consumes that ALREADY-VERIFIED output and mints (pure stdlib) — fresh
    tee_attested value, or a net-zero tier UPGRADE of a prior self_reported/holdout receipt
    for the same measurement id (no double-credit). Reuses kry_mint.promote_to_tee — the
    tee_attested mint glue is source-agnostic (Nitro and SNP share it).

Unlike Nitro, the SNP report carries NO timestamp field, so there is no wall-clock
freshness check. Anti-replay is the same mechanism the mint already enforces: a replayed
report yields the same evidence binding, so the mint decay collapses the repeat (and a
fresh measurement_id is the operator's per-run nonce). A verifier should additionally pin
a fresh report_data nonce when challenging live; here the measurement_id carries that role.

stdlib only, EXCEPT `cryptography` (optional extra: `pip install "kry-attest[tee]"`) used
solely inside verify_report for the signature + X.509 chain. Imports kry only to mint.

⚠ NOT YET PROVEN ON REAL HARDWARE. The Nitro tier shipped with a genuine-enclave
regression fixture; SEV-SNP needs an EPYC node to capture one. The synthetic tests below
default to a fast ECDSA chain, but one fixture builds the real-AMD shape (RSA-4096/RSA-PSS
ARK/ASK, ECDSA-P384 VCEK) so the verify_directly_issued_by RSA-PSS path IS exercised — that
closes the code-path gap; only the genuine AMD root keys remain unproven until an EPYC node
captures one. Treat the `tee.fail` DDR5 physical-attack class (research doc, unverified) as
a gating unknown.

Usage:
    # verify only (no mint): pin AMD's ARK; pass the KDS-fetched VCEK + ASK and the
    # measurement JSON whose SHA-512 the guest placed in report_data.
    python3 scripts/kry_snp_verify.py report.bin \
        --vcek vcek.der --ask ask.pem --ark ark.pem \
        --measurement-json measurement.json --dry-run
    # mint only after separately enrolling the expected guest and minimum TCB policy:
    python3 scripts/kry_snp_verify.py report.bin --vcek vcek.der --ask ask.pem --ark ark.pem \
        --measurement-json measurement.json --policy enrolled-policy.json
"""
from __future__ import annotations

import argparse
import binascii
import hashlib
import json
import math
import re
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

# AMD SEV-SNP ATTESTATION_REPORT layout (offsets verified against virtee/sev
# src/firmware/guest/types/snp.rs + ecdsa/mod.rs, and AMD doc 56860/58217, 2026-06-06).
# All multi-byte scalar fields are little-endian.
_REPORT_LEN = 1184          # 0x4A0 — ATT_REP_FW_LEN
_SIGNED_LEN = 0x2A0         # signature is over report[0:0x2A0]
_OFF_VERSION = 0x000        # u32
_OFF_SIG_ALGO = 0x034       # u32 — 1 == ECDSA P-384 with SHA-384
_OFF_REPORT_DATA = 0x050    # [u8; 64] — caller-supplied; we bind it to the measurement
_OFF_MEASUREMENT = 0x090    # [u8; 48] — launch measurement (the guest-image identity)
_OFF_CHIP_ID = 0x1A0        # [u8; 64] — unique per AMD chip
_OFF_SIGNATURE = 0x2A0      # 512-byte Signature: r(72 LE) || s(72 LE) || reserved(368)
_SIG_COMPONENT = 72         # bytes reserved per r/s component (P-384 uses the first 48)
_SIG_ALGO_ECDSA_P384_SHA384 = 1
_MAX_DOCUMENT_BYTES = 16 * 1024 * 1024
# DER fingerprints independently derived from AMD's HTTPS KDS cert_chain endpoints.
# Sources and scope: docs/KRY_SNP_POLICY.md. Root rotation requires a reviewed update.
AMD_ARK_SHA256 = {
    "Milan": "69d063b45344d26a2e94e1f4210de49ef555308287d4c174445c95639a540bcd",
    "Genoa": "4c6598d19c18719c5dfd4a7d335f674e5bfe1d8f800cea2cf270c10d103db2f1",
}
_TCB_COMPONENTS = {"bootloader": 0, "tee": 8, "snp": 48, "microcode": 56}


class _ReportError(ValueError):
    pass


def _reject_json_constant(value: str):
    raise ValueError(f"non-standard JSON constant {value} is not allowed")


def _json_loads(raw: str):
    return json.loads(raw, parse_constant=_reject_json_constant)


def _read_bounded(path, limit=_MAX_DOCUMENT_BYTES) -> bytes:
    with Path(path).open("rb") as source:
        data = source.read(limit + 1)
    if len(data) > limit:
        raise ValueError(f"input exceeds the {limit}-byte document limit")
    return data


def _positive_finite_number(value) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    try:
        value = float(value)
    except OverflowError:
        return 0.0
    return value if math.isfinite(value) and value > 0 else 0.0


# ── Report parsing (stdlib) ──────────────────────────────────────────────────────

def _report_fields(report: bytes) -> dict:
    """Pull the fields we use from a raw 1184-byte ATTESTATION_REPORT."""
    if len(report) != _REPORT_LEN:
        raise _ReportError(f"report is {len(report)} bytes, expected {_REPORT_LEN}")
    version = struct.unpack_from("<I", report, _OFF_VERSION)[0]
    sig_algo = struct.unpack_from("<I", report, _OFF_SIG_ALGO)[0]
    return {
        "version": version,
        "sig_algo": sig_algo,
        "report_data": report[_OFF_REPORT_DATA:_OFF_REPORT_DATA + 64],
        "measurement": report[_OFF_MEASUREMENT:_OFF_MEASUREMENT + 48],
        "chip_id": report[_OFF_CHIP_ID:_OFF_CHIP_ID + 64],
        "signature": report[_OFF_SIGNATURE:_OFF_SIGNATURE + 512],
    }


def _ecdsa_rs_from_signature(sig_field: bytes) -> tuple[int, int]:
    """Decode the AMD Signature (r||s) into big-endian ints. r and s are each stored in a
    72-byte little-endian field; only the first 48 bytes are meaningful for P-384.
    int.from_bytes(x, 'little') == int.from_bytes(reversed(x), 'big')."""
    r = int.from_bytes(sig_field[0:48], "little")
    s = int.from_bytes(sig_field[_SIG_COMPONENT:_SIG_COMPONENT + 48], "little")
    return r, s


def _parse_measurement(meas_json_bytes: bytes) -> dict | None:
    if len(meas_json_bytes) > _MAX_DOCUMENT_BYTES:
        return None
    try:
        m = _json_loads(meas_json_bytes.decode("utf-8"))
        return m if isinstance(m, dict) else None
    except (ValueError, UnicodeDecodeError, RecursionError):
        return None


def _load_cert_der(path: str) -> bytes:
    """Load an X.509 cert as DER from a PEM or DER file."""
    raw = _read_bounded(path, 64 * 1024)
    if b"-----BEGIN CERTIFICATE-----" in raw:
        import base64
        b64 = b"".join(line for line in raw.splitlines() if b"-----" not in line)
        return base64.b64decode(b64, validate=True)
    return raw


# ── Cryptographic verification boundary (audited `cryptography`; fail-closed) ─────

def _vcek_extension(cert, suffix: str) -> bytes:
    from cryptography.x509 import ObjectIdentifier
    return cert.extensions.get_extension_for_oid(
        ObjectIdentifier(f"1.3.6.1.4.1.3704.1.{suffix}")).value.value


def _der_byte_integer(raw: bytes) -> int:
    """Read a canonical DER INTEGER containing an unsigned security-version byte."""
    if len(raw) == 3 and raw[:2] == b"\x02\x01" and raw[2] < 128:
        return raw[2]
    if len(raw) == 4 and raw[:3] == b"\x02\x02\x00" and raw[3] >= 128:
        return raw[3]
    raise ValueError("VCEK security version is not a canonical unsigned byte INTEGER")


def _mint_policy_errors(report: bytes, fields: dict, vcek, ark_der: bytes, policy) -> list[str]:
    """Check independently selected policy and AMD identity, separately from signatures.

    Only family-19h Milan/Genoa version-2 reports are supported for minting. Unknown
    versions/families remain inspection-only until their layouts are reviewed.
    """
    from cryptography.x509 import ExtensionNotFound
    if not isinstance(policy, dict) or policy.get("schema") != "kry_snp_policy/v1":
        return ["a separately enrolled kry_snp_policy/v1 policy is required for minting"]
    product = policy.get("product")
    if not isinstance(product, str) or product not in AMD_ARK_SHA256:
        return ["mint policy product must be a supported AMD family: Milan or Genoa"]
    errors = []
    if hashlib.sha256(ark_der).hexdigest() != AMD_ARK_SHA256[product]:
        errors.append("the supplied root is not the recognized AMD root for the policy product")
    if type(policy.get("report_version")) is not int or policy["report_version"] != 2 or fields["version"] != 2:
        return errors + ["mint policy supports only report version 2"]
    expected = policy.get("measurement")
    if not isinstance(expected, str) or re.fullmatch(r"[0-9a-fA-F]{96}", expected) is None:
        errors.append("mint policy requires an exact 48-byte launch measurement")
    elif fields["measurement"].hex() != expected.lower():
        errors.append("launch measurement does not match the enrolled policy")
    minimum = policy.get("min_tcb")
    if (not isinstance(minimum, dict) or set(minimum) != set(_TCB_COMPONENTS)
            or any(type(value) is not int or not 0 <= value <= 255 for value in minimum.values())
            or not any(minimum.values())):
        return errors + ["min_tcb must explicitly name all four byte-valued components and cannot be all zero"]
    guest_policy = struct.unpack_from("<Q", report, 8)[0]
    if guest_policy & (1 << 19):
        errors.append("DEBUG is enabled; debug-capable guests cannot mint tee_attested value")
    if not guest_policy & (1 << 17):
        errors.append("version-2 guest policy reserved bit 17 must be one")
    if struct.unpack_from("<I", report, 0x30)[0] != 0:
        errors.append("mint policy requires a VMPL0 report")
    if struct.unpack_from("<I", report, 0x48)[0] & ~1:
        errors.append("version-2 VCEK report flags contain unsupported or reserved bits")
    if any(report[0x4C:0x50] + report[0x188:0x1A0] + report[0x1EB:0x1EC]
           + report[0x1EF:0x1F0] + report[0x1F8:0x2A0]):
        errors.append("version-2 report reserved fields must be zero")
    tcbs = {}
    for name, offset in (("current", 0x38), ("reported", 0x180),
                         ("committed", 0x1E0), ("launch", 0x1F0)):
        encoded = struct.unpack_from("<Q", report, offset)[0]
        if encoded & 0x0000FFFFFFFF0000:
            errors.append(f"{name} TCB reserved bits must be zero")
        tcbs[name] = {key: (encoded >> shift) & 255 for key, shift in _TCB_COMPONENTS.items()}
        if any(tcbs[name][key] < minimum[key] for key in minimum):
            errors.append(f"{name} TCB is below the enrolled component minimum")
    if any(tcbs["reported"][key] > tcbs["current"][key] for key in minimum):
        errors.append("reported TCB exceeds current TCB")
    try:
        if _der_byte_integer(_vcek_extension(vcek, "1")) != 0:
            errors.append("VCEK structure version is not family-19h version 0")
        product_der = _vcek_extension(vcek, "2")
        if (len(product_der) < 3 or product_der[0] != 0x16 or product_der[1] != len(product_der) - 2
                or product_der[1] >= 128):
            raise ValueError("VCEK productName is not a short DER IA5String")
        product_name = product_der[2:].decode("ascii")
        if re.fullmatch(re.escape(product) + r"-[A-Z][0-9]+", product_name) is None:
            errors.append("VCEK product family does not match the enrolled policy")
        hwid = _vcek_extension(vcek, "4")
        if len(hwid) == 66 and hwid[:2] == b"\x04\x40":
            hwid = hwid[2:]  # Legacy KDS certificates also contain an unwrapped 64-byte hwID.
        if len(hwid) != 64 or hwid != fields["chip_id"]:
            errors.append("VCEK hardware ID does not match the report chip ID")
        for key, suffix in (("bootloader", "3.1"), ("tee", "3.2"), ("snp", "3.3"), ("microcode", "3.8")):
            if _der_byte_integer(_vcek_extension(vcek, suffix)) != tcbs["reported"][key]:
                errors.append(f"VCEK {key} version does not match reported TCB")
    except (ValueError, LookupError, AttributeError, ExtensionNotFound) as exc:
        errors.append(f"VCEK policy extensions are missing or invalid: {exc}")
    return errors


def verify_report(report: bytes, *, vcek_der: bytes, ask_der: bytes, ark_der: bytes,
                  measurement_json: bytes, expected_measurement: str | None = None,
                  policy: dict | None = None) -> dict:
    """Verify a SEV-SNP ATTESTATION_REPORT. Fail-closed, errors accumulated.

    Checks (all must pass for verified=True):
      1. report is 1184 bytes and sig_algo == ECDSA P-384/SHA-384.
      2. The ECDSA-P384/SHA-384 signature over report[0:0x2A0] verifies under the VCEK leaf.
      3. report_data == SHA-512(measurement_json) (binds the report to OUR measurement).
      4. VCEK ← ASK ← ARK: each cert is directly issued by the next (signature + names),
         every cert is within its validity window, and the chain root is byte-equal to the
         PINNED ARK (ark_der — the trust anchor).
      5. The launch measurement matches expected_measurement when pinned.

    The asymmetric crypto uses the audited `cryptography` lib (handles AMD's RSA-PSS
    ARK/ASK chain); ABSENT it we return verified=False with a clear error (never a silent
    skip). verified=True means signature consistency under the supplied root.
    mint_eligible additionally requires a recognized AMD root and a matching enrolled
    guest/TCB policy; run() refuses minting without that separate result.
    """
    errs: list[str] = []
    result: dict = {"verified": False, "errors": errs, "mint_eligible": False,
                    "report_sha256": hashlib.sha256(report).hexdigest()}

    try:
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ec, utils as asym_utils
        from cryptography.exceptions import InvalidSignature
    except ImportError:
        errs.append("cryptographic verification requires the 'cryptography' package "
                    "(pip install 'kry-attest[tee]') — refusing to mint without verifying "
                    "the report signature (fail-closed)")
        return result

    # 1. structure + algorithm
    try:
        fields = _report_fields(report)
    except _ReportError as e:
        errs.append(f"not a SEV-SNP attestation report: {e}")
        return result
    if fields["sig_algo"] != _SIG_ALGO_ECDSA_P384_SHA384:
        errs.append(f"unexpected sig_algo {fields['sig_algo']} "
                    f"(expected {_SIG_ALGO_ECDSA_P384_SHA384} = ECDSA P-384/SHA-384)")
    result["statement_sha256"] = hashlib.sha256(report[:_SIGNED_LEN]).hexdigest()
    sig = fields["signature"]
    if any(sig[48:72] + sig[120:144] + sig[144:]):
        errs.append("nonzero signature padding is not a canonical SNP signature")
        return result

    # 2. report signature under the VCEK leaf (ECDSA-P384/SHA-384 over report[0:0x2A0])
    try:
        vcek = x509.load_der_x509_certificate(vcek_der)
        key = vcek.public_key()
        if not isinstance(key, ec.EllipticCurvePublicKey) or not isinstance(key.curve, ec.SECP384R1):
            errs.append("the VCEK must contain an ECDSA P-384 public key")
            return result
        r, s = _ecdsa_rs_from_signature(fields["signature"])
        der_sig = asym_utils.encode_dss_signature(r, s)
        key.verify(der_sig, report[:_SIGNED_LEN], ec.ECDSA(hashes.SHA384()))
    except InvalidSignature:
        errs.append("report signature does not verify under the VCEK certificate — "
                    "the report was tampered or not signed by this chip")
        return result
    except Exception as e:
        errs.append(f"could not verify the report signature: {e}")
        return result

    # 3. report_data binds the measurement (the 64-byte signed field == SHA-512 of the JSON)
    expected_rd = hashlib.sha512(measurement_json).digest()
    if fields["report_data"] != expected_rd:
        errs.append("report_data does not equal SHA-512(--measurement-json) — the report "
                    "is not bound to this measurement (wrong file, or not the attested one)")

    # 4. VCEK -> ASK -> ARK, terminating at exactly the pinned ARK
    try:
        ask = x509.load_der_x509_certificate(ask_der)
        ark = x509.load_der_x509_certificate(ark_der)
    except Exception as e:
        errs.append(f"could not parse the ASK/ARK certificates: {e}")
        return result
    chain = [vcek, ask, ark]
    # validity windows
    import time as _time
    now = _time.time()
    for cert in chain:
        if not (cert.not_valid_before_utc.timestamp() <= now <= cert.not_valid_after_utc.timestamp()):
            errs.append(f"certificate '{cert.subject.rfc4514_string()}' is outside its validity window")
    # each cert directly issued by the next (audited lib handles ECDSA + RSA-PSS)
    for child, issuer in zip(chain, chain[1:]):
        try:
            child.verify_directly_issued_by(issuer)
        except Exception:
            errs.append(f"chain broken: '{child.subject.rfc4514_string()}' is not directly "
                        f"issued by '{issuer.subject.rfc4514_string()}'")
    # ARK must be self-signed (a genuine root) and byte-equal to the pinned anchor
    try:
        ark.verify_directly_issued_by(ark)
    except Exception:
        errs.append("the pinned ARK is not self-signed (not a root certificate)")
    if ark.public_bytes(serialization.Encoding.DER) != bytes(ark_der):
        errs.append("internal: ARK re-encoding mismatch")  # defensive; should never fire

    # 5. launch-measurement pin (the guest-image identity)
    meas_hex = fields["measurement"].hex()
    if expected_measurement:
        if meas_hex.lower() != expected_measurement.strip().lower():
            errs.append(f"measurement {meas_hex[:16]}… != pinned {expected_measurement[:16]}… "
                        f"— this is not the attested measurement guest")

    result["version"] = fields["version"]
    result["measurement"] = meas_hex
    result["chip_id"] = fields["chip_id"].hex()
    result["report_data"] = fields["report_data"].hex()
    result["parsed_measurement"] = _parse_measurement(measurement_json)
    if result["parsed_measurement"] is None:
        errs.append("measurement JSON is invalid, oversized, deeply nested, or not an object")
    result["verified"] = not errs
    result["policy_errors"] = _mint_policy_errors(report, fields, vcek, ark_der, policy)
    result["mint_eligible"] = result["verified"] and not result["policy_errors"]
    return result


# ── Mint glue (pure stdlib; mirrors kry_tee_verify.run) ──────────────────────────

def _evidence_binding(att: dict) -> str:
    """Bind replay identity to the signed statement, independent of signature encoding."""
    return f"snp-statement/v1:{att['statement_sha256']}"


def _has_legacy_snp_history(path: Path) -> bool:
    """Old SNP rows lack signed-statement identities; never silently reset their decay.

    Other TEE receipts do not trigger this gate. Retain old rows and evidence for an
    explicit migration review; do not clear the ledger or invent missing bindings.
    """
    if not path.exists():
        return False
    with path.open(encoding="utf-8") as source:
        for line in source:
            if not line.strip():
                continue
            row = _json_loads(line)
            if not isinstance(row, dict):
                raise ValueError("mint history contains a non-object receipt")
            detail = row.get("detail") or ""
            if isinstance(detail, str) and detail.startswith("tee_attested(snp)"):
                match = re.search(r" /snp-statement:([0-9a-f]{64})(?:\s|$)", detail)
                if match is None:
                    return True
                expected = hashlib.sha256(f"snp-statement/v1:{match.group(1)}".encode()).hexdigest()
                if row.get("evidence_hash") != expected:
                    return True
    return False


def run(att: dict, *, event_type: str, avoided_model: str | None,
        served_model: str | None, tokens_saved: float | None,
        measurement_id: str | None, dry_run: bool) -> dict:
    """Consume an ALREADY-VERIFIED report (verify_report output) and mint.

    Mirrors kry_tee_verify.run / kry_tlsn_verify.run: fail-closed gate → extract the
    attested measurement → promote a prior self_reported/holdout receipt to tee_attested
    (no double-credit), or mint fresh tee_attested value when none exists. The optional
    measurement_id asserts the identity in the verified payload; it cannot replace
    it. Other overrides remain operator inputs, not authenticated document values.
    Measurement IDs must be nonempty strings without '/' or whitespace, matching
    receipt markers."""
    if att.get("verified") is not True:
        return {"verdict": "REJECTED", "errors": att.get("errors") or ["report not verified"]}

    m = att.get("parsed_measurement") or {}
    mid = measurement_id if measurement_id is not None else m.get("measurement_id")
    basis = tokens_saved if tokens_saved is not None else m.get("tokens_saved")
    basis = _positive_finite_number(basis)
    avoided = avoided_model or m.get("avoided_model")
    avoided_src = "cli" if avoided_model else ("measurement" if m.get("avoided_model") else None)
    served = served_model or m.get("served_model")

    result: dict = {
        "verdict": "OK",
        "chip_id": att.get("chip_id"),
        "measurement": att.get("measurement"),
        "measurement_id": mid,
        "avoided_model": {"value": avoided, "source": avoided_src},
        "served_model": served,
        "tokens_saved_basis": basis,
    }

    if mid is None:
        result["verdict"] = "NO_MEASUREMENT_ID"
        result["note"] = ("the attested measurement carries no measurement_id — cannot bind "
                          "the report to a measurement (embed it in the attested payload)")
        return result
    if not isinstance(mid, str) or re.fullmatch(r"[^/\s]+", mid) is None:
        result["verdict"] = "INVALID_MEASUREMENT_ID"
        result["note"] = "measurement_id must be a nonempty string without '/' or whitespace"
        return result
    attested_mid = m.get("measurement_id")
    if attested_mid is None:
        result["verdict"] = "NO_MEASUREMENT_ID"
        result["note"] = "measurement_id must come from the attested payload, not a CLI argument"
        return result
    if not isinstance(attested_mid, str) or re.fullmatch(r"[^/\s]+", attested_mid) is None:
        result["verdict"] = "INVALID_MEASUREMENT_ID"
        result["note"] = "attested measurement_id must be a nonempty string without '/' or whitespace"
        return result
    if mid != attested_mid:
        result["verdict"] = "MEASUREMENT_ID_MISMATCH"
        result["note"] = "--measurement-id must equal the measurement_id in the attested payload"
        return result
    if basis <= 0:
        result["verdict"] = "NO_BASIS"
        result["note"] = ("the attested measurement carries no positive tokens_saved — "
                          "pass --tokens-saved to mint against it")
        return result
    if avoided is None:
        result["verdict"] = "NO_DISPLACEMENT_CONTEXT"
        result["note"] = ("the attested measurement names no avoided_model and none was given "
                          "(--avoided-model) — refusing to mint displacement value we can't "
                          "substantiate (the counterfactual is never invented)")
        return result

    if dry_run:
        result["minted"] = None
        policy_status = "satisfied" if att.get("mint_eligible") is True else "not satisfied"
        result["note"] = ("dry-run: signature consistent under supplied root; "
                          f"hardware policy {policy_status}; no receipt minted")
        return result

    if att.get("mint_eligible") is not True or not att.get("statement_sha256"):
        result["verdict"] = "POLICY_REQUIRED"
        result["note"] = "signature inspection alone does not authorize AMD-backed minting"
        result["errors"] = att.get("policy_errors") or ["a successful SNP hardware policy check is required"]
        return result

    from kry import kry_mint
    try:
        legacy = _has_legacy_snp_history(kry_mint._MINT_LOG_PATH)
    except (OSError, ValueError, RecursionError):
        legacy = True
    if legacy:
        result["verdict"] = "MIGRATION_REQUIRED"
        result["note"] = "retained SNP history needs review before changing replay identities; no receipt minted"
        return result
    before = kry_mint.veracity_breakdown()
    detail = (f"tee_attested(snp) chip={(att.get('chip_id') or '')[:12]} "
              f"measurement={(att.get('measurement') or '')[:12]} /measurement:{mid} "
              f"/snp-statement:{att['statement_sha256']}")
    evidence = _evidence_binding(att)

    # Prefer UPGRADING a prior self_reported/holdout measurement receipt (no double-credit);
    # mint fresh tee value only when no prior receipt exists for this measurement id.
    prior = kry_mint._find_measurement_receipt_for_tee(mid)
    if prior is not None:
        promotion = kry_mint.promote_to_tee(mid, evidence, detail)
        if promotion is None:
            result["verdict"] = "ALREADY_UPGRADED"
            result["note"] = (f"measurement {mid} was already credited and already upgraded to "
                              f"tee_attested ({prior.get('receipt_id')}) — no-op")
            return result
        receipt, superseded_id, moved_kry = promotion
        after = kry_mint.veracity_breakdown()
        result["minted"] = {
            "receipt_id": receipt.receipt_id,
            "mode": "tier_upgrade",
            "supersedes": superseded_id,
            "kry_re_tiered": round(moved_kry, 4),
            "evidence_tier": receipt.evidence_tier,
            "chain_hash": receipt.chain_hash[:16] + "…",
        }
        result["veracity_floor"] = {"before": before["veracity_floor"], "after": after["veracity_floor"]}
        result["tee_attested_fraction"] = {"before": before["tee_attested_fraction"],
                                           "after": after["tee_attested_fraction"]}
        return result

    receipt = kry_mint.mint(
        event_type=event_type,
        tokens_saved=basis,
        detail=detail,
        evidence=evidence,
        avoided_model=avoided,
        served_model=served,
        evidence_tier=kry_mint.TIER_TEE_ATTESTED,
        # MINT-1: refuse a second FRESH tee credit for the same measurement under the mint lock — the
        # fresh-tee path had no dedup at all (the audit flagged only tlsn; this is the same race class).
        dedup_check=lambda: (_has_legacy_snp_history(kry_mint._MINT_LOG_PATH)
                             or kry_mint._find_fresh_tee_receipt_for_measurement(mid) is not None),
    )
    if receipt is None:
        result["verdict"] = "NOT_MINTED"
        result["note"] = ("mint returned None — basis decayed to dust (this report was already "
                          "minted) or was rejected at the boundary")
        return result
    after = kry_mint.veracity_breakdown()
    result["minted"] = {
        "receipt_id": receipt.receipt_id,
        "mode": "fresh_mint",
        "kry_minted": round(receipt.kry_minted, 4),
        "evidence_tier": receipt.evidence_tier,
        "chain_hash": receipt.chain_hash[:16] + "…",
    }
    result["veracity_floor"] = {"before": before["veracity_floor"], "after": after["veracity_floor"]}
    return result


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Mint a KRY T2 (tee_attested) receipt from an AMD SEV-SNP attestation report")
    p.add_argument("report", help="the SEV-SNP ATTESTATION_REPORT (1184-byte binary file)")
    p.add_argument("--vcek", required=True,
                   help="the VCEK leaf certificate (PEM or DER), fetched from the AMD KDS for "
                        "this chip + reported TCB")
    p.add_argument("--ask", required=True,
                   help="the AMD SEV signing key (ASK) intermediate certificate (PEM or DER), "
                        "from the KDS cert chain")
    p.add_argument("--ark", required=True,
                   help="the PINNED AMD root key (ARK) certificate (PEM or DER). The chain must "
                        "terminate at exactly this root. Obtain from the AMD KDS cert chain or "
                        "the virtee/sev builtin (src/certs/snp/builtin/<gen>/ark.pem)")
    p.add_argument("--ark-sha256", default=None,
                   help="OPTIONAL additional sha256(DER) file pin. It cannot replace the built-in "
                        "AMD product-root checks required for minting; custom roots allow "
                        "signature inspection only.")
    p.add_argument("--measurement-json", required=True,
                   help="the measurement JSON file whose SHA-512 the guest placed in report_data "
                        "({measurement_id, tokens_saved, avoided_model, served_model?})")
    p.add_argument("--measurement", default=None,
                   help="PIN the launch measurement (hex) — minting is refused unless the report's "
                        "measurement matches (identifies WHICH guest image produced it)")
    p.add_argument("--policy", default=None,
                   help="separately enrolled kry_snp_policy/v1 JSON: supported AMD product, "
                        "report version, expected image and component TCB minima; required to mint")
    p.add_argument("--event-type", default="short_circuit",
                   help="efficiency event this attested measurement backs (default: short_circuit)")
    p.add_argument("--measurement-id", default=None,
                   help="require this measurement id in the bound measurement JSON (cannot override it)")
    p.add_argument("--avoided-model", default=None,
                   help="OVERRIDE the avoided model (default: from the measurement JSON). "
                        "Absent both → REFUSED (the counterfactual is never invented)")
    p.add_argument("--served-model", default=None,
                   help="OVERRIDE the served model (default: from the measurement JSON)")
    p.add_argument("--tokens-saved", type=float, default=None,
                   help="OVERRIDE the saving basis (default: the attested tokens_saved)")
    p.add_argument("--dry-run", action="store_true", help="verify + parse + report only — mint nothing")
    args = p.parse_args(argv)

    try:
        ark_der = _load_cert_der(args.ark)
        vcek_der = _load_cert_der(args.vcek)
        ask_der = _load_cert_der(args.ask)
        report = _read_bounded(args.report, _REPORT_LEN)
        meas_json = _read_bounded(args.measurement_json)
        policy = _parse_measurement(_read_bounded(args.policy)) if args.policy else None
        if args.policy and policy is None:
            raise ValueError("mint policy is not a bounded JSON object")
    except (OSError, ValueError, binascii.Error, RecursionError) as exc:
        print(f"KRY SNP input — REJECTED: {exc}")
        return 1
    ark_sha = hashlib.sha256(ark_der).hexdigest()
    if args.ark_sha256 and ark_sha.lower() != args.ark_sha256.strip().lower():
        print("KRY T2 SNP mint — REJECTED (fail-closed):")
        print(f"  - --ark sha256(DER) {ark_sha} != pinned {args.ark_sha256.strip().lower()} "
              f"(not the expected AMD root)")
        return 1
    if not args.ark_sha256:
        print(f"  note: supplied root used for signature inspection (sha256(DER)={ark_sha}); "
              "AMD-backed mint eligibility is checked separately")

    att = verify_report(report, vcek_der=vcek_der, ask_der=ask_der,
                        ark_der=ark_der, measurement_json=meas_json,
                        expected_measurement=args.measurement, policy=policy)
    result = run(att, event_type=args.event_type, avoided_model=args.avoided_model,
                 served_model=args.served_model, tokens_saved=args.tokens_saved,
                 measurement_id=args.measurement_id, dry_run=args.dry_run)

    if result["verdict"] == "REJECTED":
        print("KRY T2 SNP report — REJECTED (fail-closed):")
        for e in result["errors"]:
            print(f"  - {e}")
        return 1
    if result["verdict"] in ("POLICY_REQUIRED", "MIGRATION_REQUIRED"):
        print(f"KRY SNP mint — {result['verdict']}: {result['note']}")
        for error in result.get("errors", []):
            print(f"  - {error}")
        return 1
    if args.dry_run and not att.get("mint_eligible"):
        print("SIGNATURE INSPECTION ONLY — hardware policy not satisfied; not eligible to mint")
        for error in att.get("policy_errors", []):
            print(f"  - {error}")

    print("KRY SNP signature inspection" if args.dry_run else "KRY T2 TEE (SEV-SNP) verification")
    print(f"  chip id:          {(result.get('chip_id') or '')[:24]}…")
    print(f"  measurement:      {(result.get('measurement') or '')[:24]}…")
    print(f"  measurement id:   {result.get('measurement_id')}")
    print(f"  tokens saved:     {result.get('tokens_saved_basis')}")
    av = result.get("avoided_model", {})
    print(f"  avoided model:    {av.get('value')}  (source: {av.get('source')})")
    print(f"  served model:     {result.get('served_model')}")

    if result["verdict"] in ("NO_BASIS", "NO_DISPLACEMENT_CONTEXT", "NO_MEASUREMENT_ID",
                              "INVALID_MEASUREMENT_ID", "MEASUREMENT_ID_MISMATCH"):
        print(f"  -> {result['note']}")
        return 1
    if result["verdict"] == "ALREADY_UPGRADED":
        print(f"  -> {result['note']}")
        return 0
    # HOLE #24: check NOT_MINTED FIRST (identical defect to the Nitro script). run() leaves
    # result["minted"] unset on NOT_MINTED, so the dry-run branch used to fire and exit 0 (success)
    # for a SEV-SNP mint that did NOT happen. A genuine dry-run keeps verdict "OK" and exits 0 below.
    if result["verdict"] == "NOT_MINTED":
        print(f"  -> {result['note']}")
        return 1
    if result.get("minted") is None:      # dry-run (verdict == "OK")
        print(f"  -> {result.get('note', 'no receipt minted')}")
        return 0

    mres = result["minted"]
    vf = result["veracity_floor"]
    if mres.get("mode") == "tier_upgrade":
        print(f"  UPGRADED {mres['receipt_id']}: re-tiered {mres['kry_re_tiered']} KRY "
              f"{mres['supersedes']} -> tee_attested  chain={mres['chain_hash']}")
        print("  (no new value minted — the saving was credited once at its original tier)")
        tf = result.get("tee_attested_fraction", {})
        if tf:
            print(f"  tee_attested_fraction: {tf['before']} -> {tf['after']}")
    else:
        print(f"  MINTED {mres['receipt_id']}: {mres['kry_minted']} KRY  tier={mres['evidence_tier']}  "
              f"chain={mres['chain_hash']}")
    print(f"  veracity_floor: {vf['before']} -> {vf['after']}")
    return 0


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):  # a cp1252 Windows console cannot encode the output glyphs
        if hasattr(_stream, "reconfigure"):
            _stream.reconfigure(errors="replace")
    sys.exit(main())
