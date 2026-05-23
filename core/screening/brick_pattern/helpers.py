"""砖形图定式：底层指标计算与形态辅助函数。

包括：
- _calc_indicators 一次性预计算所有指标序列
- _is_red_brick / _is_green_brick / _count_brick_color_switches 砖块辅助
- check_prerequisites 必备前提检测
- _find_pullback_phase / _find_prior_uptrend N型形态阶段辅助
- _detect_diff_dea_cross / _is_in_n_shape_decline / _detect_third_wave 风险检测辅助
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from core.indicators.algorithms import (
    compute_brick_indicator,
    compute_kdj_indicator,
    compute_macd_indicator,
    compute_zx_long_short,
    compute_zx_short_trend,
    moving_average,
)

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
# 砖块判定辅助
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
# N型形态阶段辅助
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


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 风险检测辅助：DIFF/DEA 死叉、N 型下行、三波追高
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
    如果四段都存在且回调具有实质性，判定为三波。

    豁免条件（不算三波）：
    - 任一回调段时间≥20个交易日（约1个月）：长时间调整已形成新周期
    - 任一回调段跌幅≥15%：深度回调已充分消化前期涨幅
    - 两个回调段都极短（≤2天）且跌幅极小（<5%）：属于强势趋势中的
      正常喘息，本质是上升波段延续，不构成独立的三波结构
    """
    if index < 15:
        return False, 0.0, ""

    pos = index - 1
    # 回调2：信号日前的绿砖段
    green2_start = pos
    green2 = 0
    while pos >= 1 and _is_green_brick(brick, pos):
        green2 += 1
        pos -= 1
    if green2 < 1:
        return False, 0.0, ""
    green2_end = pos + 1

    # 上涨2：绿砖段前的红砖段
    red2 = 0
    while pos >= 1 and _is_red_brick(brick, pos):
        red2 += 1
        pos -= 1
    if red2 < 2:
        return False, 0.0, ""

    # 回调1：第二段红砖前的绿砖段
    green1_start = pos
    green1 = 0
    while pos >= 1 and _is_green_brick(brick, pos):
        green1 += 1
        pos -= 1
    if green1 < 1:
        return False, 0.0, ""
    green1_end = pos + 1

    # 上涨1：第一段绿砖前的红砖段
    red1 = 0
    while pos >= 1 and _is_red_brick(brick, pos):
        red1 += 1
        pos -= 1
    if red1 < 2:
        return False, 0.0, ""

    # ── 计算每个回调段的跌幅 ──
    pullback_drops: list[float] = []
    for g_start, g_end in [(green2_start, green2_end), (green1_start, green1_end)]:
        pb_high = float(np.max(high[g_end:g_start + 1]))
        pb_low = float(np.min(close[g_end:g_start + 1]))
        drop = (pb_high - pb_low) / pb_high if pb_high > 0 else 0.0
        pullback_drops.append(drop)

    # ── 豁免1：任一回调段时间过长或跌幅过深，视为新周期 ──
    for g_len, drop in [(green2, pullback_drops[0]), (green1, pullback_drops[1])]:
        if g_len >= 20:
            return False, 0.0, ""
        if drop >= 0.15:
            return False, 0.0, ""

    # ── 豁免2：两个回调段都极短且极浅，属于强势趋势正常喘息 ──
    # 回调≤2天且跌幅<5%的不算实质性回调，本质是上升波段延续
    shallow_pullback_max_days = 2
    shallow_pullback_max_drop = 0.05
    both_shallow = (
        green1 <= shallow_pullback_max_days
        and green2 <= shallow_pullback_max_days
        and pullback_drops[0] < shallow_pullback_max_drop
        and pullback_drops[1] < shallow_pullback_max_drop
    )
    if both_shallow:
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
