"""Constructed saved-byte projection controls, separate from source assurance."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import kry_anthropic_projection as tool  # noqa: E402


@pytest.fixture
def inputs():
    response = {
        'id': 'msg_synthetic', 'type': 'message', 'role': 'assistant', 'model': tool.MODEL,
        'content': [{'type': 'text', 'text': 'PRIVATE_CONTENT_SENTINEL'}],
        'stop_reason': 'end_turn', 'stop_sequence': None,
        'usage': {'input_tokens': 12, 'output_tokens': 4,
                  'cache_creation_input_tokens': 0, 'cache_read_input_tokens': 0,
                  'service_tier': 'standard', 'speed': 'standard'},
    }
    acquisition = {
        'schema': 'kry_anthropic_acquisition/v1', 'endpoint': '/v1/messages',
        'http_status': 200, 'requested_model': tool.MODEL, 'request_id': 'req_synthetic',
        'observed_at_utc': '2026-10-21T00:00:00.123456Z', 'account_scope_sha256': None,
    }
    return response, acquisition


def raw(value):
    return json.dumps(value).encode()


def project(inputs):
    return tool.project_anthropic_response(*(raw(value) for value in inputs))


def test_saved_bytes_and_metadata_are_bound_without_copying_content(inputs):
    result = project(inputs)
    for key, value in zip(('response', 'acquisition'), inputs):
        assert result['inputs'][key] == {'sha256': hashlib.sha256(raw(value)).hexdigest(),
                                        'bytes': len(raw(value))}
    assert result['projection']['usage'] == {'prompt_tokens': 12, 'completion_tokens': 4}
    assert result['observed_usage'] == inputs[0]['usage']
    assert result['provider_row'] is None
    assert result['acquisition_basis'] == 'operator_declared'
    assert result['native_projection_consistent'] is True
    assert result['receipt_binding_verified'] is False
    assert result['actual_charge_usd'] is None
    assert result['source_truth_authenticated'] is False
    assert result['external_verified_savings'] is False
    for private in ('PRIVATE_CONTENT_SENTINEL', 'req_synthetic', 'msg_synthetic'):
        assert private not in json.dumps(result)
    spaced = tool.project_anthropic_response(raw(inputs[0]) + b' ', raw(inputs[1]))
    assert spaced['projection'] == result['projection']
    assert spaced['inputs']['response'] != result['inputs']['response']


@pytest.mark.parametrize('field', ['cache_creation_input_tokens', 'cache_read_input_tokens',
                                    'service_tier', 'speed'])
@pytest.mark.parametrize('missing', [True, False])
def test_required_metadata_cannot_be_unknown(inputs, field, missing):
    if missing:
        del inputs[0]['usage'][field]
    else:
        inputs[0]['usage'][field] = None
    with pytest.raises(ValueError):
        project(inputs)


@pytest.mark.parametrize('field', ['input_tokens', 'output_tokens',
                                    'cache_creation_input_tokens', 'cache_read_input_tokens'])
@pytest.mark.parametrize('value', [True, 0.0, -1, '0'])
def test_counts_are_exact_json_integers(inputs, field, value):
    inputs[0]['usage'][field] = value
    with pytest.raises(ValueError):
        project(inputs)


@pytest.mark.parametrize('extra', [
    {'iterations': []}, {'unknown_fee': 0}, {'cache_read_input_tokens': 1},
    {'cache_creation_input_tokens': 1}, {'server_tool_use': {'web_search_requests': 1}},
    {'output_tokens_details': {'thinking_tokens': 1}}, {'service_tier': 'priority'},
    {'speed': 'fast'}, {'cache_creation': {'ephemeral_5m_input_tokens': 1,
                                         'ephemeral_1h_input_tokens': 0}},
])
def test_categories_are_checked_before_projection(inputs, extra):
    inputs[0]['usage'].update(extra)
    with pytest.raises(ValueError):
        project(inputs)


def test_optional_absent_null_and_zero_are_distinct(inputs):
    usage = inputs[0]['usage']
    usage.update(server_tool_use=None, inference_geo='not_available',
                 output_tokens_details={'thinking_tokens': 0})
    result = project(inputs)
    assert result['observed_usage'] == usage
    assert 'cache_creation' not in result['observed_usage']
    assert result['observed_usage']['server_tool_use'] is None
    assert result['observed_usage']['output_tokens_details']['thinking_tokens'] == 0


@pytest.mark.parametrize('field', ['diagnostics', 'stop_details', 'container'])
def test_new_metadata_is_not_silently_discarded(inputs, field):
    inputs[0][field] = None
    assert project(inputs)['native_projection_consistent'] is True
    inputs[0][field] = {}
    with pytest.raises(ValueError):
        project(inputs)


def test_unknown_root_field_and_nontyped_message_are_refused(inputs):
    inputs[0]['unknown_billing_field'] = None
    with pytest.raises(ValueError):
        project(inputs)
    for value in (None, [], True):
        with pytest.raises(ValueError):
            tool.project_anthropic_response(raw(value), raw(inputs[1]))


@pytest.mark.parametrize('field,value', [
    ('schema', 'other'), ('endpoint', '/v1/messages/batches'), ('http_status', True),
    ('http_status', 201), ('requested_model', 'claude-haiku-4-5'),
    ('request_id', 'msg_synthetic'), ('request_id', ''), ('account_scope_sha256', 'a' * 63),
    ('account_scope_sha256', 'A' * 64), ('observed_at_utc', '2026-10-21'),
    ('observed_at_utc', '2026-10-21T00:00:00+00:00'),
    ('observed_at_utc', '2026-02-30T00:00:00Z'), ('extra', 'unsupported'),
])
def test_acquisition_declarations_require_supported_shape(inputs, field, value):
    inputs[1][field] = value
    with pytest.raises(ValueError):
        project(inputs)


def test_acquisition_requires_every_field_and_object(inputs):
    for missing in inputs[1]:
        changed = {key: value for key, value in inputs[1].items() if key != missing}
        with pytest.raises(ValueError):
            tool.project_anthropic_response(raw(inputs[0]), raw(changed))
    with pytest.raises(ValueError):
        tool.project_anthropic_response(raw(inputs[0]), b'[]')


@pytest.mark.parametrize('stop,status', [('max_tokens', 'truncated'), ('refusal', 'refused'),
                                       ('model_context_window_exceeded', 'truncated')])
def test_outcome_does_not_turn_usage_into_quality_or_a_charge(inputs, stop, status):
    inputs[0]['stop_reason'] = stop
    result = project(inputs)
    assert result['projection']['generation_status'] == status
    assert result['projection']['usage']['completion_tokens'] == 4
    assert result['actual_charge_usd'] is None


def test_sequence_stopping_metadata_is_consistent_and_private(inputs):
    inputs[0]['stop_sequence'] = 'PRIVATE_STOP_SENTINEL'
    with pytest.raises(ValueError):
        project(inputs)
    inputs[0]['stop_reason'] = 'stop_sequence'
    result = project(inputs)
    assert result['projection']['generation_status'] == 'stopped_by_sequence'
    assert 'PRIVATE_STOP_SENTINEL' not in json.dumps(result)
    inputs[0]['stop_sequence'] = None
    with pytest.raises(ValueError):
        project(inputs)


@pytest.mark.parametrize('malformed', [b'\xff', b'{', b'{"a":1,"a":2}', b'{"a":NaN}',
                                      b'{"a":Infinity}', b'{"a":1.0}'])
def test_json_refusals_apply_to_both_input_documents(inputs, malformed):
    for index in range(2):
        values = [raw(value) for value in inputs]
        values[index] = malformed
        with pytest.raises(ValueError):
            tool.project_anthropic_response(*values)


def test_byte_bounds_apply_to_direct_api_and_reader(inputs, tmp_path):
    values = [raw(value) for value in inputs]
    for index in range(2):
        padded = values[index] + b' ' * (tool.MAX_INPUT_BYTES - len(values[index]))
        at_bound = list(values)
        at_bound[index] = padded
        assert tool.project_anthropic_response(*at_bound)['native_projection_consistent']
        at_bound[index] += b' '
        with pytest.raises(ValueError):
            tool.project_anthropic_response(*at_bound)
    path = tmp_path / 'oversize.json'
    path.write_bytes(b' ' * (tool.MAX_INPUT_BYTES + 1))
    with pytest.raises(ValueError):
        tool._read(path)


def test_cli_rebuild_catches_tampering_and_never_leaks_content(inputs, tmp_path):
    response, acquisition, report = [tmp_path / name for name in ('response', 'acquisition', 'report')]
    response.write_bytes(raw(inputs[0]))
    acquisition.write_bytes(raw(inputs[1]))
    command = [sys.executable, tool.__file__, str(response), '--acquisition', str(acquisition)]
    completed = subprocess.run(command, capture_output=True, timeout=10)
    assert completed.returncode == 0
    result = json.loads(completed.stdout)
    report.write_bytes(completed.stdout)
    verify = command + ['--verify', str(report)]
    assert subprocess.run(verify, capture_output=True, timeout=10).returncode == 0
    for key, value in [('native_projection_consistent', 1), ('actual_charge_usd', '0'),
                        ('source_truth_authenticated', True)]:
        report.write_bytes(raw(dict(result, **{key: value})))
        assert subprocess.run(verify, capture_output=True, timeout=10).returncode == 1
    report.write_bytes(raw(result))
    inputs[1]['observed_at_utc'] = '2026-10-22T00:00:00Z'
    acquisition.write_bytes(raw(inputs[1]))
    assert subprocess.run(verify, capture_output=True, timeout=10).returncode == 1
    response.write_bytes(b'[' * 2000)
    failed = subprocess.run(command, capture_output=True, timeout=10)
    assert failed.returncode == 2
    assert json.loads(failed.stdout)['supported'] is False
    assert b'PRIVATE_CONTENT_SENTINEL' not in completed.stdout + failed.stdout + failed.stderr


def test_fifo_cli_refuses_without_waiting_for_writer(inputs, tmp_path):
    if not hasattr(os, 'mkfifo'):
        pytest.skip('POSIX FIFO API is unavailable')
    fifo, acquisition = tmp_path / 'fifo', tmp_path / 'acquisition'
    os.mkfifo(fifo)
    acquisition.write_bytes(raw(inputs[1]))
    result = subprocess.run([sys.executable, tool.__file__, str(fifo), '--acquisition',
                             str(acquisition)], capture_output=True, timeout=2)
    assert result.returncode == 2
    assert json.loads(result.stdout)['supported'] is False
