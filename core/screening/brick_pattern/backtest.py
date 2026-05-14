"""砖形图定式：个股历史回测。"""

from __future__ import annotations

import numpy as np
import pandas as pd

from core.models.brick_pattern import PatternType, ScoreBreakdown

from .helpers import _calc_indicators, check_prerequisites
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

_ALL_DETECTORS = {
    PatternType.N_SHAPE_JUMP: detect_n_shape_jump,
    PatternType.SIDEWAYS_JUMP: detect_sideways_jump,
    PatternType.UPTREND_CONTINUE: detect_uptrend_continue,
}

def backtest_stock_history(
    df: pd.DataFrame,
    indicators: dict[str, np.ndarray] | None = None,
) -> dict:
    """扫描单只股票全部历史，统计各评分等级下的 T+1/T+2 胜率。"""
    if df is None or len(df) < 30:
        return {"total_signals": 0, "records": [], "by_grade": {}, "by_pattern_grade": {}}

    if indicators is None:
        indicators = _calc_indicators(df)

    close = indicators["close"]
    n_rows = len(close)
    dates = pd.to_datetime(df["date"], errors="coerce")

    records: list[dict] = []

    for i in range(10, n_rows):
        passed, _ = check_prerequisites(indicators, i)
        if not passed:
            continue

        for pt, detector in _ALL_DETECTORS.items():
            result = detector(indicators, i)
            if not result.matched:
                continue

            specific_items = result.extra.get("specific_items", {})
            common_score, common_items = compute_common_quality_score(indicators, i, pt)
            macd_score, macd_items = compute_macd_auxiliary_score(indicators, i, pt)
            risk_penalty, risk_items, _ = compute_risk_penalty(indicators, i, pt)
            signal_score, signal_items = compute_signal_strength_score(indicators, i)

            bd = ScoreBreakdown(
                specific_score=result.score,
                specific_items=specific_items,
                common_score=common_score,
                common_items=common_items,
                macd_score=macd_score,
                macd_items=macd_items,
                signal_score=signal_score,
                signal_items=signal_items,
                risk_penalty=risk_penalty,
                risk_items=risk_items,
            )

            ret_t1 = float((close[i + 1] - close[i]) / close[i] * 100) if i + 1 < n_rows else np.nan
            ret_t2 = float((close[i + 2] - close[i]) / close[i] * 100) if i + 2 < n_rows else np.nan

            date_val = dates.iloc[i]
            date_str = date_val.strftime("%Y-%m-%d") if pd.notna(date_val) else ""

            records.append({
                "date": date_str,
                "pattern": pt.value,
                "grade": bd.grade,
                "score": bd.final_score,
                "ret_t1": ret_t1,
                "ret_t2": ret_t2,
            })

    by_grade: dict[str, dict] = {}
    by_pattern_grade: dict[str, dict] = {}

    for grade in ("S", "A", "B", "C", "D"):
        subset = [r for r in records if r["grade"] == grade]
        by_grade[grade] = _calc_group_stats(subset)

    for pt in PatternType:
        for grade in ("S", "A", "B", "C", "D"):
            key = f"{pt.value}_{grade}"
            subset = [r for r in records if r["pattern"] == pt.value and r["grade"] == grade]
            stats = _calc_group_stats(subset)
            if stats["count"] > 0:
                by_pattern_grade[key] = stats

    score_ranges = [(0, 30), (30, 40), (40, 55), (55, 70), (70, 85), (85, 101)]
    by_score_range: dict[str, dict] = {}
    for lo, hi in score_ranges:
        key = f"{lo}-{hi}"
        subset = [r for r in records if lo <= r["score"] < hi]
        stats = _calc_group_stats(subset)
        if stats["count"] > 0:
            by_score_range[key] = stats

    all_scores = sorted([r["score"] for r in records])

    return {
        "total_signals": len(records),
        "records": records,
        "by_grade": by_grade,
        "by_pattern_grade": by_pattern_grade,
        "by_score_range": by_score_range,
        "all_scores": all_scores,
    }


def _calc_group_stats(records: list[dict]) -> dict:
    if not records:
        return {"count": 0, "t1_win_rate": 0, "t1_mean": 0, "t2_win_rate": 0, "t2_mean": 0}

    t1_vals = [r["ret_t1"] for r in records if not np.isnan(r["ret_t1"])]
    t2_vals = [r["ret_t2"] for r in records if not np.isnan(r["ret_t2"])]

    return {
        "count": len(records),
        "t1_win_rate": sum(1 for v in t1_vals if v > 0) / len(t1_vals) * 100 if t1_vals else 0,
        "t1_mean": sum(t1_vals) / len(t1_vals) if t1_vals else 0,
        "t2_win_rate": sum(1 for v in t2_vals if v > 0) / len(t2_vals) * 100 if t2_vals else 0,
        "t2_mean": sum(t2_vals) / len(t2_vals) if t2_vals else 0,
    }
