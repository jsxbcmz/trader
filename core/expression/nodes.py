from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

NodeKind = Literal["constant", "field", "function", "comparison", "logical", "math"]
ComparisonOperator = Literal[">", ">=", "<", "<=", "==", "!="]
LogicalOperator = Literal["and", "or", "not"]
MathOperator = Literal["+", "-", "*", "/"]


@dataclass(frozen=True)
class ExpressionNode:
    kind: NodeKind


@dataclass(frozen=True)
class ConstantNode(ExpressionNode):
    value: Any
    value_type: str = "auto"

    def __init__(self, value: Any, value_type: str = "auto") -> None:
        object.__setattr__(self, "kind", "constant")
        object.__setattr__(self, "value", value)
        object.__setattr__(self, "value_type", value_type)


@dataclass(frozen=True)
class FieldNode(ExpressionNode):
    field: str
    offset: int = 0

    def __init__(self, field: str, offset: int = 0) -> None:
        object.__setattr__(self, "kind", "field")
        object.__setattr__(self, "field", field)
        object.__setattr__(self, "offset", int(offset))


@dataclass(frozen=True)
class FunctionNode(ExpressionNode):
    name: str
    args: tuple[ExpressionNode, ...] = field(default_factory=tuple)

    def __init__(self, name: str, args: tuple[ExpressionNode, ...] = ()) -> None:
        object.__setattr__(self, "kind", "function")
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "args", tuple(args))


@dataclass(frozen=True)
class ComparisonNode(ExpressionNode):
    operator: ComparisonOperator
    left: ExpressionNode
    right: ExpressionNode

    def __init__(self, operator: ComparisonOperator, left: ExpressionNode, right: ExpressionNode) -> None:
        object.__setattr__(self, "kind", "comparison")
        object.__setattr__(self, "operator", operator)
        object.__setattr__(self, "left", left)
        object.__setattr__(self, "right", right)


@dataclass(frozen=True)
class LogicalNode(ExpressionNode):
    operator: LogicalOperator
    operands: tuple[ExpressionNode, ...] = field(default_factory=tuple)

    def __init__(self, operator: LogicalOperator, operands: tuple[ExpressionNode, ...] = ()) -> None:
        object.__setattr__(self, "kind", "logical")
        object.__setattr__(self, "operator", operator)
        object.__setattr__(self, "operands", tuple(operands))


@dataclass(frozen=True)
class MathNode(ExpressionNode):
    operator: MathOperator
    left: ExpressionNode
    right: ExpressionNode

    def __init__(self, operator: MathOperator, left: ExpressionNode, right: ExpressionNode) -> None:
        object.__setattr__(self, "kind", "math")
        object.__setattr__(self, "operator", operator)
        object.__setattr__(self, "left", left)
        object.__setattr__(self, "right", right)
