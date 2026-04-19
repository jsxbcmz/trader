"""砖形图选股评分系统回测脚本

基于 111/砖形图选股评分系统改造方案.md 中的评分体系，
遍历 stock_daily_data 中所有股票，在 2024-01-01 ~ 2026-03-31 范围内
找出所有符合基础信号的交易日，进行模式识别 → 模式评分 → 风险过滤，
并记录信号日后 5 个交易日的涨幅，最终输出 CSV。
"""

from __future__ import annotations

import sys
import os
import warnings
from pathlib import Path
from dataclasses import dataclass, field
from enum import Enum

import numpy as np
import pandas as pd

# 将项目根目录加入 sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.chart_indicators import (
    compute_brick_indicator,
    compute_zx_short_trend,
    compute_zx_long_short,
    ema,
    moving_average,
    rolling_max,
    rolling_min,
    tdx_sma,
)

warnings.filterwarnings("ignore")

# ============================================================
# 常量
# ============================================================
DATA_DIR = PROJECT_ROOT / "stock_daily_data"
STOCKLIST_CSV = PROJECT_ROOT / "stocklist.csv"
OUTPUT_CSV = Path(__file__).resolve().parent / "brick_scoring_backtest_result.csv"

DATE_START = "2024-01-01"
DATE_END = "2026-03-31"

FUTURE_DAYS = 5  # 信号日后观察的交易日数


# ============================================================
# 枚举 & 数据模型
# ============================================================
class PatternType(Enum):
    N_SHAPE = "N型起跳"
    SIDEWAYS_BREAKOUT = "横盘起跳"
    UPTREND_CONTINUE = "上升波段延续"


@dataclass
class ScoreDetail:
    dimension: str
    score: float
    max_score: float


@dataclass
class SignalResult:
    symbol: str
    name: str
    signal_date: str
    pattern: str
    pattern_score: float
    score_details: list[ScoreDetail]
    risk_deduction: float
    risk_reasons: list[str]
    final_score: float
    grade: str
    vetoed: bool
    veto_reason: str
    # 信号日后 N 天涨幅
    future_returns: dict[str, float]


# ============================================================
# 指标计算
# ============================================================
def compute_kdj(high: np.ndarray, low: np.ndarray, close: np.ndarray):
    """计算 KDJ 指标，返回 K, D, J 数组"""
    hhv9 = rolling_max(high, 9)
    llv9 = rolling_min(low, 9)
    span = hhv9 - llv9
    safe_span = np.where(np.abs(span) < 1e-12, np.nan, span)
    rsv = (close - llv9) / safe_span * 100.0
    k = tdx_sma(rsv, 3, 1)
    d = tdx_sma(k, 3, 1)
    j = 3.0 * k - 2.0 * d
    return k, d, j


def compute_min_j(j: np.ndarray, k: np.ndarray, d: np.ndarray):
    """计算 MIN_J 指标（多周期 J 值拐点均值）"""
    length = len(j)
    # J 拐点条件：J < REF(J,1) AND J < REFX(J,1) AND J < 55 AND J < D AND J < K AND K < D
    # REFX(J,1) 是未来引用，即 J[i+1]
    j_turning = np.zeros(length, dtype=bool)
    for i in range(1, length - 1):
        if (np.isfinite(j[i]) and np.isfinite(j[i - 1]) and np.isfinite(j[i + 1])
                and np.isfinite(k[i]) and np.isfinite(d[i])):
            if (j[i] < j[i - 1] and j[i] < j[i + 1] and j[i] < 55
                    and j[i] < d[i] and j[i] < k[i] and k[i] < d[i]):
                j_turning[i] = True

    short_period, mid_period, long_period = 28, 57, 114

    def period_min_j(period: int, offset: float = 0.0) -> np.ndarray:
        j_sum = pd.Series(np.where(j_turning, j, 0.0)).rolling(window=period, min_periods=1).sum().to_numpy()
        cnt = pd.Series(j_turning.astype(float)).rolling(window=period, min_periods=1).sum().to_numpy()
        safe_cnt = np.where(cnt > 0, cnt, 1.0)
        return j_sum / safe_cnt + offset

    min_j_short = period_min_j(short_period)
    min_j_mid = period_min_j(mid_period)
    min_j_long = period_min_j(long_period, offset=10.0)
    min_j = (min_j_short + min_j_mid + min_j_long) / 3.0
    return min_j, j_turning


