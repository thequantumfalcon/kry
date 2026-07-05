#!/usr/bin/env python3
"""Differential fuzz: reference Python verifier vs the independent JS verifier.

Generates a large stream of mutated attestations (from real minted bases), records
each verdict from BOTH verifiers, and reports divergences. This is the SC2 harness
(roadmap §4): 0 divergences over a large N == implementation independence.

    PYTHONPATH=src python3 verifiers/diff_fuzz.py [N]        # default 20000

Any divergence is written to verifiers/divergences/ as a vector candidate.
"""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import random
import shutil
import subprocess
import sys
import time as _time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
import kry.kry_token as kt        # noqa: E402
import kry.kry_mint as km         # noqa: E402
import kry.kry_attest as ka       # noqa: E402
import kry.kry_settlement as ks   # noqa: E402
import kry.kry_action as kax       # noqa: E402


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


kv = _load(ROOT / "scripts" / "kry_verify.py", "kv")
kav = _load(ROOT / "scripts" / "kry_action_verify.py", "kav")

WORK = ROOT / "verifiers" / ".fuzzwork"
MULT = ROOT / "vectors" / "primitives" / "legal_multipliers.json"
_clock = [1_700_000_000.0]
_time.time = lambda: _clock[0]


def reset():
    if WORK.exists():
        shutil.rmtree(WORK)
    WORK.mkdir(parents=True)
    log = WORK / "mint.jsonl"
    km._MINT_LOG_PATH = log
    ka._MINT_LOG_PATH = log
    kt._LEDGER_PATH = WORK / "l.json"
    km._DECAY_STATE_PATH = WORK / "d.json"
    ks._REGISTRY_PATH = WORK / "r.jsonl"
    km._RECEIPT_COUNTER = 0
    km._CHAIN_TIP = "0" * 64
    km._evidence_mints = {}
    km._decay_loaded = True
    kt._ledger_instance = kt.KRYLedger()
    _clock[0] = 1_700_000_000.0
    return log


def build_savings(events):
    log = reset()
    for e in events:
        _clock[0] += 1.0
        assert km.mint(**e) is not None
    return json.loads(ka.build_attestation(log).to_public_json())


def build_action(n, tiers):
    prev = "0" * 64
    links = []
    for i in range(n):
        tier = tiers[i % len(tiers)]
        sec = kax.commit(f"srv{i}") if tier in ("server_witnessed", "attested") else None
        link = {"receipt_id": f"ACT-{i:08d}", "tool": "read", "args_commit": kax.commit(f"a{i}"),
                "result_commit": kax.commit(f"r{i}"), "status": "ok", "ts": 1_700_000_000.0 + i,
                "agent_id": "A", "evidence_tier": tier, "server_evidence_commit": sec,
                "action_hash_version": 1}
        payload = kax.ActionReceipt._payload(
            tool=link["tool"], args_commit=link["args_commit"], result_commit=link["result_commit"],
            status=link["status"], ts=link["ts"], agent_id=link["agent_id"],
            evidence_tier=link["evidence_tier"], server_evidence_commit=link["server_evidence_commit"])
        rh = hashlib.sha256(kax._canon(payload).encode()).hexdigest()
        ch = hashlib.sha256(f"{prev}:{rh}".encode()).hexdigest()
        link["receipt_hash"] = rh
        link["chain_hash"] = ch
        prev = ch
        links.append(link)
    anchored = sum(1 for link in links
                   if link["evidence_tier"] in ("server_witnessed", "attested") and link["server_evidence_commit"])
    return {"kind": "kry_action_attestation", "action_hash_version": 1, "links": links,
            "chain_tip": prev, "action_count": len(links),
            "veracity": {"veracity_floor": round(anchored / len(links), 4) if links else 0.0}}


CACHE = {"event_type": "cache_hit", "tokens_saved": 1000, "avoided_model": "gh/claude-opus-4.8"}
DISP = {"event_type": "short_circuit", "tokens_saved": 1000, "avoided_model": "or/deepseek/deepseek-v4-pro",
        "evidence_tier": "provider_metered", "metered_tokens": [100, 400]}


def bases():
    b = []
    b.append(build_savings([{**CACHE, "detail": "q0", "evidence": "u0"}]))
    b.append(build_savings([{**CACHE, "detail": f"q{i}", "evidence": f"u{i}"} for i in range(3)]))
    try:
        b.append(build_savings([{**CACHE, "detail": "q0", "evidence": "u0"},
                                {**DISP, "detail": "disp/or/deepseek-v4-pro/openrouter:gen-x", "evidence": "m0"}]))
    except Exception:
        pass
    b.append(build_action(1, ["self_reported"]))
    b.append(build_action(3, ["self_reported", "server_witnessed", "attested"]))
    return b


STR_FIELDS = ["event_type", "evidence_tier", "receipt_hash", "chain_hash", "receipt_id", "tool",
              "args_commit", "status", "agent_id", "kind"]


