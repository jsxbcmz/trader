from __future__ import annotations

import pytest

from core.expression.builder import build_expression
from core.expression.nodes import ComparisonNode, ConstantNode, FieldNode, FunctionNode



def test_build_expression_constant():
    node = build_expression(5)
    assert isinstance(node, ConstantNode)
    assert node.value == 5



def test_build_expression_field():
    node = build_expression({"kind": "field", "field": "CLOSE", "offset": 1})
    assert isinstance(node, FieldNode)
    assert node.field == "CLOSE"
    assert node.offset == 1



def test_build_expression_nested_function(ma_condition: dict):
    node = build_expression(ma_condition)
    assert isinstance(node, ComparisonNode)
    assert isinstance(node.right, FunctionNode)
    assert node.right.name == "MA"



def test_build_expression_invalid_kind():
    with pytest.raises(ValueError, match="不支持的表达式类型"):
        build_expression({"kind": "unknown"})
