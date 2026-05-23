"""砖形图定式风险扣分 — compute_risk_penalty 拆分后的子检查函数。

每个 _check_* 函数返回 (RiskFilterDetail, penalty_label_or_None)。
penalty_label_or_None: 若非 None，则该惩罚会被加入 risk_items 字典。
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


# ── 通用风险1：一字板跌停 ──
def _check_limit_down(close, open_, high, low, index):
    lookback = 10
    start = max(1, index - lookback)
    triggered = False
    penalty = 0.0
    desc = ""

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
                penalty = -50
            elif dist <= 7:
                penalty = -30
            else:
                penalty = -20
            triggered = True
            desc = f"前{dist}日一字板跌停(跌{change_pct:.1%})"
            break

    detail = RiskFilterDetail(
        filter_type=RiskFilterType.LIMIT_DOWN,
        triggered=triggered,
        description=desc,
        penalty=penalty,
    )
    return detail, ("一字板跌停" if triggered else None)


# ── 通用风险2：高位放量大阴线 ──
def _check_heavy_volume_drop(indicators, index, pattern_type, avg_volume):
    close = indicators["close"]
    open_ = indicators["open"]
    high = indicators["high"]
    low = indicators["low"]
    volume = indicators["volume"]
    brick = indicators["brick"]

    lookback = 10
    start = max(1, index - lookback)
    triggered = False
    penalty = 0.0
    desc = ""

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
                    penalty = -30
                else:
                    penalty = -15

                triggered = True
                desc = f"前{index - day}日放量大阴线(跌{change_pct:.1%},量比{volume[day] / avg_volume:.1f}倍)"
                break

    detail = RiskFilterDetail(
        filter_type=RiskFilterType.HEAVY_VOLUME_DROP,
        triggered=triggered,
        description=desc,
        penalty=penalty,
    )
    return detail, ("高位放量大阴线" if triggered else None)


# ── 通用风险3：短趋势跌破多空线 ──
def _check_trend_broken(short_trend, long_short, index):
    st_val = short_trend[index] if np.isfinite(short_trend[index]) else 0
    ls_val = long_short[index] if np.isfinite(long_short[index]) else 0
    triggered = st_val > 0 and ls_val > 0 and st_val < ls_val
    penalty = -15.0 if triggered else 0.0
    detail = RiskFilterDetail(
        filter_type=RiskFilterType.TREND_BROKEN,
        triggered=triggered,
        description="短趋势跌破多空线" if triggered else "",
        penalty=penalty,
    )
    return detail, ("短趋势跌破多空线" if triggered else None)


# ── 追高离心（上升波段延续）──
def _check_chase_high_uptrend(close, short_trend, index):
    st_v = short_trend[index] if np.isfinite(short_trend[index]) else close[index]
    vs_st = (close[index] - st_v) / st_v * 100 if st_v > 0 else 0
    triggered = vs_st > 25
    penalty = -20.0 if vs_st > 45 else 0.0
    detail = RiskFilterDetail(
        filter_type=RiskFilterType.CHASE_HIGH,
        triggered=triggered,
        description=f"追高离心(vs短趋{vs_st:.1f}%)" if triggered else "",
        penalty=penalty,
    )
    label = "追高离心" if (triggered and penalty < 0) else None
    return detail, label


# ── 天量见顶（上升波段延续）──
def _check_peak_volume(volume, avg_volume, index):
    recent5 = volume[max(0, index - 4):index + 1]
    vol_ratio = np.max(recent5) / avg_volume if avg_volume > 0 and len(recent5) > 0 else 0
    recent20_max = np.max(volume[max(0, index - 19):index + 1]) if index >= 1 else 0
    is_peak = vol_ratio > 4 and np.max(recent5) >= recent20_max * 0.99
    if is_peak:
        penalty = -25.0 if vol_ratio > 5 else -15.0
        detail = RiskFilterDetail(
            filter_type=RiskFilterType.PEAK_VOLUME,
            triggered=True,
            description=f"天量见顶(量比{vol_ratio:.1f})",
            penalty=penalty,
        )
        return detail, "天量见顶"
    return RiskFilterDetail(filter_type=RiskFilterType.PEAK_VOLUME, triggered=False), None


# ── 绿砖期放量（N型起跳，仅标签）──
def _check_green_volume_up(brick, volume, index):
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

    triggered = gv_ratio > 1.3
    detail = RiskFilterDetail(
        filter_type=RiskFilterType.GREEN_VOLUME_UP,
        triggered=triggered,
        description=f"绿砖期放量(比值{gv_ratio:.2f})" if triggered else "",
        penalty=0.0,
    )
    return detail, None


# ── 趋势衰竭（横盘起跳，仅标签）──
def _check_trend_exhaust(short_trend, close, index):
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

    triggered = slope_pct <= 0
    detail = RiskFilterDetail(
        filter_type=RiskFilterType.TREND_EXHAUST,
        triggered=triggered,
        description=f"趋势衰竭(斜率{slope_pct:.2f}%)" if triggered else "",
        penalty=0.0,
    )
    return detail, None


# ── 假横盘（横盘起跳，仅标签）──
def _check_fake_sideways(brick, index):
    switches = _count_brick_color_switches(brick, index - 1, window=10)
    triggered = switches < 3
    detail = RiskFilterDetail(
        filter_type=RiskFilterType.FAKE_SIDEWAYS,
        triggered=triggered,
        description=f"假横盘(切换{switches}次<3)" if triggered else "",
        penalty=0.0,
    )
    return detail, None


# ── 通用风险4：锤子线禁忌 ──
def _check_hammer(close, open_, high, low, index):
    body = abs(close[index] - open_[index])
    lower_shadow = min(close[index], open_[index]) - low[index]
    candle_range = high[index] - low[index]
    body_ref = max(body, 0.003 * close[index])

    triggered = lower_shadow >= 2 * body_ref and candle_range > 0.005 * close[index]
    if triggered:
        penalty = -5.0
        desc = f"锤子线(下影线{lower_shadow / body_ref:.1f}倍实体)"
    else:
        penalty = 0.0
        desc = ""

    detail = RiskFilterDetail(
        filter_type=RiskFilterType.HAMMER,
        triggered=triggered,
        description=desc,
        penalty=penalty,
    )
    return detail, ("锤子线" if triggered else None)


# ── 通用风险5：大上影线（V4 移除扣分，仅标签）──
def _check_large_upper_shadow(close, open_, high, low, index):
    body = abs(close[index] - open_[index])
    upper_shadow = high[index] - max(close[index], open_[index])
    candle_range = high[index] - low[index]
    body_ref = max(body, 0.003 * close[index])

    triggered = (
        candle_range > 0.005 * close[index]
        and upper_shadow >= 2 * body_ref
        and upper_shadow >= 0.6 * candle_range
    )
    desc = (
        f"大上影线(上影{upper_shadow / body_ref:.1f}倍实体,占振幅{upper_shadow / candle_range:.0%})"
        if triggered else ""
    )
    detail = RiskFilterDetail(
        filter_type=RiskFilterType.LARGE_UPPER_SHADOW,
        triggered=triggered,
        description=desc,
        penalty=0.0,
    )
    return detail, None


# ── 通用风险6：三波不做 ──
def _check_third_wave(brick, close, high, index):
    triggered, _, desc = _detect_third_wave(brick, close, high, index)
    penalty = -8.0 if triggered else 0.0
    detail = RiskFilterDetail(
        filter_type=RiskFilterType.THIRD_WAVE,
        triggered=triggered,
        description=desc,
        penalty=penalty,
    )
    return detail, ("三波追高" if triggered else None)


# ── 新增风险7：冲高回落 ──
def _check_chase_high_pullback(close, index):
    prev_close_val = close[index - 1] if index >= 1 else close[index]
    if prev_close_val > 0:
        t_day_change = (close[index] - prev_close_val) / prev_close_val * 100
        triggered = 6 <= t_day_change < 8
        penalty = -10.0 if triggered else 0.0
        desc = f"冲高回落(涨幅{t_day_change:.1f}%在6-8%区间)" if triggered else ""
    else:
        triggered = False
        penalty = 0.0
        desc = ""

    detail = RiskFilterDetail(
        filter_type=RiskFilterType.CHASE_HIGH,
        triggered=triggered,
        description=desc,
        penalty=penalty,
    )
    return detail, ("冲高回落" if triggered else None)


# ── 新增风险8：横盘MACD死叉 ──
def _check_sideways_macd_dead_cross(indicators, index):
    diff = indicators["macd_diff"]
    dea = indicators["macd_dea"]
    if np.isfinite(diff[index]) and np.isfinite(dea[index]):
        _, dead_dist = _detect_diff_dea_cross(diff, dea, index, lookback=3)
        triggered = dead_dist >= 0
        penalty = -10.0 if triggered else 0.0
        desc = f"横盘MACD死叉(前{dead_dist}日)" if triggered else ""
    else:
        triggered = False
        penalty = 0.0
        desc = ""

    detail = RiskFilterDetail(
        filter_type=RiskFilterType.TREND_EXHAUST,
        triggered=triggered,
        description=desc,
        penalty=penalty,
    )
    return detail, ("横盘MACD死叉" if triggered else None)


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

    avg_start = max(0, index - 30)
    avg_volume = float(np.mean(volume[avg_start:index])) if index > avg_start else 0.0

    def _record(detail_label):
        detail, label = detail_label
        risk_details.append(detail)
        if label is not None:
            risk_items[label] = detail.penalty

    # 通用风险
    _record(_check_limit_down(close, open_, high, low, index))
    _record(_check_heavy_volume_drop(indicators, index, pattern_type, avg_volume))
    _record(_check_trend_broken(short_trend, long_short, index))

    # 定式专属
    if pattern_type == PatternType.UPTREND_CONTINUE:
        _record(_check_chase_high_uptrend(close, short_trend, index))
        _record(_check_peak_volume(volume, avg_volume, index))

    if pattern_type == PatternType.N_SHAPE_JUMP:
        _record(_check_green_volume_up(brick, volume, index))

    if pattern_type == PatternType.SIDEWAYS_JUMP:
        _record(_check_trend_exhaust(short_trend, close, index))
        _record(_check_fake_sideways(brick, index))

    # 通用风险（K线形态等）
    _record(_check_hammer(close, open_, high, low, index))
    _record(_check_large_upper_shadow(close, open_, high, low, index))
    _record(_check_third_wave(brick, close, high, index))
    _record(_check_chase_high_pullback(close, index))

    if pattern_type == PatternType.SIDEWAYS_JUMP:
        _record(_check_sideways_macd_dead_cross(indicators, index))

    total_penalty = max(sum(risk_items.values()), -30.0)
    return total_penalty, risk_items, risk_details
