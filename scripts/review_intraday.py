"""M1 分钟级复盘验证脚本。

流程：
1. 读 output/screening_predictions/{T}.json 的 stocks[]（候选股 + 涨停股）
2. 逐票请求同花顺分时接口，剥 JSONP，解析 5 列分时
3. 算 8.2 衍生指标 → 推 8.3 path_shape → 查表得 intraday_verdict
4. 通过 ScoringDatabase.save_intraday_review() 落库 + 回写预测 json 增加 intraday_verdict
5. 原始分时不落盘

⚠️ 接口只返回「最新交易日」分时（接口名 last.js），必须每天收盘后当天跑，
   错过即永久缺失。脚本会校验接口返回的 date 是否等于 T+1，不一致则跳过避免错配。

用法：
    python scripts/review_intraday.py            # 复盘最近一个有次日数据的预测日
    python scripts/review_intraday.py 2026-05-28 # 复盘指定预测日 T（验证其 T+1 盘中）
"""

import sys
import json
import time
import sqlite3
import urllib.request
from pathlib import Path

PROJECT = Path('/opt/data/workspace/trader')
sys.path.insert(0, str(PROJECT))

from core.data.database import init_databases, get_scoring_db
from core.scoring.intraday_metrics import (
    parse_minute_data,
    build_intraday_review_row,
)

DB_PATH = '/opt/data/workspace/trader/db/market.db'
PRED_DIR = PROJECT / 'output' / 'screening_predictions'
THS_URL = 'https://d.10jqka.com.cn/v6/time/hs_{symbol}/defer/last.js'
HEADERS = {'User-Agent': 'Mozilla/5.0'}
REQUEST_GAP_SECONDS = 0.3


def fetch_intraday(symbol):
    """拉取并剥壳同花顺分时。返回 (pre, date, bars) 或 (None, None, [])。"""
    url = THS_URL.format(symbol=symbol)
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        raw = urllib.request.urlopen(req, timeout=10).read().decode('utf-8', 'ignore')
    except Exception as exc:
        print(f"    [接口失败] {symbol}: {type(exc).__name__} {exc}")
        return None, None, []
    try:
        body = raw[raw.index('(') + 1:raw.rindex(')')]
        obj = json.loads(body)
        node = obj.get(f'hs_{symbol}')
    except (ValueError, json.JSONDecodeError) as exc:
        print(f"    [解析失败] {symbol}: {exc}")
        return None, None, []
    if not node:
        return None, None, []
    pre = float(node.get('pre')) if node.get('pre') not in (None, '') else None
    data_date = node.get('date')
    bars = parse_minute_data(node.get('data', ''))
    return pre, data_date, bars


def _next_trading_date(conn, target_date):
    row = conn.execute(
        "SELECT MIN(date) FROM stock_daily WHERE date > ?", (target_date,)
    ).fetchone()
    return row[0] if row else None


def _daily_change(conn, symbol, date):
    """T+1 当日开盘/收盘相对前一交易日收盘的涨跌幅%。"""
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
    return (open_px - prev_close) / prev_close * 100, (close_px - prev_close) / prev_close * 100


def review_one(conn, pred_date):
    """复盘单个预测日 T 的 T+1 盘中。返回 (落库条数, 跳过原因或None)。"""
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
        return 0, f"market.db 无 {pred_date} 之后的交易日"
    next_day_compact = next_day.replace('-', '')

    rows = []
    for stock in stocks:
        symbol = stock.get('symbol')
        if not symbol:
            continue
        pre, data_date, bars = fetch_intraday(symbol)
        time.sleep(REQUEST_GAP_SECONDS)

        # 校验接口返回的交易日 == T+1，否则错配，标 NULL 不阻塞
        if not bars or pre is None:
            rows.append(_null_row(next_day, pred_date, stock))
            continue
        if data_date and data_date != next_day_compact:
            print(f"    [日期错配] {symbol}: 接口 date={data_date} != T+1 {next_day_compact}，跳过算指标")
            rows.append(_null_row(next_day, pred_date, stock))
            continue

        open_chg, day_chg = _daily_change(conn, symbol, next_day)
        row = build_intraday_review_row(
            review_date=next_day,
            score_date=pred_date,
            symbol=symbol,
            bars=bars,
            pre=pre,
            expected_direction=stock.get('expected_direction'),
            open_chg=open_chg,
            day_chg=day_chg,
        )
        rows.append(row)
        stock['intraday_verdict'] = row['intraday_verdict']
        stock['path_shape'] = row['path_shape']

    get_scoring_db().save_intraday_review(next_day, rows)

    data['intraday_reviewed_against'] = next_day
    with path.open('w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    filled = sum(1 for r in rows if r.get('seal_time') is not None or r.get('high_time') is not None)
    return filled, None


def _null_row(review_date, score_date, stock):
    """接口失败/错配时的占位行（衍生指标全 None，不阻塞落库）。"""
    return {
        "review_date": review_date,
        "score_date": score_date,
        "symbol": stock.get('symbol'),
        "expected_direction": stock.get('expected_direction'),
        "path_shape": "unknown",
        "intraday_verdict": "—（无分时数据）",
        "seal_time": None, "unseal_count": None, "high_time": None,
        "close_vs_vwap": None, "tail_chg": None, "morning_vol_pct": None,
        "intraday_drawdown": None, "is_failed_limit": None,
        "vwap_cross_count": None, "amount_weighted_late": None,
    }


def main():
    init_databases(PROJECT)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    if len(sys.argv) > 1:
        targets = sys.argv[1:]
    else:
        candidates = [
            p.stem for p in sorted(PRED_DIR.glob('2026-*.json'))
            if not p.stem.startswith('review')
        ]
        targets = [d for d in candidates if _next_trading_date(conn, d)][-1:]
    for pred_date in targets:
        filled, skip = review_one(conn, pred_date)
        if skip:
            print(f"  [跳过] {pred_date}: {skip}")
        else:
            print(f"  [复盘] {pred_date} → T+1 落库，{filled} 只有有效分时指标")
    conn.close()


if __name__ == '__main__':
    main()
