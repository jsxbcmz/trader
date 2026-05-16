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

def compute_common_quality_score(
    indicators: dict[str, np.ndarray],
    index: int,
    pattern_type: PatternType,
) -> tuple[float, dict[str, float]]:
    """计算通用质量评分(30分) V4：K线形态(10) + 信号日涨幅(8) + 翻红力度比(3) + 短趋vs多空(3) + 均线排列(3) + 短趋斜率(3)。

    V4变更：K线形态大幅增权(4→10,跨定式最强通用因子)；信号日涨幅增权(6→8)；
    翻红力度比大幅降权(7→3,无效因子)；短趋vs多空降权(6→3)；均线排列降权(4→3)。
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

    # ── 短趋势斜率 (3分, 不变) ──
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
