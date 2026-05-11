"""砖形图定式策略 — 封装 brick_pattern_engine 检测逻辑为策略接口。"""

from __future__ import annotations

import numpy as np
import pandas as pd

from core.models.brick_pattern import PatternType, ScoreBreakdown
from core.screening.brick_pattern_engine import (
    _calc_indicators,
    check_prerequisites,
    compute_common_quality_score,
    compute_macd_auxiliary_score,
    compute_risk_penalty,
    compute_signal_strength_score,
    detect_n_shape_jump,
    detect_sideways_jump,
    detect_uptrend_continue,
)
from core.strategy.base import BaseStrategy, StrategyContext
from core.strategy.signal import Signal

SCORE_GRADES = {"S": 90, "A": 75, "B": 60, "C": 45, "D": 0}

DEFAULT_PATTERNS = (
    PatternType.N_SHAPE_JUMP,
    PatternType.SIDEWAYS_JUMP,
    PatternType.UPTREND_CONTINUE,
)

DETECTORS = {
    PatternType.N_SHAPE_JUMP: detect_n_shape_jump,
    PatternType.SIDEWAYS_JUMP: detect_sideways_jump,
    PatternType.UPTREND_CONTINUE: detect_uptrend_continue,
}

MIN_BARS_REQUIRED = 60


class BrickPatternStrategy(BaseStrategy):
    """砖形图定式选股策略

    核心逻辑：
    1. 检查必备前提（绿转红 + 力度达标 + 短趋线>多空线）
    2. 检测三种定式（N型起跳、横盘起跳、上升波段延续）
    3. 综合评分达标则产生买入信号

    Args:
        patterns: 启用的定式类型列表
        min_grade: 最低评分等级 ("S"/"A"/"B"/"C"/"D")
        buy_ratio: 买入资金比例
    """

    def __init__(
        self,
        patterns: tuple[PatternType, ...] | None = None,
        min_grade: str = "B",
        buy_ratio: float = 1.0,
    ):
        super().__init__("BRICK", "砖形图定式")
        self.patterns = patterns or DEFAULT_PATTERNS
        self.min_grade = min_grade
        self.min_score = SCORE_GRADES.get(min_grade, 60)
        self.buy_ratio = buy_ratio
        self._indicators_cache: dict[str, np.ndarray] | None = None

    def on_init(self, history: pd.DataFrame) -> None:
        if len(history) >= MIN_BARS_REQUIRED:
            self._indicators_cache = _calc_indicators(history)

    def on_bar(self, bar: pd.Series, context: StrategyContext) -> list[Signal]:
        bar_index = context.bar_index

        if bar_index < MIN_BARS_REQUIRED:
            return []

        # 已持仓则不重复买入
        if context.positions:
            return []

        # 确保指标缓存有效
        if self._indicators_cache is None:
            self._indicators_cache = _calc_indicators(context.history_bars)

        indicators = self._indicators_cache

        # 检查数组边界
        if bar_index >= len(indicators.get("brick", [])):
            self._indicators_cache = _calc_indicators(context.history_bars)
            indicators = self._indicators_cache

        # 检查必备前提
        passed, _ = check_prerequisites(indicators, bar_index)
        if not passed:
            return []

        # 逐定式检测
        best_score = 0.0
        best_pattern = ""
        best_reason = ""

        for pattern_type in self.patterns:
            detector = DETECTORS.get(pattern_type)
            if not detector:
                continue

            detail = detector(indicators, bar_index)
            if not detail.matched:
                continue

            # 计算综合评分
            total_score = self._calc_total_score(indicators, bar_index, detail, pattern_type)
            if total_score > best_score:
                best_score = total_score
                best_pattern = pattern_type.value
                best_reason = detail.description

        if best_score < self.min_score:
            return []

        grade = self._score_to_grade(best_score)
        price = float(bar["close"])
        quantity = self.calc_buy_quantity(price, context.available_cash, self.buy_ratio)
        if quantity <= 0:
            return []

        return [Signal(
            strategy_id=self.strategy_id,
            direction="BUY",
            price=price,
            quantity=quantity,
            reason=f"{best_pattern}({grade}级,{best_score:.0f}分)@{context.current_date}",
            score=best_score,
        )]

    def _calc_total_score(self, indicators: dict, index: int, detail, pattern_type: PatternType) -> float:
        """计算综合评分"""
        specific_score = detail.score if hasattr(detail, "score") else 0.0
        if detail.score_breakdown:
            specific_score = detail.score_breakdown.specific_score

        common_score, _ = compute_common_quality_score(indicators, index, pattern_type)
        macd_score, _ = compute_macd_auxiliary_score(indicators, index, pattern_type)
        signal_score, _ = compute_signal_strength_score(indicators, index)
        risk_penalty, _, _ = compute_risk_penalty(indicators, index, pattern_type)

        total = specific_score + common_score + macd_score + signal_score - risk_penalty
        return max(0.0, total)

    def _score_to_grade(self, score: float) -> str:
        if score >= 90:
            return "S"
        elif score >= 75:
            return "A"
        elif score >= 60:
            return "B"
        elif score >= 45:
            return "C"
        return "D"
