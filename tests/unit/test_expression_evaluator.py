from __future__ import annotations

from core.expression.builder import build_expression
from core.expression.evaluator import EvaluationContext, evaluate_at_index, evaluate_expression



def test_evaluate_field_with_offset(sample_daily_frame):
    context = EvaluationContext(sample_daily_frame)
    node = build_expression({"kind": "field", "field": "CLOSE", "offset": 1})
    values = evaluate_expression(node, context)
    assert values[0] != values[0]
    assert values[2] == 10.7



def test_evaluate_comparison_expression_at_index(sample_daily_frame, ma_condition):
    context = EvaluationContext(sample_daily_frame, target_index=2)
    node = build_expression(ma_condition)
    value = evaluate_at_index(node, context)
    assert bool(value) is True



def test_evaluate_logical_expression(sample_daily_frame):
    context = EvaluationContext(sample_daily_frame, target_index=2)
    node = build_expression(
        {
            "kind": "logical",
            "operator": "and",
            "operands": [
                {"kind": "comparison", "operator": ">", "left": {"kind": "field", "field": "CLOSE"}, "right": 10},
                {"kind": "comparison", "operator": ">", "left": {"kind": "field", "field": "VOLUME"}, "right": 1000},
            ],
        }
    )
    value = evaluate_at_index(node, context)
    assert bool(value) is True
