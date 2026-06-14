"""全市场砖形图定式选股 - 完整版（含评分拆解 + 指标数据）

可直接运行（CLI with argparse），也可被其他模块 import 调用。

v3 — 合并自 screen_stocks.py + screen_full.py
  - 保留 screen_full.py 的评分拆解 + 分组配额 + JSON 落盘
  - 吸收 screen_stocks.py 的函数封装 + CLI 参数 + format_results
  - 新增 source 字段标记自动/手动选股（每周复盘用）
"""

import sys
import time
import sqlite3
import json
import argparse
from pathlib import Path
from datetime import datetime

import numpy as np
import pandas as pd

PROJECT = Path('/opt/data/workspace/trader')
sys.path.insert(0, str(PROJECT))

from core.screening.brick_pattern import screen_single_stock
from core.screening.brick_pattern.helpers import _calc_indicators
from core.screening.brick_pattern.scoring import (
    compute_common_quality_score,
    compute_macd_auxiliary_score,
    compute_signal_strength_score,
    compute_p3_bonus,
)
from core.screening.brick_pattern.scoring_risk import compute_risk_penalty
from core.screening.brick_pattern.postprocess import (
    build_industry_perf,
    sector_penalty,
    limit_up_quality,
)
from core.models.brick_pattern import PatternType

DEFAULT_DB = str(PROJECT / 'db' / 'market.db')
MIN_SCORE = 45.0      # 精选门槛
GROUP_QUOTA = {
    "limit_up": 6,          # 🔴 涨停组
    "strong_unsealed": 8,   # 🟡 强势未封板组
    "normal": 6,            # ⚪ 普通组
}
OUTPUT_DIR = Path('/opt/data/output/screening_raw')

enabled_patterns = (
    PatternType.N_SHAPE_JUMP,
    PatternType.SIDEWAYS_JUMP,
    PatternType.UPTREND_CONTINUE,
)

pattern_cn = {
    "N_SHAPE_JUMP": "N型起跳",
    "SIDEWAYS_JUMP": "横盘起跳",
    "UPTREND_CONTINUE": "上升波段延续",
}


def get_latest_trade_date(conn: sqlite3.Connection) -> str:
    cur = conn.execute("SELECT MAX(date) FROM stock_daily")
    r = cur.fetchone()
    return r[0] if r and r[0] else datetime.today().strftime("%Y-%m-%d")


def get_prev_trade_date(conn: sqlite3.Connection, target: str) -> str | None:
    r = conn.execute(
        "SELECT MAX(date) FROM stock_daily WHERE date < ?", (target,)
    ).fetchone()
    return r[0] if r and r[0] else None


