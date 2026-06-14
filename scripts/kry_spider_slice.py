#!/usr/bin/env python3
"""REAL-corpus slice probe — Spider text-to-SQL via execute-gate. Hardens the synthetic SQL slice.

Runs GPT's frozen 99-row Spider dev sample on the REAL Spider DBs: cheap(Haiku) vs frontier(Sonnet) generate SQL
from schema+question; gate = the query EXECUTES (read-only) against the db_id's SQLite fixture to the SAME result as
the gold query. net-not-gross, deterministic gate (no oracle, no model-judge, no semantic equivalence). This upgrades
the synthetic kry_sql_slice (93%/62%) into a real-Spider number. stdlib only (sqlite3 + urllib).

  python3 scripts/kry_spider_slice.py --dry            # gold-as-output, $0 — verifies dev_index mapping + gate
  python3 scripts/kry_spider_slice.py [N] [--budget 3]  # LIVE cheap-vs-frontier
"""
from __future__ import annotations
import json, math, os, re, sqlite3, sys, time, urllib.error
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from kry_shadow_demo import call, cost, CHEAP, FRONTIER

SPIDER = Path(os.environ.get("KRY_SPIDER_DATA", "spider_data"))
MANIFEST = Path(os.environ.get("KRY_SPIDER_MANIFEST", "spider_manifest.json"))
PROMPT = ("Given this SQLite schema:\n{schema}\n\nWrite ONE SQLite query that answers: {q}\n"
          "Return ONLY the query in a ```sql code block, no explanation.")


def _norm(rows):
    out = []
    for r in rows:
        out.append(tuple(round(c, 4) if isinstance(c, float) else c for c in r))
    return sorted(out, key=lambda t: tuple(str(x) for x in t))


def _db(db_id):
    return sqlite3.connect(f"file:{SPIDER}/database/{db_id}/{db_id}.sqlite?mode=ro", uri=True)


def run_sql(con, sql):
    try:
        return _norm(con.execute(sql).fetchall())
    except Exception:
        return None


def _schema(db_id):
    p = SPIDER / "database" / db_id / "schema.sql"
    if p.exists():
        return p.read_text(errors="ignore", encoding="utf-8")
    con = _db(db_id)
    s = "\n".join(r[0] for r in con.execute("SELECT sql FROM sqlite_master WHERE sql IS NOT NULL"))
    con.close()
    return s


def _extract(t):
    m = re.search(r"```(?:sql)?\s*\n(.*?)```", t, re.S)
    return (m.group(1) if m else t).strip().rstrip(";").strip()


def _call(prompt, model, mt=400):
    for a in range(5):
        try:
            return call(prompt, model, mt)
        except urllib.error.HTTPError as e:
            if e.code in (429, 529) and a < 4:
                time.sleep(2 * (a + 1)); continue
            raise


def wilson(k, n, z=1.96):
    if n == 0:
        return (0.0, 0.0)
    p = k / n; d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (round(c - h, 4), round(c + h, 4))


def main(argv):
    dry = "--dry" in argv
    args = [a for a in argv[1:] if a != "--dry"]
    budget = 3.0
    if "--budget" in args:
        bi = args.index("--budget"); budget = float(args[bi + 1]); args = args[:bi] + args[bi + 2:]
    nums = [a for a in args if a.isdigit()]; cap = int(nums[0]) if nums else None
    dev = json.load(open(SPIDER / "dev.json", encoding="utf-8"))
    man = json.load(open(MANIFEST, encoding="utf-8")); rows_man = man.get("rows") or man.get("sample")
    if cap:
        rows_man = rows_man[:cap]
    print(f"Spider slice {'(DRY $0)' if dry else f'(LIVE cheap={CHEAP} frontier={FRONTIER}, cap ${budget})'}  "
          f"rows={len(rows_man)}\n", flush=True)
    cp = fp = 0; cs = fs = saving = 0.0; done = 0; rows = []; mismatch = 0; gold_skip = 0
    outdir = Path("docs/evidence/spider_slice"); outdir.mkdir(parents=True, exist_ok=True)
    tag = "dry" if dry else "live"
    rowf = open(outdir / f"spider_slice_{tag}.jsonl", "w", buffering=1, encoding="utf-8")
    for m in rows_man:
        if not dry and (cs + fs) >= budget:
            print(f"** budget cap ${budget} reached after {done} **", flush=True); break
        idx = m["dev_index"]; ex = dev[idx]
        if ex["db_id"] != m["db_id"]:
            mismatch += 1; continue
        db_id, q, gold = ex["db_id"], ex["question"], ex["query"]
        try:
            con = _db(db_id); gold_res = run_sql(con, gold)
            if gold_res is None:
                con.close(); gold_skip += 1; continue
            prompt = PROMPT.format(schema=_schema(db_id), q=q)
            if dry:
                ctext, cit, cot = f"```sql\n{gold}\n```", 0, 0; ftext, fit, fot = ctext, 0, 0
            else:
                ctext, cit, cot = _call(prompt, CHEAP); ftext, fit, fot = _call(prompt, FRONTIER)
            cpass = run_sql(con, _extract(ctext)) == gold_res
            fpass = run_sql(con, _extract(ftext)) == gold_res
            con.close()
        except Exception as e:
            print(f"  [skip {idx}] {type(e).__name__}", flush=True); continue
        ccost, fcost = cost(CHEAP, cit, cot), cost(FRONTIER, fit, fot)
        cp += cpass; fp += fpass; cs += ccost; fs += fcost; done += 1
        saving += (fcost - ccost) if cpass else 0.0
        rows.append({"db_id": db_id, "dev_index": idx, "cheap_pass": cpass, "frontier_pass": fpass,
                     "cheap_cost": ccost, "frontier_cost": fcost})
        rowf.write(json.dumps(rows[-1]) + "\n")
        if done % 10 == 0 or dry:
            print(f"  [{done}/{len(rows_man)}] cp={cp} fp={fp} net=${saving:.4f} spent=${cs+fs:.4f}", flush=True)
    rowf.close(); n = len(rows)
    lo, hi = wilson(cp, n)
    summ = {"schema": "kry_spider_slice/v1",
            "label": "REAL Spider dev (frozen N=99) text-to-SQL via execute-gate; net-not-gross; deterministic",
            "mode": tag, "cheap_model": CHEAP, "frontier_model": FRONTIER, "rows": n,
            "cheap_pass": cp, "cheap_adequacy": round(cp / n, 4) if n else None, "cheap_adequacy_wilson95": [lo, hi],
            "frontier_pass": fp, "dev_index_mismatch": mismatch, "gold_unrunnable_skipped": gold_skip,
            "cheap_spend_usd": round(cs, 6), "frontier_spend_usd": round(fs, 6),
            "measured_net_saving_usd": round(saving, 6), "net_cost_reduction_rate": round(saving / fs, 4) if fs else None}
    (outdir / f"spider_slice_{tag}.json").write_text(json.dumps(summ, indent=2), encoding="utf-8")
    print(f"\n=== SPIDER SLICE ({'DRY' if dry else 'LIVE'}) ===")
    print(f"  cheap adequacy: {cp}/{n} = {100*cp/n:.0f}% (Wilson95 [{100*lo:.0f}%,{100*hi:.0f}%])  frontier: {fp}/{n}"
          f"  mismatch:{mismatch} gold_skip:{gold_skip}")
    if not dry:
        print(f"  net cost-reduction: {summ['net_cost_reduction_rate']}  net saving ${saving:.4f}  spend ${cs+fs:.4f}")
    return 0


if __name__ == "__main__":
    main(sys.argv)
