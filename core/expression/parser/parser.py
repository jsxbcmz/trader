"""通达信语法分析器"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Union

from .lexer import TdxLexer, TdxToken, TdxTokenKind


@dataclass(frozen=True, slots=True)
class TdxNumber:
    """数字字面量"""
    value: float


@dataclass(frozen=True, slots=True)
class TdxString:
    """字符串字面量"""
    value: str


@dataclass(frozen=True, slots=True)
class TdxIdentifier:
    """标识符（变量引用）"""
    name: str


@dataclass(frozen=True, slots=True)
class TdxField:
    """字段引用"""
    name: str  # CLOSE, OPEN, HIGH, LOW, VOL 等


@dataclass(frozen=True, slots=True)
class TdxFunctionCall:
    """函数调用"""
    name: str
    args: tuple["TdxExpression", ...]


@dataclass(frozen=True, slots=True)
class TdxUnaryOp:
    """一元运算符"""
    operator: str  # - 或 +
    operand: "TdxExpression"


@dataclass(frozen=True, slots=True)
class TdxBinaryOp:
    """二元运算符"""
    operator: str  # +, -, *, /, >, >=, <, <=, =, <>, AND, OR
    left: "TdxExpression"
    right: "TdxExpression"


@dataclass(frozen=True, slots=True)
class TdxCondition:
    """条件表达式 (三元运算符)"""
    condition: "TdxExpression"
    true_value: "TdxExpression"
    false_value: "TdxExpression"


# 表达式类型
TdxExpression = Union[TdxNumber, TdxString, TdxIdentifier, TdxField, TdxFunctionCall, TdxUnaryOp, TdxBinaryOp, TdxCondition]


@dataclass(frozen=True, slots=True)
class TdxAssignment:
    """变量赋值语句"""
    name: str
    value: TdxExpression


@dataclass(frozen=True, slots=True)
class TdxOutput:
    """输出语句"""
    name: str
    value: TdxExpression
    style: str | None = None  # COLORRED, COLORSTICK 等


@dataclass
class TdxProgram:
    """通达信程序（一个完整的选股条件）"""

    statements: list[Union[TdxAssignment, TdxOutput]]
    variables: dict[str, TdxExpression] = field(default_factory=dict)
    outputs: dict[str, TdxOutput] = field(default_factory=dict)

    def get_output_expression(self, name: str = "选股") -> TdxExpression | None:
        """获取指定输出变量的表达式"""
        output = self.outputs.get(name)
        return output.value if output else None


class TdxParser:
    """通达信语法分析器

    将 Token 序列解析为 AST。

    语法规则（EBNF）：
    program      → statement* EOF
    statement    → assignment | output
    assignment   → IDENTIFIER ":=" expression ";"
    output       → IDENTIFIER ":" expression ("," style)? ";"
    expression   → logical_expr
    logical_expr → comparison_expr (("AND" | "OR") comparison_expr)*
    comparison   → additive_expr ((">" | ">=" | "<" | "<=" | "=" | "<>") additive_expr)?
    additive     → multiplicative_expr (("+" | "-") multiplicative_expr)*
    multiplicative → unary_expr (("*" | "/" | "%") unary_expr)*
    unary        → ("-" | "+")? primary
    primary      → NUMBER | STRING | IDENTIFIER | function_call | "(" expression ")"
    function_call → IDENTIFIER "(" arguments? ")"
    arguments    → expression ("," expression)*
    """

    # 字段简写映射
    FIELD_ALIASES = {
        "C": "CLOSE",
        "O": "OPEN",
        "H": "HIGH",
        "L": "LOW",
        "V": "VOL",
        "VOL": "VOL",
        "CLOSE": "CLOSE",
        "OPEN": "OPEN",
        "HIGH": "HIGH",
        "LOW": "LOW",
    }

    def __init__(self, tokens: list[TdxToken] | TdxLexer):
        if isinstance(tokens, TdxLexer):
            self.tokens = tokens.tokenize()
        else:
            self.tokens = tokens
        self.pos = 0

    def parse(self) -> TdxProgram:
        """解析 Token 序列为 AST"""
        statements: list[Union[TdxAssignment, TdxOutput]] = []
        variables: dict[str, TdxExpression] = {}
        outputs: dict[str, TdxOutput] = {}

        while not self._is_at_end():
            stmt = self._parse_statement()
            if stmt:
                statements.append(stmt)
                if isinstance(stmt, TdxAssignment):
                    variables[stmt.name] = stmt.value
                elif isinstance(stmt, TdxOutput):
                    outputs[stmt.name] = stmt

        return TdxProgram(statements, variables, outputs)

    def _parse_statement(self) -> TdxAssignment | TdxOutput | None:
        """解析语句"""
        if self._is_at_end():
            return None

        # 检查是否是赋值或输出语句
        if self._check(TdxTokenKind.IDENTIFIER):
            name = self._peek().value
            self._advance()

            # 赋值语句 name := expr;
            if self._check(TdxTokenKind.ASSIGN):
                self._advance()
                value = self._parse_expression()
                self._consume_semicolon()
                return TdxAssignment(name, value)

            # 输出语句 name: expr, style;
            if self._check(TdxTokenKind.OUTPUT):
                self._advance()
                value = self._parse_expression()
                style = None
                if self._check(TdxTokenKind.COMMA):
                    self._advance()
                    style = self._parse_style()
                self._consume_semicolon()
                return TdxOutput(name, value, style)

            # 否则是表达式语句（只有标识符，回退）
            self._pos_back()

        # 纯表达式语句
        expr = self._parse_expression()
        self._consume_semicolon()
        # 表达式语句默认为输出
        return TdxOutput("选股", expr)

    def _parse_style(self) -> str | None:
        """解析样式修饰符"""
        if self._check(TdxTokenKind.IDENTIFIER):
            return self._advance().value
        return None

    def _parse_expression(self) -> TdxExpression:
        """解析表达式"""
        return self._parse_logical()

    def _parse_logical(self) -> TdxExpression:
        """解析逻辑表达式"""
        left = self._parse_comparison()

        while self._check(TdxTokenKind.AND) or self._check(TdxTokenKind.OR):
            op = self._advance().value.upper()
            right = self._parse_comparison()
            left = TdxBinaryOp(op, left, right)

        return left

    def _parse_comparison(self) -> TdxExpression:
        """解析比较表达式"""
        left = self._parse_additive()

        if self._check(TdxTokenKind.GT, TdxTokenKind.GE, TdxTokenKind.LT,
                       TdxTokenKind.LE, TdxTokenKind.EQ, TdxTokenKind.NE):
            op_token = self._advance()
            op_map = {
                TdxTokenKind.GT: ">",
                TdxTokenKind.GE: ">=",
                TdxTokenKind.LT: "<",
                TdxTokenKind.LE: "<=",
                TdxTokenKind.EQ: "=",
                TdxTokenKind.NE: "<>",
            }
            right = self._parse_additive()
            return TdxBinaryOp(op_map[op_token.kind], left, right)

        return left

    def _parse_additive(self) -> TdxExpression:
        """解析加减表达式"""
        left = self._parse_multiplicative()

        while self._check(TdxTokenKind.PLUS) or self._check(TdxTokenKind.MINUS):
            op = "+" if self._check(TdxTokenKind.PLUS) else "-"
            self._advance()
            right = self._parse_multiplicative()
            left = TdxBinaryOp(op, left, right)

        return left

    def _parse_multiplicative(self) -> TdxExpression:
        """解析乘除表达式"""
        left = self._parse_unary()

        while self._check(TdxTokenKind.STAR) or self._check(TdxTokenKind.SLASH) or self._check(TdxTokenKind.PERCENT):
            if self._check(TdxTokenKind.STAR):
                op = "*"
            elif self._check(TdxTokenKind.SLASH):
                op = "/"
            else:
                op = "%"
            self._advance()
            right = self._parse_unary()
            left = TdxBinaryOp(op, left, right)

        return left

    def _parse_unary(self) -> TdxExpression:
        """解析一元表达式"""
        if self._check(TdxTokenKind.MINUS):
            self._advance()
            operand = self._parse_unary()
            return TdxUnaryOp("-", operand)
        if self._check(TdxTokenKind.PLUS):
            self._advance()
            return self._parse_unary()
        return self._parse_primary()

    def _parse_primary(self) -> TdxExpression:
        """解析基础表达式"""
        # 数字
        if self._check(TdxTokenKind.NUMBER):
            token = self._advance()
            return TdxNumber(float(token.value))

        # 字符串
        if self._check(TdxTokenKind.STRING):
            token = self._advance()
            return TdxString(token.value)

        # 括号表达式
        if self._check(TdxTokenKind.LPAREN):
            self._advance()
            expr = self._parse_expression()
            self._expect(TdxTokenKind.RPAREN, "期望 ')' ")
            return expr

        # 标识符（可能是变量、字段或函数调用）
        if self._check(TdxTokenKind.IDENTIFIER):
            name = self._advance().value

            # 函数调用
            if self._check(TdxTokenKind.LPAREN):
                return self._parse_function_call(name)

            # 字段简写
            if name.upper() in self.FIELD_ALIASES:
                return TdxField(self.FIELD_ALIASES[name.upper()])

            # 普通标识符（变量引用）
            return TdxIdentifier(name)

        # NOT 逻辑
        if self._check(TdxTokenKind.NOT):
            self._advance()
            operand = self._parse_unary()
            return TdxUnaryOp("NOT", operand)

        raise self._error(f"意外的 token: {self._peek().value}")

    def _parse_function_call(self, name: str) -> TdxFunctionCall:
        """解析函数调用"""
        self._advance()  # 跳过 (
        args: list[TdxExpression] = []

        if not self._check(TdxTokenKind.RPAREN):
            args.append(self._parse_expression())
            while self._check(TdxTokenKind.COMMA):
                self._advance()
                args.append(self._parse_expression())

        self._expect(TdxTokenKind.RPAREN, "期望 ')' ")
        return TdxFunctionCall(name.upper(), tuple(args))

    def _consume_semicolon(self) -> None:
        """消费分号（可选）"""
        if self._check(TdxTokenKind.SEMICOLON):
            self._advance()

    # Token 辅助方法
    def _peek(self) -> TdxToken:
        """查看当前 token"""
        if self.pos >= len(self.tokens):
            return self.tokens[-1]  # EOF
        return self.tokens[self.pos]

    def _advance(self) -> TdxToken:
        """前进并返回当前 token"""
        token = self._peek()
        if not self._is_at_end():
            self.pos += 1
        return token

    def _pos_back(self) -> None:
        """回退一个位置"""
        if self.pos > 0:
            self.pos -= 1

    def _check(self, *kinds: TdxTokenKind) -> bool:
        """检查当前 token 是否是指定类型"""
        if self._is_at_end():
            return False
        return self._peek().kind in kinds

    def _is_at_end(self) -> bool:
        """是否到达末尾"""
        if self.pos >= len(self.tokens):
            return True
        return self.tokens[self.pos].kind == TdxTokenKind.EOF

    def _expect(self, kind: TdxTokenKind, message: str) -> TdxToken:
        """期望指定类型的 token"""
        if self._check(kind):
            return self._advance()
        raise self._error(message)

    def _error(self, message: str) -> SyntaxError:
        """生成语法错误"""
        token = self._peek()
        return SyntaxError(f"第 {token.line} 行，第 {token.column} 列: {message}")
