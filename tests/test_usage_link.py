"""Constructed individual-link controls, not provider authentication evidence."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import kry_usage_link as tool  # noqa: E402


def rows(key='request-a', completion=400):
    identity = {'provider': 'anthropic', 'account_scope_sha256': 'a' * 64,
                'namespace': 'http_request_id', 'request_id_sha256': hashlib.sha256(key.encode()).hexdigest()}
    provider = {'identity': identity, 'model': 'claude-sonnet-5', 'service_tier': 'standard',
                'usage': {'prompt_tokens': 2000, 'completion_tokens': completion}}
    usage = dict(copy.deepcopy(provider), avoided_model='gh/claude-opus-4.8',
                 served_model='claude-sonnet-5', event_type='short_circuit')
    return usage, provider


def case(count=1):
    import kry.kry_mint as km
    import kry.kry_attest as ka
    usages, providers, links = [], [], []
    for index in range(count):
        usage, provider = rows(f'request-{index}')
        prepared = tool.prepare_link(usage, provider)
        receipt = km.mint(**prepared['mint_arguments'])
        assert receipt is not None
        usages.append(usage)
        providers.append(provider)
        links.append({'usage_index': index, 'provider_index': index, 'receipt_id': receipt.receipt_id,
                      'report_row': prepared['report_row']})
    return {'attestation': json.loads(ka.build_attestation().to_public_json()),
            'usage_rows': usages, 'provider_rows': providers, 'links': links}


def test_linked_rows_preserve_t0_and_exact_identity():
    inputs = case(2)
    result = tool.assess_links(**inputs)
    assert result['linked_rows'] == 2
    assert result['estimated_kry'] == 480
    assert result['excluded_attestation_receipts'] == 0
    assert result['source_truth_authenticated'] is False
    assert result['actual_charge_usd'] is None
    assert result['external_verified_savings'] is False
    assert inputs['attestation']['veracity']['veracity_floor'] == 0
    assert result['rows'][0]['identity_sha256'] != result['rows'][1]['identity_sha256']


def test_equal_token_requests_cannot_exchange_receipts():
    inputs = case(2)
    inputs['links'][0]['receipt_id'], inputs['links'][1]['receipt_id'] = (
        inputs['links'][1]['receipt_id'], inputs['links'][0]['receipt_id'])
    with pytest.raises(ValueError, match='receipt_opening'):
        tool.assess_links(**inputs)


def test_swap_provider_rows_cannot_pass_by_equal_tokens():
    inputs = case(2)
    inputs['provider_rows'].reverse()
    with pytest.raises(ValueError, match='identity_mismatch'):
        tool.assess_links(**inputs)


@pytest.mark.parametrize('field,value', [('provider', 'openai'), ('namespace', 'response_id'),
                                       ('account_scope_sha256', 'b' * 64), ('request_id_sha256', 'c' * 64)])
def test_scoped_identity_components_must_match(field, value):
    usage, provider = rows()
    provider['identity'][field] = value
    with pytest.raises(ValueError, match='identity_mismatch'):
        tool.prepare_link(usage, provider)


@pytest.mark.parametrize('key', ['usage_index', 'provider_index', 'receipt_id'])
def test_duplicate_references_rejected(key):
    inputs = case(2)
    inputs['links'][1][key] = inputs['links'][0][key]
    with pytest.raises(ValueError, match='duplicate_link_reference'):
        tool.assess_links(**inputs)


def test_duplicate_request_cannot_fund_two_receipts():
    from kry.kry_mint import mint
    from kry.kry_attest import build_attestation
    inputs = case()
    args = tool.prepare_link(inputs['usage_rows'][0], inputs['provider_rows'][0])['mint_arguments']
    args['idempotent_evidence'] = False
    receipt = mint(**args)
    assert receipt is not None
    inputs['attestation'] = json.loads(build_attestation().to_public_json())
    inputs['usage_rows'].append(copy.deepcopy(inputs['usage_rows'][0]))
    inputs['provider_rows'].append(copy.deepcopy(inputs['provider_rows'][0]))
    inputs['links'].append(dict(inputs['links'][0], usage_index=1, provider_index=1, receipt_id=receipt.receipt_id))
    with pytest.raises(ValueError, match='duplicate_request_identity'):
        tool.assess_links(**inputs)


@pytest.mark.parametrize('target', ['usage_rows', 'provider_rows'])
def test_full_supplied_row_coverage(target):
    inputs = case()
    inputs[target].append(rows('uncovered')[0 if target == 'usage_rows' else 1])
    with pytest.raises(ValueError, match='incomplete_supplied_row_coverage'):
        tool.assess_links(**inputs)


def test_legacy_receipt_cannot_be_retroactively_linked():
    from kry.kry_mint import mint
    from kry.kry_attest import build_attestation
    usage, provider = rows()
    prepared = tool.prepare_link(usage, provider)
    args = dict(prepared['mint_arguments'], evidence='old-id-only-evidence')
    receipt = mint(**args)
    assert receipt is not None
    with pytest.raises(ValueError, match='receipt_opening_mismatch'):
        tool.assess_links(json.loads(build_attestation().to_public_json()), [usage], [provider],
                          [{'usage_index': 0, 'provider_index': 0, 'receipt_id': receipt.receipt_id,
                            'report_row': prepared['report_row']}])


def test_other_chain_receipts_explicitly_excluded():
    from kry.kry_mint import mint
    from kry.kry_attest import build_attestation
    inputs = case()
    assert mint('cache_hit', 500, evidence='unrelated', avoided_model='gh/claude-opus-4.8')
    inputs['attestation'] = json.loads(build_attestation().to_public_json())
    result = tool.assess_links(**inputs)
    assert result['estimated_kry'] == 240
    assert result['excluded_attestation_receipts'] == 1


@pytest.mark.parametrize('field,value', [('estimated_kry', 999), ('estimated_usd', 0.5),
                                       ('served_model', 'other'), ('source_truth_authenticated', True),
                                       ('identity_sha256', 'f' * 64)])
def test_changed_report_row_rejected(field, value):
    inputs = case()
    inputs['links'][0]['report_row'][field] = value
    with pytest.raises(ValueError, match='report_row_mismatch'):
        tool.assess_links(**inputs)


@pytest.mark.parametrize('side,field,value', [
    (1, 'service_tier', 'priority'), (0, 'event_type', 'cache_hit'),
    (1, 'model', 'other'), (1, 'iterations', []), (1, 'cost_ticks', 1),
    (0, 'batch', True), (0, 'tools', []),
])
def test_unsupported_categories_cannot_be_hidden(side, field, value):
    pair = list(rows())
    pair[side][field] = value
    with pytest.raises(ValueError):
        tool.prepare_link(*pair)


@pytest.mark.parametrize('field,value', [('prompt_tokens', True), ('completion_tokens', 4.0),
                                       ('prompt_tokens', None), ('completion_tokens', -1),
                                       ('reasoning_tokens', 0), ('cache_read_input_tokens', 2)])
def test_unsupported_usage(field, value):
    usage, provider = rows()
    provider['usage'][field] = value
    with pytest.raises(ValueError):
        tool.prepare_link(usage, provider)


def test_rewritten_normalized_inputs_still_fail_receipt_opening():
    inputs = case()
    for key in ('usage_rows', 'provider_rows'):
        inputs[key][0]['identity']['request_id_sha256'] = 'd' * 64
    inputs['links'][0]['report_row'] = tool.prepare_link(inputs['usage_rows'][0], inputs['provider_rows'][0])['report_row']
    with pytest.raises(ValueError, match='receipt_opening_mismatch'):
        tool.assess_links(**inputs)


def test_invalid_attestation_rejected():
    inputs = case()
    inputs['attestation']['links'][0]['kry_minted'] = 900
    with pytest.raises(ValueError, match='attestation_invalid'):
        tool.assess_links(**inputs)


def test_cli_roundtrip_detects_rehashed_false_claim(tmp_path):
    inputs = case()
    script = Path(tool.__file__)
    command = [sys.executable, str(script), 'assess']
    for key, value in inputs.items():
        path = tmp_path / (key + '.json')
        path.write_text(json.dumps(value))
        command.extend(['--' + key.replace('_', '-'), str(path)])
    first = subprocess.run(command, text=True, capture_output=True)
    assert first.returncode == 0, first.stderr
    report = json.loads(first.stdout)
    path = tmp_path / 'report.json'
    path.write_text(first.stdout)
    verified = subprocess.run(command + ['--verify', str(path)], text=True, capture_output=True)
    assert verified.returncode == 0
    assert json.loads(verified.stdout)['ok'] is True
    report['external_verified_savings'] = True
    report.pop('report_sha256')
    report['report_sha256'] = tool._sha(report)
    path.write_text(json.dumps(report))
    failed = subprocess.run(command + ['--verify', str(path)], text=True, capture_output=True)
    assert failed.returncode == 1
    assert json.loads(failed.stdout)['ok'] is False


def test_cli_invalid_inputs_do_not_echo_private_text(tmp_path):
    p = tmp_path / 'secret.json'
    p.write_text('{"secret": "private-canary"}')
    result = subprocess.run([sys.executable, tool.__file__, 'prepare', '--usage-row', str(p),
                             '--provider-row', str(p)], capture_output=True, text=True)
    assert result.returncode == 2
    assert 'private-canary' not in result.stdout + result.stderr
    assert not result.stderr


@pytest.mark.parametrize('count', [2 ** 53 + 1, 10 ** 400])
def test_prepare_rejects_receipt_float_precision_loss(count):
    with pytest.raises(ValueError, match='receipt_token_range_unsupported'):
        tool.prepare_link(*rows(completion=count))


@pytest.mark.parametrize('provider', ['Anthropic', ' anthropic', 'claude', '', 'open-ai'])
def test_provider_aliases_rejected(provider):
    usage, native = rows()
    usage['identity']['provider'] = native['identity']['provider'] = provider
    with pytest.raises(ValueError, match='explicit_scoped_identity'):
        tool.prepare_link(usage, native)


def test_mixed_request_namespaces_do_not_imply_unique_events():
    from kry.kry_mint import mint
    from kry.kry_attest import build_attestation
    inputs = case()
    usage, provider = rows('different-alias-of-request')
    usage['identity']['namespace'] = provider['identity']['namespace'] = 'response_id'
    prepared = tool.prepare_link(usage, provider)
    receipt = mint(**prepared['mint_arguments'])
    assert receipt
    inputs['usage_rows'].append(usage)
    inputs['provider_rows'].append(provider)
    inputs['links'].append({'usage_index': 1, 'provider_index': 1, 'receipt_id': receipt.receipt_id,
                            'report_row': prepared['report_row']})
    inputs['attestation'] = json.loads(build_attestation().to_public_json())
    with pytest.raises(ValueError, match='mixed_identity_namespaces'):
        tool.assess_links(**inputs)
