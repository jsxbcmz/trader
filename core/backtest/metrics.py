"""绩效统计器：计算回测绩效指标。"""

from __future__ import annotations

import math
from collections import defaultdict

import numpy as np

from core.backtest.models import (
    BacktestConfig,
    BacktestMetrics,
    BacktestResult,
    BacktestTradeRecord,
    DailySnapshot,
)


# 年化交易日数
TRADING_DAYS_PER_YEAR = 252
# 无风险利率
RISK_FREE_RATE = 0.03


def calculate_metrics(result: BacktestResult) -> BacktestMetrics:
    """计算回测绩效指标"""
    config = result.config
    snapshots = result.snapshots
    trades = result.trades

    if not snapshots:
        return BacktestMetrics()

    # 基础收益率
    initial_capital = config.initial_capital
    final_assets = snapshots[-1].total_assets
    total_return = (final_assets / initial_capital - 1) if initial_capital > 0 else 0.0

    # 年化收益率
    trading_days = len(snapshots)
    annual_return = _annualize_return(total_return, trading_days)

    # 每日收益率序列
    daily_returns = np.array([s.daily_return for s in snapshots])

    # 年化波动率
    annual_volatility = _calc_annual_volatility(daily_returns)

    # 最大回撤
    max_drawdown = _calc_max_drawdown(snapshots)

    # 夏普比率
    sharpe_ratio = _calc_sharpe_ratio(annual_return, annual_volatility)

    # 交易统计
    completed_trades = _pair_trades(trades)
    total_trades = len(completed_trades)
    win_rate = _calc_win_rate(completed_trades)
    profit_loss_ratio = _calc_profit_loss_ratio(completed_trades)
    average_hold_days = _calc_average_hold_days(completed_trades)
    max_consecutive_losses = _calc_max_consecutive_losses(completed_trades)

    # Calmar 比率
    calmar_ratio = (
        annual_return / max_drawdown if max_drawdown > 0 else 0.0
    )

    # 月度收益分布
    monthly_returns = _calc_monthly_returns(snapshots, initial_capital, trades)

    # 基准收益率
    benchmark_return = 0.0
    benchmark_annual_return = 0.0
    excess_return = 0.0
    if result.benchmark_snapshots:
        benchmark_return = result.benchmark_snapshots[-1].cumulative_return
        benchmark_annual_return = _annualize_return(benchmark_return, trading_days)
        excess_return = total_return - benchmark_return

    return BacktestMetrics(
        total_return=total_return,
        annual_return=annual_return,
        max_drawdown=max_drawdown,
        sharpe_ratio=sharpe_ratio,
        win_rate=win_rate,
        profit_loss_ratio=profit_loss_ratio,
        total_trades=total_trades,
        average_hold_days=average_hold_days,
        max_consecutive_losses=max_consecutive_losses,
        annual_volatility=annual_volatility,
        calmar_ratio=calmar_ratio,
        monthly_returns=monthly_returns,
        benchmark_return=benchmark_return,
        benchmark_annual_return=benchmark_annual_return,
        excess_return=excess_return,
    )


def _annualize_return(total_return: float, trading_days: int) -> float:
    """总收益率转年化收益率"""
    if trading_days <= 0:
        return 0.0
    return (1 + total_return) ** (TRADING_DAYS_PER_YEAR / trading_days) - 1


def _calc_annual_volatility(daily_returns: np.ndarray) -> float:
    """计算年化波动率"""
    if len(daily_returns) < 2:
        return 0.0
    return float(np.std(daily_returns, ddof=1) * math.sqrt(TRADING_DAYS_PER_YEAR))


def _calc_max_drawdown(snapshots: list[DailySnapshot]) -> float:
    """计算最大回撤"""
    if not snapshots:
        return 0.0

    peak = snapshots[0].total_assets
    max_dd = 0.0

    for snapshot in snapshots:
        if snapshot.total_assets > peak:
            peak = snapshot.total_assets
        drawdown = (peak - snapshot.total_assets) / peak if peak > 0 else 0.0
        max_dd = max(max_dd, drawdown)

    return max_dd


def _calc_sharpe_ratio(annual_return: float, annual_volatility: float) -> float:
    """计算夏普比率"""
    if annual_volatility <= 0:
        return 0.0
    return (annual_return - RISK_FREE_RATE) / annual_volatility


