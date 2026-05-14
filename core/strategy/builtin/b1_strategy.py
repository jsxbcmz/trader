"""B1 量价共振策略

核心计算函数 ``compute_b1_signals`` 同时被回测脚本 backtest_b1.py 直接复用，
本模块是 B1 信号定义的唯一权威来源。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from core.strategy.base import BaseStrategy, StrategyContext
from core.strategy.signal import Signal

MIN_HISTORY_BARS = 250


def compute_b1_signals(df: pd.DataFrame) -> np.ndarray:
    """对整个 DataFrame 逐日计算 B1 信号，返回布尔数组。

    要求 df 含 open/close/high/low/volume 列；前 ``MIN_HISTORY_BARS`` 行
    数据不足，结果强制为 False。
    """
    n = len(df)
    open_arr = df["open"].values.astype(float)
    close_arr = df["close"].values.astype(float)
    high_arr = df["high"].values.astype(float)
    low_arr = df["low"].values.astype(float)
    vol_arr = df["volume"].values.astype(float)

    # --- K 线分类 ---
    real_yang = (close_arr > open_arr) & ~(close_arr < np.roll(close_arr, 1))
    real_yin = (close_arr < open_arr) & ~(close_arr > np.roll(close_arr, 1))
    real_yang[0] = False
    real_yin[0] = False

    # --- KDJ ---
    rsv = np.full(n, 50.0)
    k_val = np.full(n, 50.0)
    d_val = np.full(n, 50.0)
    for i in range(8, n):
        low9 = np.min(low_arr[i - 8:i + 1])
        high9 = np.max(high_arr[i - 8:i + 1])
        if high9 - low9 > 1e-9:
            rsv[i] = (close_arr[i] - low9) / (high9 - low9) * 100
        else:
            rsv[i] = 50.0
    for i in range(1, n):
        k_val[i] = (2 * k_val[i - 1] + rsv[i]) / 3
        d_val[i] = (2 * d_val[i - 1] + k_val[i]) / 3
    j_val = 3 * k_val - 2 * d_val

    # --- 滚动求和 / 计数 / 极值 / 均线（局部闭包，依赖 n） ---
    def rolling_sum(arr, window):
        result = np.full(n, 0.0)
        cumsum = np.cumsum(arr)
        result[window - 1:] = cumsum[window - 1:] - np.concatenate([[0], cumsum[:n - window]])
        return result

    def rolling_count(bool_arr, window):
        return rolling_sum(bool_arr.astype(float), window)

    def rolling_min(arr, window):
        result = np.full(n, np.nan)
        for i in range(window - 1, n):
            result[i] = np.min(arr[i - window + 1:i + 1])
        return result

    def rolling_max(arr, window):
        result = np.full(n, np.nan)
        for i in range(window - 1, n):
            result[i] = np.max(arr[i - window + 1:i + 1])
        return result

    def moving_average(arr, window):
        result = np.full(n, np.nan)
        cumsum = np.cumsum(arr)
        result[window - 1:] = (cumsum[window - 1:] - np.concatenate([[0], cumsum[:n - window]])) / window
        return result

    ma60 = moving_average(close_arr, 60)
    ma250 = moving_average(close_arr, 250)
    ma40_vol = moving_average(vol_arr, 40)

    # --- A2: 阳量 vs 阴量（放宽倍数） ---
    vol_yang_28 = rolling_sum(vol_arr * real_yang, 28)
    vol_yin_28 = rolling_sum(vol_arr * real_yin, 28)
    vol_yang_14 = rolling_sum(vol_arr * real_yang, 14)
    vol_yin_14 = rolling_sum(vol_arr * real_yin, 14)
    yangyin_ok1 = vol_yang_28 > 1.4 * vol_yin_28
    yangyin_ok2 = vol_yang_14 > 1.8 * vol_yin_14

    # --- A3: J <= 20 ---
    j_ok = j_val <= 20

    # --- A1 / A4: 主力放量启动（放宽倍数） ---
    prev_vol = np.roll(vol_arr, 1)
    prev_vol[0] = vol_arr[0]
    plry = (vol_arr > 1.5 * prev_vol) & (close_arr > open_arr) & (vol_arr > ma40_vol)
    plry[0] = False
    plry_cnt = (rolling_count(plry, 14) >= 2) | (rolling_count(plry, 28) >= 3)

    prev_plry = np.roll(plry, 1)
    prev_plry[0] = False
    plry_first = plry & ~prev_plry
    plry_cont = plry & prev_plry

    prev_real_yin = np.roll(real_yin, 1)
    prev_real_yin[0] = False
    prev_close = np.roll(close_arr, 1)
    prev_close[0] = close_arr[0]
    prev_vol2 = np.roll(vol_arr, 1)
    prev_vol2[0] = vol_arr[0]
    half_down = (~prev_real_yin) & (close_arr < prev_close) & (vol_arr <= 0.5 * prev_vol2)

    cnt_first = rolling_count(plry_first, 28)
    cnt_cont = rolling_count(plry_cont, 28)
    cnt_half = rolling_count(half_down, 28)
    three_sum_ok = (cnt_first + cnt_cont + cnt_half) >= 3

    # --- D1: 高位放量阴线排除 ---
    open_min28 = rolling_min(open_arr, 28)
    open_max28 = rolling_max(open_arr, 28)
    o85 = np.where(
        (open_max28 - open_min28) > 1e-9,
        open_min28 + 0.95 * (open_max28 - open_min28),
        open_arr,
    )
    top15o = open_arr >= o85
    fd15 = (close_arr < prev_close) & (close_arr <= open_arr) & (vol_arr >= 1.15 * prev_vol)
    cnt28_bad = rolling_count(top15o & fd15, 28)
    good28 = cnt28_bad <= 0

    # --- D2: 28日最高量必须是阳线 ---
    maxvol28 = rolling_max(vol_arr, 28)
    max28_bad = (vol_arr == maxvol28) & real_yin
    max28_ok = rolling_count(max28_bad, 28) == 0

    # --- A1 合并 ---
    a1 = (
        (plry_cnt & yangyin_ok1 & j_ok & good28 & three_sum_ok & max28_ok)
        | (plry_cnt & yangyin_ok2 & j_ok & good28 & max28_ok)
    )

    # --- B: 当日小 K 线 ---
    today_body = np.abs(close_arr - open_arr) / np.maximum(prev_close, 1e-9) <= 0.02
    today_chg = np.abs(close_arr / np.maximum(prev_close, 1e-9) - 1) <= 0.025
    today_amp = (high_arr - low_arr) / np.maximum(prev_close, 1e-9) <= 0.045
    today_small = today_body & today_chg & today_amp

    # --- B4: 当日缩量（适度放宽） ---
    vol20_min = rolling_min(vol_arr, 20)
    today_shrink = (vol_arr <= 0.9 * prev_vol) & (vol_arr <= 1.5 * vol20_min)

    # --- C: 位置感 ---
    pos_above_huang = close_arr > ma250
    pos_near_bai = np.abs(close_arr / np.maximum(ma60, 1e-9) - 1) <= 0.06
    pos_ok = pos_above_huang & pos_near_bai

    # --- E: 时序冷静期 ---
    quiet_ok = rolling_count(plry, 5) == 0

    # --- 综合均线过滤 ---
    ma20 = moving_average(close_arr, 20)
    ma120 = moving_average(close_arr, 120)
    ql = 0.4 * ma20 + 0.3 * ma60 + 0.2 * ma120 + 0.1 * ma250
    ql_ok = close_arr > 0.99 * ql

    # --- 最终 B1 ---
    b1_signal = a1 & today_small & today_shrink & pos_ok & quiet_ok & ql_ok
    b1_signal[:MIN_HISTORY_BARS] = False
    return b1_signal


class B1Strategy(BaseStrategy):
    """B1 量价共振选股策略

    核心逻辑：
    - A1: 主力放量启动 + 阳量>阴量 + J<=20 + 无高位放量阴线
    - B: 当日小K线 + 缩量
    - C: 位置感（MA250之上、贴近MA60）
    - E: 时序冷静期（5日内无放量）
    - 综合均线过滤
    """

    def __init__(self, buy_ratio: float = 1.0):
        super().__init__("B1", "B1量价共振")
        self.buy_ratio = buy_ratio
        self._signals_cache: np.ndarray | None = None
        self._cached_length: int = 0

    def on_init(self, history: pd.DataFrame) -> None:
        """预计算全部B1信号"""
        if len(history) >= MIN_HISTORY_BARS:
            self._signals_cache = compute_b1_signals(history)
            self._cached_length = len(history)

    def on_bar(self, bar: pd.Series, context: StrategyContext) -> list[Signal]:
        bar_index = context.bar_index

        # 如果有缓存且索引在范围内
        if self._signals_cache is not None and bar_index < self._cached_length:
            if not self._signals_cache[bar_index]:
                return []
        else:
            # 无缓存时实时计算
            if bar_index < MIN_HISTORY_BARS:
                return []
            self._signals_cache = compute_b1_signals(context.history_bars)
            self._cached_length = len(context.history_bars)
            if not self._signals_cache[bar_index]:
                return []

        # 已持仓则不重复买入
        if context.positions:
            return []

        price = float(bar["close"])
        quantity = self.calc_buy_quantity(price, context.available_cash, self.buy_ratio)
        if quantity <= 0:
            return []

        return [Signal(
            strategy_id=self.strategy_id,
            direction="BUY",
            price=price,
            quantity=quantity,
            reason=f"B1信号@{context.current_date}",
            score=80.0,
        )]
