"""模拟券商：订单撮合、T+1结算、交易费用、资金与持仓管理。"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from core.backtest.config import BacktestConfig


class OrderDirection(Enum):
    BUY = "BUY"
    SELL = "SELL"


class FillStatus(Enum):
    FILLED = "FILLED"
    REJECTED = "REJECTED"


@dataclass
class Order:
    """交易订单"""

    symbol: str
    direction: OrderDirection
    price: float
    quantity: int
    reason: str = ""
    strategy_id: str = ""


@dataclass
class FillResult:
    """撮合结果"""

    status: FillStatus
    order: Order
    fill_price: float = 0.0
    fill_quantity: int = 0
    commission: float = 0.0
    stamp_tax: float = 0.0
    slippage_cost: float = 0.0
    total_cost: float = 0.0
    reject_reason: str = ""

    @property
    def is_filled(self) -> bool:
        return self.status == FillStatus.FILLED


@dataclass
class Lot:
    """持仓批次（用于T+1管理）"""

    quantity: int
    buy_price: float
    buy_date: str


@dataclass
class Position:
    """单只股票的持仓"""

    symbol: str
    lots: list[Lot] = field(default_factory=list)

    @property
    def total_quantity(self) -> int:
        return sum(lot.quantity for lot in self.lots)

    @property
    def average_cost(self) -> float:
        total_qty = self.total_quantity
        if total_qty == 0:
            return 0.0
        total_cost = sum(lot.quantity * lot.buy_price for lot in self.lots)
        return total_cost / total_qty

    def sellable_quantity(self, current_date: str, is_t_plus_1: bool) -> int:
        """计算当日可卖数量"""
        if not is_t_plus_1:
            return self.total_quantity
        return sum(
            lot.quantity for lot in self.lots if lot.buy_date != current_date
        )

    def consume_lots_fifo(self, quantity: int) -> None:
        """按FIFO规则消耗持仓"""
        remaining = quantity
        new_lots = []
        for lot in self.lots:
            if remaining <= 0:
                new_lots.append(lot)
                continue
            if lot.quantity <= remaining:
                remaining -= lot.quantity
            else:
                lot.quantity -= remaining
                remaining = 0
                new_lots.append(lot)
        self.lots = new_lots


@dataclass
class PortfolioSnapshot:
    """组合快照"""

    cash: float
    positions_value: float
    total_assets: float
    positions: dict[str, Position]


class SimBroker:
    """模拟券商

    职责：
    - 接收订单并撮合
    - 管理资金和持仓
    - 实现T+1约束
    - 计算交易费用
    """

    def __init__(self, config: BacktestConfig):
        self.config = config
        self.cash: float = config.initial_capital
        self.positions: dict[str, Position] = {}
        self.fill_history: list[FillResult] = []
        self._current_date: str = ""

    def set_current_date(self, date: str) -> None:
        """设置当前交易日（由引擎每日调用）"""
        self._current_date = date

    @property
    def current_date(self) -> str:
        return self._current_date

    def submit_order(self, order: Order) -> FillResult:
        """提交订单并撮合"""
        if order.direction == OrderDirection.BUY:
            result = self._execute_buy(order)
        else:
            result = self._execute_sell(order)
        self.fill_history.append(result)
        return result

    def _execute_buy(self, order: Order) -> FillResult:
        """执行买入"""
        # 数量必须是 lot_size 的整数倍
        quantity = (order.quantity // self.config.lot_size) * self.config.lot_size
        if quantity <= 0:
            return self._reject(order, "买入数量不足一手")

        actual_price = self.config.calc_actual_buy_price(order.price)
        total_cost = self.config.calc_buy_cost(order.price, quantity)

        # 资金检查
        if total_cost > self.cash:
            # 尝试减少数量
            affordable_qty = int(self.cash / (actual_price * 1.003))  # 粗略估算
            affordable_qty = (affordable_qty // self.config.lot_size) * self.config.lot_size
            if affordable_qty <= 0:
                return self._reject(order, f"资金不足: 需要{total_cost:.0f}, 可用{self.cash:.0f}")
            quantity = affordable_qty
            total_cost = self.config.calc_buy_cost(order.price, quantity)

        # 仓位比例检查
        total_assets = self._calc_total_assets(order.price)
        if total_assets > 0:
            position_ratio = total_cost / total_assets
            if position_ratio > self.config.max_single_position_ratio:
                return self._reject(order, f"超过单票仓位上限{self.config.max_single_position_ratio:.0%}")

        # 扣减资金
        self.cash -= total_cost

        # 计算费用明细
        amount = order.price * quantity
        slippage_cost = amount * self.config.slippage_rate
        commission = max(amount * self.config.commission_rate, self.config.min_commission)

        # 增加持仓
        if order.symbol not in self.positions:
            self.positions[order.symbol] = Position(symbol=order.symbol)
        self.positions[order.symbol].lots.append(
            Lot(quantity=quantity, buy_price=actual_price, buy_date=self._current_date)
        )

        return FillResult(
            status=FillStatus.FILLED,
            order=order,
            fill_price=actual_price,
            fill_quantity=quantity,
            commission=commission,
            stamp_tax=0.0,
            slippage_cost=slippage_cost,
            total_cost=total_cost,
        )

    def _execute_sell(self, order: Order) -> FillResult:
        """执行卖出"""
        position = self.positions.get(order.symbol)
        if not position or position.total_quantity == 0:
            return self._reject(order, f"未持有{order.symbol}")

        sellable = position.sellable_quantity(self._current_date, self.config.is_t_plus_1)
        if sellable <= 0:
            return self._reject(order, "T+1限制：当日买入不可卖出")

        quantity = min(order.quantity, sellable)
        quantity = (quantity // self.config.lot_size) * self.config.lot_size
        if quantity <= 0:
            return self._reject(order, "可卖数量不足一手")

        actual_price = self.config.calc_actual_sell_price(order.price)
        net_revenue = self.config.calc_sell_revenue(order.price, quantity)

        # 计算费用明细
        amount = order.price * quantity
        slippage_cost = amount * self.config.slippage_rate
        commission = max(amount * self.config.commission_rate, self.config.min_commission)
        stamp_tax = amount * self.config.stamp_tax_rate

        # 增加资金
        self.cash += net_revenue

        # 消耗持仓
        position.consume_lots_fifo(quantity)
        if position.total_quantity == 0:
            del self.positions[order.symbol]

        return FillResult(
            status=FillStatus.FILLED,
            order=order,
            fill_price=actual_price,
            fill_quantity=quantity,
            commission=commission,
            stamp_tax=stamp_tax,
            slippage_cost=slippage_cost,
            total_cost=net_revenue,
        )

    def get_position(self, symbol: str) -> Position | None:
        return self.positions.get(symbol)

    def get_sellable_quantity(self, symbol: str) -> int:
        """获取当日可卖数量"""
        position = self.positions.get(symbol)
        if not position:
            return 0
        return position.sellable_quantity(self._current_date, self.config.is_t_plus_1)

    def get_portfolio_snapshot(self, price_map: dict[str, float]) -> PortfolioSnapshot:
        """获取组合快照"""
        positions_value = 0.0
        for symbol, pos in self.positions.items():
            price = price_map.get(symbol, 0.0)
            positions_value += pos.total_quantity * price
        return PortfolioSnapshot(
            cash=self.cash,
            positions_value=positions_value,
            total_assets=self.cash + positions_value,
            positions=dict(self.positions),
        )

    def _calc_total_assets(self, current_price: float) -> float:
        """粗略计算当前总资产（用于仓位比例判断）"""
        positions_value = sum(
            pos.total_quantity * current_price for pos in self.positions.values()
        )
        return self.cash + positions_value

    def _reject(self, order: Order, reason: str) -> FillResult:
        return FillResult(
            status=FillStatus.REJECTED,
            order=order,
            reject_reason=reason,
        )
