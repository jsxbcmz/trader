from __future__ import annotations

import numpy as np
import pandas as pd

# Numba JIT加速是可选的，如果未安装则使用纯Python实现
try:
    from numba import njit
    HAS_NUMBA = True
except ImportError:
    HAS_NUMBA = False
    # 定义一个空的装饰器作为回退
    def njit(*args, **kwargs):
        def decorator(func):
            return func
        return decorator


ZX_MULTI_PERIODS = (14, 28, 57, 114)


def rolling_max(values: np.ndarray, period: int) -> np.ndarray:
    """向量化滚动最大值计算 - 使用pandas rolling优化"""
    return pd.Series(values).rolling(window=period, min_periods=1).max().to_numpy()


def rolling_min(values: np.ndarray, period: int) -> np.ndarray:
    """向量化滚动最小值计算 - 使用pandas rolling优化"""
    return pd.Series(values).rolling(window=period, min_periods=1).min().to_numpy()


@njit(cache=True)
def _tdx_sma_numba(values: np.ndarray, n: int, m: int) -> np.ndarray:
    """Numba加速的通达信SMA计算"""
    length = len(values)
    out = np.full(length, np.nan, dtype=np.float64)
    prev = np.nan
    for i in range(length):
        value = values[i]
        if not np.isfinite(value):
            if np.isfinite(prev):
                out[i] = prev
            continue
        if not np.isfinite(prev):
            prev = value
        else:
            prev = (m * value + (n - m) * prev) / n
        out[i] = prev
    return out


def tdx_sma(values: np.ndarray, n: int, m: int) -> np.ndarray:
    """通达信SMA计算 - 使用Numba JIT加速（如果可用）"""
    arr = np.asarray(values, dtype=np.float64)
    return _tdx_sma_numba(arr, n, m)


def moving_average(values: np.ndarray, period: int) -> np.ndarray:
    """移动平均计算 - 使用numpy卷积"""
    out = np.full_like(values, np.nan)
    if len(values) < period:
        return out
    kernel = np.ones(period) / period
    out[period - 1 :] = np.convolve(values, kernel, mode="valid")
    return out


@njit(cache=True)
def _ema_numba(values: np.ndarray, period: int) -> np.ndarray:
    """Numba加速的EMA计算"""
    length = len(values)
    out = np.full(length, np.nan, dtype=np.float64)
    if length == 0:
        return out
    alpha = 2.0 / (period + 1.0)
    # 找到第一个有效值
    start = -1
    for i in range(length):
        if np.isfinite(values[i]):
            start = i
            break
    if start < 0:
        return out
    out[start] = values[start]
    for i in range(start + 1, length):
        if np.isnan(values[i]):
            out[i] = out[i - 1]
        else:
            out[i] = alpha * values[i] + (1.0 - alpha) * out[i - 1]
    return out


def ema(values: np.ndarray, period: int) -> np.ndarray:
    """EMA计算 - 使用Numba JIT加速"""
    arr = np.asarray(values, dtype=np.float64)
    return _ema_numba(arr, period)


def compute_zx_short_trend(close: np.ndarray):
    return ema(ema(close, 10), 10)


def compute_zx_long_short(close: np.ndarray, periods=ZX_MULTI_PERIODS):
    ma_values = np.vstack([moving_average(close, period) for period in periods])
    valid_counts = np.sum(~np.isnan(ma_values), axis=0)
    ma_sums = np.nansum(ma_values, axis=0)
    return np.divide(
        ma_sums,
        valid_counts,
        out=np.full_like(close, np.nan),
        where=valid_counts > 0,
    )


def compute_brick_indicator(high: np.ndarray, low: np.ndarray, close: np.ndarray):
    hhv4 = rolling_max(high, 4)
    llv4 = rolling_min(low, 4)
    span = hhv4 - llv4
    safe_span = np.where(np.abs(span) < 1e-12, np.nan, span)

    var1a = (hhv4 - close) / safe_span * 100.0 - 90.0
    var2a = tdx_sma(var1a, 4, 1) + 100.0
    var3a = (close - llv4) / safe_span * 100.0
    var4a = tdx_sma(var3a, 6, 1)
    var5a = tdx_sma(var4a, 6, 1) + 100.0
    var6a = var5a - var2a
    brick = np.where(np.isfinite(var6a) & (var6a > 4.0), var6a - 4.0, 0.0)

    prev_brick = np.roll(brick, 1)
    prev_brick[0] = np.nan
    aa = np.isfinite(prev_brick) & (prev_brick < brick)
    aa_prev = np.roll(aa.astype(int), 1)
    aa_prev[0] = 0
    cc = (aa_prev == 0) & aa
    xg = cc.astype(bool)

    return {
        "brick": brick,
        "prev_brick": prev_brick,
        "aa": aa,
        "cc": cc,
        "xg": xg,
    }


