"""砖形图定式选股脚本 — CLI/微信通用版

直接从 SQLite 读取数据，不依赖 init_databases()，避免 scoring.db 权限问题。
默认日期=最近交易日，默认模式=砖型图。
"""
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from core.screening.brick_pattern import screen_single_stock
from core.models.brick_pattern import PatternType


def get_latest_trade_date(conn: sqlite3.Connection) -> str:
    cur = conn.execute("SELECT MAX(date) FROM stock_daily")
    r = cur.fetchone()
    return r[0] if r and r[0] else datetime.today().strftime("%Y-%m-%d")


def run_screening(date: str | None = None, pattern: str = "砖型图",
                   min_score: int = 0, limit: int = 20,
                   db_path: str = "") -> list[dict]:
    if db_path:
        db_path = Path(db_path)
    else:
        db_path = PROJECT / "db" / "market.db"
    if not db_path.exists():
        raise FileNotFoundError(f"数据库不存在: {db_path}")

    conn = sqlite3.connect(str(db_path), timeout=30)

    target = date or get_latest_trade_date(conn)

    stock_rows = conn.execute(
        "SELECT sl.symbol, sl.name FROM stock_list sl "
        "WHERE sl.symbol IN (SELECT DISTINCT symbol FROM stock_daily) "
        "ORDER BY sl.symbol"
    ).fetchall()
    total = len(stock_rows)
    print(f"日期: {target} | 模式: {pattern} | 扫描: {total} 只")

    enabled_patterns = (
        PatternType.N_SHAPE_JUMP,
        PatternType.SIDEWAYS_JUMP,
        PatternType.UPTREND_CONTINUE,
    )

    results = []
    errors = 0
    t0 = time.perf_counter()

    for idx, (symbol, name) in enumerate(stock_rows, 1):
        df = pd.read_sql_query(
            "SELECT date, open, close, high, low, volume, turnover_rate "
            "FROM stock_daily WHERE symbol = ? ORDER BY date",
            conn, params=(symbol,)
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
            df=df, index=day_index, symbol=symbol, name=name,
            target_date=target, actual_date=target,
            enabled_patterns=enabled_patterns,
        )

        if match.final_matched:
            score = match.final_score or 0
            if score >= min_score:
                results.append({
                    "symbol": symbol,
                    "name": name,
                    "pattern": match.matched_pattern or "",
                    "score": score,
                    "grade": match.grade or "",
                    "summary": match.format_summary(),
                    "source": "auto",
                })

        if match.error:
            errors += 1

        if idx % 500 == 0:
            elapsed = time.perf_counter() - t0
            print(f"  [{idx}/{total}] 匹配 {len(results)} 只, "
                  f"耗时 {elapsed:.0f}s", flush=True)

    conn.close()
    results.sort(key=lambda r: r["score"], reverse=True)
    if limit > 0:
        results = results[:limit]

    elapsed = time.perf_counter() - t0
    print(f"\n完成: {total} 只, 匹配 {len(results)} 只, "
          f"错误 {errors} 只, 耗时 {elapsed:.0f}s ({elapsed/60:.1f}min)")
    return results


def format_results(results: list[dict]) -> str:
    if not results:
        return "无匹配股票"
    lines = [f"{'代码':<8} {'名称':<10} {'定式':<16} {'评分':<5} {'等级':<4}"]
    lines.append("-" * 50)
    for r in results:
        lines.append(
            f"{r['symbol']:<8} {r['name']:<10} {r.get('pattern',''):<16} "
            f"{r.get('score',0):<5.0f} {r.get('grade',''):<4}"
        )
    grades = {}
    for r in results:
        g = r.get("grade", "")
        grades[g] = grades.get(g, 0) + 1
    gc = ", ".join(f"{g}级{x}" for g, x in sorted(grades.items()) if g)
    lines.append(f"\n等级分布: {gc}")
    return "\n".join(lines)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="砖形图定式选股")
    parser.add_argument("--date", default="", help="目标日期 YYYY-MM-DD")
    parser.add_argument("--pattern", default="砖型图", choices=["砖型图"], help="选股模式")
    parser.add_argument("--min-score", type=int, default=0, help="最低评分")
    parser.add_argument("--limit", type=int, default=20, help="最多返回")
    parser.add_argument("--db", default="", help="数据库路径")
    args = parser.parse_args()

    date = args.date if args.date else None
    results = run_screening(date, args.pattern, args.min_score, args.limit, args.db)
    print(format_results(results))
