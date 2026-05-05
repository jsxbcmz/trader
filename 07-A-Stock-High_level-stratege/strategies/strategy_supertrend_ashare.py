"""
SuperTrend 趋势跟踪策略 — A股日线版
=====================================

算法原理
--------
SuperTrend 以 ATR（平均真实波幅）为基础，构建自适应趋势通道：

  上轨（多头支撑）= (高 + 低) / 2  -  multiplier × ATR
  下轨（空头压力）= (高 + 低) / 2  +  multiplier × ATR

  趋势判断：
    - 当前处于多头，收盘价跌破上轨 → 由多转空（清仓）
    - 当前处于空头，收盘价突破下轨 → 由空转多（买入）

  轨道自适应收紧：
    - 多头状态下，上轨只升不降（防止支撑线下滑）
    - 空头状态下，下轨只降不升（防止压力线上移）

A股适配说明
-----------
  1. T+1 约束   : 当天买入的股票次日才能卖出；信号延迟一个交易日执行
  2. 仅做多     : A股个股不支持卖空，空头信号=清仓空仓
  3. 成本扣除   :
       买入时：扣除券商佣金（单向，默认 0.03%）
       卖出时：扣除券商佣金 + 印花税（默认 0.1%）
  4. 涨跌停说明 : 回测使用收盘价计算，不显式模拟无法成交情况；
       实盘中若触及涨停买/跌停卖，需人工顺延至下一交易日
  5. 数据源     : 东方财富前复权日线（通过 core.data_provider.fetch_history_em）

参数建议（A股日线）
-------------------
  period     = 14   （常用，兼顾灵敏度与稳定性）
  multiplier = 3.0  （趋势市可降至 2.0；震荡市可升至 4.0 减少假信号）

使用方法
--------
  # 基础用法（贵州茅台，从2020年起回测）
  python strategy_supertrend_ashare.py --code 600519

  # 自定义参数
  python strategy_supertrend_ashare.py --code 000858 --period 14 --multiplier 2.5

  # 使用 SMA 方式计算 ATR（默认为 Wilder 平滑）
  python strategy_supertrend_ashare.py --code 300750 --start 20210101 --use-sma-atr
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

# 将项目根目录加入 Python 路径，确保 core 模块可被导入
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PROJECT_ROOT))

from core.data_provider import fetch_history_em

# ── 默认参数（针对A股日线优化） ─────────────────────────────────────────────────

DEFAULT_ATR_PERIOD  = 14       # ATR 计算周期，日线常用 14
DEFAULT_MULTIPLIER  = 3.0      # ATR 乘数，决定趋势带宽；越大假信号越少但入场越滞后
DEFAULT_USE_WILDER  = True     # True = Wilder EMA 平滑；False = 简单均值(SMA)
DEFAULT_COMMISSION  = 0.0003   # 券商佣金率（双向），默认 0.03%
DEFAULT_STAMP_DUTY  = 0.001    # 印花税率（仅卖出方向），默认 0.1%


# ── ATR 计算 ──────────────────────────────────────────────────────────────────

def average_true_range(
    df: pd.DataFrame,
    period: int = DEFAULT_ATR_PERIOD,
    use_wilder: bool = DEFAULT_USE_WILDER,
) -> pd.Series:
    """
    计算平均真实波幅（ATR）。

    真实波幅 TR = max(当日高-低,  |当日高 - 昨收|,  |当日低 - 昨收|)

    平滑方式：
      Wilder（默认）: ATR_i = ATR_{i-1} + (1/period) × (TR_i - ATR_{i-1})
        等价于 EMA(alpha = 1/period)，对近期波动反应更平滑，
        首值用前 period 根 TR 的均值作为种子。
      SMA: 普通滑动平均，对突发波动更敏感，min_periods 保证预热期为 NaN。

    Parameters
    ----------
    df         : 含 high / low / close 列的 DataFrame（日线）
    period     : 平滑窗口长度
    use_wilder : True 使用 Wilder EMA，False 使用 SMA
    """
    high  = df["high"].astype(float)
    low   = df["low"].astype(float)
    close = df["close"].astype(float)
    prev_close = close.shift(1)  # 昨日收盘价

    # 三种波幅取最大值，得到真实波幅 TR
    tr = pd.concat(
        [
            high - low,                    # 当日高低振幅
            (high - prev_close).abs(),     # 当日高点与昨收的差距
            (low  - prev_close).abs(),     # 当日低点与昨收的差距
        ],
        axis=1,
    ).max(axis=1)

    if not use_wilder:
        # SMA 方式：前 period-1 根为 NaN，第 period 根起才有值
        return tr.rolling(window=period, min_periods=period).mean()

    # Wilder 平滑：逐根递推，首值用前 period 根均值做种子
    atr = pd.Series(np.nan, index=df.index, dtype=float)
    if len(tr) < period:
        return atr  # 数据不足，全部返回 NaN

    atr.iloc[period - 1] = tr.iloc[:period].mean()  # 种子值
    alpha = 1.0 / float(period)
    for i in range(period, len(tr)):
        atr.iloc[i] = atr.iloc[i - 1] + alpha * (tr.iloc[i] - atr.iloc[i - 1])

    return atr


# ── SuperTrend 信号生成 ───────────────────────────────────────────────────────

def supertrend_signal(
    df: pd.DataFrame,
    period: int = DEFAULT_ATR_PERIOD,
    multiplier: float = DEFAULT_MULTIPLIER,
    use_wilder: bool = DEFAULT_USE_WILDER,
) -> pd.Series:
    """
    生成 SuperTrend 趋势信号序列。

    返回值：每根 K 线对应信号
      1.0 = 多头趋势（持仓）
      0.0 = 空头趋势（空仓）

    趋势判断逻辑：
      - up（多头支撑线）= HL2 - multiplier × ATR  ← 收盘跌破此线 → 转空
      - dn（空头压力线）= HL2 + multiplier × ATR  ← 收盘突破此线 → 转多

      自适应收紧规则（防止轨道回头给出错误信号）：
        - 多头状态且昨收 > 昨日上轨时，今日上轨 = max(今日原始上轨, 昨日上轨)
        - 空头状态且昨收 < 昨日下轨时，今日下轨 = min(今日原始下轨, 昨日下轨)

    Parameters
    ----------
    df         : 含 high / low / close 列的日线 DataFrame
    period     : ATR 计算周期
    multiplier : ATR 乘数，决定趋势带宽
    use_wilder : ATR 平滑方式
    """
    if df.empty:
        return pd.Series(dtype=float)

    atr   = average_true_range(df, period=period, use_wilder=use_wilder)
    hl2   = (df["high"].astype(float) + df["low"].astype(float)) / 2.0
    close = df["close"].astype(float)

    # 原始上下轨（尚未经过自适应收紧）
    up_raw = hl2 - multiplier * atr   # 多头支撑线（价格下方）
    dn_raw = hl2 + multiplier * atr   # 空头压力线（价格上方）

    # 经自适应调整后的实际轨道
    up    = pd.Series(np.nan, index=df.index, dtype=float)
    dn    = pd.Series(np.nan, index=df.index, dtype=float)
    trend = pd.Series(np.nan, index=df.index, dtype=float)  # 1=多头, -1=空头

    for i in range(len(df)):
        cur_up     = up_raw.iloc[i]
        cur_dn     = dn_raw.iloc[i]
        prev_up    = cur_up      # 默认：没有前值时用当前值初始化
        prev_dn    = cur_dn
        prev_trend = 1.0         # 没有前值时默认多头（冷启动）

        if i > 0:
            prev_close = close.iloc[i - 1]

            # 从已确认的序列中取上一根轨道值
            if pd.notna(up.iloc[i - 1]):
                prev_up = up.iloc[i - 1]
            if pd.notna(dn.iloc[i - 1]):
                prev_dn = dn.iloc[i - 1]
            if pd.notna(trend.iloc[i - 1]):
                prev_trend = trend.iloc[i - 1]

            # 自适应收紧：多头支撑线只升不降
            if pd.notna(prev_up) and prev_close > prev_up:
                cur_up = max(cur_up, prev_up)

            # 自适应收紧：空头压力线只降不升
            if pd.notna(prev_dn) and prev_close < prev_dn:
                cur_dn = min(cur_dn, prev_dn)

        # 趋势切换判断（用当日收盘价与上一轨道值比较）
        if prev_trend == -1.0 and pd.notna(prev_dn) and close.iloc[i] > prev_dn:
            # 空头中，收盘突破压力线 → 由空转多
            trend.iloc[i] = 1.0
        elif prev_trend == 1.0 and pd.notna(prev_up) and close.iloc[i] < prev_up:
            # 多头中，收盘跌破支撑线 → 由多转空
            trend.iloc[i] = -1.0
        else:
            # 趋势延续
            trend.iloc[i] = prev_trend

        up.iloc[i] = cur_up
        dn.iloc[i] = cur_dn

    # 返回 0/1 信号：1 = 多头持仓，0 = 空仓
    return (trend == 1.0).astype(float)


# ── A股回测引擎 ───────────────────────────────────────────────────────────────

def compute_returns_ashare(
    close: pd.Series,
    signal: pd.Series,
    commission: float = DEFAULT_COMMISSION,
    stamp_duty: float = DEFAULT_STAMP_DUTY,
) -> pd.DataFrame:
    """
    A股模拟回测（含T+1约束与交易成本）。

    T+1 实现方式
    ------------
    将信号整体延迟 1 个交易日（shift(1)）：今日收盘确认信号 → 明日开盘（以
    收盘价近似）成交。这样买入当天不会产生持仓收益，满足 T+1 要求。

    成本模型
    --------
    买入：close × commission（单向佣金）
    卖出：close × (commission + stamp_duty)（佣金 + 印花税）
    注：最低佣金（通常 5 元）、过户费等微小成本忽略不计。

    Parameters
    ----------
    close      : 前复权收盘价 Series，索引为交易日期
    signal     : SuperTrend 信号（0 或 1），与 close 同索引
    commission : 单向佣金率，默认 0.03%
    stamp_duty : 印花税率（仅卖出），默认 0.1%

    Returns
    -------
    DataFrame，列包含：
      close, signal, position（实际持仓）,
      strat_ret（含成本的策略日收益率）, bh_ret（买入持有日收益率）,
      equity（策略净值，从 1.0 起）, bh_equity（买入持有净值）
    """
    # T+1：信号延迟一个交易日才能执行
    pos = signal.shift(1).fillna(0.0)

    # 检测仓位变化，确定买卖时点
    pos_change  = pos.diff().fillna(pos.iloc[0] if len(pos) > 0 else 0.0)
    buy_signal  = pos_change > 0   # 仓位 0→1：买入
    sell_signal = pos_change < 0   # 仓位 1→0：卖出

    # 当日策略收益 = 收盘价涨跌幅 × 持仓比例
    daily_ret = close.pct_change().fillna(0.0)
    strat_ret = daily_ret * pos

    # 成本扣减（发生在交易当天）
    cost = pd.Series(0.0, index=close.index)
    cost[buy_signal]  -= commission                  # 买入佣金
    cost[sell_signal] -= commission + stamp_duty     # 卖出佣金 + 印花税

    strat_ret = strat_ret + cost

    # 累计净值曲线（起始为 1.0）
    equity    = (1 + strat_ret).cumprod()
    bh_equity = (1 + daily_ret).cumprod()  # 买入持有基准

    return pd.DataFrame(
        {
            "close":     close,
            "signal":    signal,
            "position":  pos,
            "strat_ret": strat_ret,
            "bh_ret":    daily_ret,
            "equity":    equity,
            "bh_equity": bh_equity,
        }
    )


def metrics(result_df: pd.DataFrame) -> dict:
    """
    计算策略统计指标。

    Returns
    -------
    dict，包含：
      total_return  : 策略总收益率 (%)
      annual_return : 年化收益率 (%)
      sharpe        : 夏普比率（年化，无风险利率取 0）
      max_drawdown  : 最大回撤 (%)，负值
      win_rate      : 有持仓时的日胜率 (%)
      bh_return     : 同期买入持有收益率 (%)（对照基准）
      trade_count   : 交易次数（以买入信号计数）
    """
    eq  = result_df["equity"]
    ret = result_df["strat_ret"]

    if eq.empty or len(eq) < 2:
        return {}

    # 总收益率
    total_return  = float(eq.iloc[-1] / eq.iloc[0] - 1) * 100
    n_years       = (eq.index[-1] - eq.index[0]).days / 365.25

    # 年化收益率（复利）
    annual_return = float(
        (1 + total_return / 100) ** (1 / max(n_years, 0.01)) - 1
    ) * 100

    # 夏普比率：日收益率年化（无风险利率 = 0）
    sharpe = float(ret.mean() / ret.std() * np.sqrt(252)) if ret.std() > 0 else 0.0

    # 最大回撤（负值表示亏损幅度）
    rolling_max  = eq.cummax()
    drawdown     = (eq - rolling_max) / rolling_max
    max_drawdown = float(drawdown.min()) * 100

    # 有仓位时的日胜率
    active = ret[ret != 0]
    win_rate = float((active > 0).sum() / len(active) * 100) if len(active) > 0 else 0.0

    # 买入持有对照基准收益率
    bh_eq     = result_df["bh_equity"]
    bh_return = float(bh_eq.iloc[-1] / bh_eq.iloc[0] - 1) * 100 if len(bh_eq) >= 2 else 0.0

    # 交易次数 = 仓位 0→1 的次数（每次买入算一笔）
    trade_count = int((result_df["position"].diff() > 0).sum())

    return {
        "total_return":  round(total_return,  2),
        "annual_return": round(annual_return, 2),
        "sharpe":        round(sharpe,        3),
        "max_drawdown":  round(max_drawdown,  2),
        "win_rate":      round(win_rate,      2),
        "bh_return":     round(bh_return,     2),
        "trade_count":   trade_count,
    }


# ── 主入口 ────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="SuperTrend A股日线趋势跟踪策略回测",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python strategy_supertrend_ashare.py --code 600519
  python strategy_supertrend_ashare.py --code 000858 --period 14 --multiplier 2.5
  python strategy_supertrend_ashare.py --code 300750 --start 20210101 --use-sma-atr
        """,
    )
    parser.add_argument(
        "--code",       type=str,   default="600519",
        help="A股6位代码，如 600519（贵州茅台），默认 600519",
    )
    parser.add_argument(
        "--start",      type=str,   default="20200101",
        help="回测开始日期，格式 YYYYMMDD，默认 20200101",
    )
    parser.add_argument(
        "--end",        type=str,   default=None,
        help="回测结束日期，格式 YYYYMMDD，默认取今日",
    )
    parser.add_argument(
        "--period",     type=int,   default=DEFAULT_ATR_PERIOD,
        help=f"ATR 周期，默认 {DEFAULT_ATR_PERIOD}",
    )
    parser.add_argument(
        "--multiplier", type=float, default=DEFAULT_MULTIPLIER,
        help=f"ATR 乘数，默认 {DEFAULT_MULTIPLIER}",
    )
    parser.add_argument(
        "--use-sma-atr",
        action="store_true",
        help="使用简单均值(SMA)计算 ATR，默认使用 Wilder 平滑",
    )
    args = parser.parse_args()

    end_display = args.end or "今日"
    print(f"[SuperTrend A股] 获取 {args.code} 日线数据 ({args.start} ~ {end_display}) ...")

    try:
        df = fetch_history_em(args.code, start=args.start, end=args.end)
    except Exception as e:
        print(f"[错误] 数据获取失败: {e}", file=sys.stderr)
        sys.exit(1)

    if df.empty:
        print(f"[错误] 无数据: {args.code}", file=sys.stderr)
        sys.exit(1)

    print(f"  获取到 {len(df)} 根日线 K 线（{df.index[0].date()} ~ {df.index[-1].date()}）")

    # 生成 SuperTrend 信号
    signal = supertrend_signal(
        df,
        period=args.period,
        multiplier=args.multiplier,
        use_wilder=not args.use_sma_atr,
    )

    # A股回测（含T+1约束和佣金/印花税）
    result = compute_returns_ashare(df["close"], signal)
    m = metrics(result)

    atr_mode = "Wilder EMA" if not args.use_sma_atr else "SMA"
    print(
        f"\n{'='*55}\n"
        f"  股票代码  : {args.code}\n"
        f"  ATR周期   : {args.period}   乘数: {args.multiplier}   ATR方式: {atr_mode}\n"
        f"{'='*55}\n"
        f"  策略总收益 : {m.get('total_return',  0):>8.2f}%\n"
        f"  年化收益   : {m.get('annual_return', 0):>8.2f}%\n"
        f"  夏普比率   : {m.get('sharpe',        0):>8.3f}\n"
        f"  最大回撤   : {m.get('max_drawdown',  0):>8.2f}%\n"
        f"  日胜率     : {m.get('win_rate',       0):>8.2f}%\n"
        f"  交易次数   : {m.get('trade_count',    0):>8d} 次\n"
        f"  买入持有   : {m.get('bh_return',      0):>8.2f}%   (同期对照基准)\n"
        f"{'='*55}"
    )


if __name__ == "__main__":
    main()
