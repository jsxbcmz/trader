from __future__ import annotations

from typing import Any

from .nodes import ComparisonNode, ConstantNode, ExpressionNode, FieldNode, FunctionNode, LogicalNode, MathNode


_NODE_TYPES = {
    "constant",
    "field",
    "function",
    "comparison",
    "logical",
    "math",
}


def build_expression(definition: Any) -> ExpressionNode:
    if isinstance(definition, ExpressionNode):
        return definition
    if not isinstance(definition, dict):
        return ConstantNode(definition)

    kind = str(definition.get("kind", "")).strip().lower()
    if kind not in _NODE_TYPES:
        raise ValueError(f"不支持的表达式类型: {kind}")

    if kind == "constant":
        return ConstantNode(definition.get("value"), str(definition.get("value_type", "auto")))

    if kind == "field":
        return FieldNode(str(definition.get("field", "")), int(definition.get("offset", 0) or 0))

    if kind == "function":
        args = tuple(build_expression(arg) for arg in definition.get("args", []))
        return FunctionNode(str(definition.get("name", "")), args)

    if kind == "comparison":
        return ComparisonNode(
            str(definition.get("operator", "")),
            build_expression(definition.get("left")),
            build_expression(definition.get("right")),
        )

    if kind == "logical":
        operands = tuple(build_expression(arg) for arg in definition.get("operands", []))
        return LogicalNode(str(definition.get("operator", "")), operands)

    if kind == "math":
        return MathNode(
            str(definition.get("operator", "")),
            build_expression(definition.get("left")),
            build_expression(definition.get("right")),
        )

    raise ValueError(f"不支持的表达式类型: {kind}")
