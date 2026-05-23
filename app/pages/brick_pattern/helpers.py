"""砖形图定式验证页：纯工具函数与常量。

无 Qt 依赖，可被 worker / dialog / page 共用。
"""
from __future__ import annotations

import numpy as np

from core.models.brick_pattern import PatternType, ScoreBreakdown
from core.screening.brick_pattern_engine import (
    detect_n_shape_jump,
    detect_sideways_jump,
    detect_uptrend_continue,
)

# ── 文档中的默认案例数据 ──────────────────────────────────────
DEFAULT_CASES: list[tuple[str, str, str]] = []

GRADE_COLORS = {
    "S": "#D5F5D5",
    "A": "#F6FFED",
    "B": "#FFFBE6",
    "C": "#FFF1E6",
    "D": "#FFF1F0",
}

DATE_RANGE_START = "2024-01-01"
DATE_RANGE_END = "2026-03-31"

_PATTERN_DETECTORS = {
    PatternType.N_SHAPE_JUMP: detect_n_shape_jump,
    PatternType.SIDEWAYS_JUMP: detect_sideways_jump,
    PatternType.UPTREND_CONTINUE: detect_uptrend_continue,
}

_GRADE_ORDER = {"S": 0, "A": 1, "B": 2, "C": 3, "D": 4}


def _format_date(raw: str) -> str:
    raw = raw.strip()
    if len(raw) == 8 and raw.isdigit():
        return f"{raw[:4]}-{raw[4:6]}-{raw[6:]}"
    return raw

def _build_score_tooltip(breakdown: ScoreBreakdown) -> str:
    """构建评分分解的tooltip文本"""
    lines = [f"最终得分: {breakdown.final_score:.0f} ({breakdown.grade}级)"]
    base_parts = f"专属{breakdown.specific_score:.0f} + 通用{breakdown.common_score:.0f} + MACD{breakdown.macd_score:.0f} + 信号{breakdown.signal_score:.0f}"
    lines.append(f"基础分: {breakdown.base_score:.0f} = {base_parts}")
    lines.append("")

    lines.append(f"── 定式专属 ({breakdown.specific_score:.0f}/30) ──")
    for k, v in breakdown.specific_items.items():
        lines.append(f"  {k}: {v:+.0f}" if v < 0 else f"  {k}: {v:.0f}")

    lines.append(f"── 通用质量 ({breakdown.common_score:.0f}/30) ──")
    for k, v in breakdown.common_items.items():
        lines.append(f"  {k}: {v:.0f}")

    lines.append(f"── MACD环境 ({breakdown.macd_score:.0f}/25) ──")
    for k, v in breakdown.macd_items.items():
        lines.append(f"  {k}: {v:+.0f}" if v < 0 else f"  {k}: {v:.0f}")

    if breakdown.signal_items:
        lines.append(f"── 信号强度 ({breakdown.signal_score:.0f}/15) ──")
        for k, v in breakdown.signal_items.items():
            lines.append(f"  {k}: {v:.0f}")

    if breakdown.risk_penalty != 0:
        lines.append(f"── 风险扣分 ({breakdown.risk_penalty:.0f}) ──")
        for k, v in breakdown.risk_items.items():
            lines.append(f"  {k}: {v:.0f}")

    return "\n".join(lines)


def _is_feature_similar(
    pattern_type: PatternType,
    ref_extra: dict,
    candidate_extra: dict,
) -> bool:
    """判断候选结果的特征是否与参考特征相似。

    按定式类型使用不同的关键特征进行范围匹配。
    """
    if pattern_type == PatternType.N_SHAPE_JUMP:
        ref_j = ref_extra.get("kdj_j", 50)
        cand_j = candidate_extra.get("kdj_j", 50)
        # J值同区间: <0 / 0~20 / 20~40
        ref_band = 0 if ref_j < 0 else (1 if ref_j < 20 else 2)
        cand_band = 0 if cand_j < 0 else (1 if cand_j < 20 else 2)
        if abs(ref_band - cand_band) > 1:
            return False

        ref_green = ref_extra.get("max_green_segment", 0)
        cand_green = candidate_extra.get("max_green_segment", 0)
        if ref_green != cand_green:
            return False

        ref_vs = ref_extra.get("vs_short_trend", 0)
        cand_vs = candidate_extra.get("vs_short_trend", 0)
        # 价格vs短趋势偏离差距不超过5%
        if abs(ref_vs - cand_vs) > 5:
            return False

    elif pattern_type == PatternType.SIDEWAYS_JUMP:
        ref_sw = ref_extra.get("switches", 0)
        cand_sw = candidate_extra.get("switches", 0)
        # 切换次数差距不超过2
        if abs(ref_sw - cand_sw) > 2:
            return False

        ref_amp = ref_extra.get("amplitude", 0)
        cand_amp = candidate_extra.get("amplitude", 0)
        # 振幅差距不超过5%
        if abs(ref_amp - cand_amp) > 5:
            return False

        ref_jump = ref_extra.get("brick_jump", 0)
        cand_jump = candidate_extra.get("brick_jump", 0)
        # 跳升幅度比值在 0.5~2.0 之间
        if ref_jump > 0 and cand_jump > 0:
            ratio = cand_jump / ref_jump
            if ratio < 0.5 or ratio > 2.0:
                return False

    elif pattern_type == PatternType.UPTREND_CONTINUE:
        ref_red = ref_extra.get("red_count", 0)
        cand_red = candidate_extra.get("red_count", 0)
        # 红砖数差距不超过3
        if abs(ref_red - cand_red) > 3:
            return False

        ref_green = ref_extra.get("green_count", 0)
        cand_green = candidate_extra.get("green_count", 0)
        # 绿砖数差距不超过1
        if abs(ref_green - cand_green) > 1:
            return False

        ref_bv = ref_extra.get("brick_val", 0)
        cand_bv = candidate_extra.get("brick_val", 0)
        # 砖值差距不超过30
        if abs(ref_bv - cand_bv) > 30:
            return False

    return True


