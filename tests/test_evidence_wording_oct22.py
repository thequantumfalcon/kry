"""Evidence adapters identify the external checks they rely on."""
import importlib.util
import json
from pathlib import Path

from kry import kry_attest, kry_mint


_ROOT = Path(__file__).resolve().parents[1]


def _script(name):
    spec = importlib.util.spec_from_file_location(name, _ROOT / 'scripts' / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_tlsn_adapter_discloses_trusted_flag_and_field_only_pin(tmp_path, capsys):
    presentation = {
        'verified': True, 'server_name': 'openrouter.ai', 'notary_key': 'abcd',
        'recv': 'HTTP/1.1 200 OK\r\n\r\n' + json.dumps({
            'usage': {'prompt_tokens': 13, 'completion_tokens': 21}}),
    }
    path = tmp_path / 'presentation.json'
    path.write_text(json.dumps(presentation))
    result = _script('kry_tlsn_verify').main([
        str(path), '--server', 'openrouter.ai', '--notary-key', 'abcd', '--dry-run'])
    output = capsys.readouterr().out
    assert result == 0
    assert 'verified flag is trusted' in output
    assert 'signatures are not checked here' in output
    assert 'attestation_verify' in output
    assert 'server (reported)' in output
    assert 'field match only' in output
    assert 'server (verified)' not in output
    assert 'validated + parsed' not in output
    assert not kry_mint._MINT_LOG_PATH.exists()


def test_tlsn_adapter_still_rejects_false_verification_flag(tmp_path, capsys):
    path = tmp_path / 'unverified.json'
    path.write_text(json.dumps({'verified': False}))
    assert _script('kry_tlsn_verify').main([str(path), '--dry-run']) == 1
    assert 'REJECTED' in capsys.readouterr().out
    assert not kry_mint._MINT_LOG_PATH.exists()


def test_stranger_points_to_the_cryptographic_tlsn_verifier(tmp_path, capsys):
    assert kry_mint.mint('cache_hit', 1000, evidence='wording control',
                         evidence_tier=kry_mint.TIER_PROVIDER_METERED,
                         metered_tokens=[40, 60]) is not None
    path = tmp_path / 'attestation.json'
    path.write_text(kry_attest.build_attestation().to_public_json())
    assert _script('kry_verify').main([str(path)]) == 0
    output = capsys.readouterr().out
    assert 'attestation_verify' in output
    assert 'kry_tlsn_verify trusts' in output
    assert 'kry_tlsn_verify to independently check' not in output
