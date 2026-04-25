"""砖形图交易定式选股引擎。

独立于 TDX 表达式系统，直接用 Python/NumPy 实现文档中定义的全部规则：
- 3 种交易定式：N型起跳、横盘起跳、上升波段延续
- 必备前提：绿转红 + 力度达标 + 短趋线>多空线
- 4 条风险过滤规则
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
    }


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 必备前提检测
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def check_prerequisites(indicators: dict[str, np.ndarray], index: int) -> tuple[bool, str]:
    """检查三项必备前提，全部满足才返回 (True, "")。

    1. 砖形图绿转红：REF(AA,1)=0 AND AA=1
    2. 砖形图差值>0 且力度达标：变化量绝对值 / 前日变化量绝对值 >= 0.5
    3. 短趋线 > 多空线
    """
    brick = indicators["brick"]
    short_trend = indicators["short_trend"]
    long_short = indicators["long_short"]

    if index < 2:
        return False, "数据不足"

    # ── 前提1：绿转红 ──
    current_rising = brick[index] > brick[index - 1]
    prev_rising = brick[index - 1] > brick[index - 2]

    if not current_rising:
        return False, "当日非红砖"
    if prev_rising:
        return False, "前日已是红砖(非绿转红)"

    # ── 前提2：差值>0 且力度达标 ──
    delta_today = brick[index] - brick[index - 1]
    delta_yesterday = abs(brick[index - 1] - brick[index - 2])

    if delta_today <= 0:
        return False, "砖形图差值<=0"

    if delta_yesterday > 1e-9:
        force_ratio = abs(delta_today) / delta_yesterday
        if force_ratio < 0.5:
            return False, f"翻红力度不足(比值{force_ratio:.2f}<0.5)"

    # ── 前提3：短趋线 > 多空线 ──
    if not (np.isfinite(short_trend[index]) and np.isfinite(long_short[index])):
        return False, "趋势线数据无效"

    if short_trend[index] <= long_short[index]:
        return False, "短趋线未在多空线之上"

    return True, ""


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 三种交易定式检测
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def detect_n_shape_jump(indicators: dict[str, np.ndarray], index: int) -> PatternMatchDetail:
    """N型起跳检测。

    形态：最近时间内股价以N型结构呈上升趋势，砖形图从绿砖变红砖。
    N型结构 = 上涨→回调→再上涨，形成更高的低点。

    检测逻辑：
    1. 在信号日前30个交易日内寻找 N 型结构
    2. 找到一个局部高点 H1，之后回调形成局部低点 L1，然后再次上涨
    3. L1 > 前一个低点（更高的低点），确认上升趋势
    4. 配合 KDJ 指标，J值处于较低位置更佳
    """
    close = indicators["close"]
    brick = indicators["brick"]
    kdj_j = indicators["kdj_j"]

    lookback = 30
    if index < lookback:
        return PatternMatchDetail(pattern_type=PatternType.N_SHAPE_JUMP, matched=False,
                                  description="数据不足")

    window_close = close[index - lookback:index + 1]

    # 寻找局部极值点（用5日窗口判断）
    highs = []
    lows = []
    for offset in range(3, len(window_close) - 2):
        val = window_close[offset]
        if val >= max(window_close[max(0, offset - 2):offset]) and val >= max(window_close[offset + 1:min(len(window_close), offset + 3)]):
            highs.append((offset, val))
        if val <= min(window_close[max(0, offset - 2):offset]) and val <= min(window_close[offset + 1:min(len(window_close), offset + 3)]):
            lows.append((offset, val))

    if len(highs) < 1 or len(lows) < 1:
        return PatternMatchDetail(pattern_type=PatternType.N_SHAPE_JUMP, matched=False,
                                  description="未找到N型极值点")

    # 寻找 N 型结构：高点 → 低点 → 当前（再次上涨）
    # 要求低点在高点之后，且当前价格高于低点
    found_n_shape = False
    best_score = 0.0
    description = "未找到N型结构"

    for high_idx, high_val in reversed(highs):
        for low_idx, low_val in reversed(lows):
            if low_idx <= high_idx:
                continue
            # 低点之后到信号日应该是上涨的
            if close[index] <= low_val:
                continue
            # 回调幅度不能太深（不超过上涨幅度的 78.6%）
            # 寻找高点之前的起涨点
            rise_start_idx = max(0, high_idx - 10)
            rise_start_val = min(window_close[rise_start_idx:high_idx + 1])
            if high_val <= rise_start_val:
                continue
            retrace_ratio = (high_val - low_val) / (high_val - rise_start_val)
            if retrace_ratio > 0.786:
                continue

            # 确认上升趋势：当前价格接近或超过前高
            found_n_shape = True
            # 评分：倒U型，38.2%~50%回调为黄金区间（洗盘充分且趋势未破）
            if retrace_ratio <= 0.236:
                retrace_score = 20 + retrace_ratio / 0.236 * 15
            elif retrace_ratio <= 0.382:
                retrace_score = 35 + (retrace_ratio - 0.236) / (0.382 - 0.236) * 15
            elif retrace_ratio <= 0.500:
                retrace_score = 50
            elif retrace_ratio <= 0.618:
                retrace_score = 50 - (retrace_ratio - 0.500) / (0.618 - 0.500) * 20
            else:
                retrace_score = 30 - (retrace_ratio - 0.618) / (0.786 - 0.618) * 30
            retrace_score = max(0, retrace_score)
            j_val = kdj_j[index] if np.isfinite(kdj_j[index]) else 50
            j_score = max(0, (80 - j_val) / 80) * 30
            trend_score = 20 if close[index] > high_val * 0.95 else 10
            score = retrace_score + j_score + trend_score
            if score > best_score:
                best_score = score
                description = f"N型结构(回调{retrace_ratio:.0%},J={j_val:.1f})"
            break
        if found_n_shape:
            break

    return PatternMatchDetail(
        pattern_type=PatternType.N_SHAPE_JUMP,
        matched=found_n_shape,
        description=description,
        score=best_score,
        extra={"kdj_j": float(kdj_j[index]) if np.isfinite(kdj_j[index]) else None},
    )


def detect_sideways_jump(indicators: dict[str, np.ndarray], index: int) -> PatternMatchDetail:
    """横盘起跳检测。

    形态：股价近几日横盘震荡，收盘价波动不大，某天收盘价涨幅突然加大，
    且砖形图绿砖变红砖。

    检测逻辑：
    1. 信号日前 N 个交易日内收盘价振幅很小（标准差 / 均值 < 阈值）
    2. 信号日当天涨幅明显大于横盘期间的平均涨幅
    3. 横盘天数 3~10 天为宜
    """
    close = indicators["close"]
    high = indicators["high"]
    low = indicators["low"]

    if index < 5:
        return PatternMatchDetail(pattern_type=PatternType.SIDEWAYS_JUMP, matched=False,
                                  description="数据不足")

    # 信号日涨幅
    today_change = (close[index] - close[index - 1]) / close[index - 1]

    best_score = 0.0
    best_desc = "未检测到横盘"
    found = False

    # 尝试不同的横盘窗口长度（3~10天）
    for window_len in range(3, 11):
        if index - 1 < window_len:
            continue

        # 横盘区间：信号日前 window_len 天（不含信号日）
        start = index - 1 - window_len + 1
        end = index  # 不含信号日

        window_close = close[start:end]
        window_high = high[start:end]
        window_low = low[start:end]

        if len(window_close) < 3:
            continue

        mean_price = np.mean(window_close)
        if mean_price <= 0:
            continue

        # 收盘价波动率（标准差/均值）
        volatility = np.std(window_close) / mean_price

        # 收盘价振幅（用收盘价的极差，避免日内波动误判高价股）
        close_range = (np.max(window_close) - np.min(window_close)) / mean_price

        # 横盘判定：波动率 < 2% 且收盘价振幅 < 5%
        if volatility > 0.02 or close_range > 0.05:
            continue

        # 横盘期间平均日涨幅
        daily_changes = np.abs(np.diff(window_close) / window_close[:-1])
        avg_daily_change = np.mean(daily_changes) if len(daily_changes) > 0 else 0

        # 信号日涨幅应明显大于横盘期间平均涨幅（至少2倍）
        if avg_daily_change > 0 and today_change < avg_daily_change * 2:
            continue

        # 信号日涨幅至少 1%
        if today_change < 0.01:
            continue

        found = True
        # 评分：横盘越窄越好，突破越强越好
        narrow_score = max(0, (0.05 - close_range) / 0.05) * 40
        breakout_score = min(40, today_change / 0.01 * 10)
        length_score = 20 if 4 <= window_len <= 8 else 10
        score = narrow_score + breakout_score + length_score

        if score > best_score:
            best_score = score
            best_desc = f"横盘{window_len}天(振幅{close_range:.1%},突破{today_change:.1%})"

    return PatternMatchDetail(
        pattern_type=PatternType.SIDEWAYS_JUMP,
        matched=found,
        description=best_desc,
        score=best_score,
        extra={"today_change": float(today_change)},
    )


def detect_uptrend_continue(indicators: dict[str, np.ndarray], index: int) -> PatternMatchDetail:
    """上升波段延续检测。

    形态：股价处于上涨阶段，没有形成N型结构和像样的回调，
    砖形图原本连续红砖然后出现1~2个绿砖，紧接着又出现红砖。

    检测逻辑：
    1. 信号日前存在连续红砖区间（至少3根）
    2. 红砖区间之后出现1~2根绿砖
    3. 信号日重新翻红
    4. 整体处于上涨趋势（短趋线斜率为正）
    """
    brick = indicators["brick"]
    close = indicators["close"]
    short_trend = indicators["short_trend"]

    if index < 8:
        return PatternMatchDetail(pattern_type=PatternType.UPTREND_CONTINUE, matched=False,
                                  description="数据不足")

    # 从信号日往前回溯，寻找绿砖区间
    green_count = 0

    # 统计信号日前连续绿砖数量（1~2根）
    scan_pos = index - 1
    while scan_pos >= 1 and brick[scan_pos] <= brick[scan_pos - 1]:
        green_count += 1
        scan_pos -= 1

    if green_count < 1 or green_count > 2:
        return PatternMatchDetail(
            pattern_type=PatternType.UPTREND_CONTINUE,
            matched=False,
            description=f"绿砖数量{green_count}不在1~2范围内",
        )

    # 绿砖之前应该是连续红砖区间（至少3根）
    red_end = scan_pos
    red_count = 0
    while red_end >= 1 and brick[red_end] > brick[red_end - 1]:
        red_count += 1
        red_end -= 1

    if red_count < 3:
        return PatternMatchDetail(
            pattern_type=PatternType.UPTREND_CONTINUE,
            matched=False,
            description=f"前方红砖仅{red_count}根(需>=3)",
        )

    # 确认上涨趋势：短趋线斜率为正
    trend_window = 10
    trend_start = max(0, index - trend_window)
    trend_slice = short_trend[trend_start:index + 1]
    valid_trend = trend_slice[np.isfinite(trend_slice)]

    if len(valid_trend) < 3:
        return PatternMatchDetail(
            pattern_type=PatternType.UPTREND_CONTINUE,
            matched=False,
            description="趋势线数据不足",
        )

    x_vals = np.arange(len(valid_trend), dtype=float)
    slope = np.polyfit(x_vals, valid_trend, 1)[0]

    if slope <= 0:
        return PatternMatchDetail(
            pattern_type=PatternType.UPTREND_CONTINUE,
            matched=False,
            description="趋势线斜率非正(非上涨阶段)",
        )

    # 评分
    red_score = min(40, red_count * 8)
    green_penalty = 10 if green_count == 1 else 0
    slope_score = min(30, slope / close[index] * 10000)
    score = red_score + green_penalty + slope_score + 20

    description = f"连续{red_count}红砖后{green_count}绿砖再翻红(斜率{slope:.2f})"

    return PatternMatchDetail(
        pattern_type=PatternType.UPTREND_CONTINUE,
        matched=True,
        description=description,
        score=score,
        extra={"red_count": red_count, "green_count": green_count, "slope": float(slope)},
    )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 风险过滤规则（4条，已去掉原规则2"黄白线差距收窄"）
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def filter_limit_down(indicators: dict[str, np.ndarray], index: int) -> RiskFilterDetail:
    """规则1：信号日前10个交易日内不能有一字板跌停。

    一字板跌停 = 开盘价 == 收盘价 == 最低价，且跌幅 >= 9%
    """
    close = indicators["close"]
    open_ = indicators["open"]
    low = indicators["low"]
    high = indicators["high"]

    lookback = 10
    start = max(0, index - lookback)

    for day in range(start, index):
        if day < 1:
            continue
        prev_close = close[day - 1]
        if prev_close <= 0:
            continue

        change_pct = (close[day] - prev_close) / prev_close

        # 一字板跌停：开=收=低，跌幅 >= 9%
        is_limit_down = (
            abs(open_[day] - close[day]) < 0.01
            and abs(close[day] - low[day]) < 0.01
            and abs(open_[day] - high[day]) < 0.01
            and change_pct <= -0.09
        )

        if is_limit_down:
            return RiskFilterDetail(
                filter_type=RiskFilterType.LIMIT_DOWN,
                triggered=True,
                description=f"前{index - day}日出现一字板跌停(跌{change_pct:.1%})",
            )

    return RiskFilterDetail(filter_type=RiskFilterType.LIMIT_DOWN, triggered=False)


def filter_long_sideways(
    indicators: dict[str, np.ndarray],
    index: int,
    matched_pattern: PatternType | None,
) -> RiskFilterDetail:
    """规则3：横盘起跳前横盘时间不宜过长（≤10个交易日）。

    仅对横盘起跳定式生效。
    """
    if matched_pattern != PatternType.SIDEWAYS_JUMP:
        return RiskFilterDetail(filter_type=RiskFilterType.LONG_SIDEWAYS, triggered=False)

    close = indicators["close"]
    high = indicators["high"]
    low = indicators["low"]

    # 从信号日往前扫描，找到横盘开始的位置
    max_scan = min(20, index)
    sideways_days = 0

    for window_len in range(3, max_scan + 1):
        start = index - 1 - window_len + 1
        if start < 0:
            break

        window_close = close[start:index]
        mean_price = np.mean(window_close)
        if mean_price <= 0:
            break

        volatility = np.std(window_close) / mean_price
        close_range = (np.max(window_close) - np.min(window_close)) / mean_price

        if volatility <= 0.02 and close_range <= 0.05:
            sideways_days = window_len
        else:
            break

    if sideways_days > 10:
        return RiskFilterDetail(
            filter_type=RiskFilterType.LONG_SIDEWAYS,
            triggered=True,
            description=f"横盘时间过长({sideways_days}天>10天)",
            extra={"sideways_days": sideways_days},
        )

    return RiskFilterDetail(filter_type=RiskFilterType.LONG_SIDEWAYS, triggered=False)


def _is_in_n_shape_decline(indicators: dict[str, np.ndarray], day: int) -> bool:
    """判断某天是否处于N型结构的下跌回调阶段（而非顶部）。

    逻辑：在该天之前的近期内找到一个局部高点，且该天的收盘价明显低于高点，
    说明正处于从高点回调的下跌过程中。
    如果大阴线出现在高点当天或高点附近（距高点<=1天），视为出现在顶部，不豁免。
    """
    close = indicators["close"]
    high = indicators["high"]

    # 往前扫描最多15天，寻找局部高点
    scan_start = max(0, day - 15)
    window_high = high[scan_start:day + 1]

    if len(window_high) < 3:
        return False

    # 找到窗口内的最高价位置
    peak_offset = int(np.argmax(window_high))
    peak_abs_index = scan_start + peak_offset
    peak_price = float(high[peak_abs_index])

    # 大阴线距离高点太近（<=1天），视为出现在顶部，不豁免
    distance_from_peak = day - peak_abs_index
    if distance_from_peak <= 1:
        return False

    # 大阴线收盘价相对高点有明显回落（至少跌了2%），说明处于下跌回调中
    drop_from_peak = (peak_price - close[day]) / peak_price if peak_price > 0 else 0
    if drop_from_peak < 0.02:
        return False

    # 确认高点之前有上涨过程（高点不是起点）
    pre_peak_start = max(0, peak_abs_index - 10)
    pre_peak_low = float(np.min(close[pre_peak_start:peak_abs_index + 1]))
    rise_ratio = (peak_price - pre_peak_low) / pre_peak_low if pre_peak_low > 0 else 0

    return rise_ratio >= 0.03


def filter_heavy_volume_drop(indicators: dict[str, np.ndarray], index: int) -> RiskFilterDetail:
    """规则4：信号日前几个交易日内不能有放量大阴线。

    放量大阴线 = 收盘价 < 开盘价（阴线）且成交量 >= 均量的1.5倍 且跌幅 >= 3%

    特殊豁免1：如果放量大阴线之后、信号日之前，股价已超过大阴线当天最高价，
    则可以忽略本条规则。

    特殊豁免2：如果放量大阴线出现在N型结构的下跌回调过程中（而非顶部），
    说明这是正常的回调放量，不应触发风险过滤。
    """
    close = indicators["close"]
    open_ = indicators["open"]
    volume = indicators["volume"]
    high = indicators["high"]

    lookback = 10
    start = max(0, index - lookback)

    # 计算均量（前30日）
    avg_start = max(0, index - 30)
    avg_volume = np.mean(volume[avg_start:index]) if index > avg_start else 0
    if avg_volume <= 0:
        return RiskFilterDetail(filter_type=RiskFilterType.HEAVY_VOLUME_DROP, triggered=False)

    volume_threshold = avg_volume * 1.5

    for day in range(start, index):
        if day < 1:
            continue
        prev_close = close[day - 1]
        if prev_close <= 0:
            continue

        is_green = close[day] < open_[day]
        change_pct = (close[day] - prev_close) / prev_close
        is_heavy = volume[day] >= volume_threshold
        is_big_drop = change_pct <= -0.03

        if is_green and is_heavy and is_big_drop:
            # 豁免条件1：后续是否有放量突破大阴线最高价
            big_drop_high = high[day]
            exempted = False

            for recovery_day in range(day + 1, index):
                if close[recovery_day] > big_drop_high and volume[recovery_day] >= avg_volume:
                    exempted = True
                    break

            if exempted:
                continue

            # 豁免条件2：大阴线出现在N型结构的下跌回调过程中
            if _is_in_n_shape_decline(indicators, day):
                continue

            return RiskFilterDetail(
                filter_type=RiskFilterType.HEAVY_VOLUME_DROP,
                triggered=True,
                description=f"前{index - day}日出现放量大阴线(跌{change_pct:.1%},量比{volume[day] / avg_volume:.1f}倍)",
                extra={"drop_day_offset": index - day, "change_pct": float(change_pct)},
            )

    return RiskFilterDetail(filter_type=RiskFilterType.HEAVY_VOLUME_DROP, triggered=False)


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
    """对单只股票执行完整的砖形图定式选股流程。

    流程：必备前提 → 定式检测 → 风险过滤
    """
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
    """基于已经预计算好的指标执行单日定式检测。

    与 ``screen_single_stock`` 行为完全一致，但不会重复调用 ``_calc_indicators``，
    适合在「同一只股票多日扫描」的回测场景中复用，性能可提升一个数量级。
    """
    close_arr = indicators["close"]
    if index < 0 or index >= len(close_arr) or len(close_arr) < 10:
        return BrickPatternMatch(
            symbol=symbol, name=name, target_date=target_date,
            actual_date=actual_date, error="数据不足(少于10条)",
        )

    # 价格过滤
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
    matched_pattern_type = None
    best_score = -1.0

    for pattern_type in enabled_patterns:
        detector = pattern_detectors.get(pattern_type)
        if detector is None:
            continue
        result = detector(indicators, index)
        pattern_results.append(result)
        if result.matched and result.score > best_score:
            best_score = result.score
            matched_pattern_type = pattern_type

    if matched_pattern_type is None:
        return BrickPatternMatch(
            symbol=symbol, name=name, target_date=target_date,
            actual_date=actual_date,
            prerequisite_passed=True,
            prerequisite_detail="前提通过",
            pattern_matches=tuple(pattern_results),
        )

    # ── 步骤3：风险过滤（3条规则） ──
    risk_results = [
        filter_limit_down(indicators, index),
        filter_long_sideways(indicators, index, matched_pattern_type),
        filter_heavy_volume_drop(indicators, index),
    ]

    triggered_risks = [r for r in risk_results if r.triggered]

    if triggered_risks:
        risk_reasons = "; ".join(r.description for r in triggered_risks)
        return BrickPatternMatch(
            symbol=symbol, name=name, target_date=target_date,
            actual_date=actual_date,
            prerequisite_passed=True,
            prerequisite_detail="前提通过",
            pattern_matches=tuple(pattern_results),
            risk_filters=tuple(risk_results),
            risk_rejected=True,
            risk_reason=risk_reasons,
            matched_pattern=matched_pattern_type.value,
        )

    # ── 全部通过 ──
    return BrickPatternMatch(
        symbol=symbol, name=name, target_date=target_date,
        actual_date=actual_date,
        prerequisite_passed=True,
        prerequisite_detail="前提通过",
        pattern_matches=tuple(pattern_results),
        risk_filters=tuple(risk_results),
        final_matched=True,
        matched_pattern=matched_pattern_type.value,
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

        return {
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
        }
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