def run_screening(
    date: str | None = None,
    db_path: str | None = None,
    min_score: float = MIN_SCORE,
    limit: int = 0,
    source: str = "auto",
) -> list[dict]:
    """全市场砖形图定式选股，返回结果列表。

    Parameters
    ----------
    date : str | None
        目标日期 YYYY-MM-DD，None=最新交易日
    db_path : str | None
        数据库路径，None=默认 market.db
    min_score : float
        精选门槛（最终分低于此值过滤掉），默认 45.0
    limit : int
        最多返回数量，0=不限（JSON 落盘始终是全量配额结果）
    source : str
        "auto"=定时自动选股，"manual"=手动触发（每周复盘区分用）

    Returns
    -------
    list[dict]
        排序后的结果列表（含完整评分拆解 + 指标数据）
        side effect: 打印进度 + 保存 JSON 落盘
    """
    db = db_path or DEFAULT_DB
    conn = sqlite3.connect(db, timeout=30)

    target = date or get_latest_trade_date(conn)
    prev_date = get_prev_trade_date(conn, target)

    print(f"扫描日期: {target} | 来源: {source}")

    ind_today, ind_prev, top3_prev = build_industry_perf(conn, target, prev_date)

    stock_rows = conn.execute(
        "SELECT sl.symbol, sl.name, sl.industry FROM stock_list sl "
        "WHERE sl.symbol IN (SELECT DISTINCT symbol FROM stock_daily) "
        "ORDER BY sl.symbol"
    ).fetchall()

    results = []
    errors = 0
    t0 = time.perf_counter()
    total = len(stock_rows)

    for idx, (symbol, name, industry) in enumerate(stock_rows, 1):
        df = pd.read_sql_query(
            "SELECT date, open, close, high, low, volume, turnover_rate "
            "FROM stock_daily WHERE symbol = ? ORDER BY date",
            conn,
            params=(symbol,),
        )
        if df.empty or len(df) < 10:
            continue
        df["date"] = pd.to_datetime(df["date"])
        for c in ["open", "high", "low", "close", "volume", "turnover_rate"]:
            df[c] = pd.to_numeric(df[c], errors="coerce")
        df = df.dropna(subset=["date", "open", "high", "low", "close"]).reset_index(drop=True)
        dates = df["date"].dt.strftime("%Y-%m-%d")
        if target not in dates.values:
            continue
        day_index = int(dates[dates == target].index[0])

        match = screen_single_stock(
            df=df,
            index=day_index,
            symbol=symbol,
            name=name,
            target_date=target,
            actual_date=target,
            enabled_patterns=enabled_patterns,
        )

        if match.final_matched:
            # 评分拆解
            indicators = _calc_indicators(df)
            ptype = (
                PatternType(match.matched_pattern)
                if match.matched_pattern
                else PatternType.N_SHAPE_JUMP
            )
            q_score, q_items = compute_common_quality_score(indicators, day_index, ptype)
            m_score, m_items = compute_macd_auxiliary_score(indicators, day_index, ptype)
            s_score, s_items = compute_signal_strength_score(indicators, day_index)
            p_score, p_items = compute_p3_bonus(indicators, day_index, ptype)
            r_penalty, r_items, _ = compute_risk_penalty(indicators, day_index, ptype)

            prev_close = (
                float(df["close"].iloc[day_index - 1])
                if day_index > 0
                else float(df["close"].iloc[day_index])
            )
            day_change = (
                (float(df["close"].iloc[day_index]) - prev_close) / prev_close * 100
            )
            vol_30_start = max(0, day_index - 30)
            avg_vol_30 = float(df["volume"].iloc[vol_30_start : day_index + 1].mean()) / 10000

            is_limit_up = day_change >= 9.5

            # 5日累计涨幅
            if day_index >= 5 and float(df["close"].iloc[day_index - 5]) > 0:
                close_5d_ago = float(df["close"].iloc[day_index - 5])
                cum_chg_5d = (
                    (float(df["close"].iloc[day_index]) - close_5d_ago)
                    / close_5d_ago
                    * 100
                )
            else:
                cum_chg_5d = 0.0

            # T1 板块动量衰减扣分
            sec_pen, sec_flags = sector_penalty(
                industry, is_limit_up, ind_today, ind_prev, top3_prev
            )
            base_score = round(match.final_score, 1) if match.final_score else 0
            adjusted_score = round(max(0, base_score + sec_pen), 1)

            # T4 涨停质量
            ind_today_chg = ind_today.get(industry, 0.0)
            vol_ratio_val = round(
                float(df["volume"].iloc[day_index]) / 10000 / max(avg_vol_30, 0.01), 2
            )
            lu_quality = (
                limit_up_quality(
                    vol_ratio_val,
                    float(indicators["brick"][day_index]),
                    ind_today_chg,
                    cum_chg_5d,
                )
                if is_limit_up
                else None
            )

            # T5 分组
            if is_limit_up:
                group = "limit_up"
            elif 3.0 <= day_change < 9.5:
                group = "strong_unsealed"
            else:
                group = "normal"

            results.append(
                {
                    "symbol": symbol,
                    "name": name,
                    "industry": industry,
                    "pattern": pattern_cn.get(str(match.matched_pattern), "未知"),
                    "score": adjusted_score,
                    "sector_penalty": sec_pen,
                    "sector_flags": sec_flags,
                    "limit_up_quality": lu_quality,
                    "cum_chg_5d": round(cum_chg_5d, 2),
                    "group": group,
                    "grade": match.grade or "",
                    "summary": match.format_summary(),
                    "close": round(float(df["close"].iloc[day_index]), 2),
                    "day_change": round(day_change, 2),
                    "is_limit_up": is_limit_up,
                    "vol": round(float(df["volume"].iloc[day_index]) / 10000, 2),
                    "avg_vol_30": round(avg_vol_30, 2),
                    "vol_ratio": vol_ratio_val,
                    "turnover_rate": (
                        round(float(df["turnover_rate"].iloc[day_index]), 2)
                        if pd.notna(df["turnover_rate"].iloc[day_index])
                        else None
                    ),
                    # 裸分拆解
                    "q_score": q_score,
                    "q_items": q_items,
                    "m_score": m_score,
                    "m_items": m_items,
                    "s_score": s_score,
                    "s_items": s_items,
                    "p_score": p_score,
                    "p_items": p_items,
                    "r_penalty": r_penalty,
                    "r_items": r_items,
                    "raw_total": round(q_score + m_score + s_score + p_score - r_penalty, 1),
                    # 关键指标
                    "diff": round(float(indicators["macd_diff"][day_index]), 4),
                    "dea": round(float(indicators["macd_dea"][day_index]), 4),
                    "hist": round(float(indicators["macd_hist"][day_index]), 4),
                    "brick_val": round(float(indicators["brick"][day_index]), 4),
                    "st_val": round(float(indicators["short_trend"][day_index]), 4),
                    "ls_val": round(float(indicators["long_short"][day_index]), 4),
                    "open": round(float(df["open"].iloc[day_index]), 2),
                    "high": round(float(df["high"].iloc[day_index]), 2),
                    "low": round(float(df["low"].iloc[day_index]), 2),
                    "prev_close": round(prev_close, 2),
                    # 来源标记（每周复盘用）
                    "source": source,
                }
            )

        if match.error:
            errors += 1

        if idx % 500 == 0:
            print(
                f"  [{idx}/{total}] {len(results)} matches, "
                f"{time.perf_counter() - t0:.0f}s",
                flush=True,
            )

    # T2 普跌日自动降权
    market_avg = conn.execute(
        "SELECT AVG((d.close - p.close) / p.close * 100) "
        "FROM stock_daily d JOIN stock_daily p ON d.symbol = p.symbol "
        "WHERE d.date = ? AND p.date = ("
        "  SELECT MAX(date) FROM stock_daily WHERE symbol = d.symbol AND date < ?)",
        (target, target),
    ).fetchone()[0] or 0.0
    is_bearish_day = market_avg < -1.0

    conn.close()

    if is_bearish_day:
        for r in results:
            r["score"] = round(r["score"] * 0.85, 1)
            r["regime_flag"] = f"普跌日(均涨{market_avg:.2f}%)-置信度降一档"

    results.sort(key=lambda r: r["score"], reverse=True)

    # 精选门槛
    results = [r for r in results if r["score"] >= min_score]

    # 分组配额
    all_grouped = {
        "limit_up": [r for r in results if r["group"] == "limit_up"],
        "strong_unsealed": [r for r in results if r["group"] == "strong_unsealed"],
        "normal": [r for r in results if r["group"] == "normal"],
    }

    def _rank_within_group(rows, group_name):
        quota = GROUP_QUOTA.get(group_name, len(rows))
        if group_name == "limit_up":
            quality_rank = {"strong": 0, "neutral": 1, "weak": 2}
            rows = sorted(
                rows,
                key=lambda r: (quality_rank.get(r.get("limit_up_quality"), 1), -r["score"]),
            )
        else:
            rows = sorted(rows, key=lambda r: r["score"], reverse=True)
        return rows[:quota]

    grouped = {
        name: _rank_within_group(rows, name) for name, rows in all_grouped.items()
    }

    top_results = sorted(
        [r for rows in grouped.values() for r in rows],
        key=lambda r: r["score"],
        reverse=True,
    )

    # 落盘 JSON（始终保存全量配额结果）
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    filename = target
    with open(OUTPUT_DIR / f"{filename}.json", "w") as f:
        json.dump(
            {
                "date": target,
                "market_avg": round(market_avg, 2),
                "is_bearish_day": is_bearish_day,
                "group_counts": {k: len(v) for k, v in grouped.items()},
                "results": top_results,
            },
            f,
            ensure_ascii=False,
            indent=2,
            default=str,
        )

    print(
        f"\n扫描完成: {total}只 → {len(results)}只命中, "
        f"错误 {errors}只, {time.perf_counter() - t0:.0f}s"
    )
    print(f"原始结果已保存: {OUTPUT_DIR / f'{filename}.json'}")

    # limit 截断（在 JSON 落盘之后，不影响存档完整性）
    if limit > 0:
        top_results = top_results[:limit]

    return top_results


