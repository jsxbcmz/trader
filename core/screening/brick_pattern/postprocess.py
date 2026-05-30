"""砖形图选股「后处理层」优化函数。

这些优化作用于扫描脚本的最终分 score（不进 scoring.py/scoring_risk.py 的评分函数），
因为它们需要行业 / 大盘维度数据，而评分函数的 indicators 入参只有单只股票 K 线。

- build_industry_perf：构建行业今日/昨日均涨 + 昨日Top3（需 DB 连接）
- sector_penalty：板块动量衰减扣分（T1）
- limit_up_quality：涨停次日质量标签（T4，不进 score 仅预警）
"""

from __future__ import annotations


def build_industry_perf(conn, target_date, prev_date):
    """构建行业涨幅映射。返回 (今日均涨 dict, 昨日均涨 dict, 昨日Top3行业 set)。"""
    def _ind_avg(date):
        if not date:
            return {}
        rows = conn.execute(
            "SELECT sl.industry, "
            "AVG((d.close - p.close) / p.close * 100) AS chg "
            "FROM stock_daily d "
            "JOIN stock_daily p ON d.symbol = p.symbol "
            "JOIN stock_list sl ON d.symbol = sl.symbol "
            "WHERE d.date = ? AND p.date = ("
            "  SELECT MAX(date) FROM stock_daily WHERE symbol = d.symbol AND date < ?) "
            "GROUP BY sl.industry",
            (date, date),
        ).fetchall()
        return {industry: chg for industry, chg in rows if industry}

    today = _ind_avg(target_date)
    prev = _ind_avg(prev_date)
    top3_prev = set(sorted(prev, key=prev.get, reverse=True)[:3])
    return today, prev, top3_prev


def sector_penalty(industry, is_limit_up, ind_today, ind_prev, top3_prev):
    """板块动量衰减扣分（T1）。返回 (扣分, flags)。

    - 昨日板块极强(>4%) → 次日V型反转高危，-6；若昨日全市场Top3 再 -3
    - 逆板块涨停：板块今日跌>2% 但个股涨停 → 独木难支，-8
    """
    penalty = 0.0
    flags = []
    prev_chg = ind_prev.get(industry)
    today_chg = ind_today.get(industry, 0.0)
    if prev_chg is not None and prev_chg > 4.0:
        penalty -= 6
        flags.append(f"板块昨日领涨{prev_chg:+.1f}%(V反风险)")
        if industry in top3_prev:
            penalty -= 3
            flags.append("昨日全市场Top3板块")
    if today_chg < -2.0 and is_limit_up:
        penalty -= 8
        flags.append(f"逆板块涨停(板块{today_chg:+.1f}%)")
    return penalty, flags


def limit_up_quality(vol_ratio, brick_val, ind_today_chg, cum_chg_5d):
    """涨停次日质量评估（T4，不进 score，仅做次日方向预警）。

    返回 "strong" / "weak" / "neutral"。
    - 缩量锁仓(0.5~1.5量比)优于放量分歧(>3)
    - 砖值高位(>120)风险，低位(<100)健康
    - 板块今日共振(>0)优于逆势(<-2%)
    - 5日已透支(>20%)扣，起涨段(<10%)加
    """
    quality = 0
    quality += 1 if 0.5 <= vol_ratio <= 1.5 else (-1 if vol_ratio > 3 else 0)
    quality += 1 if brick_val < 100 else (-1 if brick_val > 120 else 0)
    quality += 1 if ind_today_chg > 0 else (-1 if ind_today_chg < -2 else 0)
    quality += -1 if cum_chg_5d > 20 else (1 if cum_chg_5d < 10 else 0)
    if quality >= 2:
        return "strong"
    if quality <= -1:
        return "weak"
    return "neutral"
