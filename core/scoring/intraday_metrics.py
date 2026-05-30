"""M1 分钟级复盘 — 核心纯函数（便于单测，不含网络/DB）。

输入：同花顺分时接口剥壳后的分钟序列 + 昨收 pre + 预测方向 expected_direction。
输出：8.2 衍生指标（10个）+ 8.3 路径形态 path_shape + 路径级裁定 intraday_verdict。

分时每行 5 列（同花顺 last.js 实测）：
    时间(HHMM), 现价, 当分钟成交额(元), 累计均价VWAP, 当分钟成交量(股)
原始分时用完即弃，本模块只产出衍生指标。
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Optional

from core.scoring.prediction_review import normalize_direction


@dataclass
class MinuteBar:
    """单分钟分时点。"""
    time: str          # "0930"
    price: float       # 现价
    amount: float      # 当分钟成交额（元）
    vwap: float        # 累计均价
    volume: float      # 当分钟成交量（股）


def parse_minute_data(data_str: str) -> list[MinuteBar]:
    """解析同花顺 data 字段（分号分隔，每行5列逗号分隔）为 MinuteBar 列表。

    对缺列/空行容错跳过。
    """
    bars: list[MinuteBar] = []
    if not data_str:
        return bars
    for raw_line in data_str.split(";"):
        line = raw_line.strip()
        if not line:
            continue
        cols = line.split(",")
        if len(cols) < 5:
            continue
        try:
            bars.append(MinuteBar(
                time=cols[0],
                price=float(cols[1]),
                amount=float(cols[2]),
                vwap=float(cols[3]),
                volume=float(cols[4]),
            ))
        except (ValueError, IndexError):
            continue
    return bars


def _limit_up_price(pre: float) -> float:
    """涨停价 = round(昨收 * 1.1, 2)。"""
    return round(pre * 1.1, 2)


def _time_to_minutes(hhmm: str) -> int:
    """'0935' → 9*60+35，便于比较时间先后。无效返回 -1。"""
    if not hhmm or len(hhmm) < 4:
        return -1
    try:
        return int(hhmm[:2]) * 60 + int(hhmm[2:4])
    except ValueError:
        return -1


def compute_intraday_metrics(bars: list[MinuteBar], pre: float) -> dict:
    """计算 8.2 的 10 个衍生指标。bars 为空或 pre<=0 返回全 None。"""
    empty = {
        "seal_time": None, "unseal_count": None, "high_time": None,
        "close_vs_vwap": None, "tail_chg": None, "morning_vol_pct": None,
        "intraday_drawdown": None, "is_failed_limit": None,
        "vwap_cross_count": None, "amount_weighted_late": None,
    }
    if not bars or pre <= 0:
        return empty

    limit_price = _limit_up_price(pre)
    close_bar = bars[-1]
    close_price = close_bar.price

    # 用逐分钟现价的累计最高近似当日最高（接口不直接给 high）
    high_price = max(bar.price for bar in bars)
    high_bar = max(bars, key=lambda bar: bar.price)

    # seal_time：首次触及涨停价的时间戳
    seal_time = None
    touched_limit = False
    for bar in bars:
        if bar.price >= limit_price - 1e-6:
            touched_limit = True
            if seal_time is None:
                seal_time = bar.time

    # unseal_count：封板后又跌破涨停价的次数
    unseal_count = 0
    if seal_time is not None:
        below = False
        sealed_once = False
        for bar in bars:
            at_limit = bar.price >= limit_price - 1e-6
            if at_limit:
                sealed_once = True
                below = False
            elif sealed_once and not below:
                unseal_count += 1
                below = True

    # close_vs_vwap：收盘价 / 收盘VWAP - 1
    close_vs_vwap = (close_price / close_bar.vwap - 1) if close_bar.vwap > 0 else None

    # tail_chg：14:45→15:00 涨跌幅
    tail_start = next((bar for bar in bars if _time_to_minutes(bar.time) >= 14 * 60 + 45), None)
    if tail_start and tail_start.price > 0:
        tail_chg = (close_price - tail_start.price) / tail_start.price * 100
    else:
        tail_chg = None

    # morning_vol_pct：9:30~10:30 成交量 / 全天
    total_vol = sum(bar.volume for bar in bars)
    morning_vol = sum(bar.volume for bar in bars
                      if _time_to_minutes(bar.time) <= 10 * 60 + 30)
    morning_vol_pct = (morning_vol / total_vol * 100) if total_vol > 0 else None

    # intraday_drawdown：(最高 - 收盘) / 最高
    intraday_drawdown = ((high_price - close_price) / high_price * 100) if high_price > 0 else None

    # is_failed_limit：盘中触涨停但收盘未封且收绿（收盘<昨收）
    is_failed_limit = bool(
        touched_limit
        and close_price < limit_price - 1e-6
        and close_price < pre
    )

    # vwap_cross_count：现价上下穿越累计VWAP的次数
    vwap_cross_count = 0
    prev_side = None
    for bar in bars:
        if bar.vwap <= 0:
            continue
        side = 1 if bar.price >= bar.vwap else -1
        if prev_side is not None and side != prev_side:
            vwap_cross_count += 1
        prev_side = side

    # amount_weighted_late：14:00 后成交额占全天比
    total_amount = sum(bar.amount for bar in bars)
    late_amount = sum(bar.amount for bar in bars
                      if _time_to_minutes(bar.time) >= 14 * 60)
    amount_weighted_late = (late_amount / total_amount * 100) if total_amount > 0 else None

    return {
        "seal_time": seal_time,
        "unseal_count": unseal_count if seal_time is not None else None,
        "high_time": high_bar.time,
        "close_vs_vwap": round(close_vs_vwap, 4) if close_vs_vwap is not None else None,
        "tail_chg": round(tail_chg, 2) if tail_chg is not None else None,
        "morning_vol_pct": round(morning_vol_pct, 1) if morning_vol_pct is not None else None,
        "intraday_drawdown": round(intraday_drawdown, 2) if intraday_drawdown is not None else None,
        "is_failed_limit": is_failed_limit,
        "vwap_cross_count": vwap_cross_count,
        "amount_weighted_late": round(amount_weighted_late, 1) if amount_weighted_late is not None else None,
    }


def classify_path_shape(metrics: dict, open_chg: Optional[float], day_chg: Optional[float]) -> str:
    """8.3 把真实分时路径归为 5 类形态。

    open_chg / day_chg 单位为百分比。缺关键数据返回 "unknown"。
    """
    if open_chg is None or day_chg is None:
        return "unknown"

    high_minutes = _time_to_minutes(metrics.get("high_time") or "")
    close_vs_vwap = metrics.get("close_vs_vwap")
    drawdown = metrics.get("intraday_drawdown")
    tail_chg = metrics.get("tail_chg")

    is_high_open = open_chg > 0
    is_low_open = open_chg < 0
    early_peak = 0 <= high_minutes < 9 * 60 + 45
    above_vwap = close_vs_vwap is not None and close_vs_vwap > 0
    below_vwap = close_vs_vwap is not None and close_vs_vwap < 0
    big_drawdown = drawdown is not None and drawdown >= 3.0

    # 窄幅震荡：振幅小、回撤小
    if (drawdown is not None and drawdown < 1.0) and abs(day_chg) < 1.0:
        return "narrow_range"

    if is_high_open:
        if early_peak and below_vwap and big_drawdown:
            return "high_open_low_close"
        if above_vwap:
            return "high_open_strong"
        # 高开但盘中转弱（收在均价下或大回撤）
        if below_vwap or big_drawdown:
            return "high_open_low_close"
        return "high_open_strong"

    if is_low_open:
        if day_chg > 0 and (tail_chg is None or tail_chg >= 0):
            return "low_open_red"
        if below_vwap:
            return "low_open_low_close"
        return "low_open_red" if day_chg > 0 else "low_open_low_close"

    # 平开：按收盘方向归类
    return "high_open_strong" if day_chg > 0 else "low_open_low_close"


# 8.3 裁定查表：(归一化方向, 路径形态) → intraday_verdict
_VERDICT_TABLE = {
    ("bull", "high_open_strong"): "✅ 真对",
    ("bull", "high_open_low_close"): "❌ 高开低走陷阱",
    ("bull", "low_open_red"): "⚠️ 蒙对",
    ("bull", "low_open_low_close"): "❌ 完全错",
    ("bear", "high_open_low_close"): "✅ 真对",
    ("bear", "low_open_low_close"): "✅ 真对",
    ("bear", "high_open_strong"): "❌ 看空踏空",
    ("bear", "low_open_red"): "❌ 看空踏空",
}


def judge_intraday_verdict(expected_direction: Optional[str], path_shape: str) -> str:
    """用「预测方向 × 真实路径形态」查表给出路径级裁定。

    中性预测 / 未知形态 → "—（无裁定）"。
    表中未覆盖的组合（如窄幅震荡）→ "～ 中性路径"。
    """
    direction = normalize_direction(expected_direction)
    if direction == "neutral" or path_shape in ("unknown",):
        return "—（无裁定）"
    if path_shape == "narrow_range":
        return "～ 窄幅震荡(方向不明显)"
    return _VERDICT_TABLE.get((direction, path_shape), "～ 中性路径")


# ── M2：路径级裁定的真实准确率聚合 ──

# verdict → 是否计入「真对」「错误」「蒙对」
_VERDICT_TRUE = "✅ 真对"
_VERDICT_LUCKY = "⚠️ 蒙对"
_VERDICT_ERRORS = ("❌ 高开低走陷阱", "❌ 完全错", "❌ 看空踏空")
# 不计入准确率的裁定（中性/无数据/窄幅）
_VERDICT_EXCLUDED_PREFIX = ("—", "～")

# path_shape → 错误环节归因
_SHAPE_TO_ERROR_STAGE = {
    "high_open_low_close": "盘中/尾盘跳水",
    "low_open_low_close": "开盘判错",
    "low_open_red": "开盘判错",
}


def aggregate_intraday_verdicts(rows: list[dict]) -> dict:
    """用 intraday_verdict 重算真实准确率 + 错误环节归因。

    rows：intraday_review 表的若干行（dict，含 intraday_verdict / path_shape）。
    真实准确率 = 真对 / (真对 + 蒙对 + 各类错误)。蒙对单列（结果对但开盘判错，不算高质量）。
    返回各计数 + 真实准确率 + 错误环节分布。
    """
    true_count = lucky_count = error_count = 0
    excluded = 0
    error_stage: dict[str, int] = {}
    for row in rows:
        verdict = row.get("intraday_verdict") or ""
        if verdict.startswith(_VERDICT_EXCLUDED_PREFIX):
            excluded += 1
            continue
        if verdict == _VERDICT_TRUE:
            true_count += 1
        elif verdict == _VERDICT_LUCKY:
            lucky_count += 1
        elif verdict in _VERDICT_ERRORS:
            error_count += 1
            stage = _SHAPE_TO_ERROR_STAGE.get(row.get("path_shape"), "其他")
            error_stage[stage] = error_stage.get(stage, 0) + 1
        else:
            excluded += 1

    judged = true_count + lucky_count + error_count
    true_accuracy = round(true_count / judged * 100, 1) if judged else None
    # 含蒙对的「宽口径」准确率（结果对就算，对应旧收盘价口径）
    loose_accuracy = round((true_count + lucky_count) / judged * 100, 1) if judged else None

    return {
        "judged_total": judged,
        "true_count": true_count,
        "lucky_count": lucky_count,
        "error_count": error_count,
        "true_accuracy": true_accuracy,
        "loose_accuracy": loose_accuracy,
        "excluded_count": excluded,
        "error_stage": error_stage,
    }


def build_intraday_review_row(
    review_date: str,
    score_date: str,
    symbol: str,
    bars: list[MinuteBar],
    pre: float,
    expected_direction: Optional[str],
    open_chg: Optional[float],
    day_chg: Optional[float],
) -> dict:
    """组装单只票写入 intraday_review 表的一行（衍生指标 + 路径裁定）。"""
    metrics = compute_intraday_metrics(bars, pre)
    path_shape = classify_path_shape(metrics, open_chg, day_chg)
    verdict = judge_intraday_verdict(expected_direction, path_shape)
    row = {
        "review_date": review_date,
        "score_date": score_date,
        "symbol": symbol,
        "expected_direction": expected_direction,
        "path_shape": path_shape,
        "intraday_verdict": verdict,
    }
    row.update(metrics)
    return row