def format_results(results: list[dict]) -> str:
    """格式化输出字符串（微信/CLI 友好）。"""
    if not results:
        return "无匹配股票"

    grouped = {
        "limit_up": [r for r in results if r.get("group") == "limit_up"],
        "strong_unsealed": [r for r in results if r.get("group") == "strong_unsealed"],
        "normal": [r for r in results if r.get("group") == "normal"],
    }

    lines = []

    def _add_group(title, rows, show_quality=False):
        if not rows:
            return
        lines.append(f"\n{title}（{len(rows)}只）")
        header = f"{'代码':<8} {'名称':<8} {'评分':<5} {'等级':<4} {'涨跌%':<7} {'行业':<10}"
        if show_quality:
            header += " 涨停质量"
        lines.append(header)
        lines.append("-" * (48 + (16 if show_quality else 0)))
        for r in rows:
            line = (
                f"{r['symbol']:<8} {r['name']:<8} "
                f"{r['score']:<5.1f} {r['grade']:<4} {r['day_change']:>+6.2f}% {r.get('industry', ''):<10}"
            )
            if show_quality:
                q = r.get("limit_up_quality")
                if q == "strong":
                    line += " ✅强"
                elif q == "neutral":
                    line += " ～中"
                elif q == "weak":
                    line += " ⚠️弱"
                else:
                    line += "  —"
            lines.append(line)

    _add_group(
        "🔴 涨停组（次日方向看 limit_up_quality，不直接信偏多）",
        grouped["limit_up"],
        show_quality=True,
    )
    _add_group(
        "🟡 强势未封板组（3%~9.5%，次日方向可信度高于涨停组）",
        grouped["strong_unsealed"],
    )
    _add_group("⚪ 普通组", grouped["normal"])

    # 等级分布
    grades = {}
    for r in results:
        g = r.get("grade", "")
        grades[g] = grades.get(g, 0) + 1
    gc = ", ".join(f"{g}级{x}" for g, x in sorted(grades.items()) if g)
    if gc:
        lines.append(f"\n等级分布: {gc}")

    # 普跌日提示
    if any(r.get("regime_flag") for r in results):
        flag = next(r["regime_flag"] for r in results if r.get("regime_flag"))
        lines.append(f"\n⚠️ {flag}")

    return "\n".join(lines)


