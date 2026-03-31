from __future__ import annotations

import numpy as np

from app.chart_indicators import ema as _ema
from app.chart_indicators import moving_average as _moving_average
from app.chart_indicators import rolling_max as _rolling_max
from app.chart_indicators import rolling_min as _rolling_min
from app.chart_indicators import tdx_sma as _tdx_sma


def _to_float_array(values) -> np.ndarray:
    return np.asarray(values, dtype=float)


def ma(values, period: int) -> np.ndarray:
    return _moving_average(_to_float_array(values), int(period))


def ema(values, period: int) -> np.ndarray:
    return _ema(_to_float_array(values), int(period))


def sma(values, n: int, m: int = 1) -> np.ndarray:
    return _tdx_sma(_to_float_array(values), int(n), int(m))


def hhv(values, period: int) -> np.ndarray:
    return _rolling_max(_to_float_array(values), int(period))


def llv(values, period: int) -> np.ndarray:
    return _rolling_min(_to_float_array(values), int(period))


def ref(values, period: int = 1) -> np.ndarray:
    source = _to_float_array(values)
    offset = int(period)
    out = np.full(source.shape, np.nan, dtype=float)
    if offset < 0:
        raise ValueError("REF 的 period 不能为负数")
    if offset == 0:
        out[:] = source
        return out
    if offset < len(source):
        out[offset:] = source[:-offset]
    return out


def max_series(left, right) -> np.ndarray:
    return np.maximum(_to_float_array(left), _to_float_array(right))


def min_series(left, right) -> np.ndarray:
    return np.minimum(_to_float_array(left), _to_float_array(right))


def abs_series(values) -> np.ndarray:
    return np.abs(_to_float_array(values))
