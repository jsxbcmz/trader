"""砖形图定式验证页拆分模块。

helpers  — 纯工具函数 + 常量
workers  — 后台 Worker
dialogs  — 进度/结果/明细对话框
"""
from .dialogs import (
    BacktestDetailDialog,
    SimilarPatternResultDialog,
    SimilarSearchProgressDialog,
)
from .helpers import (
    DEFAULT_CASES,
    GRADE_COLORS,
    _build_backtest_tooltip,
    _build_score_tooltip,
    _format_date,
    _generate_advice,
    _PATTERN_DETECTORS,
)
from .workers import SimilarPatternWorker

__all__ = [
    "BacktestDetailDialog",
    "DEFAULT_CASES",
    "GRADE_COLORS",
    "SimilarPatternResultDialog",
    "SimilarPatternWorker",
    "SimilarSearchProgressDialog",
    "_PATTERN_DETECTORS",
    "_build_backtest_tooltip",
    "_build_score_tooltip",
    "_format_date",
    "_generate_advice",
]
