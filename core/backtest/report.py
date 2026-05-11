"""回测报告生成：从权益曲线和交易记录计算各项绩效指标。"""

from __future__ import annotations

import numpy as np
import pandas as pd

from core.backtest.broker import FillResult, OrderDirection


def compute_metrics(
    equity_series: pd.Series,
    initial_capital: float,
    trade_fills: list[FillResult],
) -> dict:
    """计算回测绩效指标

    Args:
        equity_series: 日终权益序列（index=日期）
        initial_capital: 初始资金
        trade_fills: 已成交的交易记录

    Returns:
        包含所有指标的字典
    """
    result = _empty_metrics()

    if len(equity_series) < 2:
        return result

    final_equity = float(equity_series.iloc[-1])

    # 总收益率
    total_return = (final_equity - initial_capital) / initial_capital
    result["total_return"] = total_return

    # 年化收益率
    trading_days = len(equity_series)
    if trading_days > 1:
        years = trading_days / 252.0
        if years > 0 and final_equity > 0:
            result["annualized_return"] = (final_equity / initial_capital) ** (1 / years) - 1

    # 日收益率序列
    daily_returns = equity_series.pct_change().dropna()
    result["daily_returns"] = daily_returns

    # 最大回撤
    max_dd, max_dd_duration = _calc_max_drawdown(equity_series)
    result["max_drawdown"] = max_dd
    result["max_drawdown_duration"] = max_dd_duration

    # 波动率（年化）
    if len(daily_returns) > 1:
        result["volatility"] = float(daily_returns.std() * np.sqrt(252))

    # 夏普比率（无风险利率取3%）
    risk_free_daily = 0.03 / 252
    if result["volatility"] > 0:
        excess_return = float(daily_returns.mean()) - risk_free_daily
        result["sharpe_ratio"] = excess_return / float(daily_returns.std()) * np.sqrt(252)

    # 卡玛比率
    if result["max_drawdown"] > 0:
        result["calmar_ratio"] = result["annualized_return"] / result["max_drawdown"]

    # 交易统计
    trade_stats = _calc_trade_stats(trade_fills)
    result.update(trade_stats)

    return result


def _calc_max_drawdown(equity_series: pd.Series) -> tuple[float, int]:
    """计算最大回撤及持续天数"""
    values = equity_series.values.astype(float)
    peak = values[0]
    max_drawdown = 0.0
    current_dd_start = 0
    max_dd_duration = 0
    current_duration = 0

    for i in range(1, len(values)):
        if values[i] > peak:
            peak = values[i]
            current_duration = 0
        else:
            drawdown = (peak - values[i]) / peak
            current_duration += 1
            if drawdown > max_drawdown:
                max_drawdown = drawdown
                max_dd_duration = current_duration

    return max_drawdown, max_dd_duration


def _calc_trade_stats(trade_fills: list[FillResult]) -> dict:
    """从交易记录计算胜率、盈亏比等"""
    buys: dict[str, list[FillResult]] = {}
    completed_trades: list[dict] = []

    for fill in trade_fills:
        symbol = fill.order.symbol
        if fill.order.direction == OrderDirection.BUY:
            buys.setdefault(symbol, []).append(fill)
        elif fill.order.direction == OrderDirection.SELL:
            # 匹配最早的买入（FIFO）
            if symbol in buys and buys[symbol]:
                buy_fill = buys[symbol].pop(0)
                pnl = (fill.fill_price - buy_fill.fill_price) * fill.fill_quantity
                holding_days = _date_diff(buy_fill.order.reason, fill.order.reason)
                completed_trades.append({
                    "pnl": pnl,
                    "return": (fill.fill_price - buy_fill.fill_price) / buy_fill.fill_price,
                    "holding_days": holding_days,
                })

    total_trades = len(completed_trades)
    # 统计买入次数作为开仓次数（即使未平仓也记录）
    total_buy_count = sum(len(v) for v in buys.values()) + total_trades
    if total_trades == 0:
        return {
            "total_trades": total_buy_count,
            "win_count": 0,
            "lose_count": 0,
            "win_rate": 0.0,
            "profit_loss_ratio": 0.0,
            "avg_holding_days": 0.0,
        }

    wins = [t for t in completed_trades if t["pnl"] > 0]
    losses = [t for t in completed_trades if t["pnl"] <= 0]
    win_count = len(wins)
    lose_count = len(losses)

    avg_win = np.mean([t["pnl"] for t in wins]) if wins else 0.0
    avg_loss = abs(np.mean([t["pnl"] for t in losses])) if losses else 1.0
    profit_loss_ratio = avg_win / avg_loss if avg_loss > 0 else float("inf")

    avg_holding = np.mean([t["holding_days"] for t in completed_trades if t["holding_days"] > 0])

    return {
        "total_trades": total_trades,
        "win_count": win_count,
        "lose_count": lose_count,
        "win_rate": win_count / total_trades if total_trades > 0 else 0.0,
        "profit_loss_ratio": float(profit_loss_ratio),
        "avg_holding_days": float(avg_holding) if not np.isnan(avg_holding) else 0.0,
    }


def _date_diff(date_str1: str, date_str2: str) -> int:
    """计算两个日期字符串间的天数差（容错）"""
    try:
        d1 = pd.Timestamp(date_str1)
        d2 = pd.Timestamp(date_str2)
        return abs((d2 - d1).days)
    except Exception:
        return 0


def _empty_metrics() -> dict:
    """返回空指标字典"""
    return {
        "total_return": 0.0,
        "annualized_return": 0.0,
        "max_drawdown": 0.0,
        "max_drawdown_duration": 0,
        "total_trades": 0,
        "win_count": 0,
        "lose_count": 0,
        "win_rate": 0.0,
        "profit_loss_ratio": 0.0,
        "avg_holding_days": 0.0,
        "sharpe_ratio": 0.0,
        "calmar_ratio": 0.0,
        "volatility": 0.0,
        "daily_returns": pd.Series(dtype=float),
    }
