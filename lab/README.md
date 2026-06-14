# KRY four-node lab

What a real home lab proves that synthetic tests and a single machine cannot:
an **independent oracle** (you control the router, so you know the true outcome),
**real non-synthetic traffic + energy**, and **multi-machine** behaviour — including
the one disclosed architectural limit, **HOLE D** (cross-node double-spend).

Maps to the readiness ladder (`docs/KRY_READINESS.md`): the lab can supply the
`independent_agreement` and `real_corpus` evidence that no code round can.

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
free-tier/draft/coordination nodes. Local models cost ~$0/token, so the **dollar** leg
needs one real paid-provider key; **energy** and **distributed-correctness** are real
without it.

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

**Pass:** Phase 1 shows the double-spend (confirming the disclosure is honest); Phase 2
prevents it. This empirically closes the biggest open limitation, on real machines.

## The other experiments (specs — each a falsifier)

| # | What | How | Pass / Fail |
|---|---|---|---|
| **1** | holdout recovers REAL truth | run the router with a 2–5% randomized holdout; compare KRY's Wilson CI to the true per-class paid-rate from your full-stream log | CI brackets the true rate for ≥80% of classes |
| **2** | measured energy → carbon | power-meter served vs avoided model; set `KRY_JOULES_PER_TOKEN` to your measured J/token | measured avoided kWh ≈ KRY's reported avoided energy → carbon is MEASURED not ESTIMATE |
| **4** | stranger verify across machines + F1 | Node A mints (Node D serves as the metered provider); Node B (fresh checkout, stdlib) runs `scripts/kry_verify.py`; reconcile vs D's own usage export | B verifies VALID over the network; ≥80% of T1 receipts reconcile |
| **5** | sanctions across real identities | 3 honest nodes + 1 cheater whose claims fail reconciliation vs D | cheater reputation collapses, audit → ~100%; honest nodes at the 2% floor |
| **6** | concurrency correctness | all 4 nodes hammer a shared ledger (NFS) with parallel earn/spend | final balance == Σ deltas (no lost updates) |

## Honest boundary

- This harness **simulates** four nodes locally (separate data dirs); the double-spend
 it shows is real given unmerged registries, and the lease is a **prototype** of the
 lease/nonce/TTL fix ranked in `docs/KRY_VERACITY_BINDING.md` — not a production
 consensus system.
- Local models cost ~$0/token, so the **dollar** value is only real on the leg where the
 avoided/displaced call would have hit a **paid** provider. Wire one real provider key
 for the dollar number; energy/efficiency/distributed-correctness are real without it.
- Lab traffic is real but it is **your** traffic. The lab removes the technical doubt;
 an external user's corpus is the remaining flavour of unqualified A+.
