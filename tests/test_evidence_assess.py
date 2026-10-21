"""Constructed evidence controls; no fixture represents real provider evidence."""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]


def load_tool():
    path = _ROOT / 'scripts' / 'kry_evidence_assess.py'
    spec = importlib.util.spec_from_file_location('evidence_assess_test', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def mixed_case(tmp_path, *, t1_tokens=400):
    import kry.kry_attest as ka
    import kry.kry_mint as km

    assert km.mint('cache_hit', 500, evidence='constructed-cache',
                   avoided_model='gh/claude-opus-4.8') is not None
    if t1_tokens >= 1:
        assert km.mint('short_circuit', t1_tokens, evidence='constructed-provider',
                       avoided_model='gh/claude-opus-4.8', served_model='claude-sonnet-5',
                       evidence_tier=km.TIER_PROVIDER_METERED,
                       metered_tokens=[2000, 400]) is not None
    else:
        # The wrapper rejects subtoken issuance; the verified format allows it.
        from dataclasses import asdict
        previous = json.loads(km._MINT_LOG_PATH.read_text().splitlines()[-1])
        receipt = km.MintReceipt.create(
            receipt_id='KRY-00000002', event_type='short_circuit', tokens_saved=t1_tokens,
            detail='constructed format boundary', evidence_hash='a' * 64,
            previous_chain_hash=previous['chain_hash'], avoided_model='gh/claude-opus-4.8',
            served_model='claude-sonnet-5', evidence_tier=km.TIER_PROVIDER_METERED,
            metered_tokens=[2000, 400])
        with km._MINT_LOG_PATH.open('a') as stream:
            stream.write(json.dumps(asdict(receipt)) + '\n')
    att = tmp_path / 'attestation.json'
    att.write_text(ka.build_attestation().to_public_json())
    spec = importlib.util.spec_from_file_location('artifact_fixture', _ROOT / 'scripts/kry_verified_artifact.py')
    art = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(art)
    t1 = tmp_path / 't1.json'
    art.write_t1_manifest(km._MINT_LOG_PATH, t1)
    provider = tmp_path / 'provider.json'
    provider.write_text(json.dumps([{'prompt_tokens': 2000, 'completion_tokens': 400}]))
    manifest = tmp_path / 'provenance.json'
    manifest.write_text(json.dumps({
        'schema': 'kry_provider_export_manifest/v1', 'provider': 'Constructed provider',
        'export_source': 'constructed unit-test supplied declaration',
        'export_reference': 'constructed-example-01', 'date': '2026-06-09',
        'non_synthetic': True, 'reconciliation_mode': 'per-request',
        'provider_record_count': 1,
        'collection_window': {'since': '2026-06-09T00:00:00Z', 'until': '2026-06-09T01:00:00Z'},
        'artifact_inputs': {
            'provider_export_sha256': hashlib.sha256(provider.read_bytes()).hexdigest(),
            't1_manifest_sha256': hashlib.sha256(t1.read_bytes()).hexdigest(),
        },
    }))
    return dict(attestation=att, t1_manifest=t1, provider_export=provider,
                provider_export_manifest=manifest)


def test_mixed_contribution_has_separate_exact_scope(tmp_path):
    inputs = mixed_case(tmp_path)
    result = load_tool().assess_evidence(**inputs)
    assert result['schema'] == 'kry_evidence_assessment/v1'
    assert result['analysis']['reconciliation']['verdict'] == 'RECONCILED'
    exact = result['exact_assurance']
    assert exact['supported'] is True
    assert exact['attestation_t1_kry_raw'] == 240
    assert exact['attestation_t0_kry_raw'] == 500
    assert result['financial_claims']['external_verified_savings'] is False
    assert 'Report-component and per-row binding to this attestation are not established.' in exact['limitations']


def test_nonzero_tolerance_keeps_analysis_but_not_exact_scope(tmp_path):
    result = load_tool().assess_evidence(**mixed_case(tmp_path), tolerance=1)
    assert result['analysis']['status'] == 'complete'
    assert result['analysis']['reconciliation']['verdict'] == 'RECONCILED'
    assert result['exact_assurance']['supported'] is False
    assert 'exact_mode_required' in result['exact_assurance']['reason_codes']


# Mutations rebuild declared file hashes when testing semantic predicates; a stale
# hash alone must not mask the malformed evidence under test.
def rewrite(inputs, name, value, *, bind=True):
    inputs[name].write_text(json.dumps(value, separators=(',', ':')))
    if bind and name in ('provider_export', 't1_manifest'):
        manifest = json.loads(inputs['provider_export_manifest'].read_text())
        manifest['artifact_inputs'][name + '_sha256'] = hashlib.sha256(inputs[name].read_bytes()).hexdigest()
        inputs['provider_export_manifest'].write_text(json.dumps(manifest))


def append_receipt(inputs, *, tokens=400, metered=(2000, 400)):
    from dataclasses import asdict
    import kry.kry_attest as ka
    import kry.kry_mint as km

    previous = [json.loads(line) for line in km._MINT_LOG_PATH.read_text().splitlines()]
    row = km.MintReceipt.create(
        receipt_id=f'KRY-{len(previous) + 1:08d}', event_type='short_circuit',
        tokens_saved=tokens, detail='constructed receipt-format boundary',
        evidence_hash=hashlib.sha256(f'fixture-{len(previous)}'.encode()).hexdigest(),
        previous_chain_hash=previous[-1]['chain_hash'], avoided_model='gh/claude-opus-4.8',
        served_model='claude-sonnet-5', evidence_tier=km.TIER_PROVIDER_METERED,
        metered_tokens=list(metered))
    with km._MINT_LOG_PATH.open('a') as stream:
        stream.write(json.dumps(asdict(row)) + '\n')
    inputs['attestation'].write_text(ka.build_attestation().to_public_json())
    tool = load_tool()
    tool.artifact.write_t1_manifest(km._MINT_LOG_PATH, inputs['t1_manifest'])
    rewrite(inputs, 't1_manifest', json.loads(inputs['t1_manifest'].read_text()))
    return row


import pytest  # noqa: E402


@pytest.mark.parametrize('name', ['t1_manifest', 'provider_export', 'provider_export_manifest'])
def test_missing_evidence_cannot_support_exact_scope(tmp_path, name):
    inputs = mixed_case(tmp_path)
    inputs[name] = None
    result = load_tool().assess_evidence(**inputs)
    assert result['exact_assurance']['supported'] is False
    assert result['exact_assurance']['reason_codes']


@pytest.mark.parametrize('value', [True, 2000.0, None, -1])
def test_invalid_prompt_counter_preserved_as_rejection(tmp_path, value):
    inputs = mixed_case(tmp_path)
    rewrite(inputs, 'provider_export', [{'prompt_tokens': value, 'completion_tokens': 400}])
    result = load_tool().assess_evidence(**inputs)
    assert result['analysis']['status'] == 'invalid'
    assert result['exact_assurance']['supported'] is False
    assert 'provider_counter_shape_invalid' in result['exact_assurance']['reason_codes']


@pytest.mark.parametrize('row', [
    {'completion_tokens': 400},
    {'prompt_tokens': 2000},
    {'prompt_tokens': 2000, 'input_tokens': 1, 'completion_tokens': 400},
    {'prompt_tokens': 2000, 'completion_tokens': 400, 'output_tokens': 1},
    {'prompt_tokens': 2000, 'completion_tokens': 400, 'cache_read_input_tokens': 500},
])
def test_implicit_or_ambiguous_counter_semantics_not_exact(tmp_path, row):
    inputs = mixed_case(tmp_path)
    rewrite(inputs, 'provider_export', [row])
    result = load_tool().assess_evidence(**inputs)
    assert result['exact_assurance']['supported'] is False
    assert 'provider_counter_shape_invalid' in result['exact_assurance']['reason_codes']


def test_native_cache_full_prompt_supported_but_legacy_fallback_is_not(tmp_path):
    inputs = mixed_case(tmp_path)
    rewrite(inputs, 'provider_export', [{'input_tokens': 1000, 'cache_read_input_tokens': 600,
                                       'cache_creation_input_tokens': 400, 'output_tokens': 400}])
    exact = load_tool().assess_evidence(**inputs)
    assert exact['analysis']['reconciliation']['matched_legacy_uncached_prompt'] == 0
    assert exact['exact_assurance']['supported'] is True
    rewrite(inputs, 'provider_export', [{'input_tokens': 2000, 'cache_read_input_tokens': 600,
                                       'cache_creation_input_tokens': 400, 'output_tokens': 400}])
    legacy = load_tool().assess_evidence(**inputs)
    assert legacy['analysis']['reconciliation']['verdict'] == 'RECONCILED'
    assert legacy['analysis']['reconciliation']['matched_legacy_uncached_prompt'] == 1
    assert legacy['exact_assurance']['supported'] is False


def test_aggregate_analysis_remains_available_without_exact_claim(tmp_path):
    inputs = mixed_case(tmp_path)
    att = json.loads(inputs['attestation'].read_text())
    start = min(row['ts'] for row in att['links']) - 1
    end = max(row['ts'] for row in att['links']) + 1
    manifest = json.loads(inputs['provider_export_manifest'].read_text())
    manifest.update(reconciliation_mode='aggregate', collection_window={'since': start, 'until': end})
    rewrite(inputs, 'provider_export_manifest', manifest)
    tool = load_tool()
    result = tool.assess_evidence(**inputs, mode='aggregate', since=start, until=end, tolerance_pct=2.0)
    direct = tool.reconcile.aggregate_reconcile(
        tool.reconcile.load_t1_receipts(str(inputs['t1_manifest']), since=start, until=end),
        json.loads(inputs['provider_export'].read_text()), tol_pct=2.0)
    direct.pop('note')
    assert result['analysis']['status'] == 'complete'
    assert result['analysis']['reconciliation'] == direct
    assert result['exact_assurance']['supported'] is False
    assert 'exact_mode_required' in result['exact_assurance']['reason_codes']


@pytest.mark.parametrize('mutation', ['omit', 'duplicate', 'tokens', 'timestamp', 'identity'])
def test_manifest_identity_and_values_must_bind(tmp_path, mutation):
    inputs = mixed_case(tmp_path)
    manifest = json.loads(inputs['t1_manifest'].read_text())
    if mutation == 'omit':
        manifest['receipts'] = []
    elif mutation == 'duplicate':
        manifest['receipts'] *= 2
    elif mutation == 'tokens':
        manifest['receipts'][0]['metered_tokens'][0] = 1999
    elif mutation == 'timestamp':
        manifest['receipts'][0]['ts'] += 10
    else:
        manifest['receipts'][0]['chain_hash'] = 'f' * 64
    manifest['receipt_count'] = len(manifest['receipts'])
    rewrite(inputs, 't1_manifest', manifest)
    result = load_tool().assess_evidence(**inputs)
    assert result['exact_assurance']['supported'] is False
    assert 't1_attestation_binding_invalid' in result['exact_assurance']['reason_codes']


def test_filtered_subset_cannot_promote_full_attestation(tmp_path):
    inputs = mixed_case(tmp_path)
    extra = append_receipt(inputs)
    start = extra.ts
    manifest = json.loads(inputs['provider_export_manifest'].read_text())
    manifest['collection_window']['since'] = start
    manifest['collection_window']['until'] = start + 1
    rewrite(inputs, 'provider_export_manifest', manifest)
    result = load_tool().assess_evidence(**inputs, since=start, until=start + 1)
    assert result['analysis']['reconciliation']['reconciled_fraction'] == 1.0
    assert result['analysis']['reconciliation']['t1_receipts'] == 1
    assert result['exact_assurance']['t1_links'] == 2
    assert result['exact_assurance']['supported'] is False
    assert 'full_t1_identity_coverage_required' in result['exact_assurance']['reason_codes']


@pytest.mark.parametrize('field,value', [('non_synthetic', False), ('export_reference', 'TODO'),
                                         ('provider_record_count', True), ('date', '2099-01-01')])
def test_invalid_declared_provenance_not_exact(tmp_path, field, value):
    inputs = mixed_case(tmp_path)
    manifest = json.loads(inputs['provider_export_manifest'].read_text())
    manifest[field] = value
    rewrite(inputs, 'provider_export_manifest', manifest)
    result = load_tool().assess_evidence(**inputs)
    assert result['analysis']['reconciliation']['verdict'] == 'RECONCILED'
    assert result['exact_assurance']['supported'] is False
    assert 'export_provenance_invalid' in result['exact_assurance']['reason_codes']


def test_changed_export_without_binding_fails_despite_matching_counts(tmp_path):
    inputs = mixed_case(tmp_path)
    rewrite(inputs, 'provider_export', [{'usage': {'input_tokens': 2000, 'output_tokens': 400}}], bind=False)
    result = load_tool().assess_evidence(**inputs)
    assert result['analysis']['reconciliation']['verdict'] == 'RECONCILED'
    assert 'export_provenance_invalid' in result['exact_assurance']['reason_codes']
    assert result['exact_assurance']['supported'] is False


@pytest.mark.parametrize('field,value', [('tolerance', True), ('tolerance', 0.0), ('tolerance', None),
                                         ('tolerance', -1), ('tolerance_pct', False),
                                         ('tolerance_pct', float('inf')), ('since', True), ('mode', 'unknown')])
def test_strict_config_rejected_in_assessment_and_verify(tmp_path, field, value):
    inputs = mixed_case(tmp_path)
    tool = load_tool()
    saved = tmp_path / 'assessment.json'
    saved.write_text(json.dumps(tool.assess_evidence(**inputs)))
    with pytest.raises(ValueError):
        tool.assess_evidence(**inputs, **{field: value})
    with pytest.raises(ValueError):
        tool.verify_assessment(saved, **inputs, **{field: value})


@pytest.mark.parametrize('field,value', [('supported', False), ('attestation_t1_kry_raw', 740),
                                         ('limitations', []), ('statement', 'all savings authenticated')])
def test_saved_claim_tamper_detected_even_with_rehashed_body(tmp_path, field, value):
    inputs = mixed_case(tmp_path)
    tool = load_tool()
    result = tool.assess_evidence(**inputs)
    result['exact_assurance'][field] = value
    result.pop('assessment_sha256')
    result['assessment_sha256'] = hashlib.sha256(tool._canonical(result).encode()).hexdigest()
    saved = tmp_path / 'assessment.json'
    saved.write_text(json.dumps(result))
    assert tool.verify_assessment(saved, **inputs)['ok'] is False


def test_unchanged_rebuild_is_deterministic_and_saved_paths_are_never_followed(tmp_path):
    inputs = mixed_case(tmp_path)
    tool = load_tool()
    first = tool.assess_evidence(**inputs)
    assert first == tool.assess_evidence(**inputs)
    saved = tmp_path / 'assessment.json'
    saved.write_text(json.dumps(first))
    assert tool.verify_assessment(saved, **inputs)['ok'] is True
    first['command_inputs'] = {'attestation': '/must-not-read/secret.json'}
    first['financial_claims']['external_verified_savings'] = True
    saved.write_text(json.dumps(first))
    result = tool.verify_assessment(saved, **inputs)
    assert result['ok'] is False
    assert result['recomputed']['financial_claims']['external_verified_savings'] is False


def test_attestation_tamper_cannot_be_hidden_by_new_input_hash(tmp_path):
    inputs = mixed_case(tmp_path)
    att = json.loads(inputs['attestation'].read_text())
    att['links'][1]['kry_minted'] += 100
    rewrite(inputs, 'attestation', att)
    result = load_tool().assess_evidence(**inputs)
    assert result['exact_assurance']['attestation_valid'] is False
    assert result['exact_assurance']['supported'] is False
    assert result['exact_assurance']['attestation_t1_kry_raw'] is None


def test_json_last_member_wins_but_nonfinite_values_and_size_fail(tmp_path):
    inputs = mixed_case(tmp_path)
    tool = load_tool()
    inputs['provider_export'].write_text('[{"prompt_tokens": 1, "prompt_tokens": 2000, "completion_tokens": 400}]')
    manifest = json.loads(inputs['provider_export_manifest'].read_text())
    manifest['artifact_inputs']['provider_export_sha256'] = hashlib.sha256(inputs['provider_export'].read_bytes()).hexdigest()
    rewrite(inputs, 'provider_export_manifest', manifest)
    assert tool.assess_evidence(**inputs)['exact_assurance']['supported'] is True
    for value in ('NaN', 'Infinity', '1e999'):
        inputs['provider_export'].write_text('[{"prompt_tokens":' + value + '}]')
        with pytest.raises(ValueError):
            tool.assess_evidence(**inputs)
    inputs['provider_export'].write_bytes(b' ' * (tool.MAX_INPUT_BYTES + 1))
    with pytest.raises(ValueError, match='input_exceeds_16MiB'):
        tool.assess_evidence(**inputs)


def test_original_inputs_are_not_reread_after_snapshot(tmp_path, monkeypatch):
    inputs = mixed_case(tmp_path)
    tool = load_tool()
    original = inputs['provider_export'].read_bytes()
    binding = tool.artifact._t1_manifest_attestation_binding

    def change_original_after_snapshot(*args, **kwargs):
        inputs['provider_export'].write_text('[{"prompt_tokens": 1, "completion_tokens": 1}]')
        return binding(*args, **kwargs)

    monkeypatch.setattr(tool.artifact, '_t1_manifest_attestation_binding', change_original_after_snapshot)
    result = tool.assess_evidence(**inputs)
    assert result['exact_assurance']['supported'] is True
    assert result['inputs']['provider_export']['sha256'] == hashlib.sha256(original).hexdigest()
    assert result['analysis']['reconciliation']['matched'] == 1
    assert inputs['provider_export'].read_bytes() != original


def test_utf16_is_rejected_at_boundary_not_inconsistently_decoded(tmp_path):
    inputs = mixed_case(tmp_path)
    inputs['attestation'].write_bytes(inputs['attestation'].read_text().encode('utf-16'))
    with pytest.raises(ValueError):
        load_tool().assess_evidence(**inputs)


@pytest.mark.parametrize('attestation', [None, [], True, {'kind': 'kry_action_attestation', 'links': []}])
def test_non_savings_attestation_is_diagnosed_without_unsafe_dereference(tmp_path, attestation):
    inputs = mixed_case(tmp_path)
    rewrite(inputs, 'attestation', attestation)
    result = load_tool().assess_evidence(**inputs)
    assert result['exact_assurance']['attestation_valid'] is False
    assert result['exact_assurance']['supported'] is False
    assert result['exact_assurance']['attestation_t1_kry_raw'] is None


def test_zero_t1_cannot_promote_positive_t0(tmp_path):
    result = load_tool().assess_evidence(**mixed_case(tmp_path, t1_tokens=0))
    assert result['exact_assurance']['attestation_valid'] is True
    assert result['analysis']['reconciliation']['verdict'] == 'RECONCILED'
    assert result['exact_assurance']['attestation_t1_kry_raw'] == 0
    assert result['exact_assurance']['attestation_t0_kry_raw'] == 500
    assert result['exact_assurance']['supported'] is False
    assert 'no_positive_t1_contribution' in result['exact_assurance']['reason_codes']


def test_positive_raw_t1_survives_display_rounding(tmp_path):
    inputs = mixed_case(tmp_path, t1_tokens=1e-8)
    result = load_tool().assess_evidence(**inputs)
    assert result['exact_assurance']['supported'] is True
    assert result['exact_assurance']['attestation_t1_kry_raw'] > 0
    assert result['exact_assurance']['attestation_t1_kry'] == 0
    assert result['exact_assurance']['attestation_t0_kry_raw'] == 500
    rewrite(inputs, 'provider_export', [{'prompt_tokens': 2, 'completion_tokens': 4}])
    unsupported = load_tool().assess_evidence(**inputs)
    assert unsupported['exact_assurance']['attestation_t1_kry_raw'] > 0
    assert unsupported['exact_assurance']['supported'] is False


def test_tiny_positive_unmatched_holdout_cannot_disappear(tmp_path):
    inputs = mixed_case(tmp_path)
    append_receipt(inputs, tokens=1e-8, metered=(1, 1))
    result = load_tool().assess_evidence(**inputs)
    assert result['analysis']['reconciliation']['matched'] == 1
    assert len(result['analysis']['reconciliation']['unmatched_receipts']) == 1
    assert result['exact_assurance']['attestation_t1_kry_raw'] > 240
    assert result['exact_assurance']['attestation_t1_kry'] == 240
    assert result['exact_assurance']['supported'] is False


def test_extra_provider_rows_do_not_expand_supported_attestation_amount(tmp_path):
    inputs = mixed_case(tmp_path)
    rewrite(inputs, 'provider_export', [{'prompt_tokens': 2000, 'completion_tokens': 400},
                                      {'prompt_tokens': 999, 'completion_tokens': 999}])
    manifest = json.loads(inputs['provider_export_manifest'].read_text())
    manifest['provider_record_count'] = 2
    rewrite(inputs, 'provider_export_manifest', manifest)
    result = load_tool().assess_evidence(**inputs)
    assert result['exact_assurance']['supported'] is True
    assert result['exact_assurance']['attestation_t1_kry_raw'] == 240
    assert result['analysis']['reconciliation']['provider_records'] == 2
    assert result['analysis']['reconciliation']['matched'] == 1


def test_rounded_fraction_holdout_reaches_real_bounded_reconciliation(tmp_path, record_property):
    from dataclasses import asdict
    import kry.kry_attest as ka
    import kry.kry_mint as km

    inputs = mixed_case(tmp_path)
    cache = json.loads(km._MINT_LOG_PATH.read_text().splitlines()[0])
    previous = cache['chain_hash']
    with km._MINT_LOG_PATH.open('w') as stream:
        stream.write(json.dumps(cache) + '\n')
        for index in range(20001):
            receipt = km.MintReceipt.create(
                receipt_id=f'KRY-{index + 2:08d}', event_type='short_circuit', tokens_saved=1,
                detail='constructed rounded-fraction boundary',
                evidence_hash=hashlib.sha256(str(index).encode()).hexdigest(),
                previous_chain_hash=previous, avoided_model='gh/claude-opus-4.8',
                served_model='claude-sonnet-5', evidence_tier=km.TIER_PROVIDER_METERED,
                metered_tokens=[2000, 400])
            stream.write(json.dumps(asdict(receipt), separators=(',', ':')) + '\n')
            previous = receipt.chain_hash
    att = json.loads(ka.build_attestation().to_public_json())
    inputs['attestation'].write_text(json.dumps(att, separators=(',', ':')))
    tool = load_tool()
    tool.artifact.write_t1_manifest(km._MINT_LOG_PATH, inputs['t1_manifest'])
    rewrite(inputs, 't1_manifest', json.loads(inputs['t1_manifest'].read_text()))
    rewrite(inputs, 'provider_export', [{'prompt_tokens': 2000, 'completion_tokens': 400}] * 20000)
    provenance = json.loads(inputs['provider_export_manifest'].read_text())
    provenance['provider_record_count'] = 20000
    rewrite(inputs, 'provider_export_manifest', provenance)
    sizes = {name: path.stat().st_size for name, path in inputs.items()}
    record_property('input_bytes', json.dumps(sizes, sort_keys=True))
    assert all(size < tool.MAX_INPUT_BYTES for size in sizes.values())
    result = tool.assess_evidence(**inputs)
    assert result['exact_assurance']['attestation_valid'] is True
    assert result['analysis']['status'] == 'complete'
    reconciliation = result['analysis']['reconciliation']
    assert reconciliation['t1_receipts'] == 20001
    assert reconciliation['matched'] == 20000
    assert len(reconciliation['unmatched_receipts']) == 1
    assert reconciliation['reconciled_fraction'] == 1.0
    assert result['exact_assurance']['supported'] is False
    assert 'all_t1_exact_matches_required' in result['exact_assurance']['reason_codes']


def test_valid_legacy_v6_remains_verifiable_but_outside_exact_profile(tmp_path):
    import kry.kry_attest as ka
    import kry.kry_mint as km

    inputs = mixed_case(tmp_path)
    receipts = [json.loads(line) for line in km._MINT_LOG_PATH.read_text().splitlines()]
    previous = '0' * 64
    for row in receipts:
        row['hash_version'] = 6
        block = km._v4_public_block(**{key: row.get(key) for key in (
            'hash_version', 'tokens_saved', 'ts', 'evidence_tier', 'metered_tokens',
            'kry_minted', 'earn_rate', 'supersedes', 'receipt_id', 'event_type')})
        row['chain_hash'] = hashlib.sha256(f"{previous}:{row['receipt_hash']}:{block}".encode()).hexdigest()
        previous = row['chain_hash']
    km._MINT_LOG_PATH.write_text(''.join(json.dumps(row) + '\n' for row in receipts))
    inputs['attestation'].write_text(ka.build_attestation().to_public_json())
    tool = load_tool()
    tool.artifact.write_t1_manifest(km._MINT_LOG_PATH, inputs['t1_manifest'])
    rewrite(inputs, 't1_manifest', json.loads(inputs['t1_manifest'].read_text()))
    result = tool.assess_evidence(**inputs)
    assert result['exact_assurance']['attestation_valid'] is True
    assert result['analysis']['reconciliation']['verdict'] == 'RECONCILED'
    assert result['exact_assurance']['supported'] is False
    assert result['exact_assurance']['reason_codes'] == ['t1_v7_binding_required']
