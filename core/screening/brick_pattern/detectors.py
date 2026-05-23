"""砖形图三种定式检测器：N 型起跳 / 横盘起跳 / 上升波段延续。"""

from __future__ import annotations

import numpy as np

from core.models.brick_pattern import PatternMatchDetail, PatternType

from .helpers import (
    _count_brick_color_switches,
    _find_pullback_phase,
    _find_prior_uptrend,
    _is_green_brick,
    _is_in_n_shape_decline,
    _is_red_brick,
)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 三种定式检测器
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def detect_n_shape_jump(indicators: dict[str, np.ndarray], index: int) -> PatternMatchDetail:
    """N型起跳检测 V4。

    评分维度：超卖深度(12) + 回调充分度(8) + 价格与黄白线(5,反转) + 前段上涨基础(5) = 30分
    V4变更：超卖深度增权12分；回调充分度降权8分；价格与黄白线反转方向(远离短趋=高分)。
    """
    brick = indicators["brick"]
    close = indicators["close"]
    kdj_j = indicators["kdj_j"]
    short_trend = indicators["short_trend"]
    long_short = indicators["long_short"]

    if index < 10:
        return PatternMatchDetail(pattern_type=PatternType.N_SHAPE_JUMP, matched=False,
                                  description="数据不足")

    pullback_start, max_green, interruptions = _find_pullback_phase(brick, index)
    total_green = sum(1 for i in range(pullback_start, index) if _is_green_brick(brick, i))

    if total_green < 1:
        return PatternMatchDetail(pattern_type=PatternType.N_SHAPE_JUMP, matched=False,
                                  description="无绿砖回调")

    # ── 超卖深度 (12分, V4增权) ──
    prev_j = float(kdj_j[index - 1]) if index >= 1 and np.isfinite(kdj_j[index - 1]) else 50.0
    if prev_j < 10:
        oversold_score = 12
    elif prev_j < 20:
        oversold_score = 8
    elif prev_j < 30:
        oversold_score = 5
    elif prev_j < 40:
        oversold_score = 2
    else:
        oversold_score = 0

    if oversold_score == 0:
        return PatternMatchDetail(pattern_type=PatternType.N_SHAPE_JUMP, matched=False,
                                  description=f"J值过高({prev_j:.1f}≥40)不符合N型")

    if _is_in_n_shape_decline(indicators, index):
        return PatternMatchDetail(pattern_type=PatternType.N_SHAPE_JUMP, matched=False,
                                  description="处于N型下跌段(非真起跳)")

    # ── 回调充分度 (8分, V4降权) ──
    if 4 <= max_green <= 6:
        pullback_score = 8
    elif max_green == 7:
        pullback_score = 6
    elif max_green == 3:
        pullback_score = 5
    elif max_green == 2:
        pullback_score = 3
    elif max_green == 1:
        pullback_score = 2
    elif max_green >= 8:
        pullback_score = 2
    else:
        pullback_score = 0

    for intr in interruptions:
        if intr == 1:
            pullback_score -= 1
        elif intr >= 2:
            pullback_score -= 2
    pullback_score = max(0, pullback_score)

    # ── 价格与黄白线 (5分, V4反转方向: 远离短趋线=高分) ──
    st_val = short_trend[index] if np.isfinite(short_trend[index]) else close[index]
    ls_val = long_short[index] if np.isfinite(long_short[index]) else close[index]
    vs_short = (close[index] - st_val) / st_val * 100 if st_val > 0 else 0
    vs_long = (close[index] - ls_val) / ls_val * 100 if ls_val > 0 else 0

    if vs_short < -5 or vs_short > 6:
        price_score = 5
    elif -5 <= vs_short < -3 or 2 < vs_short <= 6:
        price_score = 3
    else:
        price_score = 1

    if vs_long < 0:
        price_score = min(price_score, 2)

    # ── 前段上涨基础 (5分) ──
    rise_pct, duration, quality = _find_prior_uptrend(close, pullback_start)

    rise_score = 2 if 0.10 <= rise_pct <= 0.25 else (1 if rise_pct >= 0.05 else 0)
    dur_score = 1 if 4 <= duration <= 20 else 0
    qual_score = min(quality, 2)
    uptrend_score = rise_score + dur_score + qual_score

    specific_score = min(30, oversold_score + pullback_score + price_score + uptrend_score)

    items = {
        "超卖深度": oversold_score,
        "回调充分度": pullback_score,
        "价格与黄白线": price_score,
        "前段上涨基础": uptrend_score,
    }

    description = f"N型(前日J={prev_j:.1f},最长绿砖段{max_green}天,vs短趋{vs_short:.1f}%)"

    return PatternMatchDetail(
        pattern_type=PatternType.N_SHAPE_JUMP,
        matched=True,
        description=description,
        score=specific_score,
        extra={
            "kdj_j": prev_j,
            "max_green_segment": max_green,
            "vs_short_trend": round(vs_short, 2),
            "vs_long_short": round(vs_long, 2),
            "specific_items": items,
        },
    )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 横盘起跳检测 (V2)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def detect_sideways_jump(indicators: dict[str, np.ndarray], index: int) -> PatternMatchDetail:
    """横盘起跳检测 V4。

    评分维度：蓄势充分度(10) + 突破弹性(12) + KDJ动能(4,反转) + 价格强度(4,反转) = 30分
    V4变更：蓄势充分度降权10分；突破弹性增权12分(最强因子)；KDJ动能反转(J低=高分)；价格强度反转(贴近多空线=高分)。
    """
    brick = indicators["brick"]
    close = indicators["close"]
    high = indicators["high"]
    low = indicators["low"]
    kdj_j = indicators["kdj_j"]
    long_short = indicators["long_short"]

    if index < 12:
        return PatternMatchDetail(pattern_type=PatternType.SIDEWAYS_JUMP, matched=False,
                                  description="数据不足")

    if index < 2 or not _is_green_brick(brick, index - 1):
        return PatternMatchDetail(pattern_type=PatternType.SIDEWAYS_JUMP, matched=False,
                                  description="前日非绿砖")
    if index >= 3 and _is_green_brick(brick, index - 2):
        return PatternMatchDetail(pattern_type=PatternType.SIDEWAYS_JUMP, matched=False,
                                  description="前2日也是绿砖(非横盘特征)")

    # ── 蓄势充分度 (10分, V4降权) ──
    switches = _count_brick_color_switches(brick, index - 1, window=10)

    if 5 <= switches <= 7:
        charge_score = 10
    elif switches == 4:
        charge_score = 7
    elif switches == 3:
        charge_score = 4
    elif switches <= 2:
        charge_score = 2
    else:
        charge_score = 6

    amp_start = max(0, index - 10)
    window_high = np.max(high[amp_start:index])
    window_low = np.min(low[amp_start:index])
    amplitude = (window_high - window_low) / window_low * 100 if window_low > 0 else 999

    if amplitude < 8:
        charge_score = min(11, charge_score + 1)

    # ── 突破弹性 (12分, V4增权 — 横盘起跳最强因子) ──
    brick_jump = brick[index] - brick[index - 1]
    if brick_jump >= 15:
        breakout_score = 12
    elif brick_jump >= 12:
        breakout_score = 9
    elif brick_jump >= 9:
        breakout_score = 6
    elif brick_jump >= 6:
        breakout_score = 3
    else:
        breakout_score = 1

    # ── KDJ动能 (4分, V4反转方向: J值低=蓄势充分=高分) ──
    j_val = float(kdj_j[index]) if np.isfinite(kdj_j[index]) else 50.0
    if j_val < 30:
        kdj_score = 4
    elif 30 <= j_val < 50:
        kdj_score = 3
    elif 50 <= j_val < 65:
        kdj_score = 2
    elif 65 <= j_val <= 85:
        kdj_score = 1
    else:
        kdj_score = 1

    # ── 价格强度 (4分, V4反转方向: 贴近多空线=支撑好=高分) ──
    ls_val = long_short[index] if np.isfinite(long_short[index]) else close[index]
    vs_long = (close[index] - ls_val) / ls_val * 100 if ls_val > 0 else 0

    if vs_long < 2:
        price_score = 4
    elif 2 <= vs_long < 5:
        price_score = 3
    elif 5 <= vs_long <= 15:
        price_score = 2
    elif 15 < vs_long <= 25:
        price_score = 1
    else:
        price_score = 1

    specific_score = min(30, charge_score + breakout_score + kdj_score + price_score)

    items = {
        "蓄势充分度": charge_score,
        "突破弹性": breakout_score,
        "KDJ动能": kdj_score,
        "价格强度": price_score,
    }

    description = f"横盘(切换{switches}次,跳升{brick_jump:.1f},J={j_val:.1f})"

    return PatternMatchDetail(
        pattern_type=PatternType.SIDEWAYS_JUMP,
        matched=True,
        description=description,
        score=specific_score,
        extra={
            "switches": switches,
            "brick_jump": round(brick_jump, 2),
            "kdj_j": round(j_val, 2),
            "vs_long_short": round(vs_long, 2),
            "amplitude": round(amplitude, 2),
            "specific_items": items,
        },
    )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 上升波段延续检测 (V2)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def detect_uptrend_continue(indicators: dict[str, np.ndarray], index: int) -> PatternMatchDetail:
    """上升波段延续检测 V4。

    评分维度：趋势连续性(6) + 回调极短性(10,反转) + 砖值绝对水平(6) + KDJ超买动能(8) = 30分
    V4变更：趋势连续性大幅降权(无效因子)；回调极短性反转(绿砖多=回调充分=高分)并增权；
    砖值略增权；KDJ超买增权(T+1正向)。
    """
    brick = indicators["brick"]
    kdj_j = indicators["kdj_j"]

    if index < 8:
        return PatternMatchDetail(pattern_type=PatternType.UPTREND_CONTINUE, matched=False,
                                  description="数据不足")

    green_count = 0
    scan_pos = index - 1
    while scan_pos >= 1 and _is_green_brick(brick, scan_pos):
        green_count += 1
        scan_pos -= 1

    if green_count < 1:
        return PatternMatchDetail(
            pattern_type=PatternType.UPTREND_CONTINUE,
            matched=False,
            description="无绿砖回调",
        )

    red_end = scan_pos
    red_count = 0
    while red_end >= 1 and _is_red_brick(brick, red_end):
        red_count += 1
        red_end -= 1

    if red_count < 3:
        return PatternMatchDetail(
            pattern_type=PatternType.UPTREND_CONTINUE,
            matched=False,
            description=f"前方红砖仅{red_count}根(需>=3)",
        )

    # ── 趋势连续性 (6分, V4大幅降权 — 回测显示无效因子) ──
    if red_count >= 7:
        trend_score = 6
    elif red_count >= 5:
        trend_score = 5
    elif red_count == 4:
        trend_score = 3
    elif red_count == 3:
        trend_score = 2
    else:
        trend_score = 1

    # ── 回调极短性 (10分, V4反转方向+增权: 绿砖多=回调充分=高分) ──
    if green_count >= 3:
        short_base = 8
    elif green_count == 2:
        short_base = 6
    elif green_count == 1:
        short_base = 2
    else:
        short_base = 1

    green_start_brick = brick[index - green_count - 1]
    green_end_brick = brick[index - 1]
    brick_drop = green_start_brick - green_end_brick

    if brick_drop >= 5:
        drop_bonus = 2
    elif brick_drop >= 1:
        drop_bonus = 1
    else:
        drop_bonus = 0

    short_score = min(10, short_base + drop_bonus)

    # ── 砖值绝对水平 (6分, V4略增权) ──
    brick_val = brick[index]
    if brick_val >= 130:
        level_score = 6
    elif brick_val >= 115:
        level_score = 5
    elif brick_val >= 100:
        level_score = 3
    elif brick_val >= 85:
        level_score = 1
    else:
        level_score = 0

    if level_score == 0:
        return PatternMatchDetail(
            pattern_type=PatternType.UPTREND_CONTINUE,
            matched=False,
            description=f"砖值{brick_val:.1f}<85,不符合波段延续",
        )

    # ── KDJ超买动能 (8分, V4增权 — T+1正向有效) ──
    j_val = float(kdj_j[index]) if np.isfinite(kdj_j[index]) else 50.0
    if j_val > 95:
        kdj_score = 8
    elif j_val > 90:
        kdj_score = 6
    elif j_val > 80:
        kdj_score = 4
    else:
        kdj_score = 1

    specific_score = min(30, trend_score + short_score + level_score + kdj_score)

    items = {
        "趋势连续性": trend_score,
        "回调极短性": short_score,
        "砖值绝对水平": level_score,
        "KDJ超买动能": kdj_score,
    }

    description = f"波段延续({red_count}红{green_count}绿,砖值{brick_val:.0f},J={j_val:.1f})"

    return PatternMatchDetail(
        pattern_type=PatternType.UPTREND_CONTINUE,
        matched=True,
        description=description,
        score=specific_score,
        extra={
            "red_count": red_count,
            "green_count": green_count,
            "brick_val": round(brick_val, 2),
            "brick_drop": round(brick_drop, 2),
            "kdj_j": round(j_val, 2),
            "specific_items": items,
        },
    )
