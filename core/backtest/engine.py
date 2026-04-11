"""回测引擎：时间步进 + 信号触发 + 交易执行 + 快照记录。"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from core.backtest.models import (
    BacktestConfig,
    BacktestHolding,
    BacktestResult,
    BacktestTradeRecord,
    BenchmarkSnapshot,
    BuyTiming,
    DailySnapshot,
)
from core.backtest.sell_strategy import SellStrategy, create_sell_strategy
from core.data.repository import StockRepository
from core.data.time_index import locate_time_index
from core.models.screening import ScreeningRequest
from core.screening.engine import ScreeningEngine
from core.stock_pool.manager import StockPoolManager


def _calc_buy_commission(amount: float, config: BacktestConfig) -> float:
    """计算买入佣金"""
    return max(amount * config.commission_rate, config.min_commission)


def _calc_sell_commission(amount: float, config: BacktestConfig) -> float:
    """计算卖出佣金"""
    return max(amount * config.commission_rate, config.min_commission)


def _calc_stamp_tax(amount: float, config: BacktestConfig) -> float:
    """计算印花税（仅卖出时收取）"""
    return amount * config.stamp_tax_rate


def _extract_trading_days(
    repository: StockRepository,
    start_date: str,
    end_date: str,
) -> list[str]:
    """从大盘股数据中提取实际交易日序列

    使用上证指数（000001）或平安银行（000001）的日线数据提取交易日。
    """
    benchmark_symbols = ["000001", "600000", "000002"]

    for symbol in benchmark_symbols:
        try:
            df = repository.get_daily_frame(symbol)
            if df is not None and not df.empty and "date" in df.columns:
                dates = pd.to_datetime(df["date"], errors="coerce").dropna()
                start = pd.Timestamp(start_date)
                end = pd.Timestamp(end_date)
                filtered = dates[(dates >= start) & (dates <= end)]
                trading_days = sorted(filtered.dt.strftime("%Y-%m-%d").unique().tolist())
                if trading_days:
                    return trading_days
        except Exception:
            continue

    raise ValueError(
        f"无法提取交易日历：尝试了 {benchmark_symbols}，"
        f"请确保至少有一只股票的日线数据覆盖 {start_date} ~ {end_date}"
    )


@dataclass(slots=True)
class BacktestEngine:
    """回测引擎

    职责：
    - 按交易日步进
    - 调用选股引擎生成买入信号
    - 调用卖出策略判断卖出
    - 管理资金和持仓
    - 记录每日快照
    """

    repository: StockRepository
    screening_engine: ScreeningEngine
    stock_pool_manager: StockPoolManager

    @classmethod
    def from_root(cls, root: Path) -> BacktestEngine:
        repository = StockRepository(root)
        stock_pool_manager = StockPoolManager(repository)
        screening_engine = ScreeningEngine.from_root(root)
        return cls(
            repository=repository,
            screening_engine=screening_engine,
            stock_pool_manager=stock_pool_manager,
        )

    def run(
        self,
        config: BacktestConfig,
        progress_callback: Callable[[dict], None] | None = None,
        cancelled_fn: Callable[[], bool] | None = None,
    ) -> BacktestResult:
        """执行回测主循环"""
        sell_strategy = create_sell_strategy(
            config.sell_strategy_name,
            config.sell_strategy_params or None,
        )

        # 提取交易日序列
        trading_days = _extract_trading_days(
            self.repository, config.start_date, config.end_date,
        )
        total_days = len(trading_days)

        if total_days == 0:
            raise ValueError(f"回测区间 {config.start_date} ~ {config.end_date} 内无交易日")

        # 初始化资金和持仓
        cash = config.initial_capital
        holdings: dict[str, BacktestHolding] = {}
        all_trades: list[BacktestTradeRecord] = []
        snapshots: list[DailySnapshot] = []
        prev_total_assets = config.initial_capital

        # 数据缓存：避免重复加载同一只股票的日线数据
        daily_data_cache: dict[str, pd.DataFrame] = {}

        # 待执行的买入信号（次日开盘价模式下使用）
        pending_buy_signals: list[dict] = []

        for day_index, trade_date in enumerate(trading_days):
            if cancelled_fn is not None and cancelled_fn():
                break

            today_trades: list[BacktestTradeRecord] = []

            # ── 步骤 0：执行前一日的待买入信号（次日开盘价模式）──
            if pending_buy_signals:
                for signal in pending_buy_signals:
                    buy_record = self._execute_buy(
                        signal["symbol"],
                        signal["name"],
                        trade_date,
                        cash,
                        holdings,
                        config,
                        daily_data_cache,
                        use_open_price=True,
                    )
                    if buy_record is not None:
                        cash -= buy_record.total_cost
                        today_trades.append(buy_record)
                pending_buy_signals.clear()

            # ── 步骤 1：更新持仓价格，检查卖出条件 ──
            symbols_to_remove: list[str] = []
            for symbol, holding in list(holdings.items()):
                daily_df = self._get_daily_data(symbol, daily_data_cache)
                if daily_df is None:
                    continue

                time_result = locate_time_index(daily_df, trade_date)
                if not time_result.matched or time_result.index is None:
                    continue

                current_close = float(daily_df.iloc[time_result.index]["close"])
                holding.update_price(current_close)

                # 调用卖出策略
                sell_signal = sell_strategy.should_sell(
                    holding, daily_df, time_result.index,
                )

                if sell_signal.action.value == "clear":
                    sell_record = self._execute_sell(
                        holding, current_close, trade_date,
                        holding.quantity, config, sell_signal.reason,
                    )
                    cash += sell_record.total_cost
                    today_trades.append(sell_record)
                    symbols_to_remove.append(symbol)

                elif sell_signal.action.value == "partial":
                    sell_quantity = max(
                        int(holding.quantity * sell_signal.ratio / 100) * 100,
                        100,
                    )
                    sell_quantity = min(sell_quantity, holding.quantity)
                    if sell_quantity >= 100:
                        sell_record = self._execute_sell(
                            holding, current_close, trade_date,
                            sell_quantity, config, sell_signal.reason,
                        )
                        cash += sell_record.total_cost
                        today_trades.append(sell_record)
                        holding.quantity -= sell_quantity
                        holding.total_cost = holding.cost_price * holding.quantity
                        holding.partial_sold = True
                        holding.update_price(current_close)
                        if holding.quantity <= 0:
                            symbols_to_remove.append(symbol)

            for symbol in symbols_to_remove:
                holdings.pop(symbol, None)

            # ── 步骤 2：运行选股引擎，生成买入信号 ──
            try:
                screening_request = ScreeningRequest(
                    tdx_source=config.tdx_source,
                    target_date=trade_date,
                    stock_pool_name=config.stock_pool_name,
                )
                screening_result = self.screening_engine.run(screening_request)
                matched_stocks = [
                    {"symbol": m.symbol, "name": m.name}
                    for m in screening_result.matches
                    if m.matched
                ]
            except Exception:
                matched_stocks = []

            # ── 步骤 3：执行买入 ──
            for match in matched_stocks:
                symbol = match["symbol"]
                name = match["name"]

                # 已持仓不重复买入
                if symbol in holdings:
                    continue

                # 持仓数量上限
                if len(holdings) >= config.max_positions:
                    break

                if config.buy_timing == BuyTiming.NEXT_OPEN:
                    # 次日开盘价模式：记录信号，下一个交易日执行
                    pending_buy_signals.append({"symbol": symbol, "name": name})
                else:
                    # 收盘价模式：当日执行
                    buy_record = self._execute_buy(
                        symbol, name, trade_date, cash, holdings,
                        config, daily_data_cache, use_open_price=False,
                    )
                    if buy_record is not None:
                        cash -= buy_record.total_cost
                        today_trades.append(buy_record)

            # ── 步骤 4：记录每日快照 ──
            holdings_value = sum(h.current_value for h in holdings.values())
            total_assets = cash + holdings_value
            daily_return = (total_assets / prev_total_assets - 1) if prev_total_assets > 0 else 0.0
            cumulative_return = (total_assets / config.initial_capital - 1)

            snapshot = DailySnapshot(
                date=trade_date,
                total_assets=total_assets,
                cash=cash,
                holdings_value=holdings_value,
                holdings_count=len(holdings),
                daily_return=daily_return,
                cumulative_return=cumulative_return,
                trades_today=today_trades,
                holdings_detail=[
                    {
                        "symbol": h.symbol,
                        "name": h.name,
                        "quantity": h.quantity,
                        "cost_price": h.cost_price,
                        "current_price": h.current_price,
                        "pnl_percent": h.pnl_percent,
                    }
                    for h in holdings.values()
                ],
            )
            snapshots.append(snapshot)
            all_trades.extend(today_trades)
            prev_total_assets = total_assets

            # 发送进度回调
            if progress_callback is not None:
                progress_callback({
                    "current": day_index + 1,
                    "total": total_days,
                    "date": trade_date,
                    "total_assets": total_assets,
                    "trades_today": len(today_trades),
                })

        # 加载基准数据（沪深300代理：使用大盘股日线数据）
        benchmark_snapshots = self._load_benchmark_snapshots(
            trading_days, daily_data_cache,
        )

        return BacktestResult(
            config=config,
            trades=all_trades,
            snapshots=snapshots,
            final_cash=cash,
            final_holdings=list(holdings.values()),
            trading_days=len(snapshots),
            benchmark_snapshots=benchmark_snapshots,
        )

    def _load_benchmark_snapshots(
        self,
        trading_days: list[str],
        daily_data_cache: dict[str, pd.DataFrame],
    ) -> list[BenchmarkSnapshot]:
        """加载基准指数数据，计算每日收益率和累计收益率

        使用大盘股（平安银行 000001 / 浦发银行 600000）的收盘价作为基准代理。
        """
        benchmark_symbols = ["000001", "600000", "000002"]

        for symbol in benchmark_symbols:
            daily_df = self._get_daily_data(symbol, daily_data_cache)
            if daily_df is None or daily_df.empty:
                continue

            snapshots: list[BenchmarkSnapshot] = []
            initial_close: float | None = None
            prev_close: float | None = None

            for trade_date in trading_days:
                time_result = locate_time_index(daily_df, trade_date)
                if not time_result.matched or time_result.index is None:
                    continue

                close = float(daily_df.iloc[time_result.index]["close"])

                if initial_close is None:
                    initial_close = close

                daily_return = (
                    (close / prev_close - 1) if prev_close is not None and prev_close > 0 else 0.0
                )
                cumulative_return = (close / initial_close - 1) if initial_close > 0 else 0.0

                snapshots.append(BenchmarkSnapshot(
                    date=trade_date,
                    close=close,
                    daily_return=daily_return,
                    cumulative_return=cumulative_return,
                ))
                prev_close = close

            if snapshots:
                return snapshots

        return []

    def _get_daily_data(
        self,
        symbol: str,
        cache: dict[str, pd.DataFrame],
    ) -> pd.DataFrame | None:
        """获取日线数据（带缓存）"""
        if symbol in cache:
            return cache[symbol]
        try:
            df = self.repository.get_daily_frame(symbol)
            if df is not None and not df.empty:
                cache[symbol] = df
                return df
        except Exception:
            pass
        return None

    def _execute_buy(
        self,
        symbol: str,
        name: str,
        trade_date: str,
        cash: float,
        holdings: dict[str, BacktestHolding],
        config: BacktestConfig,
        daily_data_cache: dict[str, pd.DataFrame],
        use_open_price: bool = False,
    ) -> BacktestTradeRecord | None:
        """执行买入操作"""
        # 已持仓不重复买入
        if symbol in holdings:
            return None

        # 持仓数量上限
        if len(holdings) >= config.max_positions:
            return None

        daily_df = self._get_daily_data(symbol, daily_data_cache)
        if daily_df is None:
            return None

        time_result = locate_time_index(daily_df, trade_date)
        if not time_result.matched or time_result.index is None:
            return None

        row = daily_df.iloc[time_result.index]
        buy_price = float(row["open"]) if use_open_price else float(row["close"])

        if buy_price <= 0:
            return None

        # 计算买入数量（向下取整到 100 股）
        available_amount = cash * config.position_size
        shares = int(available_amount / buy_price / 100) * 100

        if shares < 100:
            return None

        # 计算交易成本
        amount = shares * buy_price
        commission = _calc_buy_commission(amount, config)
        total_cost = amount + commission

        # 资金不足
        if total_cost > cash:
            shares = int((cash - config.min_commission) * config.position_size / buy_price / 100) * 100
            if shares < 100:
                return None
            amount = shares * buy_price
            commission = _calc_buy_commission(amount, config)
            total_cost = amount + commission
            if total_cost > cash:
                return None

        # 创建持仓
        holdings[symbol] = BacktestHolding(
            symbol=symbol,
            name=name,
            quantity=shares,
            cost_price=buy_price,
            total_cost=total_cost,
            buy_date=trade_date,
            current_price=buy_price,
            current_value=amount,
        )

        return BacktestTradeRecord(
            symbol=symbol,
            name=name,
            action="BUY",
            price=buy_price,
            quantity=shares,
            amount=amount,
            commission=commission,
            stamp_tax=0.0,
            total_cost=total_cost,
            trade_date=trade_date,
            reason="选股信号买入",
        )

    def _execute_sell(
        self,
        holding: BacktestHolding,
        sell_price: float,
        trade_date: str,
        sell_quantity: int,
        config: BacktestConfig,
        reason: str,
    ) -> BacktestTradeRecord:
        """执行卖出操作"""
        amount = sell_quantity * sell_price
        commission = _calc_sell_commission(amount, config)
        stamp_tax = _calc_stamp_tax(amount, config)
        net_amount = amount - commission - stamp_tax

        return BacktestTradeRecord(
            symbol=holding.symbol,
            name=holding.name,
            action="SELL",
            price=sell_price,
            quantity=sell_quantity,
            amount=amount,
            commission=commission,
            stamp_tax=stamp_tax,
            total_cost=net_amount,
            trade_date=trade_date,
            reason=reason,
        )