def compute_linear_slope(close: np.ndarray, window: int = 20) -> np.ndarray:
    """计算收盘价的滚动线性回归斜率（归一化为百分比/天）"""
    length = len(close)
    slope = np.full(length, np.nan)
    for i in range(window - 1, length):
        segment = close[i - window + 1: i + 1]
        if np.any(np.isnan(segment)):
            continue
        x = np.arange(window, dtype=float)
        mean_x = x.mean()
        mean_y = segment.mean()
        if mean_y == 0:
            continue
        ss_xx = np.sum((x - mean_x) ** 2)
        ss_xy = np.sum((x - mean_x) * (segment - mean_y))
        raw_slope = ss_xy / ss_xx if ss_xx > 0 else 0
        slope[i] = raw_slope / mean_y * 100.0  # 百分比/天
    return slope


# ============================================================
# 第 0 层：基础信号检测
# ============================================================
def check_base_signal(idx: int, brick: np.ndarray, short_trend: np.ndarray,
                      long_short: np.ndarray) -> bool:
    """检查第 0 层基础信号条件"""
    if idx < 2:
        return False

    # 条件1：绿转红 — 前一天砖形图下降，今天上升
    prev_rising = brick[idx - 1] > brick[idx - 2] if idx >= 2 else False
    prev_falling = not prev_rising  # 简化：前一天不是上升就是下降
    curr_rising = brick[idx] > brick[idx - 1]

    # 更精确：AA = REF(砖型图,1) < 砖型图，即当天砖型图 > 前一天
    # CC = REF(AA,1)=0 AND AA=1，即前一天不是上升，今天是上升
    aa_today = brick[idx] > brick[idx - 1]
    aa_yesterday = brick[idx - 1] > brick[idx - 2]
    green_to_red = (not aa_yesterday) and aa_today

    if not green_to_red:
        return False

    # 条件2：砖形图差值 > 0 且力度达标
    brick_change_today = brick[idx] - brick[idx - 1]
    if brick_change_today <= 0:
        return False

    brick_change_yesterday = abs(brick[idx - 1] - brick[idx - 2])
    if brick_change_yesterday > 1e-12:
        force_ratio = abs(brick_change_today) / brick_change_yesterday
        if force_ratio < 0.5:
            return False

    # 条件3：短趋线 > 多空线
    if not (np.isfinite(short_trend[idx]) and np.isfinite(long_short[idx])):
        return False
    if short_trend[idx] <= long_short[idx]:
        return False

    return True


# ============================================================
# 第 1 层：模式识别
# ============================================================
def detect_n_shape(idx: int, brick: np.ndarray, close: np.ndarray,
                   low: np.ndarray) -> bool:
    """检测 N 型起跳模式"""
    lookback = 30
    start = max(0, idx - lookback)

    # 识别红砖和绿砖序列
    # 红砖：brick[i] > brick[i-1]，绿砖：brick[i] <= brick[i-1]
    is_red = np.zeros(idx + 1, dtype=bool)
    for i in range(1, idx + 1):
        is_red[i] = brick[i] > brick[i - 1]

    # 在 [start, idx-1] 范围内找连续红砖 >= 3 天的上涨波段
    up_wave_end = -1
    up_wave_start = -1
    for i in range(idx - 1, start, -1):
        # 从信号日往前找，找到一段连续红砖
        if is_red[i]:
            wave_end = i
            wave_start = i
            while wave_start > start and is_red[wave_start - 1]:
                wave_start -= 1
            if wave_end - wave_start + 1 >= 3:
                up_wave_start = wave_start
                up_wave_end = wave_end
                break

    if up_wave_start < 0:
        return False

    # 上涨波段结束后，到信号日前一天之间应该有连续绿砖 >= 2 天的回调
    green_count = 0
    callback_start = up_wave_end + 1
    has_callback = False
    for i in range(callback_start, idx):
        if not is_red[i]:
            green_count += 1
        else:
            if green_count >= 2:
                has_callback = True
                break
            green_count = 0
    if green_count >= 2:
        has_callback = True

    if not has_callback:
        return False

    # 回调未破前低：回调阶段最低价 > 上涨起点最低价
    callback_low = np.min(low[callback_start:idx])
    up_start_low = low[up_wave_start]
    if callback_low <= up_start_low:
        return False

    return True


