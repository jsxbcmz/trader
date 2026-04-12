"""报告生成器：Markdown 报告 + CSV 交易明细导出。"""

from __future__ import annotations

import csv
import io
from pathlib import Path

from core.backtest.models import BacktestMetrics, BacktestResult, BacktestTradeRecord


def generate_markdown_report(result: BacktestResult) -> str:
    """生成 Markdown 格式的回测报告"""
    config = result.config
    metrics = result.metrics
    lines: list[str] = []

    lines.append("## 回测报告")
    lines.append("")

    # 基本信息
    lines.append("### 基本信息")
    lines.append(f"- **策略名称**：{config.template_name or '自定义策略'}")
    lines.append(f"- **回测区间**：{config.start_date} ~ {config.end_date}")
    lines.append(f"- **初始资金**：{config.initial_capital:,.0f} 元")
    lines.append(f"- **股票池**：{config.stock_pool_name}")
    lines.append(f"- **买入时机**：{'次日开盘价' if config.buy_timing.value == 'next_open' else '信号日收盘价'}")
    lines.append(f"- **单只仓位**：{config.position_size * 100:.0f}%")
    lines.append(f"- **最大持仓**：{config.max_positions} 只")
    lines.append(f"- **佣金费率**：万{config.commission_rate * 10000:.0f}")
    lines.append(f"- **印花税率**：千{config.stamp_tax_rate * 1000:.0f}")
    lines.append(f"- **交易天数**：{result.trading_days} 天")
    lines.append("")

    # 绩效概览
    lines.append("### 绩效概览")
    lines.append(f"- **总收益率**：{metrics.total_return * 100:+.2f}%")
    lines.append(f"- **年化收益率**：{metrics.annual_return * 100:+.2f}%")
    lines.append(f"- **最大回撤**：{metrics.max_drawdown * 100:.2f}%")
    lines.append(f"- **夏普比率**：{metrics.sharpe_ratio:.2f}")
    lines.append(f"- **胜率**：{metrics.win_rate * 100:.1f}%")
    lines.append(f"- **盈亏比**：{metrics.profit_loss_ratio:.2f}")
    lines.append(f"- **总交易次数**：{metrics.total_trades} 次")
    lines.append(f"- **平均持仓天数**：{metrics.average_hold_days:.1f} 天")
    lines.append(f"- **最大连续亏损**：{metrics.max_consecutive_losses} 次")
    lines.append(f"- **年化波动率**：{metrics.annual_volatility * 100:.2f}%")
    lines.append(f"- **Calmar 比率**：{metrics.calmar_ratio:.2f}")

    # 基准对比
    if metrics.benchmark_return != 0.0:
        lines.append(f"- **基准收益率**：{metrics.benchmark_return * 100:+.2f}%")
        lines.append(f"- **基准年化收益率**：{metrics.benchmark_annual_return * 100:+.2f}%")
        lines.append(f"- **超额收益率**：{metrics.excess_return * 100:+.2f}%")
    lines.append("")

    # 期末资产
    final_assets = result.snapshots[-1].total_assets if result.snapshots else config.initial_capital
    lines.append("### 期末资产")
    lines.append(f"- **期末总资产**：{final_assets:,.2f} 元")
    lines.append(f"- **期末现金**：{result.final_cash:,.2f} 元")
    lines.append(f"- **期末持仓数**：{len(result.final_holdings)} 只")
    lines.append("")

    # 月度收益分布
    if metrics.monthly_returns:
        lines.append("### 月度收益分布")
        lines.append("")
        lines.append("| 月份 | 收益率 | 交易次数 | 胜率 |")
        lines.append("|------|--------|----------|------|")
        for monthly in metrics.monthly_returns:
            month = monthly["month"]
            ret = monthly["return"]
            trades = monthly["trades"]
            wr = monthly["win_rate"]
            lines.append(f"| {month} | {ret * 100:+.2f}% | {trades} | {wr * 100:.0f}% |")
        lines.append("")

    # 交易明细（前 50 条）
    sell_trades = [t for t in result.trades if t.action == "SELL"]
    if sell_trades:
        lines.append("### 交易明细（卖出记录）")
        lines.append("")
        lines.append("| 日期 | 股票代码 | 股票名称 | 卖出价 | 数量 | 金额 | 原因 |")
        lines.append("|------|----------|----------|--------|------|------|------|")
        for trade in sell_trades[:50]:
            lines.append(
                f"| {trade.trade_date} | {trade.symbol} | {trade.name} "
                f"| {trade.price:.2f} | {trade.quantity} "
                f"| {trade.amount:,.0f} | {trade.reason} |"
            )
        if len(sell_trades) > 50:
            lines.append(f"| ... | 共 {len(sell_trades)} 条，仅显示前 50 条 | | | | | |")
        lines.append("")

    return "\n".join(lines)


def export_trades_csv(result: BacktestResult, file_path: str | Path) -> None:
    """导出交易明细为 CSV 文件"""
    path = Path(file_path)
    with open(path, "w", newline="", encoding="utf-8-sig") as csvfile:
        writer = csv.writer(csvfile)
        writer.writerow([
            "交易日期", "股票代码", "股票名称", "交易方向",
            "成交价", "成交数量", "成交金额",
            "佣金", "印花税", "实际金额", "原因",
        ])
        for trade in result.trades:
            writer.writerow([
                trade.trade_date,
                trade.symbol,
                trade.name,
                "买入" if trade.action == "BUY" else "卖出",
                f"{trade.price:.2f}",
                trade.quantity,
                f"{trade.amount:.2f}",
                f"{trade.commission:.2f}",
                f"{trade.stamp_tax:.2f}",
                f"{trade.total_cost:.2f}",
                trade.reason,
            ])


def export_snapshots_csv(result: BacktestResult, file_path: str | Path) -> None:
    """导出每日快照为 CSV 文件"""
    path = Path(file_path)
    with open(path, "w", newline="", encoding="utf-8-sig") as csvfile:
        writer = csv.writer(csvfile)
        writer.writerow([
            "日期", "总资产", "可用资金", "持仓市值",
            "持仓数量", "仓位占比", "当日收益率", "累计收益率",
        ])
        for snapshot in result.snapshots:
            position_ratio = getattr(snapshot, "position_ratio", 0)
            writer.writerow([
                snapshot.date,
                f"{snapshot.total_assets:.2f}",
                f"{snapshot.cash:.2f}",
                f"{snapshot.holdings_value:.2f}",
                snapshot.holdings_count,
                f"{position_ratio * 100:.1f}%",
                f"{snapshot.daily_return * 100:.4f}%",
                f"{snapshot.cumulative_return * 100:.4f}%",
            ])
