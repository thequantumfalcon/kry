"""The JS corpus runner must reach the same verdicts on a CRLF checkout of vectors/.

`node verifiers/js/cli.mjs --vectors` re-extracts each vector's raw `input` text with a regex that
required `,\\n` before `"expected"`. On CRLF files the match failed, the runner fell back to
re-serializing the parsed input (which respells numbers such as 1.0), and four valid vectors came
back INVALID. The browser page seeds its demos with the same extraction.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_NODE = shutil.which("node")
_EXTRACT = re.compile(r'raw\.match\((/"input":.*?"expected"/)\)')


def _crlf_copy(tmp_path: Path) -> Path:
    dst = tmp_path / "vectors"
    shutil.copytree(_ROOT / "vectors", dst)
    for path in dst.rglob("*.json"):
        data = path.read_bytes().replace(b"\r\n", b"\n")
        path.write_bytes(data.replace(b"\n", b"\r\n"))
    return dst


@pytest.mark.skipif(_NODE is None, reason="node is not installed")
def test_corpus_runner_passes_on_crlf_vectors(tmp_path):
    vectors = _crlf_copy(tmp_path)
    assert b"\r\n" in (vectors / "manifest.json").read_bytes()
    result = subprocess.run([_NODE, str(_ROOT / "verifiers" / "js" / "cli.mjs"), "--vectors", str(vectors)],
                            capture_output=True, text=True, encoding="utf-8")
    assert result.returncode == 0, result.stdout + result.stderr
    assert re.search(r"\d+ passed, 0 failed", result.stdout), result.stdout


def test_web_page_uses_the_same_line_ending_tolerant_extraction():
    cli = _EXTRACT.findall((_ROOT / "verifiers" / "js" / "cli.mjs").read_text(encoding="utf-8"))
    web = _EXTRACT.findall((_ROOT / "verifiers" / "web" / "index.html").read_text(encoding="utf-8"))
    assert len(cli) == 1 and len(web) == 1
    assert cli == web
    assert r",\r?\n" in cli[0]
