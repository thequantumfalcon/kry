# Routing lever validated on code — objective test-pass adequacy (executed)

**Date:** 2026-06-10 · **Instrument:** [`scripts/kry_code_routing_proof.py`](../../../scripts/kry_code_routing_proof.py)
· **Benchmark:** HumanEval (164 problems, hidden asserts **executed**) · **Models:** gpt-4o-mini vs gpt-4o

## Why this exists

The company estimate put new savings at ~9% **because it excluded routing** (cheap model when
adequate) for lack of validated adequacy. Routing is the biggest lever. Code is the honest place
to validate it: "adequate" = the generated code **passes the problem's own tests**, executed — no
judge. The grader was validated first: **50/50 canonical solutions pass** before any model was graded.

## Result

- **cheap (gpt-4o-mini) adequate: 138/164 = 84%** (95% CI [78%, 89%])
- frontier (gpt-4o) pass (sanity): 142/164 = 87% — barely above cheap (a known HumanEval result)
- all-frontier cost $0.2175 → routed cost $0.0535
- **routing savings: $0.164 = 75% of frontier spend**, with all 26 cheap-failures escalated to
 frontier and **charged twice** (the rate cannot be gamed by ignoring misses)

The savings rate ≈ `cheap_adequacy − cheap/frontier price ratio`. At 84% adequacy and a ~16×
price gap, that is ~75%.

## Honesty caveats — read before quoting 75%

1. **HumanEval is toy code.** Self-contained, well-specified, single-function problems. Real
 production coding (large repos, ambiguous specs, multi-file context, debugging — e.g. Uber's
 Claude Code workload) is much harder, and cheap-model adequacy there is **lower and contested**.
 So **84% / 75% is the CEILING for the easy, objectively-gradable code class — not production code.**
2. **Likely training contamination.** HumanEval is old and almost certainly in both models' training
 data, which inflates the 84%. Genuinely novel code would score lower.
3. **The rate scales with adequacy, so it degrades gracefully and stays large.** Even at a
 pessimistic production cheap-adequacy of 30–50%, routing still saves ~24–44% of frontier spend
 (`adequacy − cost_ratio`). That is still far above the 9% cache+prefix floor — which is the point:
 the floor excluded the dominant lever.
4. **Cost basis** = real tokens × stated OpenAI list prices, not provider-recorded.

## What it changes

Routing is no longer an asserted lever — it's measured (84% cheap-adequacy on gradable code → up to
~75% cost-avoidance). Two honest bounds on that ~75%: it is **model-pair-specific** (it tracks the
gpt-4o-mini↔gpt-4o ~16× price ratio, ≈ `adequacy − cost_ratio`), and it **assumes the avoided
frontier call would have been kept** — i.e. it credits a saving wherever the cheap answer matches
gold, without separately testing that the frontier would also have been right. For code/agent
workloads the honest total is well above 9%; the load-bearing unknown is now "what fraction of THIS
workload's traffic is cheap-adequate," which HumanEval's 84% over-estimates and a real-traffic
measurement would pin down.
