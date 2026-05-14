"""内置策略实现。

便捷 re-export：调用方可用 ``from core.strategy.builtin import B1Strategy``。
"""

from core.strategy.builtin.b1_strategy import B1Strategy
from core.strategy.builtin.brick_pattern_strategy import BrickPatternStrategy
from core.strategy.builtin.expression_strategy import ExpressionStrategy

__all__ = [
    "B1Strategy",
    "BrickPatternStrategy",
    "ExpressionStrategy",
]
