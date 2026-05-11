"""回测主引擎：逐bar事件驱动，协调策略、风控、券商完成回测。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import pandas as pd

from core.backtest.broker import FillStatus, Order, OrderDirection, SimBroker
from core.backtest.config import BacktestConfig

if TYPE_CHECKING:
    from core.strategy.base import BaseStrategy, StrategyContext
    from core.strategy.risk_guard import RiskGuard
    from core.strategy.signal import Signal


@dataclass
class BacktestResult:
    """回测结果汇总"""

    # 基础指标
    total_return: float = 0.0
    annualized_return: float = 0.0
    max_drawdown: float = 0.0
    max_drawdown_duration: int = 0

    # 交易统计
    total_trades: int = 0
    win_count: int = 0
    lose_count: int = 0
    win_rate: float = 0.0
    profit_loss_ratio: float = 0.0
    avg_holding_days: float = 0.0

    # 风险指标
    sharpe_ratio: float = 0.0
    calmar_ratio: float = 0.0
    volatility: float = 0.0

    # 明细数据
    equity_curve: pd.Series = field(default_factory=lambda: pd.Series(dtype=float))
    daily_returns: pd.Series = field(default_factory=lambda: pd.Series(dtype=float))
    trade_log: list = field(default_factory=list)

    # 元信息
    symbol: str = ""
    start_date: str = ""
    end_date: str = ""
    initial_capital: float = 0.0
    final_capital: float = 0.0


class BacktestEngine:
    """回测主引擎

    职责：
    - 逐bar推进数据
    - 调用策略产生信号
    - 通过风控审核后提交订单
    - 收集回测结果
    """

    def __init__(self, config: BacktestConfig | None = None):
        self.config = config or BacktestConfig()
        self.broker = SimBroker(self.config)
        self.strategies: list[BaseStrategy] = []
        self.risk_guard: RiskGuard | None = None

    def add_strategy(self, strategy: BaseStrategy) -> None:
        """注册策略"""
        self.strategies.append(strategy)

    def set_risk_guard(self, risk_guard: RiskGuard) -> None:
        """设置风控审核器"""
        self.risk_guard = risk_guard

    def run(self, symbol: str, data: pd.DataFrame) -> BacktestResult:
        """对单只股票执行回测

        Args:
            symbol: 股票代码
            data: 日线DataFrame，需包含 date/open/high/low/close/volume 列
        """
        if data is None or len(data) < 2:
            return BacktestResult(symbol=symbol)

        # 重置broker状态
        self.broker = SimBroker(self.config)

        # 确保按日期排序
        data = data.sort_values("date").reset_index(drop=True)
        dates = pd.to_datetime(data["date"]).dt.strftime("%Y-%m-%d").values

        # 初始化策略
        for strategy in self.strategies:
            strategy.on_init(data)

        equity_records = []

        for bar_index in range(len(data)):
            bar = data.iloc[bar_index]
            current_date = dates[bar_index]
            self.broker.set_current_date(current_date)

            # 构建策略上下文
            context = self._build_context(bar, data, bar_index, symbol)

            # 收集所有策略信号
            all_signals: list[Signal] = []
            for strategy in self.strategies:
                signals = strategy.on_bar(bar, context)
                if signals:
                    all_signals.extend(signals)

            # 风控审核 + 执行
            for signal in all_signals:
                if self.risk_guard:
                    approved, reason = self.risk_guard.check(signal, context)
                    if not approved:
                        continue

                order = self._signal_to_order(signal, symbol)
                self.broker.submit_order(order)

            # 记录日终权益
            price_map = {symbol: float(bar["close"])}
            snapshot = self.broker.get_portfolio_snapshot(price_map)
            equity_records.append({
                "date": current_date,
                "equity": snapshot.total_assets,
            })

        # 生成结果
        return self._build_result(symbol, data, equity_records)

    def _build_context(
        self, bar: pd.Series, data: pd.DataFrame, bar_index: int, symbol: str
    ) -> StrategyContext:
        """构建策略上下文"""
        from core.strategy.base import PositionInfo, StrategyContext

        positions = {}
        pos = self.broker.get_position(symbol)
        if pos:
            positions[symbol] = PositionInfo(
                symbol=symbol,
                quantity=pos.total_quantity,
                average_cost=pos.average_cost,
                sellable_quantity=pos.sellable_quantity(
                    self.broker.current_date, self.config.is_t_plus_1
                ),
            )

        price_map = {symbol: float(bar["close"])}
        snapshot = self.broker.get_portfolio_snapshot(price_map)

        return StrategyContext(
            current_date=self.broker.current_date,
            available_cash=self.broker.cash,
            total_assets=snapshot.total_assets,
            positions=positions,
            history_bars=data.iloc[: bar_index + 1],
            bar_index=bar_index,
        )

    def _signal_to_order(self, signal: Signal, default_symbol: str) -> Order:
        """将策略信号转为交易订单"""
        symbol = signal.symbol or default_symbol
        direction = (
            OrderDirection.BUY if signal.direction == "BUY" else OrderDirection.SELL
        )
        return Order(
            symbol=symbol,
            direction=direction,
            price=signal.price,
            quantity=signal.quantity,
            reason=signal.reason,
            strategy_id=signal.strategy_id,
        )

    def _build_result(
        self, symbol: str, data: pd.DataFrame, equity_records: list[dict]
    ) -> BacktestResult:
        """从权益曲线和交易记录构建回测结果"""
        from core.backtest.report import compute_metrics

        if not equity_records:
            return BacktestResult(symbol=symbol)

        equity_df = pd.DataFrame(equity_records)
        equity_series = pd.Series(
            equity_df["equity"].values, index=pd.to_datetime(equity_df["date"])
        )

        # 交易日志
        trade_log = [
            fill for fill in self.broker.fill_history if fill.status == FillStatus.FILLED
        ]

        dates = pd.to_datetime(data["date"]).dt.strftime("%Y-%m-%d").values

        metrics = compute_metrics(
            equity_series=equity_series,
            initial_capital=self.config.initial_capital,
            trade_fills=trade_log,
        )

        return BacktestResult(
            total_return=metrics["total_return"],
            annualized_return=metrics["annualized_return"],
            max_drawdown=metrics["max_drawdown"],
            max_drawdown_duration=metrics["max_drawdown_duration"],
            total_trades=metrics["total_trades"],
            win_count=metrics["win_count"],
            lose_count=metrics["lose_count"],
            win_rate=metrics["win_rate"],
            profit_loss_ratio=metrics["profit_loss_ratio"],
            avg_holding_days=metrics["avg_holding_days"],
            sharpe_ratio=metrics["sharpe_ratio"],
            calmar_ratio=metrics["calmar_ratio"],
            volatility=metrics["volatility"],
            equity_curve=equity_series,
            daily_returns=metrics["daily_returns"],
            trade_log=trade_log,
            symbol=symbol,
            start_date=str(dates[0]) if len(dates) > 0 else "",
            end_date=str(dates[-1]) if len(dates) > 0 else "",
            initial_capital=self.config.initial_capital,
            final_capital=float(equity_series.iloc[-1]) if len(equity_series) > 0 else 0.0,
        )
