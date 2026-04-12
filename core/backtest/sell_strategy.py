"""卖出策略：基类 + 策略注册表。"""

from __future__ import annotations

from abc import ABC, abstractmethod

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


# 卖出策略注册表
# NOTE: 延迟导入 BrickChartSellStrategy 避免循环引用
from core.backtest.brick_sell_strategy import BrickChartSellStrategy  # noqa: E402
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
