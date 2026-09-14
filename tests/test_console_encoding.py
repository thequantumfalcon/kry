"""A non-UTF-8 console (Windows cp1252) must not crash the CLIs.

The report/verifier output uses glyphs cp1252 cannot encode (p-hat, arrows, box rules), so an
unguarded `print` raised UnicodeEncodeError and kry_verify.py died before printing a VERDICT.
PYTHONIOENCODING=cp1252 reproduces that encode crash on any OS, so the Linux CI catches it.
One limit: a parent that captures a child's output decodes it in the LOCALE encoding, so where the
locale is UTF-8 a forced cp1252 child is a mismatch that exists only in the test (see the demo test).
"""
import ast
import locale
import os
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_GUARD = 'reconfigure(errors="replace")'
_SKIP = {".git", ".venv", "venv", "build", "dist", "node_modules", "kry_data"}


def _run(args, tmp_path):
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "cp1252"
    env["KRY_DATA_DIR"] = str(tmp_path / "kry_data")
    env["PYTHONPATH"] = os.pathsep.join(p for p in (str(_ROOT / "src"), env.get("PYTHONPATH", "")) if p)
    return subprocess.run([sys.executable, *args], cwd=str(_ROOT), env=env, capture_output=True,
                          encoding="cp1252", errors="replace", timeout=300)


def test_savings_report_and_verifier_survive_cp1252_console(tmp_path):
    att = tmp_path / "att.json"
    rep = _run(["scripts/kry_savings_report.py", "examples/sample_usage_log.jsonl",
                "--mint", "--attest", str(att)], tmp_path)
    assert rep.returncode == 0, rep.stderr
    assert "UnicodeEncodeError" not in rep.stderr
    ver = _run(["scripts/kry_verify.py", str(att)], tmp_path)
    assert ver.returncode == 0, ver.stderr
    assert "VERDICT: VALID" in ver.stdout


def test_try_kry_demo_survives_cp1252_console(tmp_path):
    """The demo's own output must not raise UnicodeEncodeError under a cp1252 console (any OS).
    It also captures kry_verify.py as a child and decodes that in the locale encoding, so a full
    returncode 0 is only a valid expectation where the locale is cp1252 too (Windows); on a UTF-8
    locale the forced cp1252 child makes that decode fail by construction of the test."""
    res = _run(["examples/try_kry.py"], tmp_path)
    assert "UnicodeEncodeError" not in res.stderr, res.stderr
    if locale.getpreferredencoding(False).lower() in ("cp1252", "windows-1252"):
        assert res.returncode == 0, res.stderr


def _is_hazard(tree):
    nonascii = any(isinstance(n, ast.Constant) and isinstance(n.value, str)
                   and any(ord(c) > 127 for c in n.value) for n in ast.walk(tree))
    prints = any(isinstance(n, ast.Call) and (
        (isinstance(n.func, ast.Name) and n.func.id == "print")
        or (isinstance(n.func, ast.Attribute) and n.func.attr == "write"
            and isinstance(n.func.value, ast.Attribute) and n.func.value.attr in ("stdout", "stderr")))
        for n in ast.walk(tree))
    main = any(isinstance(n, ast.If) and "__main__" in ast.unparse(n.test) for n in tree.body)
    return nonascii and prints and main


def test_every_printing_entry_point_guards_console_encoding():
    missing = []
    for path in sorted(_ROOT.rglob("*.py")):
        if _SKIP.intersection(path.relative_to(_ROOT).parts):
            continue
        src = path.read_text(encoding="utf-8")
        if _is_hazard(ast.parse(src)) and _GUARD not in src:
            missing.append(str(path.relative_to(_ROOT)))
    assert not missing, f"entry points that print non-ASCII without the console guard: {missing}"
