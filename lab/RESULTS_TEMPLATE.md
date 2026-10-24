# KRY Lab Results — <date>

> Template only. This file is not current release evidence until every row is
> filled with real machine IDs, command output, timestamps, and retained artifacts.

Filled in after running `lab/PLAYBOOK.md` on the four-node cluster. This is the
client-facing credibility artifact: every claim, its measured result, and the artifact
that backs it. Keep the honest-ceiling line at the bottom.

**Cluster:** A = i9+RTX5080/96GB · B = Mac Studio M4/48GB · C = ≈GTX1080 · D = ≈GTX1060
**Corpus:** ____ requests, ____ classes, holdout_rate ____, audit_rate ____
**Computed readiness label:** ____ (record `readiness_label()` inputs and their evidence;
generated lab traffic does not establish `real_corpus_validated`)

| Test | What it proves | Result (PASS/FAIL + number) | Artifact |
|---|---|---|---|
| Suite | package healthy on every node | current suite passed on A/B/C/D | CI / pytest output |
| 1 — holdout vs supplied judge | agreement with this judge on this workload | agreement ____ (bar 0.80); oracle scope ____ | `report.json`, `truth.json`, holdout_truth_check output |
| 2 — measured energy | arithmetic from supplied wall-energy readings | J/token A=____ B=____; conditional delta ____ kWh/M tok | `measurements.json`, energy_report output |
| 3 — HOLE D | conflicting lease denied in the tested setup | vulnerable→protected; lock/storage assumptions ____ | hole_d run log |
| 4 — verify + F1 | stranger verifies; metered claims reconcile | VALID on B; reconcile ____% | `attestation.json`, reconcile output |
| 5 — sanctions | reputation and proposed audit-rate arithmetic under supplied outcomes | supplied-confirmation audit ____; supplied-discrepancy audit ____ | reputation log; host enforcement remains separate |
| 6 — concurrency | no lost updates under real parallelism | balance == Σ deltas (Δ=____) | ledger before/after |

**Energy comparison:** routing the same workload to the M4 instead of the 5080
would avoid an estimated ____ g CO₂ per 1M tokens, conditional on retained wall
readings, equivalent work and supplied grid intensity ____ g/kWh.

**Evidence ceiling:** these results cover only the listed workload, hardware and
assumptions. Generated requests remain synthetic; declared tiers do not authenticate
source truth. Lab completion does not establish customer savings, an independent
real-world corpus or production readiness.
