#!/usr/bin/env python3
"""SLICE-EXTENSION probe — does the deterministic-check slice extend past code to SQL?

Gate = the generated query EXECUTES against a SQLite DB to the SAME result as the gold query (a real deterministic
check, like running tests — NO oracle, NO gold-answer-text matching, just result-equality). Tests whether
cheap-routing + an execute-gate works on SQL the way it does on code. net-not-gross (saving counts only where the
cheap query passes). stdlib only (sqlite3 + urllib).

Honest scope: the questions + gold queries are authored here (synthetic, like the toy code demo), but the GATE is
real (execute-to-same-result). This measures cheap adequacy + routing savings on the SQL slice; it is NOT real
company traffic.

  python3 scripts/kry_sql_slice.py --dry          # $0, runs gold as "model output" -> must pass 15/15 (verifies DB+golds+gate)
  python3 scripts/kry_sql_slice.py [--frontier claude-opus-4-8]   # LIVE cheap-vs-frontier
"""
from __future__ import annotations
import json, os, re, sqlite3, sys, time, urllib.error
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from kry_shadow_demo import call, cost, CHEAP, FRONTIER

SCHEMA = """
CREATE TABLE customers (id INTEGER PRIMARY KEY, name TEXT, country TEXT, signup TEXT);
CREATE TABLE products  (id INTEGER PRIMARY KEY, name TEXT, category TEXT, price REAL);
CREATE TABLE orders    (id INTEGER PRIMARY KEY, customer_id INTEGER, status TEXT, order_date TEXT);
CREATE TABLE order_items (order_id INTEGER, product_id INTEGER, qty INTEGER);
INSERT INTO customers VALUES (1,'Alice','US','2024-01-05'),(2,'Bob','UK','2024-02-10'),
  (3,'Cara','US','2024-03-01'),(4,'Dan','DE','2024-03-15'),(5,'Eve','US','2024-04-20');
INSERT INTO products VALUES (1,'Widget','hardware',10.0),(2,'Gadget','hardware',25.0),
  (3,'Doohickey','toys',5.0),(4,'Gizmo','toys',15.0),(5,'Sprocket','hardware',8.0),(6,'Bolt','hardware',3.0);
INSERT INTO orders VALUES (1,1,'completed','2024-05-01'),(2,1,'completed','2024-05-03'),
  (3,2,'cancelled','2024-05-04'),(4,3,'completed','2024-05-05'),(5,5,'pending','2024-05-06'),(6,1,'completed','2024-05-07');
INSERT INTO order_items VALUES (1,1,2),(1,3,1),(2,2,1),(3,4,3),(4,1,1),(4,5,4),(5,2,2),(6,3,5);
"""

TASKS = [
    ("How many customers are there?", "SELECT COUNT(*) FROM customers"),
    ("List the names of customers from the US.", "SELECT name FROM customers WHERE country='US'"),
    ("How many orders have status 'completed'?", "SELECT COUNT(*) FROM orders WHERE status='completed'"),
    ("What is the average price of all products?", "SELECT AVG(price) FROM products"),
    ("List the names of products in the 'hardware' category.", "SELECT name FROM products WHERE category='hardware'"),
    ("Which customer name has placed the most orders?",
     "SELECT c.name FROM customers c JOIN orders o ON c.id=o.customer_id GROUP BY c.id ORDER BY COUNT(*) DESC LIMIT 1"),
    ("What is the total quantity ordered of the product named 'Widget'?",
     "SELECT SUM(qty) FROM order_items oi JOIN products p ON oi.product_id=p.id WHERE p.name='Widget'"),
    ("How many products have never been ordered?",
     "SELECT COUNT(*) FROM products WHERE id NOT IN (SELECT DISTINCT product_id FROM order_items)"),
    ("List each product category with its number of products.", "SELECT category, COUNT(*) FROM products GROUP BY category"),
    ("How many completed orders were placed by US customers?",
     "SELECT COUNT(*) FROM orders o JOIN customers c ON o.customer_id=c.id WHERE o.status='completed' AND c.country='US'"),
    ("Which product category has the highest total price summed over its products?",
     "SELECT category FROM products GROUP BY category ORDER BY SUM(price) DESC LIMIT 1"),
    ("Name the customer who signed up earliest.", "SELECT name FROM customers ORDER BY signup ASC LIMIT 1"),
    ("How many distinct products have been ordered?", "SELECT COUNT(DISTINCT product_id) FROM order_items"),
    ("What is the name of the highest-priced product?", "SELECT name FROM products ORDER BY price DESC LIMIT 1"),
    ("List the names of customers who have no orders.",
     "SELECT name FROM customers WHERE id NOT IN (SELECT DISTINCT customer_id FROM orders)"),
]

