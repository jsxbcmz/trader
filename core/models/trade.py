"""交易相关数据模型：交易记录、持仓信息、结算结果。"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class TradeAction(Enum):
    """交易动作枚举"""

    BUY = "buy"
    SELL = "sell"


@dataclass
class TradeRecord:
    """单笔交易记录"""

    symbol: str
    name: str
    action: TradeAction
    price: float
    quantity: int
    trade_date: str
    total_amount: float


@dataclass
class HoldingPosition:
    """单只股票的持仓信息"""

    symbol: str
    name: str
    quantity: int
    average_cost: float
    total_cost: float
    current_price: float
    current_value: float
    pnl_amount: float
    pnl_percent: float

    def update_current_price(self, price: float) -> None:
        """更新当前价格并重新计算盈亏"""
        self.current_price = price
        self.current_value = price * self.quantity
        self.pnl_amount = self.current_value - self.total_cost
        self.pnl_percent = (
            (self.pnl_amount / self.total_cost * 100) if self.total_cost > 0 else 0.0
        )


@dataclass
class SettlementResult:
    """结算结果"""

    total_cost: float
    total_value: float
    total_pnl_amount: float
    total_pnl_percent: float
    trade_count: int
    trade_records: list[TradeRecord] = field(default_factory=list)
    holdings_at_settle: list[HoldingPosition] = field(default_factory=list)