def detect_sideways_breakout(idx: int, close: np.ndarray) -> tuple[bool, int]:
    """检测横盘起跳模式，返回 (是否命中, 横盘天数)"""
    if idx < 5:
        return False, 0

    # 尝试不同的横盘窗口 5~12
    best_window = 0
    for window in range(5, 13):
        if idx - window < 0:
            continue
        segment = close[idx - window: idx]  # 信号日前 N 日
        if len(segment) < window or np.any(np.isnan(segment)):
            continue

        mean_price = np.mean(segment)
        if mean_price == 0:
            continue

        # 收盘价振幅 < 5%
        price_range = (np.max(segment) - np.min(segment)) / mean_price
        if price_range >= 0.05:
            continue

        # 信号日涨幅 > 近 N 日平均涨跌幅的 2 倍
        daily_changes = np.abs(np.diff(segment) / segment[:-1])
        avg_change = np.mean(daily_changes) if len(daily_changes) > 0 else 0

        signal_change = (close[idx] - close[idx - 1]) / close[idx - 1] if close[idx - 1] > 0 else 0
        if avg_change > 0 and signal_change > avg_change * 2:
            if window > best_window:
                best_window = window

    if best_window > 0:
        return True, best_window
    return False, 0


def detect_uptrend_continue(idx: int, brick: np.ndarray, close: np.ndarray,
                            short_trend: np.ndarray, long_short: np.ndarray,
                            slope: np.ndarray) -> bool:
    """检测上升波段延续模式"""
    if idx < 5:
        return False

    # 条件1：上涨趋势 — 20日斜率 > 0 且短趋线 > 多空线
    if not (np.isfinite(slope[idx]) and slope[idx] > 0):
        return False
    if not (short_trend[idx] > long_short[idx]):
        return False

    # 识别红绿砖
    is_red = np.zeros(idx + 1, dtype=bool)
    for i in range(1, idx + 1):
        is_red[i] = brick[i] > brick[i - 1]

    # 信号日前一天应该是绿砖（因为是绿转红）
    # 从信号日往前找：先找连续绿砖（1~2天），再往前找连续红砖 >= 3 天
    green_end = idx - 1  # 信号日前一天
    green_start = green_end
    while green_start > 0 and not is_red[green_start]:
        green_start -= 1
    green_start += 1  # green_start 是第一个绿砖

    green_days = green_end - green_start + 1
    if green_days < 1 or green_days > 2:
        return False

    # 绿砖之前应该是连续红砖 >= 3 天
    red_end = green_start - 1
    if red_end < 1:
        return False
    red_start = red_end
    while red_start > 0 and is_red[red_start]:
        red_start -= 1
    red_start += 1  # 如果 red_start 处不是红砖则 +1

    red_days = red_end - red_start + 1
    if red_days < 3:
        return False

    return True


