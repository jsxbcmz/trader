"""砖形图定式：四个评分模块。

- compute_common_quality_score    通用质量(30)
- compute_macd_auxiliary_score    MACD 环境(25)
- compute_signal_strength_score   信号强度(15)
- compute_risk_penalty            风险扣分
"""

from __future__ import annotations

import numpy as np

from core.models.brick_pattern import (
    PatternType,
    RiskFilterDetail,
    RiskFilterType,
)

from .helpers import (
    _count_brick_color_switches,
    _detect_diff_dea_cross,
    _detect_third_wave,
    _find_pullback_phase,
    _is_green_brick,
    _is_in_n_shape_decline,
    _is_red_brick,
)

def _pct_to_score(pct: float, max_score: int) -> int:
    """P1-2 截面分位查表：pct ∈ [0, 1] → 分数 ∈ [0, max_score]。

    分箱设计（设计文档建议）：
        ≥ 0.95 满分 / 0.80~0.95 高 / 0.50~0.80 中 / < 0.50 低
    """
    if pct >= 0.95:
        return max_score
    if pct >= 0.80:
        return round(max_score * 0.75)
    if pct >= 0.50:
        return round(max_score * 0.50)
    if pct >= 0.20:
        return round(max_score * 0.25)
    return 0


def compute_common_quality_score(
    indicators: dict[str, np.ndarray],
    index: int,
    pattern_type: PatternType,
    cs_pcts: dict[str, float] | None = None,
) -> tuple[float, dict[str, float]]:
    """计算通用质量评分(30分) V4：K线形态(10) + 信号日涨幅(8) + 翻红力度比(3) + 短趋vs多空(3) + 均线排列(3) + 短趋斜率(3)。

    V4变更：K线形态大幅增权(4→10,跨定式最强通用因子)；信号日涨幅增权(6→8)；
    翻红力度比大幅降权(7→3,无效因子)；短趋vs多空降权(6→3)；均线排列降权(4→3)。

    P1-2 截面归一化：当传入 ``cs_pcts``（含 day_change_pct/force_ratio_pct/short_trend_slope_pct）时，
    信号日涨幅 / 翻红力度比 / 短趋斜率 改用分位查表评分；不传则走原绝对阈值（向后兼容）。
    """
    brick = indicators["brick"]
    close = indicators["close"]
    open_ = indicators["open"]
    high = indicators["high"]
    low = indicators["low"]
    short_trend = indicators["short_trend"]
    long_short = indicators["long_short"]
    ma14 = indicators["ma14"]
    ma28 = indicators["ma28"]
    ma57 = indicators["ma57"]
    ma114 = indicators["ma114"]

    items: dict[str, float] = {}

    # ── 翻红力度比 (3分, V4大幅降权 — 回测显示无效因子) ──
    if cs_pcts is not None and "force_ratio_pct" in cs_pcts:
        items["翻红力度比"] = _pct_to_score(cs_pcts["force_ratio_pct"], 3)
    else:
        delta_today = brick[index] - brick[index - 1]
        delta_yesterday = abs(brick[index - 1] - brick[index - 2]) if index >= 2 else 0
        divisor = max(abs(delta_yesterday), 2.0)
        force_ratio = delta_today / divisor

        if force_ratio >= 3:
            items["翻红力度比"] = 3
        elif force_ratio >= 2:
            items["翻红力度比"] = 2
        elif force_ratio >= 1:
            items["翻红力度比"] = 1
        else:
            items["翻红力度比"] = 0

    # ── 信号日涨幅 (8分, V4增权) ──
    if cs_pcts is not None and "day_change_pct" in cs_pcts:
        items["信号日涨幅"] = _pct_to_score(cs_pcts["day_change_pct"], 8)
    else:
        prev_close = close[index - 1] if index >= 1 else close[index]
        day_change = (close[index] - prev_close) / prev_close * 100 if prev_close > 0 else 0

        if pattern_type == PatternType.N_SHAPE_JUMP:
            if day_change >= 9.5:
                items["信号日涨幅"] = 7
            elif day_change >= 5:
                items["信号日涨幅"] = 8
            elif day_change >= 3:
                items["信号日涨幅"] = 6
            elif day_change >= 1.5:
                items["信号日涨幅"] = 4
            else:
                items["信号日涨幅"] = 2
        elif pattern_type == PatternType.SIDEWAYS_JUMP:
            if day_change >= 9.5:
                items["信号日涨幅"] = 8
            elif day_change >= 5:
                items["信号日涨幅"] = 8
            elif day_change >= 3:
                items["信号日涨幅"] = 5
            elif day_change >= 1.5:
                items["信号日涨幅"] = 3
            else:
                items["信号日涨幅"] = 1
        else:  # UPTREND_CONTINUE
            if day_change >= 9.5:
                items["信号日涨幅"] = 8
            elif day_change >= 5:
                items["信号日涨幅"] = 7
            elif day_change >= 3:
                items["信号日涨幅"] = 5
            elif day_change >= 1.5:
                items["信号日涨幅"] = 2
            else:
                items["信号日涨幅"] = 1

    # ── 短趋势 vs 多空线 (3分, V4降权 — 回测显示无效因子) ──
    st_val = short_trend[index] if np.isfinite(short_trend[index]) else 0
    ls_val = long_short[index] if np.isfinite(long_short[index]) else 0
    if st_val > 0 and ls_val > 0:
        trend_gap = (st_val - ls_val) / ls_val * 100
    else:
        trend_gap = 0

    if trend_gap <= 0:
        items["短趋vs多空"] = 0
    elif trend_gap < 0.5:
        items["短趋vs多空"] = 0
    elif trend_gap <= 2:
        items["短趋vs多空"] = 2
    elif trend_gap <= 8:
        items["短趋vs多空"] = 3
    else:
        items["短趋vs多空"] = 1

    # ── 均线排列 (3分, V4降权 — 回测显示弱反向) ──
    ma_vals = [
        float(ma14[index]) if np.isfinite(ma14[index]) else 0,
        float(ma28[index]) if np.isfinite(ma28[index]) else 0,
        float(ma57[index]) if np.isfinite(ma57[index]) else 0,
        float(ma114[index]) if np.isfinite(ma114[index]) else 0,
    ]
    ascending_count = 0
    if all(v > 0 for v in ma_vals):
        if ma_vals[0] > ma_vals[1]:
            ascending_count += 1
        if ma_vals[1] > ma_vals[2]:
            ascending_count += 1
        if ma_vals[2] > ma_vals[3]:
            ascending_count += 1

    if ascending_count == 3:
        items["均线排列"] = 3
    elif ascending_count == 2:
        items["均线排列"] = 2
    elif ascending_count == 1:
        items["均线排列"] = 1
    else:
        items["均线排列"] = 0

    # ── 短趋势斜率 (3分) ──
    if cs_pcts is not None and "short_trend_slope_pct" in cs_pcts:
        items["短趋斜率"] = _pct_to_score(cs_pcts["short_trend_slope_pct"], 3)
    else:
        trend_window = 10
        trend_start = max(0, index - trend_window + 1)
        trend_slice = short_trend[trend_start:index + 1]
        valid_mask = np.isfinite(trend_slice)

        if np.sum(valid_mask) >= 3:
            valid_trend = trend_slice[valid_mask]
            x_vals = np.arange(len(valid_trend), dtype=float)
            slope = np.polyfit(x_vals, valid_trend, 1)[0]
            price_ref = close[index] if close[index] > 0 else 1
            slope_pct = slope / price_ref * 100
        else:
            slope_pct = 0

        if slope_pct >= 0.5:
            items["短趋斜率"] = 3
        elif slope_pct >= 0.2:
            items["短趋斜率"] = 2
        elif slope_pct >= 0:
            items["短趋斜率"] = 1
        else:
            items["短趋斜率"] = 0

    # ── K线形态质量 (10分, V4大幅增权 — 跨定式最强通用因子) ──
    body = abs(close[index] - open_[index])
    candle_range = high[index] - low[index]
    upper_shadow = high[index] - max(close[index], open_[index])
    lower_shadow = min(close[index], open_[index]) - low[index]

    if candle_range > 0.003 * close[index]:
        shadow_ratio = (upper_shadow + lower_shadow) / candle_range
        if shadow_ratio < 0.15:
            items["K线形态"] = 10
        elif shadow_ratio < 0.30:
            items["K线形态"] = 7
        elif shadow_ratio < 0.50:
            items["K线形态"] = 4
        else:
            items["K线形态"] = 0
    else:
        items["K线形态"] = 5

    total = sum(items.values())
    return total, items


