from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from core.indicators.registry import get_function_spec

from .nodes import ComparisonNode, ConstantNode, ExpressionNode, FieldNode, FunctionNode, LogicalNode, MathNode

FIELD_ALIASES = {
    "OPEN": "open",
    "HIGH": "high",
    "LOW": "low",
    "CLOSE": "close",
    "VOLUME": "volume",
    "VOL": "volume",
    "AMOUNT": "volume",
    "DATE": "date",
}


@dataclass
class EvaluationContext:
    df: pd.DataFrame
    target_index: int | None = None
    cache: dict[str, Any] = field(default_factory=dict)

    def get_field_values(self, field_name: str, offset: int = 0) -> np.ndarray:
        normalized = FIELD_ALIASES.get(field_name.upper())
        if not normalized or normalized not in self.df.columns:
            raise KeyError(f"字段不存在: {field_name}")

        if normalized == "date":
            values = pd.to_datetime(self.df[normalized], errors="coerce").astype("int64").to_numpy(dtype=float)
        else:
            values = pd.to_numeric(self.df[normalized], errors="coerce").to_numpy(dtype=float)

        if offset == 0:
            return values

        shifted = np.full(values.shape, np.nan, dtype=float)
        if offset > 0:
            if offset < len(values):
                shifted[offset:] = values[:-offset]
            return shifted
        raise ValueError("字段 offset 暂不支持负数")


def evaluate_expression(node: ExpressionNode, context: EvaluationContext) -> Any:
    if isinstance(node, ConstantNode):
        return node.value

    if isinstance(node, FieldNode):
        return context.get_field_values(node.field, node.offset)

    if isinstance(node, FunctionNode):
        spec = get_function_spec(node.name)
        args = [evaluate_expression(arg, context) for arg in node.args]
        return spec.func(*args)

    if isinstance(node, MathNode):
        left = _as_array(evaluate_expression(node.left, context), context.df)
        right = _as_array(evaluate_expression(node.right, context), context.df)
        if node.operator == "+":
            return left + right
        if node.operator == "-":
            return left - right
        if node.operator == "*":
            return left * right
        if node.operator == "/":
            return np.divide(left, right, out=np.full_like(left, np.nan), where=right != 0)
        raise ValueError(f"不支持的数学运算符: {node.operator}")

    if isinstance(node, ComparisonNode):
        left = _as_array(evaluate_expression(node.left, context), context.df)
        right = _as_array(evaluate_expression(node.right, context), context.df)
        valid = np.isfinite(left) & np.isfinite(right)
        result = np.zeros(len(left), dtype=bool)
        if node.operator == ">":
            result[valid] = left[valid] > right[valid]
        elif node.operator == ">=":
            result[valid] = left[valid] >= right[valid]
        elif node.operator == "<":
            result[valid] = left[valid] < right[valid]
        elif node.operator == "<=":
            result[valid] = left[valid] <= right[valid]
        elif node.operator == "==":
            result[valid] = left[valid] == right[valid]
        elif node.operator == "!=":
            result[valid] = left[valid] != right[valid]
        else:
            raise ValueError(f"不支持的比较运算符: {node.operator}")
        return result

    if isinstance(node, LogicalNode):
        values = [_as_bool_array(evaluate_expression(operand, context), context.df) for operand in node.operands]
        if node.operator == "not":
            return ~values[0]
        if node.operator == "and":
            result = values[0].copy()
            for value in values[1:]:
                result &= value
            return result
        if node.operator == "or":
            result = values[0].copy()
            for value in values[1:]:
                result |= value
            return result
        raise ValueError(f"不支持的逻辑运算符: {node.operator}")

    raise TypeError(f"不支持的表达式节点: {type(node).__name__}")


def evaluate_at_index(node: ExpressionNode, context: EvaluationContext, index: int | None = None) -> Any:
    result = evaluate_expression(node, context)
    target = context.target_index if index is None else index
    if target is None:
        return result
    if isinstance(result, dict):
        return {key: value[target] for key, value in result.items()}
    if isinstance(result, np.ndarray):
        return result[target]
    return result


def _as_array(value: Any, df: pd.DataFrame) -> np.ndarray:
    if isinstance(value, np.ndarray):
        return value.astype(float, copy=False)
    if np.isscalar(value):
        return np.full(len(df), float(value), dtype=float)
    if isinstance(value, (list, tuple)):
        return np.asarray(value, dtype=float)
    raise TypeError(f"无法转换为数值序列: {type(value).__name__}")


def _as_bool_array(value: Any, df: pd.DataFrame) -> np.ndarray:
    if isinstance(value, np.ndarray):
        if value.dtype == bool:
            return value
        return np.isfinite(value) & (value != 0)
    if np.isscalar(value):
        return np.full(len(df), bool(value), dtype=bool)
    raise TypeError(f"无法转换为布尔序列: {type(value).__name__}")
