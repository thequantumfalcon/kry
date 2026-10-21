#!/usr/bin/env python3
"""Check ordinary usage projection against supplied saved Anthropic JSON bytes.

This opt-in sidecar neither authenticates acquisition nor mints a receipt. Its
native-byte hashes are separate from the existing normalized-row commitment.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from kry_anthropic_usage import MODEL, estimate_anthropic_usage  # noqa: E402

MAX_INPUT_BYTES = 16 * 1024 * 1024
_RESPONSE_KEYS = {'id', 'type', 'role', 'model', 'content', 'usage', 'stop_reason',
                  'stop_sequence', 'container', 'diagnostics', 'stop_details'}
_ACQUISITION_KEYS = {'schema', 'endpoint', 'http_status', 'requested_model',
                     'request_id', 'observed_at_utc', 'account_scope_sha256'}


def _reject_number(value):
    raise ValueError('only_integer_json_numbers_supported')


def _members(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate_json_member')
        result[key] = value
    return result


def _load(raw):
    if not isinstance(raw, bytes) or len(raw) > MAX_INPUT_BYTES:
        raise ValueError('bounded_json_bytes_required')
    return json.loads(raw.decode('utf-8'), object_pairs_hook=_members,
                      parse_float=_reject_number, parse_constant=_reject_number)


def _read(path):
    descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_NONBLOCK', 0))
    with os.fdopen(descriptor, 'rb') as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError('regular_file_required')
        raw = stream.read(MAX_INPUT_BYTES + 1)
    if len(raw) > MAX_INPUT_BYTES:
        raise ValueError('input_exceeds_16MiB')
    return raw


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def project_anthropic_response(response_raw, acquisition_raw):
    """Project a supported saved response; acquisition fields remain declarations.

    A null account scope preserves the projection but emits no provider row.
    Complete native usage is validated before selecting counts. Optional unknown
    metadata stays unknown. No header/body identity equivalence is inferred.
    """
    response, acquisition = _load(response_raw), _load(acquisition_raw)
    if (not isinstance(acquisition, dict) or set(acquisition) != _ACQUISITION_KEYS
            or acquisition['schema'] != 'kry_anthropic_acquisition/v1'
            or acquisition['endpoint'] != '/v1/messages'
            or type(acquisition['http_status']) is not int or acquisition['http_status'] != 200
            or acquisition['requested_model'] != MODEL):
        raise ValueError('unsupported_acquisition_profile')
    observed = acquisition['observed_at_utc']
    if (not isinstance(observed, str) or not re.fullmatch(
            r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z', observed)):
        raise ValueError('explicit_utc_observation_required')
    datetime.fromisoformat(observed[:-1] + '+00:00')
    account = acquisition['account_scope_sha256']
    if account is not None and (not isinstance(account, str)
                                or not re.fullmatch(r'[0-9a-f]{64}', account)):
        raise ValueError('declared_account_digest_or_null_required')
    if not isinstance(response, dict) or set(response) - _RESPONSE_KEYS:
        raise ValueError('unsupported_response_profile')
    if any(response.get(key) is not None for key in ('container', 'diagnostics', 'stop_details')):
        raise ValueError('unsupported_response_metadata')
    sequence = response.get('stop_sequence')
    if response.get('stop_reason') == 'stop_sequence':
        if not isinstance(sequence, str) or not sequence:
            raise ValueError('reported_stop_sequence_required')
    elif sequence is not None:
        raise ValueError('contradictory_stop_sequence')
    # Validate the complete objects; filtering first would hide unsupported usage.
    estimate = estimate_anthropic_usage(response, request_id=acquisition['request_id'])
    usage = response['usage']
    if usage.get('service_tier') != 'standard' or usage.get('speed') != 'standard':
        raise ValueError('explicit_standard_metadata_required')
    for name in ('cache_creation_input_tokens', 'cache_read_input_tokens'):
        if type(usage.get(name)) is not int or usage[name] != 0:
            raise ValueError('explicit_zero_cache_required')
    pair = {'prompt_tokens': usage['input_tokens'], 'completion_tokens': usage['output_tokens']}
    request_digest = _sha(acquisition['request_id'].encode('utf-8'))
    projection = {
        'model': MODEL, 'service_tier': 'standard', 'usage': pair,
        'http_request_id_sha256': request_digest,
        'message_id_sha256': _sha(response['id'].encode('utf-8')),
        'stop_reason': estimate['stop_reason'], 'generation_status': estimate['generation_status'],
    }
    provider = None
    if account is not None:
        provider = {
            'identity': {'provider': 'anthropic', 'account_scope_sha256': account,
                         'namespace': 'http_request_id', 'request_id_sha256': request_digest},
            'model': MODEL, 'service_tier': 'standard', 'usage': dict(pair),
        }
    return {
        'schema': 'kry_anthropic_projection/v1', 'profile': 'supplied-anthropic-ordinary/v1',
        'inputs': {name: {'sha256': _sha(raw), 'bytes': len(raw)}
                   for name, raw in (('response', response_raw), ('acquisition', acquisition_raw))},
        'acquisition': {key: value for key, value in acquisition.items() if key != 'request_id'},
        'acquisition_basis': 'operator_declared',
        'projection': projection, 'observed_usage': estimate['observed_usage'],
        'provider_row': provider, 'native_projection_consistent': True,
        'source_truth_authenticated': False, 'receipt_binding_verified': False,
        'actual_charge_usd': None, 'external_verified_savings': False,
        'limitations': [
            'Consistency is relative to supplied saved bytes, not authenticated provider traffic.',
            'Account, HTTP status, endpoint, requested model and observation time are declarations.',
            'Absent or null optional usage metadata is unknown, not observed zero.',
            'Saved JSON hashes do not establish original network bytes or complete account traffic.',
            'Existing receipts commit normalized rows, not these native hashes or acquisition fields.',
            'No displacement, answer quality, billing, invoice, settlement or causal saving is proved.',
        ],
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description='Project supplied ordinary Anthropic JSON; not a bill.')
    parser.add_argument('response', type=Path)
    parser.add_argument('--acquisition', type=Path, required=True)
    parser.add_argument('--verify', type=Path, help='Rebuild and compare a saved projection report')
    args = parser.parse_args(argv)
    try:
        result = project_anthropic_response(_read(args.response), _read(args.acquisition))
        if args.verify:
            saved = _load(_read(args.verify))
            matches = json.dumps(saved, sort_keys=True) == json.dumps(result, sort_keys=True)
            print(json.dumps({'matches': matches, 'recomputed': result}, sort_keys=True))
            return int(not matches)
    except (OSError, ValueError, RecursionError, OverflowError):
        print(json.dumps({'supported': False, 'error': 'invalid_or_unsupported_projection_inputs'}))
        return 2
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
