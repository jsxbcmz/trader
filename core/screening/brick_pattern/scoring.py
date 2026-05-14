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

    # 追高离心（上升波段延续）— V4: 阈值从35%提高到45%，取消-15档
    if pattern_type == PatternType.UPTREND_CONTINUE:
        st_v = short_trend[index] if np.isfinite(short_trend[index]) else close[index]
        vs_st = (close[index] - st_v) / st_v * 100 if st_v > 0 else 0
        ch_triggered = vs_st > 25
        if vs_st > 45:
            ch_penalty = -20.0
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
            pv_penalty = -25.0 if vol_ratio > 5 else -15.0
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

    # ── 通用风险4：锤子线禁忌 (V4大幅降低扣分: -20~-25 → -5) ──
    body = abs(close[index] - open_[index])
    lower_shadow = min(close[index], open_[index]) - low[index]
    upper_shadow = high[index] - max(close[index], open_[index])
    candle_range = high[index] - low[index]
    body_ref = max(body, 0.003 * close[index])

    hm_triggered = lower_shadow >= 2 * body_ref and candle_range > 0.005 * close[index]
    if hm_triggered:
        hm_penalty = -5.0
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

    # ── 通用风险5：大上影线 (V4移除扣分 — 回测显示不构成实质风险) ──
    lus_triggered = (
        candle_range > 0.005 * close[index]
        and upper_shadow >= 2 * body_ref
        and upper_shadow >= 0.6 * candle_range
    )
    lus_penalty = 0.0
    if lus_triggered:
        lus_desc = f"大上影线(上影{upper_shadow / body_ref:.1f}倍实体,占振幅{upper_shadow / candle_range:.0%})"
    else:
        lus_desc = ""

    risk_details.append(RiskFilterDetail(
        filter_type=RiskFilterType.LARGE_UPPER_SHADOW,
        triggered=lus_triggered,
        description=lus_desc,
        penalty=lus_penalty,
    ))

    # ── 通用风险6：三波不做 (V4降低扣分: -15~-25 → -8) ──
    tw_triggered, tw_penalty, tw_desc = _detect_third_wave(brick, close, high, index)
    if tw_triggered:
        tw_penalty = -8.0
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

    # ── 新增风险8：横盘MACD死叉 (V4降低扣分: -15 → -10) ──
    if pattern_type == PatternType.SIDEWAYS_JUMP:
        diff = indicators["macd_diff"]
        dea = indicators["macd_dea"]
        if np.isfinite(diff[index]) and np.isfinite(dea[index]):
            _, dead_dist = _detect_diff_dea_cross(diff, dea, index, lookback=3)
            md_triggered = dead_dist >= 0
            md_penalty = -10.0 if md_triggered else 0.0
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

    total_penalty = max(sum(risk_items.values()), -30.0)
    return total_penalty, risk_items, risk_details
