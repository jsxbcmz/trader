"""砖形图定式选股引擎（拆分为 helpers / detectors / scoring / pipeline / backtest）。

外部 caller 可继续从 `core.screening.brick_pattern_engine` 导入所有公开符号
（该模块已改为 shim，转发到本子包）。新代码建议直接从此包导入。
"""

from __future__ import annotations

from .helpers import (
    _calc_indicators,
    _count_brick_color_switches,
    _detect_diff_dea_cross,
    _detect_third_wave,
    _find_prior_uptrend,
    _find_pullback_phase,
    _is_green_brick,
    _is_in_n_shape_decline,
    _is_red_brick,
    check_prerequisites,
)
from .detectors import (
    detect_n_shape_jump,
    detect_sideways_jump,
    detect_uptrend_continue,
)
from .scoring import (
    compute_common_quality_score,
    compute_macd_auxiliary_score,
    compute_risk_penalty,
    compute_signal_strength_score,
)
from .pipeline import (
    DEFAULT_MAX_WORKERS,
    DEFAULT_PROGRESS_INTERVAL,
    BrickPatternEngine,
    _worker_screen_stock,
    screen_single_stock,
    screen_with_indicators,
)
from .backtest import (
    _ALL_DETECTORS,
    _calc_group_stats,
    backtest_stock_history,
)

__all__ = [
    # helpers
    "_calc_indicators",
    "_count_brick_color_switches",
    "_detect_diff_dea_cross",
    "_detect_third_wave",
    "_find_prior_uptrend",
    "_find_pullback_phase",
    "_is_green_brick",
    "_is_in_n_shape_decline",
    "_is_red_brick",
    "check_prerequisites",
    # detectors
    "detect_n_shape_jump",
    "detect_sideways_jump",
    "detect_uptrend_continue",
    # scoring
    "compute_common_quality_score",
    "compute_macd_auxiliary_score",
    "compute_risk_penalty",
    "compute_signal_strength_score",
    # pipeline
    "DEFAULT_MAX_WORKERS",
    "DEFAULT_PROGRESS_INTERVAL",
    "BrickPatternEngine",
    "_worker_screen_stock",
    "screen_single_stock",
    "screen_with_indicators",
    # backtest
    "_ALL_DETECTORS",
    "_calc_group_stats",
    "backtest_stock_history",
]