# ============================================================
# 第 2 层：模式评分
# ============================================================
def score_n_shape(idx: int, brick: np.ndarray, close: np.ndarray,
                  low: np.ndarray, high: np.ndarray, volume: np.ndarray,
                  j: np.ndarray, min_j: np.ndarray,
                  short_trend: np.ndarray, long_short: np.ndarray) -> list[ScoreDetail]:
    """N 型起跳评分"""
    details = []
    lookback = 30
    start = max(0, idx - lookback)

    is_red = np.zeros(idx + 1, dtype=bool)
    for i in range(1, idx + 1):
        is_red[i] = brick[i] > brick[i - 1]

    # 找上涨波段和回调
    up_wave_start, up_wave_end = -1, -1
    for i in range(idx - 1, start, -1):
        if is_red[i]:
            wave_end = i
            wave_start = i
            while wave_start > start and is_red[wave_start - 1]:
                wave_start -= 1
            if wave_end - wave_start + 1 >= 3:
                up_wave_start = wave_start
                up_wave_end = wave_end
                break

    # 1. N型结构完整度 (30分) — 回调幅度占前一波涨幅的比例
    score_structure = 10.0
    if up_wave_start >= 0:
        up_high = np.max(high[up_wave_start:up_wave_end + 1])
        up_low = low[up_wave_start]
        up_range = up_high - up_low
        callback_start_idx = up_wave_end + 1
        if callback_start_idx < idx and up_range > 0:
            callback_low = np.min(low[callback_start_idx:idx])
            retrace_ratio = (up_high - callback_low) / up_range
            if 0.38 <= retrace_ratio <= 0.62:
                score_structure = 30.0
            elif 0.25 <= retrace_ratio <= 0.75:
                score_structure = 20.0
    details.append(ScoreDetail("N型结构完整度", score_structure, 30.0))

    # 2. KDJ 配合 (25分) — J 值位置
    score_kdj = 0.0
    if np.isfinite(j[idx]):
        j_val = j[idx]
        if j_val < 20:
            score_kdj = 25.0
        elif j_val < 40:
            score_kdj = 18.0
        elif j_val < 55:
            score_kdj = 10.0
    details.append(ScoreDetail("KDJ配合", score_kdj, 25.0))

    # 3. B1买点配合 (20分) — 信号日前一天是否处于 B1 买点附近
    score_b1 = 0.0
    if idx >= 2 and np.isfinite(j[idx - 1]) and np.isfinite(min_j[idx - 1]):
        j_prev = j[idx - 1]
        min_j_val = min_j[idx - 1]
        # 前一天 J 值为近期拐点且 < MIN_J
        is_turning = (idx >= 3 and np.isfinite(j[idx - 2]) and np.isfinite(j[idx])
                      and j[idx - 1] < j[idx - 2] and j[idx - 1] < j[idx])
        if is_turning and j_prev < min_j_val:
            score_b1 = 20.0
        elif abs(j_prev - min_j_val) < 10:  # 接近 MIN_J
            score_b1 = 12.0
    details.append(ScoreDetail("B1买点配合", score_b1, 20.0))

    # 4. 翻红力度 (15分)
    score_force = 0.0
    brick_increase = brick[idx] - brick[idx - 1]
    brick_decrease = abs(brick[idx - 1] - brick[idx - 2]) if idx >= 2 else 0
    if brick_decrease > 1e-12:
        ratio = brick_increase / brick_decrease
        if ratio >= 1.0:
            score_force = 15.0
        elif ratio >= 0.7:
            score_force = 10.0
        elif ratio >= 0.5:
            score_force = 5.0
    else:
        score_force = 10.0  # 前一天变化极小，给中等分
    details.append(ScoreDetail("翻红力度", score_force, 15.0))

    # 5. 趋势支撑 (10分)
    score_trend = 0.0
    if np.isfinite(short_trend[idx]) and np.isfinite(long_short[idx]):
        if short_trend[idx] > long_short[idx]:
            near_trend = abs(close[idx] - short_trend[idx]) / short_trend[idx]
            if near_trend <= 0.03:
                score_trend = 10.0
            else:
                score_trend = 5.0
    details.append(ScoreDetail("趋势支撑", score_trend, 10.0))

    return details


def score_sideways_breakout(idx: int, brick: np.ndarray, close: np.ndarray,
                            volume: np.ndarray, short_trend: np.ndarray,
                            long_short: np.ndarray,
                            sideways_window: int) -> list[ScoreDetail]:
    """横盘起跳评分"""
    details = []
    segment_start = idx - sideways_window
    segment = close[segment_start:idx]
    vol_segment = volume[segment_start:idx]

    # 1. 横盘质量 (30分) — 收盘价标准差/均价
    score_quality = 5.0
    mean_price = np.mean(segment)
    if mean_price > 0:
        cv = np.std(segment) / mean_price
        if cv < 0.015:
            score_quality = 30.0
        elif cv < 0.025:
            score_quality = 22.0
        elif cv < 0.035:
            score_quality = 14.0
    details.append(ScoreDetail("横盘质量", score_quality, 30.0))

    # 2. 突破力度 (25分) — 信号日涨幅 / 横盘期间平均日涨跌幅绝对值
    score_breakout = 3.0
    daily_changes = np.abs(np.diff(segment) / segment[:-1])
    avg_change = np.mean(daily_changes) if len(daily_changes) > 0 else 0
    signal_change = (close[idx] - close[idx - 1]) / close[idx - 1] if close[idx - 1] > 0 else 0
    if avg_change > 0:
        breakout_ratio = signal_change / avg_change
        if breakout_ratio >= 3.0:
            score_breakout = 25.0
        elif breakout_ratio >= 2.0:
            score_breakout = 18.0
        elif breakout_ratio >= 1.5:
            score_breakout = 10.0
    details.append(ScoreDetail("突破力度", score_breakout, 25.0))

    # 3. 横盘时长 (20分)
    score_duration = 0.0
    if 5 <= sideways_window <= 8:
        score_duration = 20.0
    elif 3 <= sideways_window <= 4:
        score_duration = 14.0
    elif 9 <= sideways_window <= 12:
        score_duration = 10.0
    details.append(ScoreDetail("横盘时长", score_duration, 20.0))

    # 4. 量价配合 (15分) — 信号日量比 vs 横盘期间均量
    score_volume = 3.0
    avg_vol = np.mean(vol_segment) if len(vol_segment) > 0 else 0
    if avg_vol > 0:
        vol_ratio = volume[idx] / avg_vol
        if 1.3 <= vol_ratio <= 2.0:
            score_volume = 15.0
        elif 1.0 <= vol_ratio <= 1.3:
            score_volume = 10.0
        elif 2.0 < vol_ratio <= 3.0:
            score_volume = 7.0
    details.append(ScoreDetail("量价配合", score_volume, 15.0))

    # 5. 趋势支撑 (10分)
    score_trend = 0.0
    if np.isfinite(short_trend[idx]) and np.isfinite(long_short[idx]):
        if short_trend[idx] > long_short[idx]:
            # 检查差距是否收窄
            if idx >= 1 and np.isfinite(short_trend[idx - 1]) and np.isfinite(long_short[idx - 1]):
                gap_today = short_trend[idx] - long_short[idx]
                gap_yesterday = short_trend[idx - 1] - long_short[idx - 1]
                if gap_today >= gap_yesterday:
                    score_trend = 10.0
                else:
                    score_trend = 5.0
            else:
                score_trend = 5.0
    details.append(ScoreDetail("趋势支撑", score_trend, 10.0))

    return details


