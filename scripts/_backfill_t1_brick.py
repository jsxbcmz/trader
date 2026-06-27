"""T+1 砖值回填脚本 (方案G / OPT-W24-03)

功能：取前一日选股池的所有股票，用当日(T+1)收盘价计算砖值变化，
     判断红砖/绿砖，并将结果回填到 screening_predictions JSON 中。

用法：
    python scripts/_backfill_t1_brick.py [date]
    
    date: 要回填的选股日（即 predictions 文件日期），格式 YYYY-MM-DD
          不传则自动取最近一个有 predictions 但无 t1 数据的日期

依赖：
    - db/market.db (stock_daily 表)
    - output/screening_predictions/{date}.json
    - core/indicators/algorithms.py (compute_brick_indicator)
"""

import json
import sys
import sqlite3
from pathlib import Path
from datetime import datetime

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

import numpy as np
from core.indicators.algorithms import compute_brick_indicator

DB_PATH = PROJECT / 'db' / 'market.db'
PRED_DIR = PROJECT / 'output' / 'screening_predictions'


def get_next_trading_day(conn: sqlite3.Connection, date_str: str) -> str | None:
    """获取指定日期之后的下一个交易日"""
    row = conn.execute(
        "SELECT DISTINCT date FROM stock_daily WHERE date > ? ORDER BY date LIMIT 1",
        (date_str,)
    ).fetchone()
    return row[0] if row else None


def calc_brick_value_for_stock(conn: sqlite3.Connection, symbol: str, target_date: str) -> float | None:
    """计算某只股票在 target_date 当天的砖值。
    
    需要前20日的 high/low/close 数据来计算砖形图指标。
    返回当日砖值，若数据不足返回 None。
    """
    rows = conn.execute(
        "SELECT date, high, low, close FROM stock_daily "
        "WHERE symbol = ? AND date <= ? "
        "ORDER BY date DESC LIMIT 30",
        (symbol, target_date)
    ).fetchall()
    
    if len(rows) < 10:
        return None
    
    # 反转为时间正序
    rows = rows[::-1]
    
    high = np.array([r[1] for r in rows], dtype=float)
    low = np.array([r[2] for r in rows], dtype=float)
    close = np.array([r[3] for r in rows], dtype=float)
    
    result = compute_brick_indicator(high, low, close)
    brick = result["brick"]
    
    # 返回最后一日（target_date）的砖值
    last_val = brick[-1]
    return float(last_val) if np.isfinite(last_val) else None


def get_t1_close(conn: sqlite3.Connection, symbol: str, t1_date: str) -> float | None:
    """获取股票在 T+1 日的收盘价"""
    row = conn.execute(
        "SELECT close FROM stock_daily WHERE symbol = ? AND date = ?",
        (symbol, t1_date)
    ).fetchone()
    return row[0] if row else None


def backfill_t1(pred_date: str) -> dict:
    """对指定日期的 predictions 进行 T+1 砖值回填。
    
    Returns:
        统计信息 dict
    """
    pred_file = PRED_DIR / f"{pred_date}.json"
    if not pred_file.exists():
        return {"error": f"predictions file not found: {pred_file}"}
    
    with open(pred_file) as f:
        pred_data = json.load(f)
    
    stocks = pred_data.get('stocks', [])
    if not stocks:
        return {"error": "no stocks in predictions"}
    
    conn = sqlite3.connect(str(DB_PATH))
    
    # 确定 T+1 交易日
    t1_date = get_next_trading_day(conn, pred_date)
    if not t1_date:
        conn.close()
        return {"error": f"no T+1 trading day found after {pred_date}"}
    
    stats = {
        "pred_date": pred_date,
        "t1_date": t1_date,
        "total": len(stocks),
        "filled": 0,
        "red_brick": 0,
        "green_brick": 0,
        "no_data": 0,
    }
    
    for stock in stocks:
        symbol = stock['symbol']
        pred_brick = stock.get('brick_val', 0)
        
        # 获取 T+1 收盘价
        t1_close = get_t1_close(conn, symbol, t1_date)
        if t1_close is None:
            stock['t1_close'] = None
            stock['t1_brick_val'] = None
            stock['t1_brick_color'] = "no_data"
            stats["no_data"] += 1
            continue
        
        # 计算 T+1 砖值
        t1_brick = calc_brick_value_for_stock(conn, symbol, t1_date)
        if t1_brick is None:
            stock['t1_close'] = t1_close
            stock['t1_brick_val'] = None
            stock['t1_brick_color'] = "no_data"
            stats["no_data"] += 1
            continue
        
        # 判断红砖/绿砖
        color = "red" if t1_brick > pred_brick else "green"
        
        stock['t1_close'] = t1_close
        stock['t1_brick_val'] = round(t1_brick, 2)
        stock['t1_brick_color'] = color
        stock['t1_change_pct'] = round((t1_close - stock.get('close', t1_close)) / stock.get('close', t1_close) * 100, 2)
        
        stats["filled"] += 1
        if color == "red":
            stats["red_brick"] += 1
        else:
            stats["green_brick"] += 1
    
    conn.close()
    
    # 计算汇总指标
    if stats["filled"] > 0:
        stats["red_brick_rate"] = round(stats["red_brick"] / stats["filled"] * 100, 1)
    else:
        stats["red_brick_rate"] = 0.0
    
    # 回写 predictions 文件
    pred_data['t1_backfill'] = {
        "t1_date": t1_date,
        "filled": stats["filled"],
        "red_brick_rate": stats["red_brick_rate"],
        "backfill_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    
    with open(pred_file, 'w') as f:
        json.dump(pred_data, f, ensure_ascii=False, indent=2)
    
    return stats


def main():
    if len(sys.argv) > 1:
        pred_date = sys.argv[1]
    else:
        # 自动找最近一个没有 t1 数据的 predictions 文件
        pred_files = sorted(PRED_DIR.glob('*.json'), reverse=True)
        pred_date = None
        for pf in pred_files:
            with open(pf) as f:
                data = json.load(f)
            if 't1_backfill' not in data and data.get('stocks'):
                pred_date = pf.stem
                break
        
        if not pred_date:
            print("所有 predictions 文件均已回填 T+1 数据，无需操作。")
            return
    
    print(f"正在回填 T+1 砖值: {pred_date}")
    result = backfill_t1(pred_date)
    
    if "error" in result:
        print(f"❌ 错误: {result['error']}")
        return
    
    print(f"✅ 回填完成:")
    print(f"   选股日: {result['pred_date']}")
    print(f"   T+1日: {result['t1_date']}")
    print(f"   总数: {result['total']}只")
    print(f"   已填充: {result['filled']}只")
    print(f"   无数据: {result['no_data']}只")
    print(f"   红砖: {result['red_brick']}只 ({result['red_brick_rate']:.1f}%)")
    print(f"   绿砖: {result['green_brick']}只")


if __name__ == "__main__":
    main()
