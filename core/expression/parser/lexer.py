"""通达信词法分析器"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum, auto
from typing import Iterator


class TdxTokenKind(Enum):
    """Token 类型枚举"""

    # 字面量
    NUMBER = auto()  # 数字：123, 3.14, -5
    STRING = auto()  # 字符串（通达信较少使用）

    # 标识符（变量名、函数名、字段名）
    IDENTIFIER = auto()

    # 运算符
    PLUS = auto()  # +
    MINUS = auto()  # -
    STAR = auto()  # *
    SLASH = auto()  # /
    PERCENT = auto()  # %

    # 赋值和输出
    ASSIGN = auto()  # :=
    OUTPUT = auto()  # :

    # 比较运算符
    GT = auto()  # >
    GE = auto()  # >=
    LT = auto()  # <
    LE = auto()  # <=
    EQ = auto()  # = （通达信等于用单等号）
    NE = auto()  # <> 或 !=

    # 逻辑运算符（关键字）
    AND = auto()
    OR = auto()
    NOT = auto()

    # 括号和分隔符
    LPAREN = auto()  # (
    RPAREN = auto()  # )
    COMMA = auto()  # ,
    SEMICOLON = auto()  # ;

    # 特殊
    COMMENT = auto()  # {...} 注释
    EOF = auto()


@dataclass(frozen=True, slots=True)
class TdxToken:
    """Token 结构"""

    kind: TdxTokenKind
    value: str
    line: int = 1
    column: int = 1


class TdxLexer:
    """通达信词法分析器

    将通达信条件文本拆分为 Token 序列。
    支持中文标识符、字段简写、通达信运算符等。
    """

    # 逻辑关键字
    KEYWORDS = {
        "AND": TdxTokenKind.AND,
        "OR": TdxTokenKind.OR,
        "NOT": TdxTokenKind.NOT,
        "AND#": TdxTokenKind.AND,  # 通达信变体
        "OR#": TdxTokenKind.OR,
    }

    # 字段简写映射
    FIELD_ALIASES = {
        "C": "CLOSE",
        "O": "OPEN",
        "H": "HIGH",
        "L": "LOW",
        "V": "VOL",
        "VOL": "VOL",
    }

    def __init__(self, source: str):
        self.source = source
        self.pos = 0
        self.line = 1
        self.column = 1
        self._tokens: list[TdxToken] | None = None

    def tokenize(self) -> list[TdxToken]:
        """将源代码转换为 Token 列表"""
        if self._tokens is not None:
            return self._tokens

        tokens: list[TdxToken] = []
        self._skip_whitespace_and_comments()

        while self.pos < len(self.source):
            ch = self.source[self.pos]

            # 注释 { ... }
            if ch == "{":
                self._read_comment()
                self._skip_whitespace_and_comments()
                continue

            # 数字
            if ch.isdigit() or (ch == "." and self._peek_digit()):
                tokens.append(self._read_number())
                self._skip_whitespace_and_comments()
                continue

            # 标识符（支持中文）
            if self._is_identifier_start(ch):
                tokens.append(self._read_identifier())
                self._skip_whitespace_and_comments()
                continue

            # 字符串
            if ch == '"':
                tokens.append(self._read_string())
                self._skip_whitespace_and_comments()
                continue

            # 运算符和分隔符
            token = self._read_operator()
            if token:
                tokens.append(token)
                self._skip_whitespace_and_comments()
                continue

            # 无法识别的字符，跳过
            self._advance()

        tokens.append(TdxToken(TdxTokenKind.EOF, "", self.line, self.column))
        self._tokens = tokens
        return tokens

    def _advance(self) -> str:
        """前进一个字符"""
        if self.pos >= len(self.source):
            return ""
        ch = self.source[self.pos]
        self.pos += 1
        if ch == "\n":
            self.line += 1
            self.column = 1
        else:
            self.column += 1
        return ch

    def _peek(self, offset: int = 0) -> str:
        """查看当前或后续字符"""
        idx = self.pos + offset
        if idx >= len(self.source):
            return ""
        return self.source[idx]

    def _peek_digit(self) -> bool:
        """检查下一个字符是否是数字"""
        return self._peek(1).isdigit()

    def _skip_whitespace_and_comments(self) -> None:
        """跳过空白字符和注释"""
        while self.pos < len(self.source):
            ch = self.source[self.pos]
            if ch in " \t\r\n":
                self._advance()
            elif ch == "{":
                self._read_comment()
            else:
                break

    def _read_comment(self) -> None:
        """读取注释 { ... }"""
        # 跳过 {
        self._advance()
        while self.pos < len(self.source):
            ch = self.source[self.pos]
            if ch == "}":
                self._advance()
                break
            self._advance()

    def _read_number(self) -> TdxToken:
        """读取数字"""
        start_line, start_col = self.line, self.column
        num_str = ""

        # 整数部分
        while self.pos < len(self.source) and self.source[self.pos].isdigit():
            num_str += self._advance()

        # 小数部分
        if self._peek() == "." and self._peek(1).isdigit():
            num_str += self._advance()  # .
            while self.pos < len(self.source) and self.source[self.pos].isdigit():
                num_str += self._advance()

        # 负号（前置处理在外层）
        return TdxToken(TdxTokenKind.NUMBER, num_str, start_line, start_col)

    def _is_identifier_start(self, ch: str) -> bool:
        """检查是否是标识符起始字符（支持中文）"""
        if not ch:
            return False
        return ch.isalpha() or ch == "_" or "\u4e00" <= ch <= "\u9fff"

    def _is_identifier_char(self, ch: str) -> bool:
        """检查是否是标识符字符（支持中文和%）"""
        return self._is_identifier_start(ch) or ch.isdigit() or ch == "%"

    def _read_identifier(self) -> TdxToken:
        """读取标识符"""
        start_line, start_col = self.line, self.column
        ident = ""

        while self.pos < len(self.source) and self._is_identifier_char(self.source[self.pos]):
            ident += self._advance()

        # 检查是否是关键字
        kind = self.KEYWORDS.get(ident.upper())
        if kind:
            return TdxToken(kind, ident, start_line, start_col)

        return TdxToken(TdxTokenKind.IDENTIFIER, ident, start_line, start_col)

    def _read_string(self) -> TdxToken:
        """读取字符串"""
        start_line, start_col = self.line, self.column
        self._advance()  # 跳过起始引号
        value = ""

        while self.pos < len(self.source):
            ch = self.source[self.pos]
            if ch == '"':
                self._advance()
                break
            value += self._advance()

        return TdxToken(TdxTokenKind.STRING, value, start_line, start_col)

    def _read_operator(self) -> TdxToken | None:
        """读取运算符"""
        start_line, start_col = self.line, self.column
        ch = self._peek()

        # 双字符运算符
        two_char = self.source[self.pos : self.pos + 2]
        if two_char == ":=":
            self._advance()
            self._advance()
            return TdxToken(TdxTokenKind.ASSIGN, ":=", start_line, start_col)
        if two_char == ">=":
            self._advance()
            self._advance()
            return TdxToken(TdxTokenKind.GE, ">=", start_line, start_col)
        if two_char == "<=":
            self._advance()
            self._advance()
            return TdxToken(TdxTokenKind.LE, "<=", start_line, start_col)
        if two_char == "<>":
            self._advance()
            self._advance()
            return TdxToken(TdxTokenKind.NE, "<>", start_line, start_col)
        if two_char == "!=":
            self._advance()
            self._advance()
            return TdxToken(TdxTokenKind.NE, "!=", start_line, start_col)

        # 单字符运算符
        ops = {
            "+": TdxTokenKind.PLUS,
            "-": TdxTokenKind.MINUS,
            "*": TdxTokenKind.STAR,
            "/": TdxTokenKind.SLASH,
            "%": TdxTokenKind.PERCENT,
            ":": TdxTokenKind.OUTPUT,
            ">": TdxTokenKind.GT,
            "<": TdxTokenKind.LT,
            "=": TdxTokenKind.EQ,
            "(": TdxTokenKind.LPAREN,
            ")": TdxTokenKind.RPAREN,
            ",": TdxTokenKind.COMMA,
            ";": TdxTokenKind.SEMICOLON,
        }

        if ch in ops:
            self._advance()
            return TdxToken(ops[ch], ch, start_line, start_col)

        return None

    def __iter__(self) -> Iterator[TdxToken]:
        """支持迭代"""
        return iter(self.tokenize())
