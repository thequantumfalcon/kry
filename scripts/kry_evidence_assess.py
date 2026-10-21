#!/usr/bin/env python3
"""Separate supplied-export analysis from exact attestation token consistency.

This opt-in sidecar does not modify artifact eligibility or authenticate a source.
All files are explicit caller inputs, bounded to16MiB each and snapshotted once.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import sys
import tempfile

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / 'scripts'))
import kry_reconcile as reconcile  # noqa: E402
import kry_verified_artifact as artifact  # noqa: E402
import kry_verify  # noqa: E402

MAX_INPUT_BYTES = 16 * 1024 * 1024
SCHEMA = 'kry_evidence_assessment/v1'
PROFILE = 'supplied-export-t1-exact/v1'
LIMITATIONS = (
    'Supplied export bytes are not authenticated provider records.',
    'Token-pair matching is not a unique request-ID join.',
    'Report-component and per-row binding to this attestation are not established.',
    'Actual account charges, invoice settlement and retained cash are not established.',
    'Causal savings, output correctness and customer benefit are not established.',
    'Review identity, independence and completion are not assessed or authenticated.',
    'The attestation T0 remainder is outside the exact attestation T1 scope.',
    'Legacy artifact eligibility does not authorize this assessment or a financial claim.',
)
_TOOL_FILES = (
    'scripts/kry_evidence_assess.py', 'scripts/kry_verify.py',
    'scripts/kry_reconcile.py', 'scripts/kry_verified_artifact.py',
    'scripts/kry_artifact_io.py', 'scripts/kry_artifact_privacy.py',
)


def _canonical(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _reject_constant(value):
    raise ValueError('nonfinite_json')


def _finite_float(text):
    value = float(text)
    if not math.isfinite(value):
        raise ValueError('nonfinite_json')
    return value


def _read_input(path):
    with Path(path).open('rb') as stream:
        data = stream.read(MAX_INPUT_BYTES + 1)
    if len(data) > MAX_INPUT_BYTES:
        raise ValueError('input_exceeds_16MiB')
    parsed = json.loads(data.decode('utf-8'), parse_constant=_reject_constant, parse_float=_finite_float)
    return data, parsed


def _snapshots(paths, directory):
    parsed, snapshots, hashes = {}, {}, {}
    for name, path in paths.items():
        if path is None:
            parsed[name], snapshots[name], hashes[name] = None, None, None
            continue
        data, parsed[name] = _read_input(path)
        target = directory / (name + '.json')
        target.write_bytes(data)
        snapshots[name] = target
        hashes[name] = {'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data)}
    return parsed, snapshots, hashes


def _manifest_shape(data):
    return (isinstance(data, dict) and isinstance(data.get('receipts'), list)
            and all(isinstance(row, dict)
                    and isinstance(row.get('receipt_hash'), str)
                    and isinstance(row.get('chain_hash'), str) for row in data['receipts']))


def _counter_shape(rows):
    if not rows:
        return False
    for row in rows:
        if not isinstance(row, dict):
            return False
        usage = row.get('usage', row)
        if not isinstance(usage, dict):
            return False
        prompts = [k for k in ('prompt_tokens', 'tokens_prompt', 'input_tokens') if k in usage]
        outputs = [k for k in ('completion_tokens', 'tokens_completion', 'output_tokens') if k in usage]
        if len(prompts) != 1 or len(outputs) != 1:
            return False
        cache_keys = [k for k in ('cache_read_input_tokens', 'cache_creation_input_tokens') if k in usage]
        if prompts[0] != 'input_tokens' and cache_keys:
            return False
        if any(type(usage[k]) is not int or usage[k] < 0 for k in prompts + outputs + cache_keys):
            return False
    return True


def _analysis(manifest, snapshot, export, config):
    if not _manifest_shape(manifest):
        return {'status': 'unavailable', 'reason_code': 't1_manifest_unavailable', 'reconciliation': None}, []
    if export is None:
        return {'status': 'unavailable', 'reason_code': 'provider_export_unavailable', 'reconciliation': None}, []
    try:
        rows = reconcile.load_t1_receipts(str(snapshot), since=config['since'], until=config['until'])
        if config['mode'] == 'aggregate':
            result = reconcile.aggregate_reconcile(rows, export, tol_pct=config['tolerance_pct'])
        else:
            result = reconcile.reconcile(rows, reconcile.provider_record_rows(export), tolerance=config['tolerance'])
    except (ValueError, TypeError, AttributeError, OverflowError):
        return {'status': 'invalid', 'reason_code': 'reconciliation_input_invalid', 'reconciliation': None}, []
    # Keep numeric semantics; legacy prose and caller-supplied IDs are not evidence.
    result.pop('note', None)
    if 'unmatched_receipts' in result:
        result['unmatched_receipts'] = [
            {'receipt_id_sha256': hashlib.sha256(_canonical(row['receipt_id']).encode()).hexdigest(),
             'metered': row['metered']} for row in result['unmatched_receipts']
        ]
    return {'status': 'complete', 'reason_code': None, 'reconciliation': result}, rows


def assess_evidence(attestation, *, t1_manifest=None, provider_export=None,
                    provider_export_manifest=None, mode='per-request', tolerance=0,
                    tolerance_pct=5.0, since=None, until=None):
    """Assess explicit files; unsupported exact scope is a completed diagnosis.

    No artifact/review is consumed or promoted. The fixed exact profile requires
    full T1 coverage even if diagnostic analysis selects a narrower time window.
    """
    if mode not in ('per-request', 'aggregate'):
        raise ValueError('invalid_mode')
    tolerance = reconcile._validate_tolerance(tolerance)
    tolerance_pct = reconcile._validate_tolerance_pct(tolerance_pct)
    since, until = reconcile._validate_window(since, until, require_complete=mode == 'aggregate')
    config = dict(mode=mode, tolerance=tolerance, tolerance_pct=tolerance_pct, since=since, until=until)
    paths = dict(attestation=attestation, t1_manifest=t1_manifest, provider_export=provider_export,
                 provider_export_manifest=provider_export_manifest)
    with tempfile.TemporaryDirectory(prefix='kry-assess-') as directory:
        parsed, snapshots, hashes = _snapshots(paths, Path(directory))
        att = parsed['attestation']
        valid, _ = kry_verify.verify_attestation(att)
        links = att['links'] if valid else []
        t1 = [row for row in links if row.get('evidence_tier') == 'provider_metered']
        t0 = [row for row in links if row.get('evidence_tier', 'self_reported') == 'self_reported']
        reasons = []
        if not valid:
            reasons.append('attestation_invalid')
        if any(row.get('evidence_tier', 'self_reported') not in ('self_reported', 'provider_metered')
               or row.get('supersedes') for row in links):
            reasons.append('unsupported_attestation_profile')
        if not any(row['kry_minted'] > 0 for row in t1):
            reasons.append('no_positive_t1_contribution')
        if any(row.get('hash_version') != 7 for row in t1):
            reasons.append('t1_v7_binding_required')
        if mode != 'per-request' or tolerance != 0:
            reasons.append('exact_mode_required')
        binding = False
        if valid and _manifest_shape(parsed['t1_manifest']):
            binding = artifact._t1_manifest_attestation_binding(snapshots['t1_manifest'], att)['ok']
        if not binding:
            reasons.append('t1_attestation_binding_invalid')
        analysis, assessed = _analysis(parsed['t1_manifest'], snapshots['t1_manifest'],
                                       parsed['provider_export'], config)
        expected = {(row['receipt_hash'], row['chain_hash']) for row in t1}
        actual = {(row['receipt_hash'], row['chain_hash']) for row in assessed}
        if not binding or actual != expected or len(assessed) != len(t1) or len(actual) != len(assessed):
            reasons.append('full_t1_identity_coverage_required')
        provenance = artifact._provider_export_manifest(
            snapshots['provider_export_manifest'], hashes, snapshots['provider_export'], mode,
            since=since, until=until)
        if not provenance['ok']:
            reasons.append('export_provenance_invalid')
        if not _counter_shape(reconcile.provider_record_rows(parsed['provider_export'])):
            reasons.append('provider_counter_shape_invalid')
        rec = analysis['reconciliation'] or {}
        if (rec.get('verdict') != 'RECONCILED' or rec.get('matched') != len(t1)
                or rec.get('t1_receipts') != len(t1) or rec.get('unmatched_receipts') != []
                or rec.get('matched_legacy_uncached_prompt') != 0):
            reasons.append('all_t1_exact_matches_required')
        t1_raw = math.fsum(row['kry_minted'] for row in t1) if valid else None
        t0_raw = math.fsum(row['kry_minted'] for row in t0) if valid else None
        supported = not reasons
        statement = (
            f'The attestation T1 contribution of {t1_raw!r} KRY has exact token-pair consistency '
            f'with the supplied export. The attestation T0 remainder of {t0_raw!r} KRY is outside '
            'that scope.' if supported else 'Exact supplied-export support for the attestation T1 contribution is not established.'
        )
        result = {
            'schema': SCHEMA, 'config': config, 'inputs': hashes,
            'tool_sha256': {name: hashlib.sha256((_ROOT / name).read_bytes()).hexdigest() for name in _TOOL_FILES},
            'analysis': analysis,
            'exact_assurance': {
                'profile': PROFILE, 'supported': supported,
                'status': 'supported' if supported else 'unsupported', 'reason_codes': reasons,
                'attestation_valid': valid, 't1_links': len(t1),
                'attestation_t1_kry_raw': t1_raw, 'attestation_t0_kry_raw': t0_raw,
                'attestation_t1_kry': round(t1_raw, 4) if valid else None,
                'attestation_t0_kry': round(t0_raw, 4) if valid else None,
                'statement': statement, 'limitations': list(LIMITATIONS),
            },
            'financial_claims': {'external_verified_savings': False, 'actual_charge_usd': None},
        }
        result['assessment_sha256'] = hashlib.sha256(_canonical(result).encode()).hexdigest()
        return result


def verify_assessment(assessment, **inputs):
    """Compare a saved assessment with a fresh explicit-input rebuild.

    Stored paths/configuration are never followed. Consistency is not authenticity;
    a self-consistent set of fabricated inputs can still satisfy this profile.
    """
    _, saved = _read_input(assessment)
    rebuilt = assess_evidence(**inputs)
    return {'ok': _canonical(saved) == _canonical(rebuilt), 'recomputed': rebuilt}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--attestation', required=True)
    parser.add_argument('--t1-manifest')
    parser.add_argument('--provider-export')
    parser.add_argument('--provider-export-manifest')
    parser.add_argument('--mode', choices=('per-request', 'aggregate'), default='per-request')
    parser.add_argument('--tolerance', type=int, default=0)
    parser.add_argument('--tolerance-pct', type=float, default=5.0)
    parser.add_argument('--since', type=float)
    parser.add_argument('--until', type=float)
    parser.add_argument('--verify', metavar='ASSESSMENT')
    args = vars(parser.parse_args(argv))
    saved = args.pop('verify')
    try:
        result = verify_assessment(saved, **args) if saved else assess_evidence(**args)
    except (OSError, ValueError, RecursionError):
        print(json.dumps({'error': 'invalid_or_unreadable_input'}))
        return 2
    print(json.dumps(result, indent=2, sort_keys=True, allow_nan=False))
    return int(saved is not None and not result['ok'])


if __name__ == '__main__':
    raise SystemExit(main())
