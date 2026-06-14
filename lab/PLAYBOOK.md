# KRY Lab Playbook — the complete plan (all phases, all tests, everything you need)

Goal: take KRY from `internally_consistent` (synthetic) to **`production_ready
(lab-validated)`** — every disclosed capability tested on **real distributed hardware**,
energy **measured**, the counterfactual **recovered against real truth**, double-spend
**fixed** — so you can hand it to a client and stand behind every number.

This is a lab-validation playbook, not the default release gate. The current
release checklist is `docs/RELEASE_CHECKLIST.md`; do not treat this playbook as
evidence until the result template is filled with real node artifacts.

**Honest ceiling (read first).** A lab gets you ~95%. It cannot prove (a) per-event
counterfactual veracity — impossible for any software; `veracity_floor` is the honest
label — or (b) that an *external party relies* on the number / dollar value at real
scale. Those need a real client. The lab makes you **credible to** a client; it isn't a
substitute for one. Label the result `production_ready (lab-validated)`, never bare "A+".

Everything is **stdlib + Ollama** (one-line install) **+ two smart plugs**. The router's
logic is unit-tested; only the live Ollama HTTP call needs validating on your box (run
`--dry-run` first to prove the whole pipeline with no models).

---

## 0. Hardware & accounts checklist

- [ ] **Node A** — i9 + RTX 5080 (16GB VRAM), 96GB RAM → router/earner, fast model, paid-API gateway, energy meter
- [ ] **Node B** — Mac Studio M4, 48GB unified → capable green model + energy oracle (`powermetrics`)
- [ ] **Node C** — ≈GTX 1080 (~8GB) → small free-tier model + 2nd settlement party
- [ ] **Node D** — ≈GTX 1060 (3–6GB) → registry/lease authority + metered "provider" log
- [ ] All four on one LAN, SSH between them, known hostnames/IPs
- [ ] **One shared folder** `SHARE/` reachable by all (NFS/SMB/Syncthing) — holds attestations, the lease file, the shared ledger
- [ ] **Smart plug** on A and on B (any plug with a Wh readout)
- [ ] *(Optional, makes the dollar leg real)* one **OpenRouter** key on A

## 0b. Install (once per node)

```bash
git clone <your KRY repo> && cd kry
python3 -m venv .venv && . .venv/bin/activate && pip install pytest ruff
python3 -m pytest tests/ -q # current suite output -> the package is healthy on this node
# Nodes A, B, C also:
curl -fsSL https://ollama.com/install.sh | sh
# pull a model sized to the node:
# A: ollama pull qwen2.5:14b (fast, fully on the 5080)
# B: ollama pull qwen2.5:32b (~20GB, capable + green on 48GB)
# C: ollama pull llama3.1:8b (the free tier)
```

Smoke-test the loop on each node:
```bash
PYTHONPATH=src python3 examples/try_kry.py # earn -> mint -> attest -> stranger-verifies -> carbon
```

## 0c. Prove the WHOLE pipeline locally first (one command, no models/meters)

```bash
PYTHONPATH=src python3 lab/run_local.py
# Tests 1, 2, 3, 5, 6 all run + PASS on one machine. If this is green, the only things
# the cluster adds are live model calls and real wall-meter readings.

# Reproducibility check (prove the proofs aren't one-offs — N rounds of all four families:
# the current suite, run_local, cross-process concurrency, HOLE D). Exits non-zero on any fail:
bash lab/reproduce.sh 10 # -> "ALL PROOFS REPRODUCIBLE across 10 rounds ✅"
```

---

## Phase 1 — Generate the REAL corpus (Node A)

1. Copy `lab/routes.example.json` → `routes.json`; set `nodes` to your hostnames and
 `frontier_node`/`frontier_model` (a paid-API gateway if you have the key).
 Keep local Ollama routes prefixed as `local/...` for KRY accounting; the router
 strips that prefix before calling Ollama.