def score_uptrend_continue(idx: int, brick: np.ndarray, close: np.ndarray,
                           high: np.ndarray, volume: np.ndarray,
                           slope: np.ndarray,
                           short_trend: np.ndarray) -> list[ScoreDetail]:
    """上升波段延续评分"""
    details = []

    is_red = np.zeros(idx + 1, dtype=bool)
    for i in range(1, idx + 1):
        is_red[i] = brick[i] > brick[i - 1]

    # 找绿砖段和前面的红砖段
    green_end = idx - 1
    green_start = green_end
    while green_start > 0 and not is_red[green_start]:
        green_start -= 1
    green_start += 1
    green_days = green_end - green_start + 1

    red_end = green_start - 1
    red_start = red_end
    while red_start > 0 and is_red[red_start]:
        red_start -= 1
    red_start += 1
    red_days = red_end - red_start + 1

    # 1. 上涨趋势强度 (30分)
    score_trend_strength = 10.0
    slope_val = slope[idx] if np.isfinite(slope[idx]) else 0
    if red_days >= 5 and slope_val > 0.5:
        score_trend_strength = 30.0
    elif red_days >= 3 and slope_val > 0.2:
        score_trend_strength = 22.0
    details.append(ScoreDetail("上涨趋势强度", score_trend_strength, 30.0))

    # 2. 绿砖短暂性 (25分)
    score_brevity = 0.0
    if green_days == 1:
        score_brevity = 25.0
    elif green_days == 2:
        score_brevity = 18.0
    elif green_days == 3:
        score_brevity = 8.0
    details.append(ScoreDetail("绿砖短暂性", score_brevity, 25.0))

    # 3. 绿砖缩量 (20分)
    score_shrink = 2.0
    if red_start <= red_end and green_start <= green_end:
        red_avg_vol = np.mean(volume[red_start:red_end + 1])
        green_avg_vol = np.mean(volume[green_start:green_end + 1])
        if red_avg_vol > 0:
            vol_ratio = green_avg_vol / red_avg_vol
            if vol_ratio < 0.6:
                score_shrink = 20.0
            elif vol_ratio < 0.8:
                score_shrink = 14.0
            elif vol_ratio < 1.0:
                score_shrink = 8.0
    details.append(ScoreDetail("绿砖缩量", score_shrink, 20.0))

    # 4. 翻红力度 (15分)
    score_force = 0.0
    brick_increase = brick[idx] - brick[idx - 1]
    # 绿砖总下降量
    green_total_decrease = 0.0
    for i in range(green_start, green_end + 1):
        if i > 0:
            green_total_decrease += max(0, brick[i - 1] - brick[i])
    if green_total_decrease > 1e-12:
        ratio = brick_increase / green_total_decrease
        if ratio >= 1.0:
            score_force = 15.0
        elif ratio >= 0.7:
            score_force = 10.0
        elif ratio >= 0.5:
            score_force = 5.0
    else:
        score_force = 10.0
    details.append(ScoreDetail("翻红力度", score_force, 15.0))

    # 5. 价格位置 (10分) — 信号日收盘价 vs 前期红砖最高收盘价
    score_position = 0.0
    if red_start <= red_end:
        red_max_close = np.max(close[red_start:red_end + 1])
        if red_max_close > 0:
            gap_pct = (red_max_close - close[idx]) / red_max_close
            if gap_pct < 0.02:
                score_position = 10.0
            elif gap_pct < 0.05:
                score_position = 7.0
            elif gap_pct < 0.08:
                score_position = 4.0
    details.append(ScoreDetail("价格位置", score_position, 10.0))

    return details


