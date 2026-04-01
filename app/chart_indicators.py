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
    var5a = var4a + 100.0
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