def calc_brick_threshold_price(
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    target_index: int,
    target_brick: float,
) -> float | None:
    """计算使砖型差值恰好为零的临界收盘价。

    在 target_index 当天，求出使 brick[target_index] == target_brick
    （通常 = brick[target_index-1]）的收盘价。

    原理：hhv4/llv4 仅依赖 high/low（固定），brick 对 close 是线性的，
    可通过解析 SMA 递推公式直接解出。

    Returns:
        临界价格，若无法计算则返回 None。
    """
    if target_index < 1 or target_index >= len(close):
        return None

    # ── 计算 target_index 当天的 hhv4 / llv4 ──
    start = max(0, target_index - 3)
    hhv4_val = float(np.max(high[start : target_index + 1]))
    llv4_val = float(np.min(low[start : target_index + 1]))
    span = hhv4_val - llv4_val
    if span < 1e-12:
        return None

    # ── 递推 SMA 状态到 target_index - 1 ──
    # 需要 var2a_raw, var4a, var5a_raw 在前一天的值
    hhv4_all = rolling_max(high[: target_index], 4)
    llv4_all = rolling_min(low[: target_index], 4)
    span_all = hhv4_all - llv4_all
    safe_span_all = np.where(np.abs(span_all) < 1e-12, np.nan, span_all)

    var1a_prev = (hhv4_all - close[: target_index]) / safe_span_all * 100.0 - 90.0
    var2a_raw_prev = tdx_sma(var1a_prev, 4, 1)  # 不加 100

    var3a_prev = (close[: target_index] - llv4_all) / safe_span_all * 100.0
    var4a_prev = tdx_sma(var3a_prev, 6, 1)
    var5a_raw_prev = tdx_sma(var4a_prev, 6, 1)  # 不加 100

    p2 = float(var2a_raw_prev[-1])  # prev_var2a_raw
    p4 = float(var4a_prev[-1])      # prev_var4a
    p5 = float(var5a_raw_prev[-1])  # prev_var5a_raw

    if not (np.isfinite(p2) and np.isfinite(p4) and np.isfinite(p5)):
        return None

    # ── 解析求解 ──
    # target_brick > 0 时: var6a = target_brick + 4
    # target_brick == 0 时: var6a <= 4，取 var6a = 4（临界点）
    target_var6a = target_brick + 4.0 if target_brick > 0 else 4.0

    a = 100.0 / span
    # C = (36*target_var6a + a*(llv4 + 9*hhv4) - 5*P4 - 30*P5 - 810 + 27*P2) / (10*a)
    numerator = (
        36.0 * target_var6a
        + a * (llv4_val + 9.0 * hhv4_val)
        - 5.0 * p4
        - 30.0 * p5
        - 810.0
        + 27.0 * p2
    )
    threshold = numerator / (10.0 * a)

    if not np.isfinite(threshold):
        return None

    # ── 边界约束 ──
    day_high = float(high[target_index])
    day_low = float(low[target_index])

    if threshold > day_high:
        # 开盘即已绿砖，使用开盘价
        return None
    if threshold < day_low:
        # 理论上不应触发（如果 brick 确实下降了），回退
        return None

    return round(threshold, 2)


def compute_kdj_indicator(high: np.ndarray, low: np.ndarray, close: np.ndarray):
    hhv9 = rolling_max(high, 9)
    llv9 = rolling_min(low, 9)
    span = hhv9 - llv9
    safe_span = np.where(np.abs(span) < 1e-12, np.nan, span)

    rsv = (close - llv9) / safe_span * 100.0
    k = tdx_sma(rsv, 3, 1)
    d = tdx_sma(k, 3, 1)
    j = 3.0 * k - 2.0 * d

    return {
        "k": k,
        "d": d,
        "j": j,
    }
