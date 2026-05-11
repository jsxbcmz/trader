"""通达信公式驱动策略 — 用表达式定义买卖条件，自动回测。"""

from __future__ import annotations

import numpy as np
import pandas as pd

from core.expression.evaluator import EvaluationContext, evaluate_at_index
from core.expression.parser.lexer import TdxLexer
from core.expression.parser.parser import TdxParser
from core.expression.parser.transpiler import TdxTranspiler
from core.expression.nodes import ExpressionNode
from core.strategy.base import BaseStrategy, StrategyContext
from core.strategy.signal import Signal

MIN_WARMUP_BARS = 30


class ExpressionStrategy(BaseStrategy):
    """基于通达信公式表达式的通用策略

    通过 buy_expr（买入条件）和 sell_expr（卖出条件）驱动交易。
    表达式格式遵循项目现有的 core/expression/ 解析体系。

    示例:
        strategy = ExpressionStrategy(
            strategy_id="MACD_CROSS",
            name="MACD金叉",
            buy_expr="CROSS(EMA(CLOSE,12)-EMA(CLOSE,26), SMA(EMA(CLOSE,12)-EMA(CLOSE,26),9,1))",
            sell_expr="CROSS(SMA(EMA(CLOSE,12)-EMA(CLOSE,26),9,1), EMA(CLOSE,12)-EMA(CLOSE,26))",
        )
    """

    def __init__(
        self,
        strategy_id: str,
        name: str,
        buy_expr: str,
        sell_expr: str = "",
        buy_ratio: float = 1.0,
        max_holding_bars: int = 0,
    ):
        super().__init__(strategy_id, name)
        self.buy_expr_text = buy_expr
        self.sell_expr_text = sell_expr
        self.buy_ratio = buy_ratio
        self.max_holding_bars = max_holding_bars

        self._buy_node: ExpressionNode | None = self._parse_expr(buy_expr)
        self._sell_node: ExpressionNode | None = self._parse_expr(sell_expr) if sell_expr else None
        self._entry_bar_index: int | None = None

    def on_bar(self, bar: pd.Series, context: StrategyContext) -> list[Signal]:
        bar_index = context.bar_index
        if bar_index < MIN_WARMUP_BARS:
            return []

        price = float(bar["close"])
        has_position = bool(context.positions)

        # 有持仓时检查卖出条件
        if has_position:
            should_sell = False
            sell_reason = ""

            # 最大持仓时间检查
            if self.max_holding_bars > 0 and self._entry_bar_index is not None:
                holding_bars = bar_index - self._entry_bar_index
                if holding_bars >= self.max_holding_bars:
                    should_sell = True
                    sell_reason = f"持仓达{holding_bars}bar超时"

            # 表达式卖出条件
            if not should_sell and self._sell_node is not None:
                sell_triggered = self._evaluate_at(self._sell_node, context.history_bars, bar_index)
                if sell_triggered:
                    should_sell = True
                    sell_reason = f"卖出条件触发@{context.current_date}"

            if should_sell:
                # 卖出全部持仓
                pos_info = next(iter(context.positions.values()))
                sell_qty = pos_info.sellable_quantity
                if sell_qty > 0:
                    self._entry_bar_index = None
                    return [Signal(
                        strategy_id=self.strategy_id,
                        direction="SELL",
                        price=price,
                        quantity=sell_qty,
                        reason=sell_reason,
                    )]
            return []

        # 无持仓时检查买入条件
        if self._buy_node is None:
            return []

        buy_triggered = self._evaluate_at(self._buy_node, context.history_bars, bar_index)
        if not buy_triggered:
            return []

        quantity = self.calc_buy_quantity(price, context.available_cash, self.buy_ratio)
        if quantity <= 0:
            return []

        self._entry_bar_index = bar_index
        return [Signal(
            strategy_id=self.strategy_id,
            direction="BUY",
            price=price,
            quantity=quantity,
            reason=f"买入条件触发@{context.current_date}",
        )]

    def _evaluate_at(self, node: ExpressionNode, df: pd.DataFrame, index: int) -> bool:
        """在指定bar位置求值表达式"""
        eval_context = EvaluationContext(df=df, target_index=index)
        result = evaluate_at_index(node, eval_context, index)
        if isinstance(result, (bool, np.bool_)):
            return bool(result)
        if isinstance(result, (int, float, np.integer, np.floating)):
            return bool(result) and np.isfinite(result)
        return False

    def _parse_expr(self, expr_text: str) -> ExpressionNode | None:
        """解析通达信表达式文本为AST节点"""
        if not expr_text.strip():
            return None
        try:
            lexer = TdxLexer()
            tokens = lexer.tokenize(expr_text)
            parser = TdxParser()
            tdx_ast = parser.parse(tokens)
            transpiler = TdxTranspiler()
            return transpiler.transpile(tdx_ast)
        except Exception:
            return None
