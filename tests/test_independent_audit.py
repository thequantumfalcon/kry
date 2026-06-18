"""Independent-audit regressions — the one testable code change (F1 verifier caveat).

The other actionable findings are doc/UX: F2 (qualify the research_grade scope), F3 (disclose the
CPython float->JSON cross-language binding), F4 (lead the T2 wording with the trust ceiling). F5/F6
are disclosed/inert; F7/F8 are positive. This pins F1: the DEFAULT verifier (no --anchor) must
LOUDLY flag that any non-self_reported (anchored) fraction is operator-asserted — closing the
"VALID + veracity_floor 1.0, no warning" gap a genesis re-mint exploited.
"""
import importlib.util
import json


def _load_verifier():
    spec = importlib.util.spec_from_file_location("kry_verify_ia", "scripts/kry_verify.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_f1_default_verifier_warns_on_unanchored_anchored_fraction(capsys, tmp_path):
    import kry.kry_attest as a
    import kry.kry_mint as m
    # Forge a genesis re-mint: every link claims tee_attested -> veracity_floor 1.0.
    for i in range(3):
        m.mint("cache_hit", 1000, evidence=f"x{i}", evidence_tier=m.TIER_TEE_ATTESTED)
    forged = tmp_path / "forged.json"
    forged.write_text(a.build_attestation().to_public_json())
    kv = _load_verifier()

    rc = kv.main([str(forged)])                       # the natural command — NO --anchor
    out = capsys.readouterr().out
    assert rc == 0                                    # integrity/conservation/magnitude still hold ...
    assert "OPERATOR-ASSERTED" in out                 # ... but the anchored fraction is now caveated
    assert "--anchor" in out and "re-mint" in out

    # With a published anchor the caveat is replaced by the real anchor check (no false caveat).
    anchor = tmp_path / "anchor.json"
    anchor.write_text(json.dumps(m.export_chain_anchor()))
    kv.main([str(forged), "--anchor", str(anchor)])
    out2 = capsys.readouterr().out
    assert "OPERATOR-ASSERTED" not in out2
    assert "anchor check:" in out2
