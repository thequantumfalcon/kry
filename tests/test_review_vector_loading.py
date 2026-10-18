"""Malformed corpus wrappers must not enter the raw member scanner."""
import json
from pathlib import Path
import subprocess

import pytest


@pytest.mark.parametrize("raw", ["null", "[]", "7", '"input"'])
def test_nonobject_vector_wrapper_is_rejected(raw):
    module = Path(__file__).resolve().parents[1] / "verifiers/js/verify.mjs"
    script = (
        f'import {{ extractVector }} from {json.dumps(module.as_uri())};\n'
        'try { extractVector(process.argv[1]); process.exitCode = 1; }\n'
        'catch (error) { console.log(error.message); }\n'
    )
    result = subprocess.run(
        ["node", "--input-type=module", "-e", script, raw],
        capture_output=True, text=True, timeout=5,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "vector must be an object"
