"""The JS corpus runner must reach the same verdicts on a CRLF checkout of vectors/.

`node verifiers/js/cli.mjs --vectors` re-extracts each vector's raw `input` text with a regex that
required `,\\n` before `"expected"`. On CRLF files the match failed, the runner fell back to
re-serializing the parsed input (which respells numbers such as 1.0), and four valid vectors came
back INVALID. The browser page seeds its demos with the same extraction.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_NODE = shutil.which("node")


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


@pytest.mark.parametrize("newline", ["\n", "\r\n"], ids=["lf", "crlf"])
@pytest.mark.parametrize("nested_input", [False, True], ids=["plain", "anchor-input-key"])
def test_web_page_uses_the_same_line_ending_tolerant_extraction(tmp_path, newline, nested_input):
    assert _NODE is not None, "Node is required to compare CLI and browser extraction"
    # Execute the CLI runner and browser loadDemo with a verifier spy and minimal DOM.
    # A parsed/reserialized value would lose these number spellings.
    input_text = '{\n  "number": 1.0,\n  "large": 9007199254740993,\n  "text": "é"\n}'
    input_text = input_text.replace("\n", newline)
    anchor = '\n  "input_anchor": {"metadata": {"input": "nested"}},' if nested_input else ""
    raw = '{' + anchor + '\n  "input": ' + input_text + ',\n  "expected": {"verdict": "VALID"}\n}'
    raw = raw.replace("\r\n", "\n").replace("\n", newline)
    vectors = tmp_path / "vectors"
    (vectors / "hardening" / "action").mkdir(parents=True)
    (vectors / "primitives").mkdir()
    (vectors / "hardening" / "action" / "text.json").write_bytes(raw.encode("utf-8"))
    (vectors / "manifest.json").write_text(json.dumps({
        "vectors": [{"category": "hardening/action", "id": "text"}],
    }))
    for name in ("canon_f64", "canonical_json"):
        (vectors / "primitives" / f"{name}.json").write_text('{"cases": []}')
    script = r"""
import * as vm from "node:vm";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { pathToFileURL } from "node:url";
const [root, vectors] = process.argv.slice(1);
const raw = readFileSync(join(vectors, "hardening/action/text.json"), "utf8");
async function run(browser) {
  const inputs = [], elements = new Map();
  const element = () => ({ style: {}, replaceChildren() {},
    get value() { return this._value; },
    set value(text) { this._value = text.replace(/\r\n?/g, "\n"); },
  });
  const sandbox = {
    console: { log() {}, error() {} },
    process: { argv: ["node", "cli.mjs", "--vectors", vectors], exit(code) {
      if (code !== 0) throw new Error(`CLI exit ${code}`);
    } },
    document: { getElementById(id) {
      if (!elements.has(id)) elements.set(id, element());
      return elements.get(id);
    }, createElement: element },
    fetch: async () => ({ text: async () => raw }),
  };
  const context = vm.createContext(sandbox), cache = new Map();
  const capture = (text) => { inputs.push(text); return "VALID"; };
  const implementation = await import(pathToFileURL(join(root, "verifiers/js/verify.mjs")));
  const exports = { ...implementation, verdict: capture, verdictWithAnchor: capture,
    explain: (text) => ({ verdict: capture(text), reasons: [] }) };
  const verifier = new vm.SyntheticModule(Object.keys(exports), function() {
    for (const [key, value] of Object.entries(exports)) this.setExport(key, value);
  }, { context });
  async function link(specifier, referencing) {
    if (specifier.endsWith("verify.mjs")) return verifier;
    if (specifier.startsWith("node:")) {
      const native = await import(specifier);
      return new vm.SyntheticModule(Object.keys(native), function() {
        for (const [key, value] of Object.entries(native)) this.setExport(key, value);
      }, { context });
    }
    return load(new URL(specifier, referencing.identifier));
  }
  async function load(url, source = readFileSync(url, "utf8")) {
    if (cache.has(url.href)) return cache.get(url.href);
    const module = new vm.SourceTextModule(source, { context, identifier: url.href,
      initializeImportMeta(meta) { meta.url = url.href; } });
    cache.set(url.href, module);
    await module.link(link);
    return module;
  }
  const url = pathToFileURL(join(root, browser ? "verifiers/web/index.html" : "verifiers/js/cli.mjs"));
  let source = readFileSync(url, "utf8");
  if (browser) source = source.match(/<script type="module">([\s\S]*?)<\/script>/)[1] + "\nexport { loadDemo };";
  const module = await load(url, source);
  await module.evaluate();
  if (browser) { inputs.length = 0; await module.namespace.loadDemo("vector.json", "test", ""); }
  return inputs;
}
console.log(JSON.stringify({ cli: await run(false), browser: await run(true) }));
"""
    result = subprocess.run(
        [_NODE, "--experimental-vm-modules", "--input-type=module", "-e", script,
         str(_ROOT), str(vectors)], capture_output=True, text=True, encoding="utf-8",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    observed = json.loads(result.stdout)
    assert observed["cli"] == [input_text]
    assert observed["browser"] == observed["cli"]
