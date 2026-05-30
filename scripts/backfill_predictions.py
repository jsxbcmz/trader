"""T6 预测正确率回填脚本。

读 output/screening_predictions/{T}.json 的 stocks[]，用 market.db 查 T+1 实际行情，
回填 open_chg / day_chg / open_correct / close_correct / sector_reverted 等字段。

用法：
    python scripts/backfill_predictions.py            # 回填所有可回填的历史预测
    python scripts/backfill_predictions.py 2026-05-26 # 只回填指定预测日

注意（2026-05-30）：market.db 日线目前只到 2026-05-22，05-23 之后无数据，
故 05-25 及以后的预测暂时回填不出 T+1 行情（会跳过并提示），等数据补齐后重跑即可。
"""

import sys
import json
import sqlite3
from pathlib import Path

PROJECT = Path('/opt/data/workspace/trader')
sys.path.insert(0, str(PROJECT))

from core.scoring.prediction_review import build_backfill_fields

DB_PATH = '/opt/data/workspace/trader/db/market.db'
PRED_DIR = PROJECT / 'output' / 'screening_predictions'


def _next_trading_date(conn, target_date):
    """返回 market.db 中 target_date 之后的第一个交易日，无则 None。"""
    row = conn.execute(
        "SELECT MIN(date) FROM stock_daily WHERE date > ?", (target_date,)
    ).fetchone()
    return row[0] if row else None


def _daily_change(conn, symbol, date):
    """返回 (open_chg%, day_chg%)：相对前一交易日收盘的开盘/收盘涨跌幅。缺数据返回 (None, None)。"""
    row = conn.execute(
        "SELECT open, close FROM stock_daily WHERE symbol = ? AND date = ?",
        (symbol, date),
    ).fetchone()
    if not row:
        return None, None
    open_px, close_px = float(row[0]), float(row[1])
    prev = conn.execute(
        "SELECT close FROM stock_daily WHERE symbol = ? AND date = ("
        "  SELECT MAX(date) FROM stock_daily WHERE symbol = ? AND date < ?)",
        (symbol, symbol, date),
    ).fetchone()
    if not prev or float(prev[0]) <= 0:
        return None, None
    prev_close = float(prev[0])
    open_chg = (open_px - prev_close) / prev_close * 100
    day_chg = (close_px - prev_close) / prev_close * 100
    return open_chg, day_chg


def _industry_avg_change(conn, industry, date):
    """返回指定行业在 date 当日的平均涨跌幅%，无数据返回 None。"""
    if not industry:
        return None
    row = conn.execute(
        "SELECT AVG((d.close - p.close) / p.close * 100) "
        "FROM stock_daily d "
        "JOIN stock_daily p ON d.symbol = p.symbol "
        "JOIN stock_list sl ON d.symbol = sl.symbol "
        "WHERE sl.industry = ? AND d.date = ? AND p.date = ("
        "  SELECT MAX(date) FROM stock_daily WHERE symbol = d.symbol AND date < ?)",
        (industry, date, date),
    ).fetchone()
    return float(row[0]) if row and row[0] is not None else None


def _stock_industry(conn, symbol, fallback):
    """优先用预测里自带的 industry，否则查 stock_list。"""
    if fallback:
        return fallback
    row = conn.execute(
        "SELECT industry FROM stock_list WHERE symbol = ?", (symbol,)
    ).fetchone()
    return row[0] if row else None


def backfill_one(conn, pred_date):
    """回填单个预测日文件，返回 (回填条数, 跳过原因或None)。"""
    path = PRED_DIR / f'{pred_date}.json'
    if not path.exists():
        return 0, f"预测文件不存在: {path}"
    with path.open('r', encoding='utf-8') as f:
        data = json.load(f)

    stocks = data.get('stocks', [])
    if not stocks:
        return 0, "无 stocks"

    next_day = _next_trading_date(conn, pred_date)
    if not next_day:
        return 0, f"market.db 无 {pred_date} 之后的交易日（数据未补齐）"

    filled = 0
    for stock in stocks:
        symbol = stock.get('symbol')
        if not symbol:
            continue
        open_chg, day_chg = _daily_change(conn, symbol, next_day)
        industry = _stock_industry(conn, symbol, stock.get('industry'))
        prev_sector = _industry_avg_change(conn, industry, pred_date)
        next_sector = _industry_avg_change(conn, industry, next_day)

        fields = build_backfill_fields(
            stock.get('expected_direction'),
            open_chg, day_chg, prev_sector, next_sector,
        )
        stock.update(fields)
        if open_chg is not None:
            filled += 1

    data['backfilled_against'] = next_day
    with path.open('w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return filled, None


def main():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    targets = sys.argv[1:] if len(sys.argv) > 1 else [
        p.stem for p in sorted(PRED_DIR.glob('2026-*.json'))
        if not p.stem.startswith('review')
    ]
    for pred_date in targets:
        filled, skip = backfill_one(conn, pred_date)
        if skip:
            print(f"  [跳过] {pred_date}: {skip}")
        else:
            print(f"  [回填] {pred_date}: {filled} 只成功写入 T+1 行情")
    conn.close()


if __name__ == '__main__':
    main()