# ============================================================
# 第 3 层：风险过滤
# ============================================================
def apply_risk_filter(idx: int, brick: np.ndarray, close: np.ndarray,
                      open_price: np.ndarray, high: np.ndarray, low: np.ndarray,
                      volume: np.ndarray, short_trend: np.ndarray,
                      long_short: np.ndarray,
                      pattern: PatternType, sideways_window: int,
                      pattern_score: float) -> tuple[bool, float, list[str], str]:
    """
    风险过滤，返回 (是否被否决, 扣分, 扣分原因列表, 否决原因)
    """
    vetoed = False
    veto_reason = ""
    deduction = 0.0
    reasons = []

    lookback = 10

    # ── 规则 1：近期一字板跌停 ──
    for i in range(max(1, idx - lookback), idx):
        if close[i - 1] > 0:
            change_pct = (close[i] - close[i - 1]) / close[i - 1]
            if change_pct <= -0.095 and abs(open_price[i] - low[i]) < 1e-6:
                vetoed = True
                veto_reason = "规则1:近期一字板跌停"
                return vetoed, 0, [], veto_reason

    # ── 规则 4：近期放量大阴线 ──
    avg_vol_window = min(30, idx)
    avg_vol = np.mean(volume[max(0, idx - avg_vol_window):idx]) if avg_vol_window > 0 else 0

    for i in range(max(1, idx - lookback), idx):
        if close[i - 1] > 0:
            change_pct = (close[i] - close[i - 1]) / close[i - 1]
            vol_ratio = volume[i] / avg_vol if avg_vol > 0 else 0
            if vol_ratio > 1.5 and change_pct < -0.03:
                # 检查豁免条件：大阴线之后、信号日之前，股价是否放量突破了大阴线当天最高价
                exempted = False
                big_drop_high = high[i]
                for k in range(i + 1, idx):
                    if close[k] > big_drop_high:
                        exempted = True
                        break
                if not exempted:
                    vetoed = True
                    veto_reason = "规则4:近期放量大阴线"
                    return vetoed, 0, [], veto_reason

    # ── 规则 2：黄白线差距收窄 ──
    if idx >= 1:
        if (np.isfinite(short_trend[idx]) and np.isfinite(long_short[idx])
                and np.isfinite(short_trend[idx - 1]) and np.isfinite(long_short[idx - 1])):
            gap_today = short_trend[idx] - long_short[idx]
            gap_yesterday = short_trend[idx - 1] - long_short[idx - 1]
            if gap_today < gap_yesterday:
                deduction += 15.0
                reasons.append("规则2:黄白线差距收窄(-15)")

    # ── 规则 3：横盘时间过长 ──
    if pattern == PatternType.SIDEWAYS_BREAKOUT and sideways_window > 12:
        deduction += 20.0
        reasons.append("规则3:横盘时间过长(-20)")

    # ── 规则 5：连续未缩量绿砖 ──
    lookback_5 = 15
    is_red = np.zeros(idx + 1, dtype=bool)
    for i in range(1, idx + 1):
        is_red[i] = brick[i] > brick[i - 1]

    avg_vol_5 = np.mean(volume[max(0, idx - lookback_5):idx]) if lookback_5 > 0 else 0

    search_start = max(1, idx - lookback_5)
    consecutive_green = 0
    green_vol_sum = 0.0
    green_vol_count = 0
    found_rule5 = False

    for i in range(search_start, idx):
        if not is_red[i]:
            consecutive_green += 1
            green_vol_sum += volume[i]
            green_vol_count += 1
        else:
            if consecutive_green >= 3 and green_vol_count > 0:
                avg_green_vol = green_vol_sum / green_vol_count
                if avg_vol_5 > 0 and avg_green_vol / avg_vol_5 > 0.8:
                    found_rule5 = True
                    break
            consecutive_green = 0
            green_vol_sum = 0.0
            green_vol_count = 0

    if not found_rule5 and consecutive_green >= 3 and green_vol_count > 0:
        avg_green_vol = green_vol_sum / green_vol_count
        if avg_vol_5 > 0 and avg_green_vol / avg_vol_5 > 0.8:
            found_rule5 = True

    if found_rule5:
        deduction += 15.0
        reasons.append("规则5:连续未缩量绿砖(-15)")

    # 检查扣分后是否低于 60 分
    final = pattern_score - deduction
    if final < 60 and deduction > 0:
        vetoed = True
        veto_reason = f"扣分后低于60分({final:.0f}分)"

    return vetoed, deduction, reasons, veto_reason