def mutate(att, rng):
    a = copy.deepcopy(att)
    for _ in range(rng.randint(1, 3)):
        op = rng.random()
        links = a.get("links") if isinstance(a.get("links"), list) else []
        if op < 0.18 and links:                                    # perturb a link number
            link = rng.choice(links)
            k = rng.choice(["kry_minted", "tokens_saved", "ts", "earn_rate", "hash_version", "seq"])
            if k in link and isinstance(link[k], (int, float)):
                link[k] = link[k] + rng.choice([1, -1, 0.5, 1000, -1000])
        elif op < 0.34 and links:                                  # relabel a string field
            link = rng.choice(links)
            k = rng.choice(STR_FIELDS)
            if k in link and isinstance(link[k], str):
                link[k] = rng.choice([link[k] + "x", "café", "", link[k][:-1] if link[k] else "z",
                                      "provider_metered", "attested"])
        elif op < 0.46 and len(links) > 1:                         # reorder / drop
            if rng.random() < 0.5:
                rng.shuffle(links)
            else:
                links.pop(rng.randrange(len(links)))
        elif op < 0.58:                                            # tamper an envelope field
            k = rng.choice(["total_kry", "usd_equivalent", "chain_head", "attestation_hash", "chain_tip",
                            "receipts", "action_count", "chain_valid", "veracity"])
            if k in a:
                cur = a[k]
                if isinstance(cur, bool):
                    a[k] = not cur
                elif isinstance(cur, (int, float)):
                    a[k] = cur + rng.choice([1, -1, 0.001])
                elif isinstance(cur, str):
                    a[k] = (cur + "0")[:64] if cur else "x"
                else:
                    a[k] = rng.choice([None, {}, 0])
        elif op < 0.70 and links:                                  # duplicate a link
            links.insert(rng.randrange(len(links) + 1), copy.deepcopy(rng.choice(links)))
        elif op < 0.82 and links:                                  # tier upgrade / metered fiddling
            link = rng.choice(links)
            if "evidence_tier" in link:
                link["evidence_tier"] = rng.choice(
                    ["self_reported", "provider_metered", "tee_attested", "attested", "bogus"])
            if rng.random() < 0.5:
                link["metered_tokens"] = rng.choice([None, [1, 2], [1], [-1, 2], "x", [1.5, 2]])
        elif op < 0.90:                                            # type-confuse a field
            if links:
                link = rng.choice(links)
                k = rng.choice(list(link.keys()))
                link[k] = rng.choice([None, [], {}, True, "x", 0])
        else:                                                      # server_evidence toggle (action)
            if links:
                link = rng.choice(links)
                if "server_evidence_commit" in link:
                    link["server_evidence_commit"] = rng.choice([None, kax.commit("x"), ""])
    return a


def py_verdict(text):
    try:
        att = json.loads(text, parse_constant=lambda v: (_ for _ in ()).throw(ValueError(v)))
    except Exception:
        return "PARSE_ERROR"
    try:
        if isinstance(att, dict) and att.get("kind") == "kry_action_attestation":
            ok = kav.verify_action_attestation(att)[0]
        else:
            ok = kv.verify_attestation(att)[0]
        return "VALID" if ok else "INVALID"
    except Exception:
        return "CRASH"


MALFORMED = ['{"kind":"kry_action_attestation","action_hash_version":1,"links":[{"kry_minted":NaN}]}',
             '{"links":[]', '{"receipts":1,"links":[{"ts":Infinity}]}', 'not json', '{"a":']


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 20000
    shutil.rmtree(ROOT / "verifiers" / "divergences", ignore_errors=True)  # fresh each run
    rng = random.Random(1234)
    base = bases()
    reset()  # clean state; from here we only serialize, never mint
    if WORK.exists():
        shutil.rmtree(WORK)
    ndj = ROOT / "verifiers" / ".fuzz_batch.ndjson"
    chunk = 50000
    agree = {"VALID": 0, "INVALID": 0, "PARSE_ERROR": 0, "CRASH": 0}
    ndiv = 0
    done = 0
    while done < n:
        k = min(chunk, n - done)
        texts, pys = [], []
        for i in range(done, done + k):
            text = rng.choice(MALFORMED) if i % 50 == 7 else json.dumps(mutate(rng.choice(base), rng))
            texts.append(text)
            pys.append(py_verdict(text))
        ndj.write_text("\n".join(texts) + "\n")
        res = subprocess.run(
            ["node", str(ROOT / "verifiers" / "js" / "verify.mjs"), "--batch", str(ndj), str(MULT)],
            capture_output=True, text=True)
        js = res.stdout.strip("\n").split("\n")
        if len(js) != len(pys):
            print(f"FATAL: js emitted {len(js)} verdicts for {len(pys)} inputs")
            print(res.stderr[:2000])
            sys.exit(2)
        for text, p, j in zip(texts, pys, js):
            if p == j:
                agree[p] = agree.get(p, 0) + 1
            else:
                if ndiv < 200:
                    outd = ROOT / "verifiers" / "divergences"
                    outd.mkdir(exist_ok=True)
                    (outd / f"div_{ndiv:04d}.json").write_text(
                        json.dumps({"py": p, "js": j, "input": text}, indent=2))
                ndiv += 1
        done += k
        print(f"  ...{done}/{n}  divergences so far={ndiv}")
    ndj.unlink(missing_ok=True)
    print(f"differential fuzz: N={n}  divergences={ndiv}")
    print(f"  agree: VALID={agree['VALID']} INVALID={agree['INVALID']} "
          f"PARSE_ERROR={agree['PARSE_ERROR']} CRASH={agree['CRASH']}")
    if ndiv:
        print("  wrote up to 200 divergence case(s) to verifiers/divergences")
        sys.exit(1)
    print("  0 divergences — the JS verifier agrees with the Python reference on every case.")


if __name__ == "__main__":
    main()