def compute_macd_auxiliary_score(
    indicators: dict[str, np.ndarray],
    index: int,
    pattern_type: PatternType,
) -> tuple[float, dict[str, float]]:
    """计算 MACD 环境评分（0~25分）。所有定式类型均生效。"""
    diff = indicators["macd_diff"]
    dea = indicators["macd_dea"]
    hist = indicators["macd_hist"]
    close = indicators["close"]

    if index < 3 or not np.isfinite(diff[index]) or not np.isfinite(dea[index]):
        return 0.0, {}

    items: dict[str, float] = {}
    price_ref = close[index] if close[index] > 0 else 1.0
    diff_pct = diff[index] / price_ref * 100

    golden_dist, dead_dist = _detect_diff_dea_cross(diff, dea, index, lookback=5)

    if pattern_type != PatternType.UPTREND_CONTINUE:
        # ── N型起跳/横盘起跳的MACD评分 ──

        # DIFF位置 (0~10)
        if diff[index] < 0:
            items["DIFF位置"] = 10
        elif diff_pct < 0.5:
            items["DIFF位置"] = 7
        elif diff[index] > 0 and diff[index] < dea[index]:
            items["DIFF位置"] = 4
        elif diff[index] > dea[index] and golden_dist >= 0 and golden_dist <= 5:
            items["DIFF位置"] = 6
        else:
            items["DIFF位置"] = 2

        # MACD柱状态 (0~8)
        if index >= 1 and hist[index] > 0 and hist[index - 1] < 0:
            items["MACD柱状态"] = 8
        elif hist[index] > 0 and index >= 1 and hist[index] > hist[index - 1]:
            items["MACD柱状态"] = 6
        elif hist[index] > 0:
            items["MACD柱状态"] = 3
        elif hist[index] < 0 and index >= 1 and hist[index] > hist[index - 1]:
            items["MACD柱状态"] = 3
        else:
            items["MACD柱状态"] = 0

        # 金叉确认 (0~7, 可扣分)
        if golden_dist > 0 and golden_dist <= 5:
            items["金叉确认"] = 7
        elif golden_dist == 0:
            items["金叉确认"] = 4
        elif diff[index] > dea[index]:
            items["金叉确认"] = 3
        elif index >= 1 and diff[index] > diff[index - 1]:
            items["金叉确认"] = 2
        else:
            items["金叉确认"] = 0

        if dead_dist >= 0 and dead_dist <= 3:
            items["近期死叉"] = -3

    else:
        # ── 上升波段延续的MACD评分 ──

        # DIFF趋势 (0~10)
        if diff[index] > dea[index]:
            diff_rising = index >= 1 and diff[index] > diff[index - 1]
            diff_flat = index >= 1 and abs(diff[index] - diff[index - 1]) < price_ref * 0.001
            if diff_rising:
                items["DIFF趋势"] = 10
            elif diff_flat:
                items["DIFF趋势"] = 7
            else:
                items["DIFF趋势"] = 3
        else:
            items["DIFF趋势"] = 0

        # MACD柱趋势 (0~8)
        if hist[index] > 0:
            consecutive_up = 0
            for d in range(index, max(index - 5, 0), -1):
                if d >= 1 and hist[d] > hist[d - 1]:
                    consecutive_up += 1
                else:
                    break
            if consecutive_up >= 2:
                items["MACD柱趋势"] = 8
            else:
                items["MACD柱趋势"] = 4
        else:
            items["MACD柱趋势"] = 0

        # DIFF水平 (0~7)
        if 0 < diff_pct <= 1:
            items["DIFF水平"] = 7
        elif diff_pct > 1:
            items["DIFF水平"] = 4
        elif diff[index] < 0:
            items["DIFF水平"] = 1
        else:
            items["DIFF水平"] = 3

    total = max(0, sum(items.values()))
    return total, items


