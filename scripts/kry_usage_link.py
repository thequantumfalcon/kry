#!/usr/bin/env python3
"""Bind individual ordinary-displacement report rows to supplied usage and receipts.

This optional profile commits rows before minting. It does not authenticate a
provider, convert imported logs to T1, or establish invoice or causal savings.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import sys

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / 'scripts'))
sys.path.insert(0, str(_ROOT / 'src'))
from kry_evidence_assess import _canonical, _read_input  # noqa: E402
import kry_verify  # noqa: E402
from kry.kry_token import EARN_RATES, USD_PER_KRY, net_value_multiplier  # noqa: E402

PROFILE = 'supplied-ordinary-displacement-row/v1'
_ID_KEYS = {'provider', 'account_scope_sha256', 'namespace', 'request_id_sha256'}
_USAGE_KEYS = {'prompt_tokens', 'completion_tokens'}
_PROVIDER_KEYS = {'identity', 'model', 'usage', 'service_tier'}
_LOG_KEYS = _PROVIDER_KEYS | {'avoided_model', 'served_model', 'event_type'}
LIMITATIONS = [
    'Supplied records and identifier declarations are not authenticated.',
    'Projection from native provider captures to these normalized rows is not verified.',
    'Support covers only the supplied individual report rows; other receipts are excluded.',
    'Legacy aggregate reports do not inherit this row binding.',
    'Amounts are KRY output-price benchmark estimates, not actual provider charges.',
    'Invoices, credits, settlement, causal savings and customer benefit are not established.',
    'A whole-chain rewrite needs a separately trusted earlier anchor to be detected.',
]


def _sha(value):
    return hashlib.sha256(_canonical(value).encode()).hexdigest()


def _digest(value):
    return (isinstance(value, str) and len(value) == 64
            and all(c in '0123456789abcdef' for c in value))


def _identity(row):
    value = row.get('identity')
    if (not isinstance(value, dict) or set(value) != _ID_KEYS
            or not _digest(value.get('account_scope_sha256'))
            or not _digest(value.get('request_id_sha256'))
            or value.get('namespace') not in ('http_request_id', 'response_id')
            or value.get('provider') not in ('anthropic', 'openai', 'xai')):
        raise ValueError('explicit_scoped_identity_required')
    return value


def _usage(row):
    value = row.get('usage')
    if (not isinstance(value, dict) or set(value) != _USAGE_KEYS
            or any(type(v) is not int or v < 0 for v in value.values())):
        raise ValueError('ordinary_explicit_usage_required')
    return [value['prompt_tokens'], value['completion_tokens']]


def prepare_link(usage_row, provider_row):
    """Return a content-free report row and arguments for existing mint().

    Rows use this profile's explicit normalized envelope, not arbitrary provider
    responses. The caller must preserve original captures separately and must not
    omit observed categories to fit this profile. Unknown fields fail closed.
    """
    for row, keys in ((usage_row, _LOG_KEYS), (provider_row, _PROVIDER_KEYS)):
        if (not isinstance(row, dict) or set(row) != keys
                or row.get('service_tier') != 'standard'
                or not isinstance(row.get('model'), str) or not row['model']):
            raise ValueError('unsupported_row_profile')
    identity = _identity(usage_row)
    if identity != _identity(provider_row):
        raise ValueError('identity_mismatch')
    pair = _usage(usage_row)
    if pair != _usage(provider_row):
        raise ValueError('token_mismatch')
    served, avoided = usage_row['served_model'], usage_row['avoided_model']
    if (usage_row['event_type'] != 'short_circuit' or not isinstance(avoided, str)
            or not avoided or served != usage_row['model'] or served != provider_row['model']
            or avoided == served or pair[1] < 1):
        raise ValueError('ordinary_displacement_required')
    # Receipt tokens use binary64; reject a count that mint would silently round.
    try:
        receipt_tokens = float(pair[1])
    except OverflowError as exc:
        raise ValueError('receipt_token_range_unsupported') from exc
    if not math.isfinite(receipt_tokens) or int(receipt_tokens) != pair[1]:
        raise ValueError('receipt_token_range_unsupported')
    # This is the existing KRY benchmark arithmetic, not a category billing model.
    amount = pair[1] * EARN_RATES['short_circuit'] * net_value_multiplier(avoided, served)
    if not math.isfinite(amount) or amount <= 0:
        raise ValueError('positive_displacement_required')
    report_row = {
        'profile': PROFILE, 'usage_row_sha256': _sha(usage_row),
        'provider_row_sha256': _sha(provider_row), 'identity_sha256': _sha(identity),
        'event_type': 'short_circuit', 'metered_tokens': pair,
        'avoided_model': avoided, 'served_model': served,
        'estimated_kry': amount, 'estimated_usd': amount * USD_PER_KRY,
        'source_truth_authenticated': False, 'normalization_verified': False,
    }
    evidence = _canonical({'domain': PROFILE, 'report_row': report_row})
    return {
        'report_row': report_row,
        'mint_arguments': {
            'event_type': 'short_circuit', 'tokens_saved': pair[1],
            'evidence': evidence, 'avoided_model': avoided, 'served_model': served,
            'evidence_tier': 'self_reported', 'idempotent_evidence': True,
        },
    }


def assess_links(attestation, usage_rows, provider_rows, links):
    """Verify full supplied-row coverage; extra attestation links stay out of scope.

    Link rows contain usage_index, provider_index, receipt_id and report_row.
    Recompute the receipt content hash; the short public evidence seal alone is
    not a sufficient opening. This profile accepts only newly committed v7 T0
    displacement receipts, retaining their original source-assurance tier.
    """
    valid, _ = kry_verify.verify_attestation(attestation)
    if not valid:
        raise ValueError('attestation_invalid')
    if not all(isinstance(rows, list) and rows for rows in (usage_rows, provider_rows, links)):
        raise ValueError('nonempty_rows_required')
    by_id = {row.get('receipt_id'): row for row in attestation['links']}
    id_counts = Counter(row.get('receipt_id') for row in attestation['links'])
    seen_usage, seen_provider, seen_receipts, seen_identity = set(), set(), set(), set()
    namespaces = {}
    report_rows = []
    for item in links:
        if (not isinstance(item, dict)
                or set(item) != {'usage_index', 'provider_index', 'receipt_id', 'report_row'}):
            raise ValueError('invalid_link_shape')
        ui, pi, rid = item['usage_index'], item['provider_index'], item['receipt_id']
        if (type(ui) is not int or not 0 <= ui < len(usage_rows)
                or type(pi) is not int or not 0 <= pi < len(provider_rows)
                or not isinstance(rid, str) or rid not in by_id):
            raise ValueError('invalid_link_reference')
        if ui in seen_usage or pi in seen_provider or rid in seen_receipts:
            raise ValueError('duplicate_link_reference')
        if id_counts[rid] != 1:
            raise ValueError('ambiguous_receipt_identity')
        prepared = prepare_link(usage_rows[ui], provider_rows[pi])
        identity = usage_rows[ui]['identity']
        scope = (identity['provider'], identity['account_scope_sha256'])
        if namespaces.setdefault(scope, identity['namespace']) != identity['namespace']:
            raise ValueError('mixed_identity_namespaces')
        row, arguments = prepared['report_row'], prepared['mint_arguments']
        if row['identity_sha256'] in seen_identity:
            raise ValueError('duplicate_request_identity')
        if _canonical(item['report_row']) != _canonical(row):
            raise ValueError('report_row_mismatch')
        receipt = by_id[rid]
        if (receipt.get('hash_version') != 7 or receipt.get('supersedes')
                or receipt.get('event_type') != 'short_circuit'
                or receipt.get('evidence_tier') != 'self_reported'
                or receipt.get('metered_tokens') is not None
                or receipt.get('tokens_saved') != arguments['tokens_saved']
                or receipt.get('earn_rate') != EARN_RATES['short_circuit']
                or receipt.get('kry_minted') != row['estimated_kry']):
            raise ValueError('receipt_semantics_mismatch')
        evidence_hash = hashlib.sha256(arguments['evidence'].encode()).hexdigest()
        content = (f"short_circuit:{float(receipt['tokens_saved'])}:{float(receipt['ts'])}:"
                   f'{evidence_hash}:self_reported:null')
        if hashlib.sha256(content.encode()).hexdigest() != receipt['receipt_hash']:
            raise ValueError('receipt_opening_mismatch')
        seen_usage.add(ui)
        seen_provider.add(pi)
        seen_receipts.add(rid)
        seen_identity.add(row['identity_sha256'])
        report_rows.append(dict(row, receipt_id=rid, receipt_hash=receipt['receipt_hash'],
                                chain_hash=receipt['chain_hash']))
    if len(seen_usage) != len(usage_rows) or len(seen_provider) != len(provider_rows):
        raise ValueError('incomplete_supplied_row_coverage')
    report_rows.sort(key=lambda row: row['receipt_id'])
    result = {
        'schema': 'kry_linked_report/v1', 'profile': PROFILE, 'supported': True,
        'rows': report_rows, 'linked_rows': len(report_rows),
        'excluded_attestation_receipts': len(attestation['links']) - len(seen_receipts),
        'estimated_kry': sum(row['estimated_kry'] for row in report_rows),
        'source_truth_authenticated': False, 'normalization_verified': False, 'actual_charge_usd': None,
        'external_verified_savings': False, 'limitations': list(LIMITATIONS),
    }
    result['report_sha256'] = _sha(result)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    prepare = commands.add_parser('prepare', help='prepare a prospective receipt commitment')
    prepare.add_argument('--usage-row', required=True)
    prepare.add_argument('--provider-row', required=True)
    assess = commands.add_parser('assess', help='verify explicit linked rows')
    assess.add_argument('--attestation', required=True)
    assess.add_argument('--usage-rows', required=True)
    assess.add_argument('--provider-rows', required=True)
    assess.add_argument('--links', required=True)
    assess.add_argument('--verify', metavar='LINKED_REPORT')
    args = parser.parse_args(argv)
    try:
        if args.command == 'prepare':
            result = prepare_link(_read_input(args.usage_row)[1], _read_input(args.provider_row)[1])
        else:
            paths = {key: getattr(args, key) for key in ('attestation', 'usage_rows', 'provider_rows', 'links')}
            snapshots = {key: _read_input(path) for key, path in paths.items()}
            result = assess_links(**{key: value[1] for key, value in snapshots.items()})
            result['inputs'] = {key: {'sha256': hashlib.sha256(value[0]).hexdigest(), 'bytes': len(value[0])}
                               for key, value in snapshots.items()}
            result.pop('report_sha256')
            result['report_sha256'] = _sha(result)
            if args.verify:
                matches = _canonical(_read_input(args.verify)[1]) == _canonical(result)
                print(json.dumps({'ok': matches, 'recomputed': result}, sort_keys=True, allow_nan=False))
                return int(not matches)
    except (OSError, ValueError, RecursionError, OverflowError):
        print(json.dumps({'supported': False, 'error': 'invalid_or_unsupported_link_inputs'}))
        return 2
    print(json.dumps(result, sort_keys=True, indent=2, allow_nan=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
