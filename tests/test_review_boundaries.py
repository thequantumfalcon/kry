"""Drop into tests/ or set KRY_REVIEW_ROOT to exercise boundary findings on either tag."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(os.environ.get('KRY_REVIEW_ROOT', Path(__file__).resolve().parents[1]))


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'scripts' / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def sample(kind):
    return json.loads((ROOT / 'vectors' / kind / 'valid/single_self_reported.json').read_text())['input']


def cli(kind, language):
    if language == 'js':
        return ['node', str(ROOT / 'verifiers/js/cli.mjs')]
    return [sys.executable, str(ROOT / 'scripts' / ('kry_verify.py' if kind == 'savings' else 'kry_action_verify.py'))]


def test_raw_vector_anchor_integer_limit(tmp_path):
    """A raw-text vector must preserve the second input's 641-digit integer."""
    (tmp_path / 'hardening/anchor').mkdir(parents=True)
    (tmp_path / 'primitives').mkdir()
    for name in ('canon_f64', 'canonical_json', 'legal_multipliers'):
        (tmp_path / 'primitives' / (name + '.json')).write_text((ROOT / 'vectors/primitives' / (name + '.json')).read_text())
    att = sample('savings')
    anchor = {'schema': 'kry_chain_anchor/v1', 'count': att['links'][0]['seq'], 'tip': att['chain_head'], 'metadata': 'INTEGER_MARKER'}
    vector = {'id': 'raw_anchor_limit', 'description': 'Over-limit anchor is INVALID', 'input_anchor': anchor,
              'input_raw_text': json.dumps(att), 'expected': {'verdict': 'INVALID'}}
    text = json.dumps(vector, indent=2).replace('"INTEGER_MARKER"', '9' * 641)
    (tmp_path / 'hardening/anchor/raw_anchor_limit.json').write_text(text)
    (tmp_path / 'manifest.json').write_text(json.dumps({'vectors': [{'category': 'hardening/anchor', 'id': 'raw_anchor_limit'}]}))
    result = subprocess.run(cli('savings', 'js') + ['--vectors', str(tmp_path)], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize('kind', ['savings', 'action'])
@pytest.mark.parametrize('links', [[None], [{}], []])
def test_genesis_anchor_helper_checks_link_shapes(kind, links):
    """The prefix-only helper checks link shapes; an empty genesis prefix is valid."""
    if kind == 'savings':
        fn = load('kry_verify').verify_attestation_against_anchor
        anchor = {'schema': 'kry_chain_anchor/v1', 'count': 0, 'tip': '0' * 64}
    else:
        fn = load('kry_action_verify').verify_against_anchor
        anchor = {'kind': 'kry_action_anchor', 'count': 0, 'chain_tip': '0' * 64}
    result = fn({'links': links}, anchor)
    if links:
        assert result[0] is False and result[1], result
    else:
        assert result == (True, []), result


@pytest.mark.parametrize('kind', ['savings', 'action'])
def test_empty_links_cannot_satisfy_positive_anchor_count(kind):
    if kind == 'savings':
        fn = load('kry_verify').verify_attestation_against_anchor
        anchor = {'schema': 'kry_chain_anchor/v1', 'count': 1, 'tip': '0' * 64}
    else:
        fn = load('kry_action_verify').verify_against_anchor
        anchor = {'kind': 'kry_action_anchor', 'count': 1, 'chain_tip': '0' * 64}
    result = fn({'links': []}, anchor)
    assert result[0] is False and result[1], result


@pytest.mark.parametrize('kind', ['savings', 'action'])
@pytest.mark.parametrize('language', ['python', 'js'])
def test_explicit_empty_anchor_path_is_not_ignored(kind, language, tmp_path):
    att = tmp_path / 'attestation.json'
    att.write_text(json.dumps(sample(kind)))
    args = ['--anchor', ''] if language == 'python' else ['']
    result = subprocess.run(cli(kind, language) + [str(att)] + args, capture_output=True, text=True)
    assert result.returncode == 1 and 'VERDICT: INVALID' in result.stdout, result.stdout + result.stderr


def test_js_action_anchor_accepts_honest_document(tmp_path):
    att = sample('action')
    anchor = {'kind': 'kry_action_anchor', 'count': len(att['links']), 'chain_tip': att['chain_tip']}
    att_path = tmp_path / 'attestation.json'
    anchor_path = tmp_path / 'anchor.json'
    att_path.write_text(json.dumps(att))
    anchor_path.write_text(json.dumps(anchor))
    result = subprocess.run(cli('action', 'js') + [str(att_path), str(anchor_path)], capture_output=True, text=True)
    assert result.returncode == 0 and 'VERDICT: VALID' in result.stdout, result.stdout + result.stderr


@pytest.mark.parametrize('kind', ['savings', 'action'])
def test_cli_no_arguments_agree_and_print_a_verdict(kind):
    results = [subprocess.run(cli(kind, language), capture_output=True, text=True) for language in ('python', 'js')]
    observed = [(r.returncode, r.stdout, r.stderr) for r in results]
    assert results[0].returncode == results[1].returncode and all('VERDICT:' in r.stdout for r in results), observed


def test_js_help_is_help():
    result = subprocess.run(cli('savings', 'js') + ['--help'], capture_output=True, text=True)
    assert result.returncode == 0 and 'usage:' in result.stdout, result.stdout + result.stderr


@pytest.mark.parametrize('kind', ['savings', 'action'])
def test_python_missing_anchor_argument_prints_verdict(kind, tmp_path):
    path = tmp_path / 'attestation.json'
    path.write_text(json.dumps(sample(kind)))
    result = subprocess.run(cli(kind, 'python') + [str(path), '--anchor'], capture_output=True, text=True)
    assert result.returncode != 0 and 'VERDICT:' in result.stdout, result.stdout + result.stderr


def test_nested_anchor_input_key_preserves_honest_vector(tmp_path):
    """New regex extraction must not mistake a metadata key for the outer input key."""
    (tmp_path / 'hardening/anchor').mkdir(parents=True)
    (tmp_path / 'primitives').mkdir()
    for name in ('canon_f64', 'canonical_json', 'legal_multipliers'):
        (tmp_path / 'primitives' / (name + '.json')).write_text((ROOT / 'vectors/primitives' / (name + '.json')).read_text())
    att = sample('savings')
    anchor = {'schema': 'kry_chain_anchor/v1', 'count': att['links'][0]['seq'], 'tip': att['chain_head'],
              'metadata': {'before': 0, 'input': 1}}
    vector = {'id': 'nested_anchor_input', 'description': 'Honest metadata must not break anchor extraction',
              'input_anchor': anchor, 'input_raw_text': json.dumps(att), 'expected': {'verdict': 'VALID'}}
    (tmp_path / 'hardening/anchor/nested_anchor_input.json').write_text(json.dumps(vector, indent=2))
    (tmp_path / 'manifest.json').write_text(json.dumps({'vectors': [{'category': 'hardening/anchor', 'id': 'nested_anchor_input'}]}))
    result = subprocess.run(cli('savings', 'js') + ['--vectors', str(tmp_path)], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
