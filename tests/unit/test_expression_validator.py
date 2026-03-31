from __future__ import annotations

import pytest

from core.expression.builder import build_expression
from core.expression.validator import ExpressionValidationError, validate_expression



def test_validate_expression_accepts_valid_condition(ma_condition: dict):
    node = build_expression(ma_condition)
    validate_expression(node)



def test_validate_expression_rejects_unknown_field():
    node = build_expression(
        {"kind": "comparison", "operator": ">", "left": {"kind": "field", "field": "FOO"}, "right": 1}
    )
    with pytest.raises(ExpressionValidationError, match="不支持的字段"):
        validate_expression(node)



def test_validate_expression_rejects_unknown_function():
    node = build_expression(
        {"kind": "comparison", "operator": ">", "left": {"kind": "function", "name": "UNKNOWN", "args": []}, "right": 1}
    )
    with pytest.raises(ExpressionValidationError, match="未注册的函数"):
        validate_expression(node)



def test_validate_expression_rejects_non_boolean_root():
    node = build_expression({"kind": "field", "field": "CLOSE"})
    with pytest.raises(ExpressionValidationError, match="布尔"):
        validate_expression(node)
