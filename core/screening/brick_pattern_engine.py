"""砖形图交易定式选股引擎 V3。

独立于 TDX 表达式系统，直接用 Python/NumPy 实现：
- 3 种交易定式：N型起跳、横盘起跳、上升波段延续
- 必备前提：绿转红 + 力度达标(≥0.3) + 短趋线>多空线
- V3 评分：定式专属(30) + 通用质量(30) + MACD环境(25) + 信号强度(15) + 风险扣分
- S/A/B/C/D 五级评分
"""

from __future__ import annotations

import os
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from app.chart_indicators import (
    compute_brick_indicator,
    compute_kdj_indicator,
    compute_macd_indicator,
    compute_zx_long_short,
    compute_zx_short_trend,
    moving_average,
)
from core.data.repository import StockRepository
from core.data.time_index import locate_time_index
from core.models.brick_pattern import (
    BrickPatternMatch,
    BrickPatternRequest,
    BrickPatternResult,
    PatternMatchDetail,
    PatternType,
    RiskFilterDetail,
    RiskFilterType,
    ScoreBreakdown,
)
from core.stock_pool.manager import StockPoolManager

DEFAULT_PROGRESS_INTERVAL = 20
DEFAULT_MAX_WORKERS = max(1, (os.cpu_count() or 2) - 1)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 指标计算辅助
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _calc_indicators(df: pd.DataFrame) -> dict[str, np.ndarray]:
    """一次性计算所有需要的指标序列，避免重复计算。"""
    high = df["high"].values.astype(float)
    low = df["low"].values.astype(float)
    close = df["close"].values.astype(float)
    open_ = df["open"].values.astype(float)
    volume = df["volume"].values.astype(float) if "volume" in df.columns else np.zeros(len(close))

    brick_result = compute_brick_indicator(high, low, close)
    brick = brick_result["brick"]

    short_trend = compute_zx_short_trend(close)
    long_short = compute_zx_long_short(close)

    kdj = compute_kdj_indicator(high, low, close)

    macd_result = compute_macd_indicator(close)

    ma14 = moving_average(close, 14)
    ma28 = moving_average(close, 28)
    ma57 = moving_average(close, 57)
    ma114 = moving_average(close, 114)

    return {
        "high": high,
        "low": low,
        "close": close,
        "open": open_,
        "volume": volume,
        "brick": brick,
        "short_trend": short_trend,
        "long_short": long_short,
        "kdj_k": kdj["k"],
        "kdj_d": kdj["d"],
        "kdj_j": kdj["j"],
        "macd_diff": macd_result["diff"],
        "macd_dea": macd_result["dea"],
        "macd_hist": macd_result["macd"],
        "macd_cross_up": macd_result["cross_up"],
        "ma14": ma14,
        "ma28": ma28,
        "ma57": ma57,
        "ma114": ma114,
    }


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 砖形图辅助函数
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _is_red_brick(brick: np.ndarray, i: int) -> bool:
    """判断第i天是否为红砖（砖值上升）"""
    return i >= 1 and brick[i] > brick[i - 1]


def _is_green_brick(brick: np.ndarray, i: int) -> bool:
    """判断第i天是否为绿砖（砖值下降或持平）"""
    return i >= 1 and brick[i] <= brick[i - 1]


def _count_brick_color_switches(brick: np.ndarray, index: int, window: int = 10) -> int:
    """计算前window日砖色切换次数"""
    start = max(2, index - window + 1)
    switches = 0
    for i in range(start, index + 1):
        curr_red = _is_red_brick(brick, i)
        prev_red = _is_red_brick(brick, i - 1)
        if curr_red != prev_red:
            switches += 1
    return switches


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 必备前提检测
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def check_prerequisites(indicators: dict[str, np.ndarray], index: int) -> tuple[bool, str]:
    """检查三项必备前提，全部满足才返回 (True, "")。

    1. 砖形图绿转红
    2. 砖形图差值>0 且力度达标(≥0.3)
    3. 短趋线 > 多空线
    """
    brick = indicators["brick"]
    short_trend = indicators["short_trend"]
    long_short = indicators["long_short"]

    if index < 2:
        return False, "数据不足"

    current_rising = brick[index] > brick[index - 1]
    prev_rising = brick[index - 1] > brick[index - 2]

    if not current_rising:
        return False, "当日非红砖"
    if prev_rising:
        return False, "前日已是红砖(非绿转红)"

    delta_today = brick[index] - brick[index - 1]
    delta_yesterday = abs(brick[index - 1] - brick[index - 2])

    if delta_today <= 0:
        return False, "砖形图差值<=0"

    if delta_yesterday > 1e-9:
        force_ratio = abs(delta_today) / delta_yesterday
        if force_ratio < 0.3:
            return False, f"翻红力度不足(比值{force_ratio:.2f}<0.3)"

    if not (np.isfinite(short_trend[index]) and np.isfinite(long_short[index])):
        return False, "趋势线数据无效"

    if short_trend[index] <= long_short[index]:
        return False, "短趋线未在多空线之上"

    return True, ""


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# N型起跳检测 (V2)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _find_pullback_phase(brick: np.ndarray, index: int) -> tuple[int, int, list[int]]:
    """从信号日向前追溯回调阶段。

    Returns:
        (回调阶段起点index, 最长连续绿砖段天数, 打断红砖天数列表)
    """
    pos = index - 1
    green_segments: list[int] = []
    interruptions: list[int] = []
    current_green = 0

    while pos >= 1:
        if _is_green_brick(brick, pos):
            current_green += 1
            pos -= 1
        else:
            if current_green > 0:
                green_segments.append(current_green)
                current_green = 0
            red_count = 0
            check_pos = pos
            while check_pos >= 1 and _is_red_brick(brick, check_pos):
                red_count += 1
                check_pos -= 1
            if red_count >= 3:
                break
            interruptions.append(red_count)
            pos = check_pos
    if current_green > 0:
        green_segments.append(current_green)

    # 过滤：尾部（远离信号日）的1天绿砖段通常是上涨→回调的过渡日，
    # 不应视为回调阶段的有效延伸，移除该段及其关联的打断记录
    while len(green_segments) > 1 and green_segments[-1] < 2 and interruptions:
        green_segments.pop()
        interruptions.pop()

    max_green = max(green_segments) if green_segments else 0
    pullback_start = pos + 1
    return pullback_start, max_green, interruptions


