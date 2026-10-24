# KRY four-node lab

This lab exercises routing, supplied wall-energy measurements and multi-machine
behaviour, including **HOLE D** (cross-node double-spend). Generated prompts remain
synthetic even when served by real models. An operator-controlled router or judge
is not automatically an independent oracle.

The results can inform the readiness ladder (`docs/KRY_READINESS.md`), but lab
completion alone does not supply independent real-corpus validation or authorize
a `production_ready` claim.

## Node roles

```
Node A earner / router — runs inference, caches, routes; mints KRY; emits attestations
Node B counterparty — verifies A's attestation, settles offers
Node C counterparty — same; the second party needed to exercise HOLE D
Node D registry authority — shared lease / reconciliation service (+ acts as a metered "provider")
```

Any node with a power meter (smart plug, or `nvidia-smi`/RAPL) also feeds Test 2.

### Example mapping to a heterogeneous home lab

A real lab is rarely uniform; map roles to capability (strongest = earner/heavy model,
weakest = coordination authority). One worked example (adjust to your exact gear):

| Machine (example) | Role | Why |
|---|---|---|
| i9 + RTX 5080 (16GB) | **A — earner/router + heavy served model** | strongest; FP8 + TensorRT-LLM/EAGLE-3 spec-decoding; best energy numbers |
| Apple Silicon (M-series) | **B — efficient served model + energy contrast** | very low J/token → the green counterpart to a discrete GPU |
| ≈GTX 1080 (~8GB) | **C — small free-tier model + 2nd counterparty** | Pascal runs a 7–8B GGUF; the cheap routing destination |
| ≈GTX 1060 (3–6GB) | **D — registry/lease authority + metered provider log** | coordination needs ~no compute; fits the weakest node |

Notes: a discrete-GPU node + an Apple-Silicon node cannot share one GPU pool (different
stacks) — this is a heterogeneous **routing** pool (KRY's actual use case), not unified
VRAM. Older Pascal cards run small quantized models only (Ollama/llama.cpp); fine as
free-tier/draft/coordination nodes. Local inference still consumes energy and hardware.
A paid-provider call can supply usage and incurred-charge evidence; it does not prove
that a different call was avoided or that money was saved. Energy and distributed
experiments need no paid API.

## Test 3 — cross-node double-spend (HOLE D) — RUNNABLE NOW

The killer test; only multiple nodes can run it. Demonstrated locally (4 dirs):

```bash
PYTHONPATH=src python3 lab/hole_d_double_spend.py
# PHASE 1 (no authority): B and C each settle 7,000 vs a 10,000 balance -> 14,000 -> VULNERABLE
# PHASE 2 (Node D lease): the over-balance second lease is DENIED -> PROTECTED
# PHASE 2b: concurrent race -> exactly one lease granted (atomic)
```

On real hardware, Node D runs the lease service and A/B/C are three machines:

```bash
# Node D (authority): a leased file on shared storage, or a tiny HTTP lease endpoint
# Node A: earn + attest, publish attestation.json (scp/http) to B and C
PYTHONPATH=src python3 -c "import kry.kry_mint as m,kry.kry_attest as a; \
 m.mint('cache_hit',10000,'epoch1',evidence='A1',avoided_model='gh/claude-opus-4.8'); \
 open('attestation.json','w').write(a.build_attestation().to_public_json())"
# Node B and Node C: lease from D FIRST, then verify_and_accept against the local registry
```

**Pass:** Phase 1 shows the double-spend; Phase 2 denies the conflicting lease in
the tested setup. This does not establish a universal cross-node guarantee:
every participant must use the authority, and shared-storage lock semantics matter.

## The other experiments (specs — each a falsifier)

| # | What | How | Pass / Fail |
|---|---|---|---|
| **1** | holdout recovers REAL truth | run the router with a 2–5% randomized holdout; compare KRY's Wilson CI to the true per-class paid-rate from your full-stream log | CI brackets the true rate for ≥80% of classes |
| **2** | wall-energy comparison | meter the same batch on both nodes; retain readings and token counts | compare measured energy per token; the hypothetical routing delta and CO₂ conversion remain conditional estimates |
| **4** | stranger verify across machines + F1 | Node A mints; Node B verifies; compare retained records with the declared provider source | current per-request research grading requires every supplied request ID and token pair to match and verdict RECONCILED; operator-controlled Node D is not independently authenticated evidence |
| **5** | sanctions across real identities | 3 honest nodes + 1 cheater whose claims fail reconciliation vs D | cheater reputation collapses, audit → ~100%; honest nodes at the 2% floor |
| **6** | concurrency correctness | parallel processes share a local-filesystem ledger on one host | final balance == Σ deltas for the tested workload; network filesystems are outside the lock guarantee |

## Honest boundary

- This harness **simulates** four nodes locally (separate data dirs); the double-spend
 it shows is real given unmerged registries, and the lease is a **prototype** of the
 lease/nonce/TTL fix ranked in `docs/KRY_VERACITY_BINDING.md` — not a production
 consensus system.
- Provider usage, dated price estimates and observed account charges are separate
 records. A paid key does not establish the counterfactual, customer benefit or cash
 savings. Keep those claims pending independently obtained evidence.
- Generated lab requests are synthetic and operator-controlled. Hardware execution
 and passing arithmetic checks do not establish an independent corpus, source truth,
 customer savings or production readiness. See `docs/CLAIMS_BOUNDARY.md`.