def _pair_trades(trades: list[BacktestTradeRecord]) -> list[dict]:
    """将买卖交易配对，形成完整的交易记录

    Returns:
        list[dict]: 每个 dict 包含 buy_record, sell_record, profit, hold_days
    """
    # 按股票分组
    buy_records: dict[str, list[BacktestTradeRecord]] = defaultdict(list)
    completed: list[dict] = []

    for trade in trades:
        if trade.action == "BUY":
            buy_records[trade.symbol].append(trade)
        elif trade.action == "SELL":
            if buy_records[trade.symbol]:
                buy = buy_records[trade.symbol].pop(0)
                sell_amount = trade.quantity * trade.price
                buy_amount = trade.quantity * buy.price
                profit = sell_amount - buy_amount - trade.commission - trade.stamp_tax - (
                    trade.quantity / buy.quantity * buy.commission if buy.quantity > 0 else 0
                )
                hold_days = _calc_date_diff(buy.trade_date, trade.trade_date)
                completed.append({
                    "buy_record": buy,
                    "sell_record": trade,
                    "profit": profit,
                    "profit_rate": profit / buy_amount if buy_amount > 0 else 0.0,
                    "hold_days": hold_days,
                })

    return completed


def _calc_date_diff(start_date: str, end_date: str) -> int:
    """计算两个日期之间的自然天数差"""
    from datetime import datetime
    try:
        start = datetime.strptime(start_date, "%Y-%m-%d")
        end = datetime.strptime(end_date, "%Y-%m-%d")
        return (end - start).days
    except (ValueError, TypeError):
        return 0


def _calc_win_rate(completed_trades: list[dict]) -> float:
    """计算胜率"""
    if not completed_trades:
        return 0.0
    wins = sum(1 for t in completed_trades if t["profit"] > 0)
    return wins / len(completed_trades)


def _calc_profit_loss_ratio(completed_trades: list[dict]) -> float:
    """计算盈亏比"""
    profits = [t["profit"] for t in completed_trades if t["profit"] > 0]
    losses = [abs(t["profit"]) for t in completed_trades if t["profit"] < 0]

    if not profits or not losses:
        return 0.0

    avg_profit = sum(profits) / len(profits)
    avg_loss = sum(losses) / len(losses)

    return avg_profit / avg_loss if avg_loss > 0 else 0.0


def _calc_average_hold_days(completed_trades: list[dict]) -> float:
    """计算平均持仓天数"""
    if not completed_trades:
        return 0.0
    total_days = sum(t["hold_days"] for t in completed_trades)
    return total_days / len(completed_trades)


def _calc_max_consecutive_losses(completed_trades: list[dict]) -> int:
    """计算最大连续亏损次数"""
    max_streak = 0
    current_streak = 0

    for trade in completed_trades:
        if trade["profit"] < 0:
            current_streak += 1
            max_streak = max(max_streak, current_streak)
        else:
            current_streak = 0

    return max_streak


def _calc_monthly_returns(
    snapshots: list[DailySnapshot],
    initial_capital: float,
    trades: list[BacktestTradeRecord] | None = None,
) -> list[dict]:
    """计算月度收益分布"""
    if not snapshots:
        return []

    # 通过买卖配对正确计算每月胜率
    monthly_trade_stats: dict[str, dict] = {}
    if trades:
        completed = _pair_trades(trades)
        for paired in completed:
            sell_date = paired["sell_record"].trade_date
            month_key = sell_date[:7]  # "YYYY-MM"
            if month_key not in monthly_trade_stats:
                monthly_trade_stats[month_key] = {"trades": 0, "wins": 0}
            monthly_trade_stats[month_key]["trades"] += 1
            if paired["profit"] > 0:
                monthly_trade_stats[month_key]["wins"] += 1

    # 按月收集每月最后一天的总资产
    monthly_end_assets: dict[str, float] = {}
    month_order: list[str] = []

    for snapshot in snapshots:
        month_key = snapshot.date[:7]
        if month_key not in monthly_end_assets:
            month_order.append(month_key)
        monthly_end_assets[month_key] = snapshot.total_assets

    # 计算环比月度收益率
    result = []
    prev_assets = initial_capital

    for month_key in month_order:
        end = monthly_end_assets[month_key]
        monthly_return = (end / prev_assets - 1) if prev_assets > 0 else 0.0

        stats = monthly_trade_stats.get(month_key, {"trades": 0, "wins": 0})
        win_rate = stats["wins"] / stats["trades"] if stats["trades"] > 0 else 0.0

        result.append({
            "month": month_key,
            "return": monthly_return,
            "trades": stats["trades"],
            "win_rate": win_rate,
        })
        prev_assets = end

    return result