PROMPT = ("Given this SQLite schema:\n{schema}\n\nWrite ONE SQLite query that answers: {q}\n"
          "Return ONLY the query in a ```sql code block, no explanation.")


def build_db():
    con = sqlite3.connect(":memory:"); con.executescript(SCHEMA); return con


def _norm(rows):
    out = []
    for row in rows:
        out.append(tuple(round(c, 4) if isinstance(c, float) else c for c in row))
    return sorted(out, key=lambda t: tuple(str(x) for x in t))


def run_sql(con, sql):
    try:
        return _norm(con.execute(sql).fetchall())
    except Exception:
        return None


def _extract_sql(text):
    m = re.search(r"```(?:sql)?\s*\n(.*?)```", text, re.S)
    sql = (m.group(1) if m else text).strip().rstrip(";").strip()
    return sql


def gate(con, model_text, gold_sql):
    r_model = run_sql(con, _extract_sql(model_text))
    r_gold = run_sql(con, gold_sql)
    return r_model is not None and r_model == r_gold


def _call(prompt, model, mt=400):
    for a in range(5):
        try:
            return call(prompt, model, mt)
        except urllib.error.HTTPError as e:
            if e.code in (429, 529) and a < 4:
                time.sleep(2 * (a + 1)); continue
            raise


def main(argv):
    dry = "--dry" in argv
    frontier = argv[argv.index("--frontier") + 1] if "--frontier" in argv else FRONTIER
    con = build_db()
    schema_txt = SCHEMA.strip()
    print(f"SQL slice probe {'(DRY: gold-as-model, $0)' if dry else f'(LIVE cheap={CHEAP} frontier={frontier})'}  "
          f"tasks={len(TASKS)}\n", flush=True)
    cp = fp = 0
    cs = fs = saving = 0.0
    rows = []
    for i, (q, gold) in enumerate(TASKS):
        prompt = PROMPT.format(schema=schema_txt, q=q)
        if dry:
            cpass = gate(con, f"```sql\n{gold}\n```", gold); fpass = cpass
            ccost = fcost = 0.0
        else:
            ctext, cit, cot = _call(prompt, CHEAP); ftext, fit, fot = _call(prompt, frontier)
            cpass = gate(con, ctext, gold); fpass = gate(con, ftext, gold)
            ccost, fcost = cost(CHEAP, cit, cot), cost(frontier, fit, fot)
        cp += cpass; fp += fpass; cs += ccost; fs += fcost
        row_saving = (fcost - ccost) if cpass else 0.0
        saving += row_saving
        rows.append({"q": q, "cheap_pass": cpass, "frontier_pass": fpass, "cheap_cost": ccost, "frontier_cost": fcost})
        print(f"[{i:2d}] cheap={'PASS' if cpass else 'fail'} frontier={'PASS' if fpass else 'fail'}  {q[:48]}", flush=True)
    n = len(TASKS)
    net = round(saving / fs, 4) if fs else None
    summary = {
        "schema": "kry_sql_slice/v1",
        "label": "SLICE-EXTENSION probe: SQL via execute-gate (synthetic questions, REAL deterministic gate); net-not-gross",
        "mode": "dry" if dry else "live", "cheap_model": CHEAP, "frontier_model": frontier,
        "tasks": n, "cheap_pass": cp, "cheap_adequacy": round(cp / n, 4), "frontier_pass": fp,
        "cheap_spend_usd": round(cs, 6), "frontier_spend_usd": round(fs, 6),
        "measured_net_saving_usd": round(saving, 6), "net_cost_reduction_rate": net,
        "rows": rows,
        "honest_note": "Compare cheap_adequacy here vs code's ~83%: if comparable, the deterministic-check slice "
                       "EXTENDS to SQL (addressable share grows). Synthetic questions; the execute-gate is real.",
    }
    outdir = Path("docs/evidence/sql_slice"); outdir.mkdir(parents=True, exist_ok=True)
    tag = "dry" if dry else "live"
    (outdir / f"sql_slice_{tag}.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"\n=== SQL SLICE RECEIPT ({'DRY' if dry else 'LIVE'}) ===")
    print(f"  cheap adequacy (execute-gate): {cp}/{n} = {100*cp/n:.0f}%   frontier: {fp}/{n}")
    if not dry:
        print(f"  net cost-reduction: {net}   measured net saving: ${saving:.4f}   spend ${cs+fs:.4f}")
    print(f"  -> {outdir}/sql_slice_{tag}.json")
    return 0


if __name__ == "__main__":
    main(sys.argv)
