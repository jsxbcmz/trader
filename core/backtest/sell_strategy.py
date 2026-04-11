"""卖出策略：基类 + 砖形图专属实现。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

import numpy as np
import pandas as pd

from core.backtest.models import BacktestHolding, SellAction, SellSignal


class SellStrategy(ABC):
    """卖出策略抽象基类"""

    @abstractmethod
    def should_sell(
        self,
        holding: BacktestHolding,
        daily_data: pd.DataFrame,
        current_index: int,
    ) -> SellSignal:
        """判断是否应该卖出

        Args:
            holding: 当前持仓信息
            daily_data: 该股票的完整日线数据
            current_index: 当前交易日在 daily_data 中的索引

        Returns:
            SellSignal: 卖出信号
        """

    @abstractmethod
    def get_display_params(self) -> dict[str, str]:
        """返回策略参数的展示信息（用于 GUI 显示）"""


class DefaultSellStrategy(SellStrategy):
    """默认卖出策略：仅在跌破成本价时止损"""

    def should_sell(
        self,
        holding: BacktestHolding,
        daily_data: pd.DataFrame,
        current_index: int,
    ) -> SellSignal:
        current_close = daily_data.iloc[current_index]["close"]

        if current_close < holding.cost_price * 0.95:
            return SellSignal(
                action=SellAction.CLEAR,
                reason="跌破成本5%止损",
            )

        return SellSignal(action=SellAction.HOLD)

    def get_display_params(self) -> dict[str, str]:
        return {"止损线": "成本价 × 95%"}


@dataclass
class BrickChartSellStrategy(SellStrategy):
    """砖形图专属卖出策略

    止损规则（满足任一即触发）：
    - 绿砖止损：砖型图值下降
    - 破成本止损：收盘价跌破买入成本价

    止盈规则：
    - 分批止盈：涨幅 ≥ 阈值时卖出 1/4
    - 红转绿清仓：红砖首次转绿时清仓
    """

    partial_profit_threshold: float = 0.045  # 分批止盈阈值（4.5%）
    partial_sell_ratio: float = 0.25         # 分批卖出比例（1/4）

    def should_sell(
        self,
        holding: BacktestHolding,
        daily_data: pd.DataFrame,
        current_index: int,
    ) -> SellSignal:
        if current_index < 1:
            return SellSignal(action=SellAction.HOLD)

        brick_values = self._calc_brick_series(daily_data)
        if brick_values is None or current_index >= len(brick_values):
            return SellSignal(action=SellAction.HOLD)

        current_brick = brick_values[current_index]
        prev_brick = brick_values[current_index - 1]
        current_close = daily_data.iloc[current_index]["close"]

        # 止损：绿砖（砖型图值下降）
        if current_brick < prev_brick:
            return SellSignal(
                action=SellAction.CLEAR,
                reason="绿砖止损",
            )

        # 止损：跌破成本价
        if current_close < holding.cost_price:
            return SellSignal(
                action=SellAction.CLEAR,
                reason="破成本止损",
            )

        # 分批止盈：涨幅达标且未做过分批
        profit_rate = (current_close - holding.cost_price) / holding.cost_price
        if profit_rate >= self.partial_profit_threshold and not holding.partial_sold:
            return SellSignal(
                action=SellAction.PARTIAL,
                ratio=self.partial_sell_ratio,
                reason="分批止盈",
            )

        return SellSignal(action=SellAction.HOLD)

    def get_display_params(self) -> dict[str, str]:
        return {
            "分批止盈阈值": f"{self.partial_profit_threshold * 100:.1f}%",
            "分批卖出比例": f"{self.partial_sell_ratio * 100:.0f}%",
            "止损条件": "绿砖止损 / 破成本止损",
            "清仓条件": "红砖首次转绿",
        }

    def _calc_brick_series(self, daily_data: pd.DataFrame) -> np.ndarray | None:
        """计算砖型图指标序列

        复用模板中的 VAR1A-VAR6A 公式逻辑：
        VAR1A = (C - REF(C,1)) / REF(C,1) * 100
        砖型图值 = EMA(VAR1A, N) 的累积效果
        """
        close = daily_data["close"].values.astype(float)
        if len(close) < 2:
            return None

        # 计算日涨跌幅
        change_pct = np.zeros(len(close))
        change_pct[1:] = (close[1:] - close[:-1]) / close[:-1] * 100

        # 使用 EMA 平滑（周期 3）
        period = 3
        alpha = 2.0 / (period + 1)
        ema_values = np.zeros(len(change_pct))
        ema_values[0] = change_pct[0]
        for i in range(1, len(change_pct)):
            ema_values[i] = alpha * change_pct[i] + (1 - alpha) * ema_values[i - 1]

        return ema_values


# 卖出策略注册表
SELL_STRATEGY_REGISTRY: dict[str, type[SellStrategy]] = {
    "default": DefaultSellStrategy,
    "brick_chart": BrickChartSellStrategy,
}


def create_sell_strategy(name: str, params: dict | None = None) -> SellStrategy:
    """根据名称和参数创建卖出策略实例"""
    strategy_class = SELL_STRATEGY_REGISTRY.get(name)
    if strategy_class is None:
        raise ValueError(f"未知的卖出策略: {name}，可选: {list(SELL_STRATEGY_REGISTRY.keys())}")

    if params:
        return strategy_class(**params)
    return strategy_class()
