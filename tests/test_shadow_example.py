"""The shadow template must distinguish price estimates from provider receipts."""
import importlib.util
import sys
from pathlib import Path


def test_shadow_row_labels_litellm_costs_as_estimates(monkeypatch):
    monkeypatch.setitem(sys.modules, "litellm", None)
    path = Path(__file__).resolve().parents[1] / "examples" / "kry_litellm_shim.py"
    monkeypatch.syspath_prepend(str(path.parents[1] / "scripts"))
    spec = importlib.util.spec_from_file_location("shadow_example", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "is_checkable", lambda messages, model: "code")
    monkeypatch.setattr(module, "deterministic_check", lambda messages, output: True)
    row = module._process(
        {"litellm_call_id": "constructed-shadow", "model": "frontier",
         "messages": [], "response_cost": 0.012},
        {"model": "frontier", "usage": {"completion_tokens": 120}},
        cheap_fn=lambda messages: ("ok", 30, 40, 0.0008),
    )
    assert row["provider_cost_source"] == "pricing_table"
    assert row["cost_source"] == "pricing_table"
    assert row["measured_row_value_usd"] == 0.0112
