"""砖形图交易定式选股引擎 V4 — shim 转发到 :mod:`core.screening.brick_pattern` 子包。

历史上本模块是单文件 2000 行的实现，现已拆分为：
    core/screening/brick_pattern/helpers.py      指标 & 形态辅助
    core/screening/brick_pattern/detectors.py    三种定式检测器
    core/screening/brick_pattern/scoring.py      四个评分模块
    core/screening/brick_pattern/pipeline.py     选股入口 + 引擎
    core/screening/brick_pattern/backtest.py     个股历史回测

为保持向后兼容，本文件继续 re-export 全部公开符号；新代码建议直接
从 `core.screening.brick_pattern` 导入。
"""

from __future__ import annotations

from core.screening.brick_pattern import (  # noqa: F401
    DEFAULT_MAX_WORKERS,
    DEFAULT_PROGRESS_INTERVAL,
    BrickPatternEngine,
    _ALL_DETECTORS,
    _calc_group_stats,
    _calc_indicators,
    _count_brick_color_switches,
    _detect_diff_dea_cross,
    _detect_third_wave,
    _find_prior_uptrend,
    _find_pullback_phase,
    _is_green_brick,
    _is_in_n_shape_decline,
    _is_red_brick,
    _worker_screen_stock,
    backtest_stock_history,
    check_prerequisites,
    compute_common_quality_score,
    compute_macd_auxiliary_score,
    compute_risk_penalty,
    compute_signal_strength_score,
    detect_n_shape_jump,
    detect_sideways_jump,
    detect_uptrend_continue,
    screen_single_stock,
    screen_with_indicators,
)