def _find_score_range(score: float) -> str:
    for lo, hi in [(0, 30), (30, 40), (40, 55), (55, 70), (70, 85), (85, 101)]:
        if lo <= score < hi:
            return f"{lo}-{hi}"
    return "85-101"


def _calc_percentile(all_scores: list[float], current_score: float) -> float:
    if not all_scores:
        return 0
    below = sum(1 for s in all_scores if s < current_score)
    return below / len(all_scores) * 100


def _build_backtest_tooltip(
    backtest_stats: dict, pattern_name: str, grade: str, current_score: float,
) -> str:
    lines = [f"该股历史定式命中总计: {backtest_stats['total_signals']}次"]

    all_scores = backtest_stats.get("all_scores", [])
    if all_scores:
        pct = _calc_percentile(all_scores, current_score)
        lines.append(f"当前{current_score:.0f}分 超过历史{pct:.0f}%的信号")
    lines.append("")

    lines.append("── 各等级 T+1 胜率 ──")
    for g in ("S", "A", "B", "C", "D"):
        gs = backtest_stats["by_grade"].get(g, {})
        cnt = gs.get("count", 0)
        if cnt > 0:
            wr = gs.get("t1_win_rate", 0)
            mn = gs.get("t1_mean", 0)
            marker = " ◀ 当前" if g == grade else ""
            lines.append(f"  {g}级: 胜率{wr:.0f}% 均值{mn:+.2f}% ({cnt}次){marker}")
        else:
            lines.append(f"  {g}级: 无数据")

    sr_key = _find_score_range(current_score)
    by_sr = backtest_stats.get("by_score_range", {})
    sr_stats = by_sr.get(sr_key, {})
    if sr_stats.get("count", 0) > 0:
        lines.append("")
        lines.append(f"── 当前分数区间 [{sr_key}) ──")
        lines.append(f"  T+1: 胜率{sr_stats['t1_win_rate']:.0f}% 均值{sr_stats['t1_mean']:+.2f}% ({sr_stats['count']}次)")
        lines.append(f"  T+2: 胜率{sr_stats['t2_win_rate']:.0f}% 均值{sr_stats['t2_mean']:+.2f}%")

    key = f"{pattern_name}_{grade}"
    pg = backtest_stats["by_pattern_grade"].get(key, {})
    if pg.get("count", 0) > 0:
        lines.append("")
        lines.append(f"── {pattern_name} {grade}级 ──")
        lines.append(f"  T+1: 胜率{pg['t1_win_rate']:.0f}% 均值{pg['t1_mean']:+.2f}% ({pg['count']}次)")
        lines.append(f"  T+2: 胜率{pg['t2_win_rate']:.0f}% 均值{pg['t2_mean']:+.2f}%")

    return "\n".join(lines)


def _generate_advice(
    grade: str,
    backtest_stats: dict,
    pattern_name: str,
    indicators: dict,
    index: int,
    risk_penalty: float,
    current_score: float,
) -> tuple[str, str]:
    """生成回测文本和建议文本。

    优先级：分数区间统计 > 定式×等级统计 > 等级统计 > 全量统计。
    """
    sr_key = _find_score_range(current_score)
    sr_stats = backtest_stats.get("by_score_range", {}).get(sr_key, {})
    pg_key = f"{pattern_name}_{grade}"
    pg_stats = backtest_stats["by_pattern_grade"].get(pg_key, {})
    grade_stats = backtest_stats["by_grade"].get(grade, {})

    if sr_stats.get("count", 0) >= 3:
        ref_stats = sr_stats
    elif pg_stats.get("count", 0) >= 3:
        ref_stats = pg_stats
    elif grade_stats.get("count", 0) >= 3:
        ref_stats = grade_stats
    else:
        all_count = backtest_stats["total_signals"]
        if all_count == 0:
            return "无历史信号", "数据不足"
        all_t1 = [r["ret_t1"] for r in backtest_stats["records"] if not np.isnan(r["ret_t1"])]
        wr = sum(1 for v in all_t1 if v > 0) / len(all_t1) * 100 if all_t1 else 0
        return f"T+1胜率{wr:.0f}%({all_count}次)", "数据不足"

    count = ref_stats["count"]
    t1_wr = ref_stats["t1_win_rate"]
    t1_mean = ref_stats["t1_mean"]

    all_scores = backtest_stats.get("all_scores", [])
    pct = _calc_percentile(all_scores, current_score)
    backtest_text = f"T+1胜率{t1_wr:.0f}%({count}次) 前{100 - pct:.0f}%"

    short_trend = indicators["short_trend"]
    long_short = indicators["long_short"]
    close = indicators["close"]

    st_val = float(short_trend[index]) if np.isfinite(short_trend[index]) else close[index]
    ls_val = float(long_short[index]) if np.isfinite(long_short[index]) else close[index]
    close_val = float(close[index])

    if risk_penalty <= -25:
        return backtest_text, "回避(高风险)"

    if grade in ("S", "A") and t1_wr >= 60:
        buy_price = min(close_val, st_val * 1.02)
        stop_loss = ls_val * 0.98
        return backtest_text, f"建议买入 ≤{buy_price:.2f} 止损{stop_loss:.2f}"
    elif grade in ("S", "A", "B") and t1_wr >= 50:
        buy_price = st_val
        return backtest_text, f"谨慎买入 ≤{buy_price:.2f}"
    elif t1_wr < 40:
        return backtest_text, "回避(胜率低)"
    else:
        return backtest_text, "观望"
