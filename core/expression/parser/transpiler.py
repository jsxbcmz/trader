"""通达信到内部表达式的转换器"""

from __future__ import annotations

from typing import Any

from core.expression.nodes import (
    ComparisonNode,
    ConstantNode,
    ExpressionNode,
    FieldNode,
    FunctionNode,
    LogicalNode,
    MathNode,
)

from .parser import (
    TdxAssignment,
    TdxBinaryOp,
    TdxExpression,
    TdxField,
    TdxFunctionCall,
    TdxIdentifier,
    TdxNumber,
    TdxOutput,
    TdxProgram,
    TdxString,
    TdxUnaryOp,
)


class TdxTranspileError(Exception):
    """转换错误"""
    pass


class TdxTranspiler:
    """通达信 AST 到内部 ExpressionNode 的转换器

    主要职责：
    1. 将通达信表达式转换为内部 ExpressionNode
    2. 处理变量引用和内联展开
    3. 检测循环依赖
    """

    # 比较运算符映射
    COMPARISON_OPS = {
        ">": ">",
        ">=": ">=",
        "<": "<",
        "<=": "<=",
        "=": "==",  # 通达信的 = 转为 ==
        "<>": "!=",
    }

    def __init__(self, program: TdxProgram):
        self.program = program
        self._expanding: set[str] = set()  # 用于检测循环依赖

    def transpile(self, output_name: str = "选股") -> ExpressionNode:
        """转换程序为内部表达式

        Args:
            output_name: 输出变量名，默认为"选股"

        Returns:
            ExpressionNode: 内部表达式节点
        """
        output_expr = self.program.get_output_expression(output_name)
        if output_expr is None:
            raise TdxTranspileError(f"未找到输出变量: {output_name}")

        return self._convert_expression(output_expr)

    def _convert_expression(self, expr: TdxExpression) -> ExpressionNode:
        """递归转换表达式"""
        if isinstance(expr, TdxNumber):
            return ConstantNode(expr.value)

        if isinstance(expr, TdxString):
            return ConstantNode(expr.value, "string")

        if isinstance(expr, TdxField):
            return FieldNode(expr.name)

        if isinstance(expr, TdxIdentifier):
            return self._expand_variable(expr.name)

        if isinstance(expr, TdxUnaryOp):
            return self._convert_unary(expr)

        if isinstance(expr, TdxBinaryOp):
            return self._convert_binary(expr)

        if isinstance(expr, TdxFunctionCall):
            return self._convert_function(expr)

        raise TdxTranspileError(f"不支持的表达式类型: {type(expr).__name__}")

    def _expand_variable(self, name: str) -> ExpressionNode:
        """展开变量引用"""
        # 检查循环依赖
        if name in self._expanding:
            raise TdxTranspileError(f"检测到循环依赖: {name}")

        # 查找变量定义
        if name not in self.program.variables:
            raise TdxTranspileError(f"未定义的变量: {name}")

        self._expanding.add(name)
        try:
            return self._convert_expression(self.program.variables[name])
        finally:
            self._expanding.discard(name)

    def _convert_unary(self, expr: TdxUnaryOp) -> ExpressionNode:
        """转换一元运算符"""
        operand = self._convert_expression(expr.operand)

        if expr.operator == "-":
            # -x 转换为 0 - x
            return MathNode("-", ConstantNode(0), operand)

        if expr.operator == "+":
            return operand

        if expr.operator == "NOT":
            return LogicalNode("not", (operand,))

        raise TdxTranspileError(f"不支持的一元运算符: {expr.operator}")

    def _convert_binary(self, expr: TdxBinaryOp) -> ExpressionNode:
        """转换二元运算符"""
        left = self._convert_expression(expr.left)
        right = self._convert_expression(expr.right)

        op = expr.operator.upper()

        # 逻辑运算符
        if op == "AND":
            return LogicalNode("and", (left, right))
        if op == "OR":
            return LogicalNode("or", (left, right))

        # 比较运算符
        if expr.operator in self.COMPARISON_OPS:
            return ComparisonNode(self.COMPARISON_OPS[expr.operator], left, right)

        # 数学运算符
        if expr.operator in ("+", "-", "*", "/", "%"):
            return MathNode(expr.operator, left, right)

        raise TdxTranspileError(f"不支持的二元运算符: {expr.operator}")

    def _convert_function(self, expr: TdxFunctionCall) -> ExpressionNode:
        """转换函数调用"""
        args = tuple(self._convert_expression(arg) for arg in expr.args)
        return FunctionNode(expr.name, args)


def transpile_tdx_source(source: str, output_name: str = "选股") -> ExpressionNode:
    """便捷函数：将通达信源码直接转换为内部表达式

    Args:
        source: 通达信条件代码
        output_name: 输出变量名，默认为"选股"

    Returns:
        ExpressionNode: 内部表达式节点
    """
    from .lexer import TdxLexer
    from .parser import TdxParser

    lexer = TdxLexer(source)
    tokens = lexer.tokenize()
    parser = TdxParser(tokens)
    program = parser.parse()
    transpiler = TdxTranspiler(program)
    return transpiler.transpile(output_name)
