"""模拟交易管理器：管理交易记录、维护持仓状态、计算盈亏、执行结算。"""

from __future__ import annotations

from dataclasses import dataclass, field

from core.models.trade import (
    HoldingPosition,
    SettlementResult,
    TradeAction,
    TradeRecord,
)


@dataclass
class TradeSimulator:
    """模拟交易管理器

    职责：
    - 管理交易记录
    - 维护持仓状态
    - 计算盈亏
    - 执行结算
    """

    holdings: dict[str, HoldingPosition] = field(default_factory=dict)
    trade_records: list[TradeRecord] = field(default_factory=list)

    def buy(
        self,
        symbol: str,
        name: str,
        price: float,
        quantity: int,
        trade_date: str,
    ) -> TradeRecord:
        """执行买入操作

        新持仓时创建 HoldingPosition；已有持仓时按加权平均更新成本价。
        """
        total_amount = price * quantity

        record = TradeRecord(
            symbol=symbol,
            name=name,
            action=TradeAction.BUY,
            price=price,
            quantity=quantity,
            trade_date=trade_date,
            total_amount=total_amount,
        )
        self.trade_records.append(record)

        if symbol in self.holdings:
            holding = self.holdings[symbol]
            new_total_cost = holding.total_cost + total_amount
            new_quantity = holding.quantity + quantity
            new_average_cost = new_total_cost / new_quantity
            holding.quantity = new_quantity
            holding.average_cost = new_average_cost
            holding.total_cost = new_total_cost
            holding.update_current_price(price)
        else:
            self.holdings[symbol] = HoldingPosition(
                symbol=symbol,
                name=name,
                quantity=quantity,
                average_cost=price,
                total_cost=total_amount,
                current_price=price,
                current_value=total_amount,
                pnl_amount=0.0,
                pnl_percent=0.0,
            )

        return record

    def sell(
        self,
        symbol: str,
        name: str,
        price: float,
        quantity: int,
        trade_date: str,
    ) -> TradeRecord:
        """执行卖出操作

        Raises:
            ValueError: 未持有该股票或持仓不足时抛出
        """
        if symbol not in self.holdings:
            raise ValueError(f"当前未持有股票 {symbol}")

        holding = self.holdings[symbol]
        if quantity > holding.quantity:
            raise ValueError(
                f"持仓不足，当前持有 {holding.quantity} 股，"
                f"尝试卖出 {quantity} 股"
            )

        total_amount = price * quantity

        record = TradeRecord(
            symbol=symbol,
            name=name,
            action=TradeAction.SELL,
            price=price,
            quantity=quantity,
            trade_date=trade_date,
            total_amount=total_amount,
        )
        self.trade_records.append(record)

        holding.quantity -= quantity
        holding.total_cost = holding.average_cost * holding.quantity

        if holding.quantity == 0:
            del self.holdings[symbol]
        else:
            holding.update_current_price(price)

        return record

    def get_holding(self, symbol: str) -> HoldingPosition | None:
        """获取指定股票的持仓信息"""
        return self.holdings.get(symbol)

    def get_all_holdings(self) -> list[HoldingPosition]:
        """获取所有持仓列表"""
        return list(self.holdings.values())

    def update_all_prices(self, price_map: dict[str, float]) -> None:
        """批量更新所有持仓的当前价格"""
        for symbol, price in price_map.items():
            if symbol in self.holdings:
                self.holdings[symbol].update_current_price(price)

    def settle(self) -> SettlementResult:
        """执行结算，汇总所有持仓的盈亏"""
        holdings_snapshot = list(self.holdings.values())
        total_cost = sum(h.total_cost for h in holdings_snapshot)
        total_value = sum(h.current_value for h in holdings_snapshot)
        total_pnl_amount = total_value - total_cost
        total_pnl_percent = (
            (total_pnl_amount / total_cost * 100) if total_cost > 0 else 0.0
        )

        return SettlementResult(
            total_cost=total_cost,
            total_value=total_value,
            total_pnl_amount=total_pnl_amount,
            total_pnl_percent=total_pnl_percent,
            trade_count=len(self.trade_records),
            trade_records=list(self.trade_records),
            holdings_at_settle=holdings_snapshot,
        )

    def reset(self) -> None:
        """重置所有交易数据"""
        self.holdings.clear()
        self.trade_records.clear()