def compute_signal_strength_score(
    indicators: dict[str, np.ndarray],
    index: int,
) -> tuple[float, dict[str, float]]:
    """计算信号强度评分（0~15分）：T日涨幅(10) + 涨幅质量(5)。

    回测验证（2025-01~2026-04，89335条信号）：
    - 涨幅质量只有涨停封板（5分）显著有效：T+1均值1.57%、胜率57.6%
    - 涨6-8%未封板区间反而胜率最低(45.1%)，冲高未封可能是诱多
    - 因此涨幅质量简化为：封板5分 / 炸板2分 / 其余0分
    """
    close = indicators["close"]
    high = indicators["high"]
    low = indicators["low"]
    items: dict[str, float] = {}

    prev_close = close[index - 1] if index >= 1 else close[index]
    if prev_close <= 0:
        return 0.0, {}

    day_change = (close[index] - prev_close) / prev_close * 100

    # ── T日涨幅 (10分) ──
    if day_change >= 9.5:
        items["T日涨幅"] = 10
    elif day_change >= 8:
        items["T日涨幅"] = 9
    elif day_change >= 6:
        items["T日涨幅"] = 7
    elif day_change >= 4:
        items["T日涨幅"] = 6
    elif day_change >= 2:
        items["T日涨幅"] = 4
    elif day_change >= 0:
        items["T日涨幅"] = 2
    else:
        items["T日涨幅"] = 0

    # ── 涨幅质量 (5分) ──
    # 只有涨停封板才给满分，炸板给2分，其余不加分
    if day_change >= 9.5:
        is_sealed = abs(high[index] - close[index]) < 0.01 * close[index]
        if is_sealed:
            items["涨幅质量"] = 5
        else:
            items["涨幅质量"] = 2
    else:
        items["涨幅质量"] = 0

    total = sum(items.values())
    return min(15.0, total), items


