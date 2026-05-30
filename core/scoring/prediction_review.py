"""T6 预测正确率回填 — 核心判定逻辑（纯函数，便于单测）。

把 screening_predictions/{T}.json 的 expected_direction 与 T+1 实际行情比对，
判定 open_correct（开盘方向对）/ close_correct（收盘方向对），并判 sector_reverted（板块V反）。

设计要点：
- expected_direction 取值很多（"惯性高开"/"高开低走"/"震荡偏多"/"偏空"…），
  统一归一化为 bull(看涨) / bear(看跌) / neutral(中性，不计入准确率)。
- open/close 双判定，对应文档第八章「路径级」思路的前身。
"""

from __future__ import annotations

from typing import Optional

# 明确的看跌信号词（优先级最高，含即判 bear）
_BEAR_KEYWORDS = ("低走", "回落", "下跌", "看空", "偏空", "偏弱", "回调")
# 看涨信号词
_BULL_KEYWORDS = ("偏多", "高开", "冲高", "续涨", "惯性", "上行", "偏强", "跟涨")
# 纯中性 / 不表态词
_NEUTRAL_KEYWORDS = ("中性", "谨慎", "观察")


def normalize_direction(expected_direction: Optional[str]) -> str:
    """把任意 expected_direction 文本归一化为 bull / bear / neutral。

    规则（按优先级）：
    1. 含明确看跌词（低走/回落/下跌/看空/偏空…）→ bear
       （即使同时含"高开"，如"高开低走"也判 bear，因为落脚点是跌）
    2. 否则含看涨词（偏多/冲高/续涨…）→ bull
    3. 否则 → neutral（中性/谨慎/观察 或无法识别）
    """
    if not expected_direction:
        return "neutral"
    text = str(expected_direction)
    if any(kw in text for kw in _BEAR_KEYWORDS):
        return "bear"
    if any(kw in text for kw in _BULL_KEYWORDS):
        return "bull"
    return "neutral"


def judge_direction_correct(
    expected_direction: Optional[str],
    open_chg: Optional[float],
    day_chg: Optional[float],
) -> tuple[Optional[bool], Optional[bool]]:
    """判定 (open_correct, close_correct)。

    open_chg / day_chg 单位为百分比（如 +2.99 表示 +2.99%）。
    - bull：open_chg > 0 视为开盘对；day_chg > 0 视为收盘对
    - bear：open_chg < 0 视为开盘对；day_chg < 0 视为收盘对
    - neutral：方向不表态，返回 (None, None) 不计入准确率
    缺数据（None）对应位返回 None。
    """
    direction = normalize_direction(expected_direction)
    if direction == "neutral":
        return None, None

    def _correct(chg):
        if chg is None:
            return None
        if direction == "bull":
            return chg > 0
        return chg < 0

    return _correct(open_chg), _correct(day_chg)


def judge_sector_reverted(
    prev_sector_chg: Optional[float],
    next_sector_chg: Optional[float],
    strong_threshold: float = 4.0,
    revert_threshold: float = -2.0,
) -> Optional[bool]:
    """判定板块是否 V 型反转。

    prev_sector_chg：预测日 T 当天该行业均涨；next_sector_chg：T+1 当天该行业均涨。
    昨日强势(>strong_threshold) 且 次日转跌(<revert_threshold) → True。
    任一缺失 → None。
    """
    if prev_sector_chg is None or next_sector_chg is None:
        return None
    return prev_sector_chg > strong_threshold and next_sector_chg < revert_threshold


def build_backfill_fields(
    expected_direction: Optional[str],
    open_chg: Optional[float],
    day_chg: Optional[float],
    prev_sector_chg: Optional[float] = None,
    next_sector_chg: Optional[float] = None,
) -> dict:
    """组装单只股票的回填字段字典。"""
    open_correct, close_correct = judge_direction_correct(
        expected_direction, open_chg, day_chg)
    return {
        "open_chg": round(open_chg, 2) if open_chg is not None else None,
        "day_chg": round(day_chg, 2) if day_chg is not None else None,
        "direction_norm": normalize_direction(expected_direction),
        "open_correct": open_correct,
        "close_correct": close_correct,
        "sector_reverted": judge_sector_reverted(prev_sector_chg, next_sector_chg),
    }


def aggregate_accuracy(stocks: list[dict]) -> dict:
    """统计一批已回填的 stocks 的方向准确率。

    只统计已回填（open_correct / close_correct 非 None，即非中性且有 T+1 行情）的样本。
    返回各项计数与准确率（无样本时准确率为 None，不写 0 以免误导）。
    """
    open_total = open_hit = 0
    close_total = close_hit = 0
    neutral = 0
    not_filled = 0
    for stock in stocks:
        open_correct = stock.get("open_correct")
        close_correct = stock.get("close_correct")
        direction = stock.get("direction_norm") or normalize_direction(
            stock.get("expected_direction"))
        if direction == "neutral":
            neutral += 1
            continue
        if open_correct is None and close_correct is None:
            not_filled += 1
            continue
        if open_correct is not None:
            open_total += 1
            open_hit += 1 if open_correct else 0
        if close_correct is not None:
            close_total += 1
            close_hit += 1 if close_correct else 0

    def _rate(hit, total):
        return round(hit / total * 100, 1) if total else None

    return {
        "open_total": open_total,
        "open_hit": open_hit,
        "open_accuracy": _rate(open_hit, open_total),
        "close_total": close_total,
        "close_hit": close_hit,
        "close_accuracy": _rate(close_hit, close_total),
        "neutral_count": neutral,
        "not_filled_count": not_filled,
    }