def _find_prior_uptrend(close: np.ndarray, pullback_start: int) -> tuple[float, int, int]:
    """从回调阶段起点向前追溯，找到波段高点和起涨点。

    如果从起涨点到波段高点之间存在中间调整（跌幅>3%），则截取第一段波段
    作为N型第一笔，避免把整段大行情都算作一个波段。

    Returns:
        (波段涨幅, 上涨波段持续天数, 趋势质量分)
    """
    if pullback_start < 2:
        return 0.0, 0, 0

    peak_idx = pullback_start
    peak_val = close[pullback_start]
    scan_end = max(0, pullback_start - 15)
    for i in range(pullback_start, scan_end - 1, -1):
        if close[i] >= peak_val:
            peak_val = close[i]
            peak_idx = i

    search_start = max(0, peak_idx - 30)
    trough_idx = peak_idx
    trough_val = close[peak_idx]
    for i in range(search_start, peak_idx):
        if close[i] < trough_val:
            trough_val = close[i]
            trough_idx = i

    if trough_val <= 0:
        return 0.0, 0, 0

    effective_peak_idx = peak_idx
    effective_peak_val = peak_val

    running_max = trough_val
    running_max_idx = trough_idx
    for i in range(trough_idx + 1, peak_idx + 1):
        if close[i] > running_max:
            running_max = close[i]
            running_max_idx = i
        if running_max > trough_val and running_max_idx < i:
            decline = (running_max - close[i]) / running_max
            if decline > 0.03:
                wave_rise = (running_max - trough_val) / trough_val
                wave_dur = running_max_idx - trough_idx
                if wave_rise >= 0.10 and wave_dur >= 4:
                    effective_peak_idx = running_max_idx
                    effective_peak_val = running_max
                    break

    rise_pct = (effective_peak_val - trough_val) / trough_val
    duration = effective_peak_idx - trough_idx

    quality = 0
    if duration >= 3:
        seg_len = max(1, duration // 3)
        seg1 = close[trough_idx:trough_idx + seg_len]
        seg2 = close[trough_idx + seg_len:trough_idx + 2 * seg_len]
        seg3 = close[trough_idx + 2 * seg_len:effective_peak_idx + 1]
        if len(seg1) > 0 and len(seg2) > 0 and len(seg3) > 0:
            m1, m2, m3 = np.mean(seg1), np.mean(seg2), np.mean(seg3)
            ascending = 0
            if m2 > m1:
                ascending += 1
            if m3 > m2:
                ascending += 1
            if m2 > m1 and m3 > m2:
                quality = 3
            elif ascending >= 1:
                if m2 > m1 * 0.99 and m3 > m2 * 0.99:
                    quality = 2
                else:
                    quality = 1
            else:
                quality = 1

    return rise_pct, duration, quality


def detect_n_shape_jump(indicators: dict[str, np.ndarray], index: int) -> PatternMatchDetail:
    """N型起跳检测 V3。

    评分维度：超卖深度(10) + 回调充分度(10) + 价格与黄白线(5) + 前段上涨基础(5) = 30分
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

    # ── 超卖深度 (10分) ──
    prev_j = float(kdj_j[index - 1]) if index >= 1 and np.isfinite(kdj_j[index - 1]) else 50.0
    if prev_j < 10:
        oversold_score = 10
    elif prev_j < 20:
        oversold_score = 6
    elif prev_j < 30:
        oversold_score = 4
    elif prev_j < 40:
        oversold_score = 2
    else:
        oversold_score = 0

    if oversold_score == 0:
        return PatternMatchDetail(pattern_type=PatternType.N_SHAPE_JUMP, matched=False,
                                  description=f"J值过高({prev_j:.1f}≥40)不符合N型")

    # ── 回调充分度 (10分) ──
    if 4 <= max_green <= 6:
        pullback_score = 10
    elif max_green == 7:
        pullback_score = 8
    elif max_green == 3:
        pullback_score = 7
    elif max_green == 2:
        pullback_score = 4
    elif max_green == 1:
        pullback_score = 2
    elif max_green >= 8:
        pullback_score = 3
    else:
        pullback_score = 0

    for intr in interruptions:
        if intr == 1:
            pullback_score -= 1
        elif intr >= 2:
            pullback_score -= 2
    pullback_score = max(0, pullback_score)

    # ── 价格与黄白线 (5分) ──
    st_val = short_trend[index] if np.isfinite(short_trend[index]) else close[index]
    ls_val = long_short[index] if np.isfinite(long_short[index]) else close[index]
    vs_short = (close[index] - st_val) / st_val * 100 if st_val > 0 else 0
    vs_long = (close[index] - ls_val) / ls_val * 100 if ls_val > 0 else 0

    if -3 <= vs_short <= 2:
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
    """横盘起跳检测 V3。

    评分维度：蓄势充分度(12) + 突破弹性(8) + KDJ动能(5) + 价格强度(5) = 30分
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

    # ── 蓄势充分度 (12分) ──
    switches = _count_brick_color_switches(brick, index - 1, window=10)

    if 5 <= switches <= 7:
        charge_score = 12
    elif switches == 4:
        charge_score = 9
    elif switches == 3:
        charge_score = 5
    elif switches <= 2:
        charge_score = 2
    else:
        charge_score = 8

    amp_start = max(0, index - 10)
    window_high = np.max(high[amp_start:index])
    window_low = np.min(low[amp_start:index])
    amplitude = (window_high - window_low) / window_low * 100 if window_low > 0 else 999

    if amplitude < 8:
        charge_score = min(13, charge_score + 1)

    # ── 突破弹性 (8分) ──
    brick_jump = brick[index] - brick[index - 1]
    if brick_jump >= 15:
        breakout_score = 8
    elif brick_jump >= 12:
        breakout_score = 6
    elif brick_jump >= 9:
        breakout_score = 4
    elif brick_jump >= 6:
        breakout_score = 2
    else:
        breakout_score = 1

    # ── KDJ动能 (5分) ──
    j_val = float(kdj_j[index]) if np.isfinite(kdj_j[index]) else 50.0
    if 65 <= j_val <= 85:
        kdj_score = 5
    elif 50 <= j_val < 65:
        kdj_score = 4
    elif 85 < j_val <= 100:
        kdj_score = 3
    elif 30 <= j_val < 50:
        kdj_score = 2
    else:
        kdj_score = 1

    # ── 价格强度 (5分) ──
    ls_val = long_short[index] if np.isfinite(long_short[index]) else close[index]
    vs_long = (close[index] - ls_val) / ls_val * 100 if ls_val > 0 else 0

    if 5 <= vs_long <= 15:
        price_score = 5
    elif 2 <= vs_long < 5:
        price_score = 3
    elif 15 < vs_long <= 25:
        price_score = 2
    elif vs_long < 2:
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
    """上升波段延续检测 V3。

    评分维度：趋势连续性(12) + 回调极短性(8) + 砖值绝对水平(5) + KDJ超买动能(5) = 30分
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

    # ── 趋势连续性 (12分) ──
    if red_count >= 7:
        trend_score = 12
    elif red_count >= 5:
        trend_score = 9
    elif red_count == 4:
        trend_score = 6
    elif red_count == 3:
        trend_score = 3
    else:
        trend_score = 1

    # ── 回调极短性 (8分) ──
    if green_count == 1:
        short_base = 6
    elif green_count == 2:
        short_base = 3
    else:
        short_base = 1

    green_start_brick = brick[index - green_count - 1]
    green_end_brick = brick[index - 1]
    brick_drop = green_start_brick - green_end_brick

    if brick_drop < 1:
        drop_bonus = 2
    elif brick_drop <= 5:
        drop_bonus = 1
    else:
        drop_bonus = 0

    short_score = short_base + drop_bonus

    # ── 砖值绝对水平 (5分) ──
    brick_val = brick[index]
    if brick_val >= 130:
        level_score = 5
    elif brick_val >= 115:
        level_score = 4
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

    # ── KDJ超买动能 (5分) ──
    j_val = float(kdj_j[index]) if np.isfinite(kdj_j[index]) else 50.0
    if j_val > 95:
        kdj_score = 5
    elif j_val > 90:
        kdj_score = 4
    elif j_val > 80:
        kdj_score = 3
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


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 通用质量评分 (30分)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def compute_common_quality_score(
    indicators: dict[str, np.ndarray],
    index: int,
    pattern_type: PatternType,
) -> tuple[float, dict[str, float]]:
    """计算通用质量评分(30分)：翻红质量 + 趋势环境 + K线形态。"""
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

    # ── 翻红力度比 (7分) ──
    delta_today = brick[index] - brick[index - 1]
    delta_yesterday = abs(brick[index - 1] - brick[index - 2]) if index >= 2 else 0
    divisor = max(abs(delta_yesterday), 2.0)
    force_ratio = delta_today / divisor

    if force_ratio >= 3:
        items["翻红力度比"] = 7
    elif force_ratio >= 2:
        items["翻红力度比"] = 5
    elif force_ratio >= 1.5:
        items["翻红力度比"] = 4
    elif force_ratio >= 1:
        items["翻红力度比"] = 2
    elif force_ratio >= 0.5:
        items["翻红力度比"] = 1
    else:
        items["翻红力度比"] = 0

    # ── 信号日涨幅 (6分) ──
    prev_close = close[index - 1] if index >= 1 else close[index]
    day_change = (close[index] - prev_close) / prev_close * 100 if prev_close > 0 else 0

    if pattern_type == PatternType.N_SHAPE_JUMP:
        if day_change >= 9.5:
            items["信号日涨幅"] = 5
        elif day_change >= 5:
            items["信号日涨幅"] = 6
        elif day_change >= 3:
            items["信号日涨幅"] = 5
        elif day_change >= 1.5:
            items["信号日涨幅"] = 4
        else:
            items["信号日涨幅"] = 2
    elif pattern_type == PatternType.SIDEWAYS_JUMP:
        if day_change >= 9.5:
            items["信号日涨幅"] = 6
        elif day_change >= 5:
            items["信号日涨幅"] = 6
        elif day_change >= 3:
            items["信号日涨幅"] = 4
        elif day_change >= 1.5:
            items["信号日涨幅"] = 3
        else:
            items["信号日涨幅"] = 1
    else:  # UPTREND_CONTINUE
        if day_change >= 9.5:
            items["信号日涨幅"] = 6
        elif day_change >= 5:
            items["信号日涨幅"] = 5
        elif day_change >= 3:
            items["信号日涨幅"] = 4
        elif day_change >= 1.5:
            items["信号日涨幅"] = 2
        else:
            items["信号日涨幅"] = 1

    # ── 短趋势 vs 多空线 (6分) ──
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
        items["短趋vs多空"] = 4
    elif trend_gap <= 8:
        items["短趋vs多空"] = 6
    else:
        items["短趋vs多空"] = 3

    # ── 均线排列 (4分) ──
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
        items["均线排列"] = 4
    elif ascending_count == 2:
        items["均线排列"] = 3
    elif ascending_count == 1:
        items["均线排列"] = 1
    else:
        items["均线排列"] = 0

    # ── 短趋势斜率 (3分) ──
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

    # ── K线形态质量 (4分) ──
    body = abs(close[index] - open_[index])
    candle_range = high[index] - low[index]
    upper_shadow = high[index] - max(close[index], open_[index])
    lower_shadow = min(close[index], open_[index]) - low[index]

    if candle_range > 0.003 * close[index]:
        shadow_ratio = (upper_shadow + lower_shadow) / candle_range
        if shadow_ratio < 0.15:
            items["K线形态"] = 4
        elif shadow_ratio < 0.30:
            items["K线形态"] = 3
        elif shadow_ratio < 0.50:
            items["K线形态"] = 2
        else:
            items["K线形态"] = 0
    else:
        items["K线形态"] = 2

    total = sum(items.values())
    return total, items


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# MACD 辅助评分
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _detect_diff_dea_cross(diff: np.ndarray, dea: np.ndarray, index: int, lookback: int = 5) -> tuple[int, int]:
    """检测近期DIFF/DEA金叉和死叉。返回 (金叉距离, 死叉距离)，-1表示未找到。"""
    golden_dist = -1
    dead_dist = -1
    for d in range(1, lookback + 1):
        i = index - d
        if i < 1:
            break
        if golden_dist < 0 and diff[i] >= dea[i] and diff[i - 1] < dea[i - 1]:
            golden_dist = d
        if dead_dist < 0 and diff[i] < dea[i] and diff[i - 1] >= dea[i - 1]:
            dead_dist = d
    if diff[index] >= dea[index] and index >= 1 and diff[index - 1] < dea[index - 1]:
        golden_dist = 0
    if diff[index] < dea[index] and index >= 1 and diff[index - 1] >= dea[index - 1]:
        dead_dist = 0
    return golden_dist, dead_dist


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
        else:
            items["MACD柱状态"] = 0

        # 金叉确认 (0~7, 可扣分)
        if golden_dist > 0 and golden_dist <= 5:
            items["金叉确认"] = 7
        elif golden_dist == 0:
            items["金叉确认"] = 4
        elif diff[index] > dea[index]:
            items["金叉确认"] = 3
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


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 信号强度评分 (15分)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def compute_signal_strength_score(
    indicators: dict[str, np.ndarray],
    index: int,
) -> tuple[float, dict[str, float]]:
    """计算信号强度评分（0~15分）：T日涨幅(10) + 封板质量(5)。"""
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
        items["T日涨幅"] = 8
    elif 4 <= day_change < 6:
        items["T日涨幅"] = 6
    elif 2 <= day_change < 4:
        items["T日涨幅"] = 4
    elif 0 <= day_change < 2:
        items["T日涨幅"] = 3
    elif 6 <= day_change < 8:
        items["T日涨幅"] = 1
    else:
        items["T日涨幅"] = 0

    # ── 封板质量 (5分, 仅涨幅>=9.5%时) ──
    if day_change >= 9.5:
        candle_range = high[index] - low[index]
        amplitude_pct = candle_range / prev_close * 100 if prev_close > 0 else 999
        is_sealed = abs(high[index] - close[index]) < 0.01 * close[index]

        if is_sealed and amplitude_pct < 2:
            items["封板质量"] = 5
        elif is_sealed:
            items["封板质量"] = 3
        elif high[index] > close[index]:
            items["封板质量"] = 1
        else:
            items["封板质量"] = 2

    total = sum(items.values())
    return min(15.0, total), items


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 风险扣分系统
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def compute_risk_penalty(
    indicators: dict[str, np.ndarray],
    index: int,
    pattern_type: PatternType,
) -> tuple[float, dict[str, float], list[RiskFilterDetail]]:
    """计算风险扣分。返回 (总扣分, 扣分明细, 风险过滤详情列表)。"""
    risk_items: dict[str, float] = {}
    risk_details: list[RiskFilterDetail] = []
    close = indicators["close"]
    open_ = indicators["open"]
    high = indicators["high"]
    low = indicators["low"]
    volume = indicators["volume"]
    short_trend = indicators["short_trend"]
    long_short = indicators["long_short"]
    brick = indicators["brick"]

    # ── 通用风险1：一字板跌停 ──
    lookback = 10
    start = max(1, index - lookback)
    ld_triggered = False
    ld_penalty = 0.0
    ld_desc = ""

    for day in range(start, index):
        if day < 1:
            continue
        prev_c = close[day - 1]
        if prev_c <= 0:
            continue
        change_pct = (close[day] - prev_c) / prev_c
        is_limit_down = (
            abs(open_[day] - close[day]) < 0.01
            and abs(close[day] - low[day]) < 0.01
            and abs(open_[day] - high[day]) < 0.01
            and change_pct <= -0.09
        )
        if is_limit_down:
            dist = index - day
            if dist <= 3:
                ld_penalty = -50
            elif dist <= 7:
                ld_penalty = -30
            else:
                ld_penalty = -20
            ld_triggered = True
            ld_desc = f"前{dist}日一字板跌停(跌{change_pct:.1%})"
            break

    risk_details.append(RiskFilterDetail(
        filter_type=RiskFilterType.LIMIT_DOWN,
        triggered=ld_triggered,
        description=ld_desc,
        penalty=ld_penalty,
    ))
    if ld_triggered:
        risk_items["一字板跌停"] = ld_penalty

    # ── 通用风险2：高位放量大阴线 ──
    avg_start = max(0, index - 30)
    avg_volume = np.mean(volume[avg_start:index]) if index > avg_start else 0
    hv_triggered = False
    hv_penalty = 0.0
    hv_desc = ""

    if avg_volume > 0:
        volume_threshold = avg_volume * 1.5
        for day in range(start, index):
            if day < 1:
                continue
            prev_c = close[day - 1]
            if prev_c <= 0:
                continue
            is_green = close[day] < open_[day]
            change_pct = (close[day] - prev_c) / prev_c
            is_heavy = volume[day] >= volume_threshold
            is_big_drop = change_pct <= -0.03

            if is_green and is_heavy and is_big_drop:
                # 豁免：后续已回收高点
                big_drop_high = high[day]
                exempted = False
                for recovery_day in range(day + 1, index):
                    if close[recovery_day] > big_drop_high and volume[recovery_day] >= avg_volume:
                        exempted = True
                        break
                if exempted:
                    continue

                # 豁免：处于N型下跌回调中
                if _is_in_n_shape_decline(indicators, day):
                    continue

                # 豁免：N型起跳时大阴线在回调阶段（绿砖段）内
                # 但跌停级别(≥7%)的暴跌不豁免
                if pattern_type == PatternType.N_SHAPE_JUMP and change_pct > -0.07:
                    pb_s, _, _ = _find_pullback_phase(brick, index)
                    if pb_s <= day < index and _is_green_brick(brick, day):
                        continue

                # 豁免：信号日收盘价已恢复到大阴线前水平（市场已消化）
                pre_drop_close = close[day - 1] if day >= 1 else close[day]
                if pre_drop_close > 0 and close[index] >= pre_drop_close * 0.95:
                    continue

                scan_s = max(0, day - 15)
                peak_price = float(np.max(high[scan_s:day + 1]))
                dist_from_peak = (peak_price - close[day]) / peak_price if peak_price > 0 else 0
                if dist_from_peak < 0.05:
                    hv_penalty = -30
                else:
                    hv_penalty = -15

                hv_triggered = True
                hv_desc = f"前{index - day}日放量大阴线(跌{change_pct:.1%},量比{volume[day] / avg_volume:.1f}倍)"
                break

    risk_details.append(RiskFilterDetail(
        filter_type=RiskFilterType.HEAVY_VOLUME_DROP,
        triggered=hv_triggered,
        description=hv_desc,
        penalty=hv_penalty,
    ))
    if hv_triggered:
        risk_items["高位放量大阴线"] = hv_penalty

    # ── 通用风险3：短趋势跌破多空线 ──
    st_val = short_trend[index] if np.isfinite(short_trend[index]) else 0
    ls_val = long_short[index] if np.isfinite(long_short[index]) else 0
    tb_triggered = st_val > 0 and ls_val > 0 and st_val < ls_val
    tb_penalty = -15 if tb_triggered else 0.0

    risk_details.append(RiskFilterDetail(
        filter_type=RiskFilterType.TREND_BROKEN,
        triggered=tb_triggered,
        description="短趋势跌破多空线" if tb_triggered else "",
        penalty=tb_penalty,
    ))
    if tb_triggered:
        risk_items["短趋势跌破多空线"] = tb_penalty

    # ── 定式专属风险 ──

    # 追高离心（上升波段延续）— 仅>35%才扣分
    if pattern_type == PatternType.UPTREND_CONTINUE:
        st_v = short_trend[index] if np.isfinite(short_trend[index]) else close[index]
        vs_st = (close[index] - st_v) / st_v * 100 if st_v > 0 else 0
        ch_triggered = vs_st > 25
        if vs_st > 45:
            ch_penalty = -20.0
        elif vs_st > 35:
            ch_penalty = -15.0
        else:
            ch_penalty = 0.0

        risk_details.append(RiskFilterDetail(
            filter_type=RiskFilterType.CHASE_HIGH,
            triggered=ch_triggered,
            description=f"追高离心(vs短趋{vs_st:.1f}%)" if ch_triggered else "",
            penalty=ch_penalty,
        ))
        if ch_triggered and ch_penalty < 0:
            risk_items["追高离心"] = ch_penalty

    # 天量见顶（上升波段延续）
    if pattern_type == PatternType.UPTREND_CONTINUE:
        recent5 = volume[max(0, index - 4):index + 1]
        vol_ratio = np.max(recent5) / avg_volume if avg_volume > 0 and len(recent5) > 0 else 0
        recent20_max = np.max(volume[max(0, index - 19):index + 1]) if index >= 1 else 0
        is_peak = vol_ratio > 4 and np.max(recent5) >= recent20_max * 0.99
        if is_peak:
            pv_penalty = -20.0 if vol_ratio > 5 else -10.0
            risk_items["天量见顶"] = pv_penalty
            risk_details.append(RiskFilterDetail(
                filter_type=RiskFilterType.PEAK_VOLUME,
                triggered=True,
                description=f"天量见顶(量比{vol_ratio:.1f})",
                penalty=pv_penalty,
            ))
        else:
            risk_details.append(RiskFilterDetail(
                filter_type=RiskFilterType.PEAK_VOLUME, triggered=False))

    # 绿砖期放量（N型起跳）
    if pattern_type == PatternType.N_SHAPE_JUMP:
        pb_start, _, _ = _find_pullback_phase(brick, index)
        green_vol_sum = 0.0
        green_vol_count = 0
        for i in range(max(1, pb_start), index):
            if _is_green_brick(brick, i):
                green_vol_sum += volume[i]
                green_vol_count += 1

        red_vol_sum = 0.0
        red_vol_count = 0
        rp = pb_start - 1
        while rp >= 1 and _is_red_brick(brick, rp):
            red_vol_sum += volume[rp]
            red_vol_count += 1
            rp -= 1

        if red_vol_count > 0 and green_vol_count > 0:
            gv_ratio = (green_vol_sum / green_vol_count) / (red_vol_sum / red_vol_count)
        else:
            gv_ratio = 1.0

        gv_triggered = gv_ratio > 1.3

        risk_details.append(RiskFilterDetail(
            filter_type=RiskFilterType.GREEN_VOLUME_UP,
            triggered=gv_triggered,
            description=f"绿砖期放量(比值{gv_ratio:.2f})" if gv_triggered else "",
            penalty=0.0,
        ))

    # 趋势衰竭（横盘起跳）
    if pattern_type == PatternType.SIDEWAYS_JUMP:
        trend_window = 10
        t_start = max(0, index - trend_window + 1)
        t_slice = short_trend[t_start:index + 1]
        valid_mask = np.isfinite(t_slice)
        if np.sum(valid_mask) >= 3:
            valid_t = t_slice[valid_mask]
            x_v = np.arange(len(valid_t), dtype=float)
            slope = np.polyfit(x_v, valid_t, 1)[0]
            price_ref = close[index] if close[index] > 0 else 1
            slope_pct = slope / price_ref * 100
        else:
            slope_pct = 0.1

        te_triggered = slope_pct <= 0

        risk_details.append(RiskFilterDetail(
            filter_type=RiskFilterType.TREND_EXHAUST,
            triggered=te_triggered,
            description=f"趋势衰竭(斜率{slope_pct:.2f}%)" if te_triggered else "",
            penalty=0.0,
        ))

    # 假横盘（横盘起跳）— 仅标签
    if pattern_type == PatternType.SIDEWAYS_JUMP:
        switches = _count_brick_color_switches(brick, index - 1, window=10)
        fs_triggered = switches < 3

        risk_details.append(RiskFilterDetail(
            filter_type=RiskFilterType.FAKE_SIDEWAYS,
            triggered=fs_triggered,
            description=f"假横盘(切换{switches}次<3)" if fs_triggered else "",
            penalty=0.0,
        ))

    # ── 通用风险4：锤子线禁忌 ──
    body = abs(close[index] - open_[index])
    lower_shadow = min(close[index], open_[index]) - low[index]
    upper_shadow = high[index] - max(close[index], open_[index])
    candle_range = high[index] - low[index]
    body_ref = max(body, 0.003 * close[index])

    hm_triggered = lower_shadow >= 2 * body_ref and candle_range > 0.005 * close[index]
    if hm_triggered:
        hm_ratio = lower_shadow / body_ref
        if hm_ratio >= 3:
            hm_penalty = -25.0
        else:
            hm_penalty = -20.0
        hm_desc = f"锤子线(下影线{lower_shadow / body_ref:.1f}倍实体)"
    else:
        hm_penalty = 0.0
        hm_desc = ""

    risk_details.append(RiskFilterDetail(
        filter_type=RiskFilterType.HAMMER,
        triggered=hm_triggered,
        description=hm_desc,
        penalty=hm_penalty,
    ))
    if hm_triggered:
        risk_items["锤子线"] = hm_penalty

    # ── 通用风险5：大上影线禁忌 ──
    lus_triggered = (
        candle_range > 0.005 * close[index]
        and upper_shadow >= 2 * body_ref
        and upper_shadow >= 0.6 * candle_range
    )
    if lus_triggered:
        us_ratio = upper_shadow / body_ref
        if us_ratio >= 3:
            lus_penalty = -20.0
        else:
            lus_penalty = -15.0
        lus_desc = f"大上影线(上影{upper_shadow / body_ref:.1f}倍实体,占振幅{upper_shadow / candle_range:.0%})"
    else:
        lus_penalty = 0.0
        lus_desc = ""

    risk_details.append(RiskFilterDetail(
        filter_type=RiskFilterType.LARGE_UPPER_SHADOW,
        triggered=lus_triggered,
        description=lus_desc,
        penalty=lus_penalty,
    ))
    if lus_triggered:
        risk_items["大上影线"] = lus_penalty

    # ── 通用风险6：三波不做 ──
    tw_triggered, tw_penalty, tw_desc = _detect_third_wave(brick, close, high, index)
    risk_details.append(RiskFilterDetail(
        filter_type=RiskFilterType.THIRD_WAVE,
        triggered=tw_triggered,
        description=tw_desc,
        penalty=tw_penalty,
    ))
    if tw_triggered:
        risk_items["三波追高"] = tw_penalty

    # ── 新增风险7：冲高回落 ──
    prev_close_val = close[index - 1] if index >= 1 else close[index]
    if prev_close_val > 0:
        t_day_change = (close[index] - prev_close_val) / prev_close_val * 100
        cr_triggered = 6 <= t_day_change < 8
        cr_penalty = -10.0 if cr_triggered else 0.0
        cr_desc = f"冲高回落(涨幅{t_day_change:.1f}%在6-8%区间)" if cr_triggered else ""
    else:
        cr_triggered = False
        cr_penalty = 0.0
        cr_desc = ""

    risk_details.append(RiskFilterDetail(
        filter_type=RiskFilterType.CHASE_HIGH,
        triggered=cr_triggered,
        description=cr_desc,
        penalty=cr_penalty,
    ))
    if cr_triggered:
        risk_items["冲高回落"] = cr_penalty

    # ── 新增风险8：横盘MACD死叉 ──
    if pattern_type == PatternType.SIDEWAYS_JUMP:
        diff = indicators["macd_diff"]
        dea = indicators["macd_dea"]
        if np.isfinite(diff[index]) and np.isfinite(dea[index]):
            _, dead_dist = _detect_diff_dea_cross(diff, dea, index, lookback=3)
            md_triggered = dead_dist >= 0
            md_penalty = -15.0 if md_triggered else 0.0
            md_desc = f"横盘MACD死叉(前{dead_dist}日)" if md_triggered else ""
        else:
            md_triggered = False
            md_penalty = 0.0
            md_desc = ""

        risk_details.append(RiskFilterDetail(
            filter_type=RiskFilterType.TREND_EXHAUST,
            triggered=md_triggered,
            description=md_desc,
            penalty=md_penalty,
        ))
        if md_triggered:
            risk_items["横盘MACD死叉"] = md_penalty

    total_penalty = sum(risk_items.values())
    return total_penalty, risk_items, risk_details


def _is_in_n_shape_decline(indicators: dict[str, np.ndarray], day: int) -> bool:
    """判断某天是否处于N型结构的下跌回调阶段。"""
    close = indicators["close"]
    high = indicators["high"]

    scan_start = max(0, day - 15)
    window_high = high[scan_start:day + 1]

    if len(window_high) < 3:
        return False

    peak_offset = int(np.argmax(window_high))
    peak_abs_index = scan_start + peak_offset
    peak_price = float(high[peak_abs_index])

    distance_from_peak = day - peak_abs_index
    if distance_from_peak <= 1:
        return False

    drop_from_peak = (peak_price - close[day]) / peak_price if peak_price > 0 else 0
    if drop_from_peak < 0.02:
        return False

    pre_peak_start = max(0, peak_abs_index - 10)
    pre_peak_low = float(np.min(close[pre_peak_start:peak_abs_index + 1]))
    rise_ratio = (peak_price - pre_peak_low) / pre_peak_low if pre_peak_low > 0 else 0

    return rise_ratio >= 0.03


def _detect_third_wave(
    brick: np.ndarray,
    close: np.ndarray,
    high: np.ndarray,
    index: int,
) -> tuple[bool, float, str]:
    """检测三波追高：如果信号日之前已完成两波上涨-回调，当前是第三波尝试则扣分。

    向前回溯砖形图颜色段：
    当前信号 = 绿转红（第三波起点），往前找：
    回调2(绿段) → 上涨2(红段) → 回调1(绿段) → 上涨1(红段)
    如果四段都存在且实质性（红段>=2砖，绿段>=1砖），判定为三波。
    """
    if index < 15:
        return False, 0.0, ""

    pos = index - 1
    # 回调2：信号日前的绿砖段
    green2 = 0
    while pos >= 1 and _is_green_brick(brick, pos):
        green2 += 1
        pos -= 1
    # 允许横盘起跳型只有1天绿砖
    if green2 < 1:
        return False, 0.0, ""

    # 上涨2：绿砖段前的红砖段
    red2 = 0
    while pos >= 1 and _is_red_brick(brick, pos):
        red2 += 1
        pos -= 1
    if red2 < 2:
        return False, 0.0, ""

    # 回调1：第二段红砖前的绿砖段
    green1 = 0
    while pos >= 1 and _is_green_brick(brick, pos):
        green1 += 1
        pos -= 1
    if green1 < 1:
        return False, 0.0, ""

    # 上涨1：第一段绿砖前的红砖段
    red1 = 0
    while pos >= 1 and _is_red_brick(brick, pos):
        red1 += 1
        pos -= 1
    if red1 < 2:
        return False, 0.0, ""

    # 确认处于高位：信号日收盘价接近区间最高价
    scan_start = max(0, pos)
    period_high = float(np.max(high[scan_start:index + 1]))
    if period_high <= 0:
        return False, 0.0, ""
    dist_from_high = (period_high - close[index]) / period_high
    if dist_from_high > 0.10:
        return False, 0.0, ""

    total_waves_len = red1 + green1 + red2 + green2
    if total_waves_len > 50:
        return False, 0.0, ""

    if red1 >= 3 and red2 >= 3:
        penalty = -25.0
    else:
        penalty = -15.0

    desc = f"三波追高({red1}红{green1}绿{red2}红{green2}绿,距高点{dist_from_high:.1%})"
    return True, penalty, desc


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 单只股票完整检测流程
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def screen_single_stock(
    df: pd.DataFrame,
    index: int,
    symbol: str,
    name: str,
    target_date: str,
    actual_date: str,
    enabled_patterns: tuple[PatternType, ...],
    price_limit: float = 0.0,
) -> BrickPatternMatch:
    """对单只股票执行完整的砖形图定式选股流程。"""
    if len(df) < 10:
        return BrickPatternMatch(
            symbol=symbol, name=name, target_date=target_date,
            actual_date=actual_date, error="数据不足(少于10条)",
        )

    indicators = _calc_indicators(df)
    return screen_with_indicators(
        indicators=indicators,
        index=index,
        symbol=symbol,
        name=name,
        target_date=target_date,
        actual_date=actual_date,
        enabled_patterns=enabled_patterns,
        price_limit=price_limit,
    )

def screen_with_indicators(
    indicators: dict[str, np.ndarray],
    index: int,
    symbol: str,
    name: str,
    target_date: str,
    actual_date: str,
    enabled_patterns: tuple[PatternType, ...],
    price_limit: float = 0.0,
) -> BrickPatternMatch:
    """基于已经预计算好的指标执行单日定式检测（V3评分）。"""
    close_arr = indicators["close"]
    if index < 0 or index >= len(close_arr) or len(close_arr) < 10:
        return BrickPatternMatch(
            symbol=symbol, name=name, target_date=target_date,
            actual_date=actual_date, error="数据不足(少于10条)",
        )

    close_val = float(close_arr[index])
    if price_limit > 0 and close_val > price_limit:
        return BrickPatternMatch(
            symbol=symbol, name=name, target_date=target_date,
            actual_date=actual_date,
            prerequisite_detail=f"股价{close_val:.2f}超过限制{price_limit:.0f}",
        )

    # ── 步骤1：必备前提检测 ──
    prereq_passed, prereq_detail = check_prerequisites(indicators, index)
    if not prereq_passed:
        return BrickPatternMatch(
            symbol=symbol, name=name, target_date=target_date,
            actual_date=actual_date,
            prerequisite_passed=False,
            prerequisite_detail=prereq_detail,
        )

    # ── 步骤2：三种定式检测 ──
    pattern_detectors = {
        PatternType.N_SHAPE_JUMP: detect_n_shape_jump,
        PatternType.SIDEWAYS_JUMP: detect_sideways_jump,
        PatternType.UPTREND_CONTINUE: detect_uptrend_continue,
    }

    pattern_results = []
    for pt in enabled_patterns:
        detector = pattern_detectors.get(pt)
        if detector is None:
            continue
        result = detector(indicators, index)
        pattern_results.append(result)

    matched_results = [r for r in pattern_results if r.matched]

    if not matched_results:
        return BrickPatternMatch(
            symbol=symbol, name=name, target_date=target_date,
            actual_date=actual_date,
            prerequisite_passed=True,
            prerequisite_detail="前提通过",
            pattern_matches=tuple(pattern_results),
        )

    # ── 步骤3-5：对每个匹配的定式计算完整分数，取最高 ──
    best_match_result = None
    best_breakdown = None
    best_final = -1.0

    signal_score, signal_items = compute_signal_strength_score(indicators, index)

    for match_r in matched_results:
        specific_score = match_r.score
        specific_items = match_r.extra.get("specific_items", {})

        common_score, common_items = compute_common_quality_score(
            indicators, index, match_r.pattern_type,
        )

        macd_score, macd_items = compute_macd_auxiliary_score(
            indicators, index, match_r.pattern_type,
        )

        risk_penalty, risk_items, risk_details_list = compute_risk_penalty(
            indicators, index, match_r.pattern_type,
        )

        breakdown = ScoreBreakdown(
            specific_score=specific_score,
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

        if breakdown.final_score > best_final:
            best_final = breakdown.final_score
            best_breakdown = breakdown
            best_match_result = match_r
            best_risk_details = risk_details_list

    triggered_risks = [r for r in best_risk_details if r.triggered]
    risk_reason = "; ".join(r.description for r in triggered_risks) if triggered_risks else ""

    return BrickPatternMatch(
        symbol=symbol, name=name, target_date=target_date,
        actual_date=actual_date,
        prerequisite_passed=True,
        prerequisite_detail="前提通过",
        pattern_matches=tuple(pattern_results),
        risk_filters=tuple(best_risk_details),
        final_matched=True,
        matched_pattern=best_match_result.pattern_type.value,
        risk_rejected=False,
        risk_reason=risk_reason,
        final_score=best_breakdown.final_score,
        grade=best_breakdown.grade,
        score_breakdown=best_breakdown,
    )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 并行工作函数（进程池）
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _worker_screen_stock(args: tuple) -> dict:
    """进程池工作函数：处理单只股票的砖形图定式选股。"""
    (root_str, symbol, stock_name, target_date, enabled_pattern_values, price_limit) = args

    try:
        repository = StockRepository(Path(root_str))
        df = repository.get_daily_frame(symbol)
        time_result = locate_time_index(df, target_date)

        if not time_result.matched or time_result.index is None:
            return {
                "symbol": symbol,
                "name": stock_name,
                "target_date": time_result.requested_date,
                "actual_date": time_result.actual_date or "",
                "error": f"日期未匹配: {time_result.reason}",
            }

        enabled_patterns = tuple(PatternType(v) for v in enabled_pattern_values)

        match = screen_single_stock(
            df=df,
            index=time_result.index,
            symbol=symbol,
            name=stock_name,
            target_date=time_result.requested_date,
            actual_date=time_result.actual_date or "",
            enabled_patterns=enabled_patterns,
            price_limit=price_limit,
        )

        result = {
            "symbol": match.symbol,
            "name": match.name,
            "target_date": match.target_date,
            "actual_date": match.actual_date,
            "prerequisite_passed": match.prerequisite_passed,
            "prerequisite_detail": match.prerequisite_detail,
            "final_matched": match.final_matched,
            "matched_pattern": match.matched_pattern,
            "risk_rejected": match.risk_rejected,
            "risk_reason": match.risk_reason,
            "error": match.error,
            "summary": match.format_summary(),
            "final_score": match.final_score,
            "grade": match.grade,
        }

        if match.score_breakdown is not None:
            result["score_breakdown"] = match.score_breakdown.to_dict()

        return result
    except Exception as exc:
        return {
            "symbol": symbol,
            "name": stock_name,
            "target_date": target_date,
            "actual_date": "",
            "error": str(exc),
        }

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 引擎主类
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@dataclass
class BrickPatternEngine:
    """砖形图交易定式选股引擎。"""

    repository: StockRepository
    stock_pool_manager: StockPoolManager
    progress_interval: int = DEFAULT_PROGRESS_INTERVAL
    max_workers: int = DEFAULT_MAX_WORKERS

    @classmethod
    def from_root(cls, root: Path) -> BrickPatternEngine:
        repository = StockRepository(root)
        stock_pool_manager = StockPoolManager(repository)
        return cls(repository=repository, stock_pool_manager=stock_pool_manager)

    def run(
        self,
        request: BrickPatternRequest,
        progress_callback: Callable[[dict], None] | None = None,
        cancelled_fn: Callable[[], bool] | None = None,
    ) -> BrickPatternResult:
        """执行砖形图定式选股。"""
        pool = (
            self.stock_pool_manager.get_pool_by_symbols(request.symbols, request.stock_pool_name)
            if request.symbols
            else self.stock_pool_manager.get_default_pool(request.stock_pool_name)
        )

        stock_map = {stock.symbol: stock for stock in pool.stocks}
        total = len(pool.symbols)
        interval = max(1, self.progress_interval)

        if progress_callback is not None and total > 0:
            progress_callback({
                "current": 0,
                "total": total,
                "symbol": "",
                "matched": 0,
                "errors": 0,
            })

        enabled_pattern_values = tuple(p.value for p in request.enabled_patterns)
        root_str = str(self.repository.root)

        task_args = [
            (
                root_str,
                symbol,
                stock_map.get(symbol).name if stock_map.get(symbol) else "",
                request.target_date,
                enabled_pattern_values,
                request.price_limit,
            )
            for symbol in pool.symbols
        ]

        all_matches: list[BrickPatternMatch] = []
        matched_count = 0
        risk_filtered_count = 0
        error_count = 0
        completed = 0

        with ProcessPoolExecutor(max_workers=self.max_workers) as executor:
            futures = {executor.submit(_worker_screen_stock, args): args[1] for args in task_args}

            for future in as_completed(futures):
                completed += 1
                result = future.result()

                if result.get("error"):
                    error_count += 1
                    all_matches.append(BrickPatternMatch(
                        symbol=result["symbol"],
                        name=result.get("name", ""),
                        target_date=result.get("target_date", ""),
                        actual_date=result.get("actual_date", ""),
                        error=result["error"],
                    ))
                else:
                    bd = None
                    if "score_breakdown" in result and result["score_breakdown"]:
                        bd = ScoreBreakdown.from_dict(result["score_breakdown"])

                    match = BrickPatternMatch(
                        symbol=result["symbol"],
                        name=result.get("name", ""),
                        target_date=result.get("target_date", ""),
                        actual_date=result.get("actual_date", ""),
                        prerequisite_passed=result.get("prerequisite_passed", False),
                        prerequisite_detail=result.get("prerequisite_detail", ""),
                        final_matched=result.get("final_matched", False),
                        matched_pattern=result.get("matched_pattern", ""),
                        risk_rejected=result.get("risk_rejected", False),
                        risk_reason=result.get("risk_reason", ""),
                        final_score=result.get("final_score", 0.0),
                        grade=result.get("grade", ""),
                        score_breakdown=bd,
                    )
                    all_matches.append(match)

                    if match.final_matched:
                        matched_count += 1
                    if match.risk_rejected:
                        risk_filtered_count += 1

                if progress_callback is not None and (completed % interval == 0 or completed == total):
                    progress_callback({
                        "current": completed,
                        "total": total,
                        "symbol": result["symbol"],
                        "matched": matched_count,
                        "errors": error_count,
                    })

                if cancelled_fn is not None and cancelled_fn():
                    for pending_future in futures:
                        pending_future.cancel()
                    break

        return BrickPatternResult(
            request=request,
            matches=tuple(all_matches),
            total=total,
            matched_count=matched_count,
            risk_filtered_count=risk_filtered_count,
            error_count=error_count,
        )