def _print_group(title, rows, show_quality=False):
    """终端直接打印版（保留 screen_full.py 原有输出风格）。"""
    if not rows:
        return
    print(f"\n{title}（{len(rows)}只）")
    header = (
        f"{'代码':<8} {'名称':<10} {'定式':<14} {'评分':<6} "
        f"{'等级':<4} {'涨跌%':<8} {'成交额(亿)':<10}"
    )
    if show_quality:
        header += " 涨停质量"
    print(header)
    print("-" * (72 + (10 if show_quality else 0)))
    for r in rows:
        line = (
            f"{r['symbol']:<8} {r['name']:<10} {r['pattern']:<14} "
            f"{r['score']:<6.1f} {r['grade']:<4} {r['day_change']:>+7.2f}% {r['vol']:>9.2f}"
        )
        if show_quality:
            quality = r.get("limit_up_quality")
            if quality == "weak":
                line += "  ⚠️ 弱(次日防高开低走)"
            elif quality == "strong":
                line += "  ✅ 强"
            else:
                line += f"  {quality or ''}"
        print(line)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="砖形图定式选股 - 完整版 v3")
    parser.add_argument("--date", default="", help="目标日期 YYYY-MM-DD（默认最新交易日）")
    parser.add_argument("--limit", type=int, default=0, help="最多返回数量（默认不限；JSON 落盘始终是全量）")
    parser.add_argument("--db", default="", help="数据库路径（默认 market.db）")
    parser.add_argument(
        "--min-score", type=float, default=MIN_SCORE, help=f"最低评分门槛（默认 {MIN_SCORE}）"
    )
    parser.add_argument(
        "--source", default="auto", choices=["auto", "manual"], help="来源标记（每周复盘区分用）"
    )
    args = parser.parse_args()

    t0 = time.perf_counter()
    date = args.date if args.date else None
    db = args.db if args.db else None

    results = run_screening(
        date=date,
        db_path=db,
        min_score=args.min_score,
        limit=args.limit,
        source=args.source,
    )

    elapsed = time.perf_counter() - t0
    print(format_results(results))
    print(f"\n总耗时: {elapsed:.0f}s")
