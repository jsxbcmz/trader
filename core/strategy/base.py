"""策略基类：所有策略的统一接口定义。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

import pandas as pd

from core.strategy.signal import Signal


@dataclass
class PositionInfo:
    """持仓摘要信息（只读，供策略查询）"""

    symbol: str
    quantity: int
    average_cost: float
    sellable_quantity: int


@dataclass
class StrategyContext:
    """策略上下文 — 由引擎在每个bar注入"""

    current_date: str
    """当前交易日"""

    available_cash: float
    """可用资金"""

    total_assets: float
    """总资产（现金+持仓市值）"""

    positions: dict[str, PositionInfo]
    """当前持仓"""

    history_bars: pd.DataFrame
    """截至当前bar的历史K线数据"""

    bar_index: int
    """当前bar在历史数据中的索引"""


class BaseStrategy(ABC):
    """策略基类

    所有策略必须继承此类并实现 on_bar 方法。
    引擎在每根K线到达时调用 on_bar，策略返回信号列表。
    """

    def __init__(self, strategy_id: str, name: str):
        self.strategy_id = strategy_id
        self.name = name

    @abstractmethod
    def on_bar(self, bar: pd.Series, context: StrategyContext) -> list[Signal]:
        """核心钩子：每根K线调用一次

        Args:
            bar: 当前K线数据（含 open/high/low/close/volume/date）
            context: 策略上下文（资金、持仓、历史数据）

        Returns:
            信号列表，无信号时返回空列表
        """

    def on_init(self, history: pd.DataFrame) -> None:
        """策略初始化回调（可选覆写）

        在回测开始前调用，可用于预热指标、缓存计算等。
        """

    def on_trade_filled(self, symbol: str, direction: str, quantity: int, price: float) -> None:
        """成交回调（可选覆写）

        当策略信号被成功执行后由引擎回调。
        """

    def calc_buy_quantity(self, price: float, available_cash: float, ratio: float = 1.0) -> int:
        """计算买入数量的通用工具方法

        Args:
            price: 买入价格
            available_cash: 可用资金
            ratio: 使用资金比例（0~1）

        Returns:
            整手买入数量（股）
        """
        if price <= 0 or available_cash <= 0:
            return 0
        budget = available_cash * ratio
        max_shares = int(budget / price)
        return (max_shares // 100) * 100

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} id={self.strategy_id} name={self.name}>"
