from __future__ import annotations

from .nodes import ComparisonNode, ConstantNode, ExpressionNode, FieldNode, FunctionNode, LogicalNode, MathNode
from core.indicators.registry import get_function_spec, is_registered_function

VALID_FIELDS = {"OPEN", "HIGH", "LOW", "CLOSE", "VOLUME", "VOL", "AMOUNT", "DATE"}
VALID_COMPARISON_OPERATORS = {">", ">=", "<", "<=", "==", "!="}
VALID_LOGICAL_OPERATORS = {"and", "or", "not"}
VALID_MATH_OPERATORS = {"+", "-", "*", "/"}


class ExpressionValidationError(ValueError):
    pass


def validate_expression(node: ExpressionNode, require_boolean_result: bool = True) -> None:
    result_type = _validate_node(node)
    if require_boolean_result and result_type != "boolean":
        raise ExpressionValidationError("表达式最终结果必须为布尔类型")


def _validate_node(node: ExpressionNode) -> str:
    if isinstance(node, ConstantNode):
        return "constant"

    if isinstance(node, FieldNode):
        if not node.field or node.field.upper() not in VALID_FIELDS:
            raise ExpressionValidationError(f"不支持的字段: {node.field}")
        return "series"

    if isinstance(node, FunctionNode):
        if not node.name or not is_registered_function(node.name):
            raise ExpressionValidationError(f"未注册的函数: {node.name}")
        spec = get_function_spec(node.name)
        for arg in node.args:
            _validate_node(arg)
        if not spec.supports_arg_count(len(node.args)):
            raise ExpressionValidationError(
                f"函数 {spec.name} 参数个数不合法: {len(node.args)}"
            )
        return "series" if spec.return_kind == "series" else "multi_series"

    if isinstance(node, ComparisonNode):
        if node.operator not in VALID_COMPARISON_OPERATORS:
            raise ExpressionValidationError(f"不支持的比较运算符: {node.operator}")
        _validate_node(node.left)
        _validate_node(node.right)
        return "boolean"

    if isinstance(node, LogicalNode):
        if node.operator not in VALID_LOGICAL_OPERATORS:
            raise ExpressionValidationError(f"不支持的逻辑运算符: {node.operator}")
        if node.operator == "not":
            if len(node.operands) != 1:
                raise ExpressionValidationError("NOT 运算必须且只能有一个操作数")
        elif len(node.operands) < 2:
            raise ExpressionValidationError("AND/OR 运算至少需要两个操作数")
        for operand in node.operands:
            operand_type = _validate_node(operand)
            if operand_type != "boolean":
                raise ExpressionValidationError("逻辑运算的操作数必须为布尔表达式")
        return "boolean"

    if isinstance(node, MathNode):
        if node.operator not in VALID_MATH_OPERATORS:
            raise ExpressionValidationError(f"不支持的数学运算符: {node.operator}")
        _validate_node(node.left)
        _validate_node(node.right)
        return "series"

    raise ExpressionValidationError(f"不支持的节点类型: {type(node).__name__}")
