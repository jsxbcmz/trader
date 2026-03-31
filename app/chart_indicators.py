from __future__ import annotations

import numpy as np


ZX_MULTI_PERIODS = (14, 28, 57, 114)


def rolling_max(values: np.ndarray, period: int):
    out = np.full(len(values), np.nan, dtype=float)
    for i in range(len(values)):
        start = max(0, i - period + 1)
        window = values[start : i + 1]
        if len(window) > 0:
            out[i] = np.nanmax(window)
    return out


def rolling_min(values: np.ndarray, period: int):
    out = np.full(len(values), np.nan, dtype=float)
    for i in range(len(values)):
        start = max(0, i - period + 1)
        window = values[start : i + 1]
        if len(window) > 0:
            out[i] = np.nanmin(window)
    return out


def tdx_sma(values: np.ndarray, n: int, m: int):
    out = np.full(len(values), np.nan, dtype=float)
    prev = np.nan
    for i, value in enumerate(values):
        if not np.isfinite(value):
            continue
        if not np.isfinite(prev):
            prev = value
        else:
            prev = (m * value + (n - m) * prev) / n
        out[i] = prev
    return out


def moving_average(values: np.ndarray, period: int):
    out = np.full_like(values, np.nan)
    if len(values) < period:
        return out
    kernel = np.ones(period) / period
    out[period - 1 :] = np.convolve(values, kernel, mode="valid")
    return out


def ema(values: np.ndarray, period: int):
    out = np.full_like(values, np.nan)
    if len(values) == 0:
        return out
    alpha = 2.0 / (period + 1.0)
    first_idx = np.flatnonzero(~np.isnan(values))
    if len(first_idx) == 0:
        return out
    start = int(first_idx[0])
    out[start] = values[start]
    for i in range(start + 1, len(values)):
        if np.isnan(values[i]):
            out[i] = out[i - 1]
        else:
            out[i] = alpha * values[i] + (1.0 - alpha) * out[i - 1]
    return out


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
