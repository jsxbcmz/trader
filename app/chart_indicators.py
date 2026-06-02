"""Indicator algorithms (shim).

历史上本模块定义了所有指标算法；现已下沉到 ``core.indicators.algorithms``。
保留此 shim 仅为兼容 app/* 内的现有 import 路径，不要在新代码中再 import 本文件。
core 层应直接 ``from core.indicators.algorithms import ...``。
"""

from __future__ import annotations

from core.indicators.algorithms import (  # noqa: F401
    HAS_NUMBA,
    ZX_MULTI_PERIODS,
    _ema_numba,
    _tdx_sma_numba,
    calc_brick_threshold_price,
    compute_brick_indicator,
    compute_didi_indicator,
    compute_kdj_indicator,
    compute_macd_indicator,
    compute_needle20_indicator,
    compute_oamv,
    compute_zx_long_short,
    compute_zx_short_trend,
    ema,
    moving_average,
    rolling_max,
    rolling_min,
    tdx_sma,
)