# ============================================================
# 信号分级
# ============================================================
def grade_signal(score: float) -> str:
    if score >= 80:
        return "⭐⭐⭐ S级"
    elif score >= 65:
        return "⭐⭐ A级"
    elif score >= 50:
        return "⭐ B级"
    else:
        return "无效"


# ============================================================
# 主流程
# ============================================================
def process_stock(symbol: str, name: str) -> list[SignalResult]:
    """处理单只股票，返回所有信号结果"""
    csv_path = DATA_DIR / f"{symbol}.csv"
    if not csv_path.exists():
        return []

    df = pd.read_csv(csv_path)
    if df.empty or "date" not in df.columns:
        return []

    # 标准化
    for col in ["open", "close", "high", "low", "volume"]:
        if col not in df.columns:
            return []
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.dropna(subset=["date", "open", "close", "high", "low"]).sort_values("date").reset_index(drop=True)

    if len(df) < 120:  # 需要足够的历史数据计算指标
        return []

    close = df["close"].to_numpy(dtype=float)
    high_arr = df["high"].to_numpy(dtype=float)
    low_arr = df["low"].to_numpy(dtype=float)
    open_arr = df["open"].to_numpy(dtype=float)
    volume_arr = df["volume"].to_numpy(dtype=float)
    volume_arr = np.where(np.isfinite(volume_arr), volume_arr, 0.0)
    dates = df["date"].to_numpy()

    # 计算指标
    brick_data = compute_brick_indicator(high_arr, low_arr, close)
    brick = brick_data["brick"]
    short_trend = compute_zx_short_trend(close)
    long_short = compute_zx_long_short(close)
    k, d, j = compute_kdj(high_arr, low_arr, close)
    min_j, _ = compute_min_j(j, k, d)
    slope = compute_linear_slope(close, 20)

    # 确定回测日期范围
    date_start = pd.Timestamp(DATE_START)
    date_end = pd.Timestamp(DATE_END)

    results = []
    length = len(df)

    for idx in range(2, length):
        current_date = pd.Timestamp(dates[idx])
        if current_date < date_start or current_date > date_end:
            continue

        # 第 0 层：基础信号
        if not check_base_signal(idx, brick, short_trend, long_short):
            continue

        # 第 1 层：模式识别 + 第 2 层：评分
        candidates = []

        # N 型起跳
        if detect_n_shape(idx, brick, close, low_arr):
            score_details = score_n_shape(idx, brick, close, low_arr, high_arr,
                                          volume_arr, j, min_j, short_trend, long_short)
            total = sum(d.score for d in score_details)
            candidates.append((PatternType.N_SHAPE, score_details, total, 0))

        # 横盘起跳
        is_sideways, sw_window = detect_sideways_breakout(idx, close)
        if is_sideways:
            score_details = score_sideways_breakout(idx, brick, close, volume_arr,
                                                    short_trend, long_short, sw_window)
            total = sum(d.score for d in score_details)
            candidates.append((PatternType.SIDEWAYS_BREAKOUT, score_details, total, sw_window))

        # 上升波段延续
        if detect_uptrend_continue(idx, brick, close, short_trend, long_short, slope):
            score_details = score_uptrend_continue(idx, brick, close, high_arr,
                                                   volume_arr, slope, short_trend)
            total = sum(d.score for d in score_details)
            candidates.append((PatternType.UPTREND_CONTINUE, score_details, total, 0))

        if not candidates:
            continue

        # 取得分最高的模式
        candidates.sort(key=lambda x: x[2], reverse=True)
        best_pattern, best_details, best_score, best_sw = candidates[0]

        # 第 3 层：风险过滤
        vetoed, deduction, risk_reasons, veto_reason = apply_risk_filter(
            idx, brick, close, open_arr, high_arr, low_arr, volume_arr,
            short_trend, long_short, best_pattern, best_sw, best_score
        )

        final_score = best_score - deduction

        if vetoed:
            grade = "排除"
        else:
            grade = grade_signal(final_score)

        # 信号日后 5 个交易日涨幅
        future_returns = {}
        signal_close = close[idx]
        for day_offset in range(1, FUTURE_DAYS + 1):
            future_idx = idx + day_offset
            if future_idx < length and signal_close > 0:
                future_ret = (close[future_idx] - signal_close) / signal_close * 100.0
                future_returns[f"T+{day_offset}涨幅%"] = round(future_ret, 2)
            else:
                future_returns[f"T+{day_offset}涨幅%"] = np.nan

        signal_date_str = pd.Timestamp(dates[idx]).strftime("%Y%m%d")

        result = SignalResult(
            symbol=symbol,
            name=name,
            signal_date=signal_date_str,
            pattern=best_pattern.value,
            pattern_score=round(best_score, 1),
            score_details=best_details,
            risk_deduction=round(deduction, 1),
            risk_reasons=risk_reasons,
            final_score=round(final_score, 1),
            grade=grade,
            vetoed=vetoed,
            veto_reason=veto_reason,
            future_returns=future_returns,
        )
        results.append(result)

    return results


