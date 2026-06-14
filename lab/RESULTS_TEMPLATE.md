# KRY Lab Results — <date>

> Template only. This file is not current release evidence until every row is
> filled with real machine IDs, command output, timestamps, and retained artifacts.

Filled in after running `lab/PLAYBOOK.md` on the four-node cluster. This is the
client-facing credibility artifact: every claim, its measured result, and the artifact
that backs it. Keep the honest-ceiling line at the bottom.

**Cluster:** A = i9+RTX5080/96GB · B = Mac Studio M4/48GB · C = ≈GTX1080 · D = ≈GTX1060
**Corpus:** ____ requests, ____ classes, holdout_rate ____, audit_rate ____
**Computed readiness label:** `production_ready (lab-validated)` (from `readiness_label()`)

| Test | What it proves | Result (PASS/FAIL + number) | Artifact |
|---|---|---|---|
| Suite | package healthy on every node | current suite passed on A/B/C/D | CI / pytest output |
| 1 — holdout vs truth | counterfactual recovered on real traffic | agreement ____ (bar 0.80) | `report.json`, `truth.json`, holdout_truth_check output |
| 2 — measured energy | carbon is measured, not estimated | J/token A=____ B=____; avoided ____ kWh/M tok | `measurements.json`, energy_report output |
| 3 — HOLE D | cross-node double-spend fixed | vulnerable→protected; lease atomic | hole_d run log |
| 4 — verify + F1 | stranger verifies; metered claims reconcile | VALID on B; reconcile ____% | `attestation.json`, reconcile output |
| 5 — sanctions | cheating is the losing strategy | honest audit ____; cheater audit ____ | reputation log |
| 6 — concurrency | no lost updates under real parallelism | balance == Σ deltas (Δ=____) | ledger before/after |

**Energy headline:** routing to the M4 instead of the 5080 avoids ____ g CO₂ per 1M tokens
(measured at the wall, grid ____ g/kWh).

**Honest ceiling (state to every client):** this validates the technical claims on real
distributed hardware. It does NOT claim per-event counterfactual proof (a self-reported
cache hit is unprovable — disclosed as `veracity_floor`) or dollar value at a scale we have
not run. Those require a pilot with you.
