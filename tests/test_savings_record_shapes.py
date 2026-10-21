"""Unusable record containers cannot become savings events or crash a report."""
import importlib.util
from pathlib import Path

import pytest


@pytest.fixture
def sr():
    path = Path(__file__).resolve().parents[1] / "scripts" / "kry_savings_report.py"
    spec = importlib.util.spec_from_file_location("savings_shapes", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("record", [None, [], "not a record", 7, True])
def test_nonobject_records_are_unusable(sr, record):
    assert sr.normalize(record) is None
    assert sr.analyze([record]) == sr.analyze([])


@pytest.mark.parametrize("usage", [None, [], ["completion_tokens"], "completion_tokens", 7, True])
def test_nonobject_usage_is_unusable_even_with_top_level_counts(sr, usage):
    record = {"model": "gh/claude-opus-4.8", "cache_hit": True,
              "completion_tokens": 900, "usage": usage}
    assert sr.normalize(record) is None
    assert sr.analyze([record]) == sr.analyze([])


@pytest.mark.parametrize("nested", [False, True])
def test_valid_record_survives_mixed_unusable_containers(sr, nested):
    record = {"model": "gh/claude-opus-4.8", "cache_hit": True}
    if nested:
        record["usage"] = {"completion_tokens": 17}
    else:
        record["completion_tokens"] = 17
    assert sr.analyze([None, record, {"model": "unused", "usage": []}]) == sr.analyze([record])
    assert sr.analyze([record])["saved_kry"] == 17


@pytest.mark.parametrize("field", ["model", "model_name", "avoided_model", "served_model",
                                   "request_class", "class", "tag"])
@pytest.mark.parametrize("value", [True, 17, ["invalid"], {"invalid": 1}])
def test_nonstring_model_or_class_cannot_enter_valuation(sr, field, value):
    record = {"cache_hit": True, "completion_tokens": 17,
              "model_name" if field == "model_name" else "model": "gh/claude-opus-4.8"}
    record[field] = value
    assert sr.normalize(record) is None
    assert sr.analyze([record]) == sr.analyze([])


@pytest.mark.parametrize("field", ["cache_hit", "cached", "holdout"])
@pytest.mark.parametrize("value", [0.0, 1.0])
def test_float_flags_do_not_enter_the_documented_integer_boolean_path(sr, field, value):
    record = {"model": "gh/claude-opus-4.8", "completion_tokens": 17, field: value}
    assert sr.normalize(record) is None
    assert sr.analyze([record]) == sr.analyze([])
