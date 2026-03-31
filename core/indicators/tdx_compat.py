from __future__ import annotations

from app.chart_indicators import ZX_MULTI_PERIODS
from app.chart_indicators import compute_kdj_indicator as _compute_kdj_indicator
from app.chart_indicators import compute_zx_long_short as _compute_zx_long_short
from app.chart_indicators import compute_zx_short_trend as _compute_zx_short_trend

from .builtin import ema, hhv, llv, sma


def tdx_sma(values, n: int, m: int = 1):
    return sma(values, n, m)


def kdj(high, low, close):
    return _compute_kdj_indicator(high, low, close)


def zx_short_trend(close):
    return _compute_zx_short_trend(close)


def zx_long_short(close, periods=ZX_MULTI_PERIODS):
    return _compute_zx_long_short(close, periods=periods)


__all__ = [
    "ZX_MULTI_PERIODS",
    "ema",
    "hhv",
    "kdj",
    "llv",
    "sma",
    "tdx_sma",
    "zx_long_short",
    "zx_short_trend",
]
