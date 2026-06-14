#!/usr/bin/env python3
"""Lab Test 2 helper — turn wall-meter readings into a MEASURED energy/carbon number.

KRY's carbon denomination ships an ESTIMATE (a fixed J/token constant). With a smart
plug on two nodes you replace the estimate with a measurement: serve the same batch on
each node, read total wall energy, divide by tokens -> measured J/token. Routing a
request to the greener node instead of the dirtier one avoids the DIFFERENCE in energy,
which this converts to kWh and CO2. Pure stdlib.

Fairness note (matters for credibility): compare WALL energy on both nodes (smart plug
= whole-system). `nvidia-smi`/`powermetrics` are component-level and not comparable
across an NVIDIA box and an Apple-Silicon box — use the wall meter for the cross-node
number, the component meters only for within-node detail.

Input JSON (measurements.json):
    {"grid_co2_g_per_kwh": 400,
     "nodes": {"rtx5080": {"tokens": 50000, "energy_wh": 38.0},
               "mac_m4":  {"tokens": 50000, "energy_wh": 7.0}}}

Usage:
    python lab/energy_report.py measurements.json
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

_J_PER_KWH = 3.6e6
_J_PER_WH = 3600.0


def _reject_json_constant(value: str):
    raise ValueError(f"non-standard JSON constant {value} is not allowed")


def _json_loads(raw: str):
    return json.loads(raw, parse_constant=_reject_json_constant)


def _json_dumps(value, **kwargs) -> str:
    return json.dumps(value, allow_nan=False, **kwargs)


def _finite_number(value, field: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be a finite JSON number")
    value = float(value)
    if not math.isfinite(value):
        raise ValueError(f"{field} must be finite")
    return value


def j_per_token(energy_wh: float, tokens: float) -> float:
    """Measured joules per token = (Wh x 3600) / tokens."""
    if tokens <= 0:
        return 0.0
    return energy_wh * _J_PER_WH / tokens


def avoided_kwh(tokens: float, j_baseline: float, j_chosen: float) -> float:
    """kWh avoided by serving `tokens` on the chosen (greener) node instead of the
    baseline node. Floored at 0 (never credit a 'greener' choice that wasn't)."""
    joules = max(0.0, j_baseline - j_chosen) * tokens
    return joules / _J_PER_KWH


def report(measurements: dict) -> dict:
    grid = _finite_number(measurements.get("grid_co2_g_per_kwh", 400.0), "grid_co2_g_per_kwh")
    nodes = measurements["nodes"]
    jpt = {
        n: round(j_per_token(_finite_number(m["energy_wh"], f"{n}.energy_wh"),
                             _finite_number(m["tokens"], f"{n}.tokens")), 4)
        for n, m in nodes.items()
    }
    greenest = min(jpt, key=jpt.get)
    dirtiest = max(jpt, key=jpt.get)
    # Illustration: route 1M tokens to the greenest node instead of the dirtiest.
    demo_tokens = 1_000_000.0
    kwh = avoided_kwh(demo_tokens, jpt[dirtiest], jpt[greenest])
    return {
        "measured_j_per_token": jpt,
        "greenest_node": greenest,
        "dirtiest_node": dirtiest,
        "per_million_tokens_displaced": {
            "from": dirtiest, "to": greenest,
            "avoided_kwh": round(kwh, 6),
            "avoided_co2_g": round(kwh * grid, 4),
        },
        "note": ("measured at the wall (smart plug) — set KRY_JOULES_PER_TOKEN to the "
                 "greenest measured value to make kry_carbon a MEASURED number for this lab"),
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="KRY lab energy/carbon report from wall-meter readings")
    p.add_argument("measurements", help="JSON: {grid_co2_g_per_kwh, nodes:{name:{tokens,energy_wh}}}")
    args = p.parse_args(argv)
    rep = report(_json_loads(Path(args.measurements).read_text(encoding="utf-8")))
    print(_json_dumps(rep, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