2. **Make the corpus** (handles the repeats + the ≥30-holdouts/class volume rule for you):
```bash
PYTHONPATH=src python3 lab/make_prompts.py --n 20000 --out prompts.jsonl
```
3. Prove with no models, then run live **with the real judge**:
```bash
PYTHONPATH=src python3 lab/router.py --config routes.json --corpus prompts.jsonl \
 --out usage_real.jsonl --truth-out truth_full.jsonl --dry-run # no Ollama
PYTHONPATH=src python3 lab/router.py --config routes.json --corpus prompts.jsonl \
 --judge frontier-compare --out usage_real.jsonl --truth-out truth_full.jsonl # live
```

Outputs: **`usage_real.jsonl`** (KRY's view — no ground truth leaks in) and
**`truth_full.jsonl`** (your independent ground truth from the 25% audit sample).

> **The judge.** `--judge frontier-compare` is a real (heuristic) judge: it serves each
> judged request on BOTH the cheap route and the frontier and marks "needed the frontier"
> when the answers diverge beyond `judge_threshold` (token overlap). It costs 2 calls per
> judged request — the honest price of ground truth. Tune `judge_threshold` and spot-check
> it; for a stronger oracle swap in a semantic grader. Test 1 then checks KRY's 2% holdout
> *adequately estimates the rate your judge measures* — it does **not** validate the judge
> itself (that's yours to verify). `--judge prob` is the dry-run/CI stand-in only.

---

## Phase 2 — Test 1: holdout recovers REAL truth → `research_grade` oracle

```bash
PYTHONPATH=src python3 lab/compute_truth.py truth_full.jsonl --out truth.json
PYTHONPATH=src python3 scripts/kry_savings_report.py usage_real.jsonl --json > report.json
PYTHONPATH=src python3 lab/holdout_truth_check.py --report report.json --truth truth.json
```
**PASS:** `agreement ≥ 0.80` (≥80% of classes' holdout CI brackets the true rate). This
is an oracle independent of KRY's math → it supplies `independent_agreement`.
*(Proven in dry-run: agreement 1.0, all 4 classes' CIs bracket truth.)*

## Phase 3 — Test 2: MEASURED energy → carbon

Serve the **same batch** on A (5080) and B (M4); read each smart plug's Wh; record tokens.
```bash
# measurements.json: {"grid_co2_g_per_kwh":400,"nodes":{"rtx5080":{"tokens":N,"energy_wh":E},"mac_m4":{...}}}
PYTHONPATH=src python3 lab/energy_report.py measurements.json
export KRY_JOULES_PER_TOKEN=<greenest measured J/token> # kry_carbon now MEASURED
```
**PASS:** measured J/token per node + a real avoided-kWh/CO₂ for routing to the M4.
**Fairness:** use the **wall plug** on both (`powermetrics`/`nvidia-smi` are component-level
and not cross-comparable).

## Phase 4 — Test 3: cross-node double-spend (HOLE D) — the 4-node killer

```bash
PYTHONPATH=src python3 lab/hole_d_double_spend.py # see it locally first (already passes)
```
Real version across machines via `lab/node.py` (SHARE = your NFS/SMB mount, holds the
attestation + the lease dir; each node keeps its own `--kry-dir`):
```bash
# Node A: python3 lab/node.py earner --share /mnt/share --kry-dir ~/kryA --amount 10000
# Node B & C, WITHOUT --use-lease -> double-spend shows; WITH it -> second is refused:
python3 lab/node.py accept --share /mnt/share --kry-dir ~/kryB --party A --offer 7000 --use-lease
python3 lab/node.py accept --share /mnt/share --kry-dir ~/kryC --party A --offer 7000 --use-lease
```
**PASS:** without the lease B and C both settle (double-spend); with the lease the second
is refused.

## Phase 5 — Test 4: stranger verify across machines + F1 reconcile

```bash
# Node A: mint (some calls served by the paid API or by D as 'provider'), then publish:
PYTHONPATH=src python3 -c "import kry.kry_attest as a; open('SHARE/attestation.json','w').write(a.build_attestation().to_public_json())"
# Any other node (fresh checkout, stdlib only, NO shared state):
python3 lab/node.py verify --share /mnt/share # -> VALID
# If you used OpenRouter on A:
python3 scripts/kry_or_fetch.py kry_data/kry_mint_log.jsonl --out or.json
python3 scripts/kry_reconcile.py kry_data/kry_mint_log.jsonl --provider-export or.json
```
**PASS:** B verifies VALID with zero shared state; ≥80% of T1 receipts reconcile (a second
independent oracle).

## Phase 6 — Test 5: sanctions across real identities

Give each node a distinct party id; make one node a **cheater** (claims that fail
reconciliation against D's log). Feed outcomes to the reputation loop:
```bash
PYTHONPATH=src python3 -c "
import kry.kry_sanctions as ks
for _ in range(8): ks.record_reconciliation('nodeA', confirmed=True)
for _ in range(6): ks.record_reconciliation('cheater', confirmed=False)
print('honest audit', round(ks.audit_rate_for('nodeA'),3), '| cheater audit', round(ks.audit_rate_for('cheater'),3))
"
```
**PASS:** cheater reputation collapses, audit → ~100%; honest nodes settle at the 2% floor.

## Phase 7 — Test 6: concurrency correctness

```bash
PYTHONPATH=src python3 lab/concurrency_check.py --workers 4 --earns 200 --share /mnt/share/kry_ledger
```
**PASS:** `lost: 0` — concurrent processes/nodes sharing the ledger lose no updates (the
ledger now takes a cross-process lock; a real bug Test 6 caught earlier). **Note:** flock
over NFS is unreliable — for production multi-node, prefer per-node ledgers + reconciliation
(the mint chain) over one shared mutable ledger.

---

## Phase 8 — Compute the honest grade (the deliverable)

```bash
PYTHONPATH=src python3 -c "
import kry.kry_capabilities as cap
r = cap.readiness_label(
 replay_pass_rate=1.0, # suite green on every node
 independent_agreement=0.85, # <- Phase 2 agreement (and/or Phase 5 reconcile)
 real_corpus_validated=True, # <- Phase 1 real traffic + Phases 3-7 passed
 audit_clean=True)
print(r.label)
"
```
With the lab evidence in, this prints **`production_ready`**. Record it as
**`production_ready (lab-validated)`** and fill in `lab/RESULTS_TEMPLATE.md` with the
artifacts: `report.json`, the energy report, the HOLE-D run, the cross-machine VALID, the
reconcile result, and the sanctions/concurrency logs.

---

## Order of operations (the easy path)

```
0. setup (≈1h) -> 0c. run_local.py (prove it all on ONE machine, no models)
-> install models -> 1. make_prompts + live router (real corpus) -> 2. Test 1
-> 3. Test 2 (energy, wall meter) -> 4. Test 3 (HOLE D, node.py)
-> 5. Test 4 (verify+reconcile) -> 6. Test 5 -> 7. Test 6 (concurrency_check)
-> 8. compute grade + fill RESULTS_TEMPLATE.md
```

## Troubleshooting

- **Test 1 says `classes_checked: 0`** → not enough holdouts/class. Raise corpus volume or
 `holdout_rate` until each class has ≥30 holdouts (see the Volume rule).
- **Test 1 agreement low** → either the 2% sample is too small (raise volume) or your judge
 is inconsistent (spot-check it). A genuine low number is information, not failure.
- **`kry_verify` INVALID on Node B** → you copied a stale attestation; re-publish after the
 last mint. The chain is order-sensitive.
- **Energy numbers look wrong** → confirm you read the **wall plug**, not `nvidia-smi`, and
 that both nodes served the *same* token volume.
- **Ollama OOM on the M4** → drop to a smaller quant or a 14B; 48GB tops out ~32B.

## What you can then honestly tell a client

> "Every capability we claim is tested on a real four-node cluster: the savings
> counterfactual is measured against ground truth (2% holdout, 95% CI, checked vs a 25%
> audit), energy is measured at the wall, cross-node double-spend is demonstrated and
> fixed, and any stranger verifies our attestations with ~200 lines of standard-library
> Python. Here are the artifacts. What we do **not** claim: that a self-reported cache hit
> can be proven to a third party (we label that `veracity_floor` and disclose it), or
> dollar value at a scale we haven't run — that's what a pilot with you would establish."
