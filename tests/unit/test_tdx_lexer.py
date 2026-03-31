"""通达信词法分析器测试"""

from __future__ import annotations

import pytest

from core.expression.parser.lexer import TdxLexer, TdxTokenKind


class TestTdxLexer:
    """词法分析器测试"""

    def test_tokenize_simple_number(self):
        """测试简单数字"""
        lexer = TdxLexer("123")
        tokens = lexer.tokenize()
        assert len(tokens) == 2  # NUMBER + EOF
        assert tokens[0].kind == TdxTokenKind.NUMBER
        assert tokens[0].value == "123"

    def test_tokenize_float(self):
        """测试浮点数"""
        lexer = TdxLexer("3.14")
        tokens = lexer.tokenize()
        assert tokens[0].kind == TdxTokenKind.NUMBER
        assert tokens[0].value == "3.14"

    def test_tokenize_identifier(self):
        """测试标识符"""
        lexer = TdxLexer("VAR1")
        tokens = lexer.tokenize()
        assert tokens[0].kind == TdxTokenKind.IDENTIFIER
        assert tokens[0].value == "VAR1"

    def test_tokenize_chinese_identifier(self):
        """测试中文标识符"""
        lexer = TdxLexer("当日收盘价")
        tokens = lexer.tokenize()
        assert tokens[0].kind == TdxTokenKind.IDENTIFIER
        assert tokens[0].value == "当日收盘价"

    def test_tokenize_assignment(self):
        """测试赋值语句"""
        lexer = TdxLexer("VAR1:=MA(CLOSE,5);")
        tokens = lexer.tokenize()

        assert tokens[0].kind == TdxTokenKind.IDENTIFIER
        assert tokens[0].value == "VAR1"
        assert tokens[1].kind == TdxTokenKind.ASSIGN
        assert tokens[2].kind == TdxTokenKind.IDENTIFIER
        assert tokens[2].value == "MA"
        assert tokens[3].kind == TdxTokenKind.LPAREN
        assert tokens[4].kind == TdxTokenKind.IDENTIFIER
        assert tokens[4].value == "CLOSE"
        assert tokens[5].kind == TdxTokenKind.COMMA
        assert tokens[6].kind == TdxTokenKind.NUMBER
        assert tokens[6].value == "5"
        assert tokens[7].kind == TdxTokenKind.RPAREN
        assert tokens[8].kind == TdxTokenKind.SEMICOLON

    def test_tokenize_comparison_operators(self):
        """测试比较运算符"""
        lexer = TdxLexer("A>B A>=B A<B A<=B A=B A<>B")
        tokens = lexer.tokenize()

        # 过滤掉 EOF 和标识符
        ops = [t for t in tokens if t.kind in (
            TdxTokenKind.GT, TdxTokenKind.GE,
            TdxTokenKind.LT, TdxTokenKind.LE,
            TdxTokenKind.EQ, TdxTokenKind.NE
        )]

        assert ops[0].kind == TdxTokenKind.GT
        assert ops[1].kind == TdxTokenKind.GE
        assert ops[2].kind == TdxTokenKind.LT
        assert ops[3].kind == TdxTokenKind.LE
        assert ops[4].kind == TdxTokenKind.EQ
        assert ops[5].kind == TdxTokenKind.NE

    def test_tokenize_logical_keywords(self):
        """测试逻辑关键字"""
        lexer = TdxLexer("A AND B OR C")
        tokens = lexer.tokenize()

        assert any(t.kind == TdxTokenKind.AND for t in tokens)
        assert any(t.kind == TdxTokenKind.OR for t in tokens)

    def test_tokenize_comment(self):
        """测试注释"""
        lexer = TdxLexer("{这是注释}VAR1:=1;")
        tokens = lexer.tokenize()

        # 注释应该被过滤掉
        assert all(t.kind != TdxTokenKind.COMMENT for t in tokens)
        assert tokens[0].kind == TdxTokenKind.IDENTIFIER

    def test_tokenize_math_operators(self):
        """测试数学运算符"""
        lexer = TdxLexer("A+B-C*D/E")
        tokens = lexer.tokenize()

        math_ops = [t for t in tokens if t.kind in (
            TdxTokenKind.PLUS, TdxTokenKind.MINUS,
            TdxTokenKind.STAR, TdxTokenKind.SLASH
        )]

        assert len(math_ops) == 4
        assert math_ops[0].kind == TdxTokenKind.PLUS
        assert math_ops[1].kind == TdxTokenKind.MINUS
        assert math_ops[2].kind == TdxTokenKind.STAR
        assert math_ops[3].kind == TdxTokenKind.SLASH

    def test_tokenize_complex_expression(self):
        """测试复杂表达式"""
        code = """
        VAR1A:=(HHV(HIGH,4)-CLOSE)/(HHV(HIGH,4)-LLV(LOW,4))*100-90;
        选股:VAR1A>50;
        """
        lexer = TdxLexer(code)
        tokens = lexer.tokenize()

        # 检查关键字标识符
        identifiers = [t.value for t in tokens if t.kind == TdxTokenKind.IDENTIFIER]
        assert "VAR1A" in identifiers
        assert "选股" in identifiers
        assert "HHV" in identifiers
        assert "LLV" in identifiers
