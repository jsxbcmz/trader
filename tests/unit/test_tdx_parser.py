"""通达信语法分析器和转换器测试"""

from __future__ import annotations

import pytest

from core.expression.nodes import ComparisonNode, ConstantNode, FieldNode, FunctionNode, LogicalNode, MathNode
from core.expression.parser.lexer import TdxLexer
from core.expression.parser.parser import TdxParser, TdxProgram, TdxAssignment, TdxOutput
from core.expression.parser.transpiler import TdxTranspiler, TdxTranspileError, transpile_tdx_source


class TestTdxParser:
    """语法分析器测试"""

    def test_parse_simple_assignment(self):
        """测试简单赋值语句"""
        lexer = TdxLexer("VAR1:=10;")
        tokens = lexer.tokenize()
        parser = TdxParser(tokens)
        program = parser.parse()

        assert len(program.statements) == 1
        assert isinstance(program.statements[0], TdxAssignment)
        assert program.statements[0].name == "VAR1"

    def test_parse_output_statement(self):
        """测试输出语句"""
        lexer = TdxLexer("选股:C>MA(C,5);")
        tokens = lexer.tokenize()
        parser = TdxParser(tokens)
        program = parser.parse()

        assert len(program.statements) == 1
        assert isinstance(program.statements[0], TdxOutput)
        assert program.statements[0].name == "选股"
        assert "选股" in program.outputs

    def test_parse_multiple_assignments(self):
        """测试多个赋值语句"""
        code = """
        短趋线:=EMA(EMA(C,10),10);
        多空线:=(MA(C,14)+MA(C,28))/2;
        选股:C>短趋线 AND C>多空线;
        """
        lexer = TdxLexer(code)
        tokens = lexer.tokenize()
        parser = TdxParser(tokens)
        program = parser.parse()

        assert len(program.statements) == 3
        assert "短趋线" in program.variables
        assert "多空线" in program.variables
        assert "选股" in program.outputs

    def test_parse_function_call(self):
        """测试函数调用"""
        lexer = TdxLexer("VAR1:=MA(CLOSE,5);")
        tokens = lexer.tokenize()
        parser = TdxParser(tokens)
        program = parser.parse()

        assert "VAR1" in program.variables

    def test_parse_nested_function(self):
        """测试嵌套函数调用"""
        lexer = TdxLexer("VAR1:=EMA(EMA(C,10),10);")
        tokens = lexer.tokenize()
        parser = TdxParser(tokens)
        program = parser.parse()

        assert "VAR1" in program.variables

    def test_parse_logical_expression(self):
        """测试逻辑表达式"""
        lexer = TdxLexer("选股:A AND B OR C;")
        tokens = lexer.tokenize()
        parser = TdxParser(tokens)
        program = parser.parse()

        assert "选股" in program.outputs

    def test_parse_comparison_expression(self):
        """测试比较表达式"""
        lexer = TdxLexer("选股:C>REF(C,1);")
        tokens = lexer.tokenize()
        parser = TdxParser(tokens)
        program = parser.parse()

        assert "选股" in program.outputs


class TestTdxTranspiler:
    """转换器测试"""

    def test_transpile_simple_comparison(self):
        """测试简单比较表达式"""
        node = transpile_tdx_source("选股:C>5;")

        assert isinstance(node, ComparisonNode)
        assert node.operator == ">"
        assert isinstance(node.left, FieldNode)
        assert node.left.field == "CLOSE"
        assert isinstance(node.right, ConstantNode)
        assert node.right.value == 5.0

    def test_transpile_function_call(self):
        """测试函数调用"""
        node = transpile_tdx_source("选股:C>MA(C,5);")

        assert isinstance(node, ComparisonNode)
        assert isinstance(node.right, FunctionNode)
        assert node.right.name == "MA"

    def test_transpile_logical_expression(self):
        """测试逻辑表达式"""
        node = transpile_tdx_source("选股:C>10 AND V>1000;")

        assert isinstance(node, LogicalNode)
        assert node.operator == "and"
        assert len(node.operands) == 2

    def test_transpile_variable_expansion(self):
        """测试变量展开"""
        code = """
        MA5:=MA(C,5);
        选股:C>MA5;
        """
        node = transpile_tdx_source(code)

        # 变量 MA5 应该被展开
        assert isinstance(node, ComparisonNode)
        assert isinstance(node.right, FunctionNode)
        assert node.right.name == "MA"

    def test_transpile_field_aliases(self):
        """测试字段别名"""
        # C 应该转为 CLOSE
        node1 = transpile_tdx_source("选股:C>1;")
        assert isinstance(node1, ComparisonNode)
        assert isinstance(node1.left, FieldNode)

    def test_transpile_comparison_operators(self):
        """测试比较运算符转换"""
        # 通达信的 = 应该转为 ==
        node = transpile_tdx_source("选股:C=10;")
        assert isinstance(node, ComparisonNode)
        assert node.operator == "=="

        # <> 应该转为 !=
        node2 = transpile_tdx_source("选股:C<>10;")
        assert isinstance(node2, ComparisonNode)
        assert node2.operator == "!="

    def test_transpile_math_expression(self):
        """测试数学表达式"""
        node = transpile_tdx_source("选股:(H+L)/2>10;")

        assert isinstance(node, ComparisonNode)
        assert isinstance(node.left, MathNode)

    def test_transpile_complex_condition(self):
        """测试复杂条件"""
        code = """
        短趋线:=EMA(EMA(C,10),10);
        选股:C>短趋线 AND C>10;
        """
        node = transpile_tdx_source(code)

        assert isinstance(node, LogicalNode)
        assert node.operator == "and"


class TestTdxTranspileErrors:
    """转换错误测试"""

    def test_undefined_variable(self):
        """测试未定义变量"""
        with pytest.raises(TdxTranspileError, match="未定义的变量"):
            transpile_tdx_source("选股:VAR1>10;")

    def test_missing_output(self):
        """测试缺少输出变量"""
        with pytest.raises(TdxTranspileError, match="未找到输出变量"):
            transpile_tdx_source("VAR1:=10;", output_name="选股")

    def test_circular_dependency(self):
        """测试循环依赖"""
        code = """
        A:=B+1;
        B:=A+1;
        选股:A>0;
        """
        with pytest.raises(TdxTranspileError, match="循环依赖"):
            transpile_tdx_source(code)