def main():
    print("=" * 60, flush=True)
    print("砖形图选股评分系统回测", flush=True)
    print(f"时间范围: {DATE_START} ~ {DATE_END}", flush=True)
    print("=" * 60, flush=True)

    # 加载股票列表
    stocklist = pd.read_csv(STOCKLIST_CSV, dtype={"symbol": str})
    stocklist["symbol"] = stocklist["symbol"].astype(str).str.zfill(6)
    symbol_to_name = dict(zip(stocklist["symbol"], stocklist["name"]))

    all_symbols = sorted(stocklist["symbol"].unique())
    total = len(all_symbols)
    print(f"共 {total} 只股票待处理\n", flush=True)

    all_results: list[SignalResult] = []
    processed = 0

    for i, symbol in enumerate(all_symbols):
        name = symbol_to_name.get(symbol, "")
        try:
            results = process_stock(symbol, name)
            all_results.extend(results)
        except Exception as exc:
            pass  # 静默跳过异常股票

        processed += 1
        if processed % 200 == 0 or processed == total:
            print(f"进度: {processed}/{total}  已发现 {len(all_results)} 个信号", flush=True)

    print(f"\n回测完成，共发现 {len(all_results)} 个信号", flush=True)

    # 构建 DataFrame
    rows = []
    for r in all_results:
        row = {
            "股票代码": r.symbol,
            "股票名称": r.name,
            "信号日期": r.signal_date,
            "命中模式": r.pattern,
            "模式得分": r.pattern_score,
        }
        # 各维度得分
        for sd in r.score_details:
            row[sd.dimension] = f"{sd.score:.0f}/{sd.max_score:.0f}"
        row["风险扣分"] = r.risk_deduction
        row["风险原因"] = "; ".join(r.risk_reasons) if r.risk_reasons else ""
        row["最终得分"] = r.final_score
        row["信号等级"] = r.grade
        row["是否排除"] = "是" if r.vetoed else "否"
        row["排除原因"] = r.veto_reason
        # 未来涨幅
        for k, v in r.future_returns.items():
            row[k] = v
        rows.append(row)

    result_df = pd.DataFrame(rows)

    # 按最终得分降序排列
    if not result_df.empty:
        result_df = result_df.sort_values(["信号日期", "最终得分"], ascending=[True, False])

    result_df.to_csv(OUTPUT_CSV, index=False, encoding="utf-8-sig")
    print(f"\n结果已保存到: {OUTPUT_CSV}")

    # 打印统计摘要
    if not result_df.empty:
        valid = result_df[result_df["是否排除"] == "否"]
        print(f"\n{'=' * 40}")
        print("统计摘要")
        print(f"{'=' * 40}")
        print(f"总信号数: {len(result_df)}")
        print(f"有效信号数: {len(valid)}")
        print(f"被排除信号数: {len(result_df) - len(valid)}")

        if not valid.empty:
            print(f"\n按等级分布:")
            for grade in ["⭐⭐⭐ S级", "⭐⭐ A级", "⭐ B级", "无效"]:
                count = len(valid[valid["信号等级"] == grade])
                print(f"  {grade}: {count}")

            print(f"\n按模式分布:")
            for pattern in valid["命中模式"].unique():
                count = len(valid[valid["命中模式"] == pattern])
                print(f"  {pattern}: {count}")

            # 各等级的平均未来涨幅
            print(f"\n各等级平均未来涨幅:")
            for grade in ["⭐⭐⭐ S级", "⭐⭐ A级", "⭐ B级"]:
                subset = valid[valid["信号等级"] == grade]
                if not subset.empty:
                    avg_returns = []
                    for day in range(1, FUTURE_DAYS + 1):
                        col = f"T+{day}涨幅%"
                        if col in subset.columns:
                            avg_ret = subset[col].mean()
                            avg_returns.append(f"T+{day}: {avg_ret:.2f}%")
                    print(f"  {grade}: {', '.join(avg_returns)}")


if __name__ == "__main__":
    main()