# compute_risk_penalty 拆分到 scoring_risk.py（13个独立检查函数 + 聚合主函数）
from .scoring_risk import compute_risk_penalty  # noqa: E402,F401


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# P3 战法加分（红柱比、地量、金叉时间细化）
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def compute_p3_bonus(
    indicators: dict[str, np.ndarray],
    index: int,
    pattern_type: PatternType,
) -> tuple[float, dict[str, float]]:
    """P3 战法加分（命中即加，不命中不扣）。

    - 红柱 ≥ 绿柱 2/3 → +2 分（战法明文"不够 2/3 不做"）
    - 地量（信号日 volume 缩到近 30~60 日最低区间）→ +2 分（战法"地量 + N 型 = 最佳买点"）
    - DIFF/DEA 刚金叉（≤ 2 日内）→ +1 分（区别于"金叉多日"）

    总加分 0~5 分。
    """
    items: dict[str, float] = {}

    # ── P3-4 红柱 ≥ 绿柱 2/3 ──
    if _red_green_ratio_ok(indicators["brick"], index):
        items["红柱比2/3"] = 2

    # ── P3-5 地量 ──
    if _is_dry_volume(indicators["volume"], index):
        items["地量"] = 2

    # ── P3-7 DIFF/DEA 刚金叉 ──
    diff = indicators.get("macd_diff")
    dea = indicators.get("macd_dea")
    if diff is not None and dea is not None:
        cross_age = _diff_dea_cross_age(diff, dea, index, lookback=5)
        if cross_age is not None and cross_age <= 2:
            items["DIFF/DEA刚金叉"] = 1

    return float(sum(items.values())), items


def _red_green_ratio_ok(brick: np.ndarray, index: int, window: int = 10) -> bool:
    """近 window 砖红/绿砖累计长度比 ≥ 2/3 视为多头力量足够。

    红砖累计长度 = sum(max(0, brick[i] - brick[i-1]))
    绿砖累计长度 = sum(max(0, brick[i-1] - brick[i]))
    """
    start = max(1, index - window + 1)
    red_sum = green_sum = 0.0
    for i in range(start, index + 1):
        delta = float(brick[i] - brick[i - 1])
        if delta > 0:
            red_sum += delta
        elif delta < 0:
            green_sum += -delta
    if green_sum < 1e-9:
        return True  # 全红
    return (red_sum / green_sum) >= (2.0 / 3.0)


def _is_dry_volume(volume: np.ndarray, index: int, lookback: int = 60) -> bool:
    """信号日 volume 缩到近 lookback 日最低 20% 区间内视为地量。"""
    start = max(0, index - lookback + 1)
    window = volume[start: index + 1]
    valid = window[np.isfinite(window) & (window > 0)]
    if len(valid) < 20:
        return False
    threshold = float(np.percentile(valid, 20))
    today = float(volume[index])
    return today > 0 and today <= threshold


def _diff_dea_cross_age(
    diff: np.ndarray,
    dea: np.ndarray,
    index: int,
    lookback: int = 5,
) -> int | None:
    """向前找最近一次 diff 上穿 dea 的天数；找不到返回 None。"""
    start = max(1, index - lookback + 1)
    for i in range(index, start - 1, -1):
        if i < 1:
            continue
        if not (np.isfinite(diff[i]) and np.isfinite(diff[i - 1])
                and np.isfinite(dea[i]) and np.isfinite(dea[i - 1])):
            continue
        # 上穿：前一日 diff <= dea，本日 diff > dea
        if diff[i - 1] <= dea[i - 1] and diff[i] > dea[i]:
            return index - i
    return None
