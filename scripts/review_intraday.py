#!/usr/bin/env python3
"""
review_intraday.py — 分钟级预测回顾

功能：
1. 读取 screening_predictions/{score_date}.json（自动预测）+ {score_date}_manual.json（手动分析）
2. 从同花顺 d.10jqka.com.cn 拉取 T+1 日分钟级数据
3. 计算分钟级衍生指标（封板时刻、开板次数、尾盘涨跌等）
4. 分类路径形态（高开强势/高开低走/低开翻红/低开低收/窄幅震荡）
5. 查表给出路径级裁定（真对/蒙对/各种错误）
6. 也做传统收盘价方向判定
7. 写入 scoring.db 的 intraday_review 表
8. 输出完整回顾报告

用法：
    python3 scripts/review_intraday.py --score-date 20260604 --review-date 20260605
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
import time
from typing import Optional
from urllib.request import Request, urlopen
from urllib.error import URLError

# ── 项目路径 ──
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

import sqlite3
import pandas as pd

from core.scoring.intraday_metrics import (
    parse_minute_data,
    compute_intraday_metrics,
    classify_path_shape,
    judge_intraday_verdict,
    build_intraday_review_row,
    aggregate_intraday_verdicts,
)
from core.scoring.prediction_review import (
    normalize_direction,
    judge_direction_correct,
    build_backfill_fields,
    aggregate_accuracy,
)

# ── 常量 ──
PREDICTIONS_DIR = "/opt/data/output/screening_predictions"
MARKET_DB_PATH = os.path.join(PROJECT_ROOT, "db", "market.db")
SCORING_DB_PATH = os.path.join(PROJECT_ROOT, "db", "scoring.db")

# 同花顺分钟接口
THS_MINUTE_URL = "https://d.10jqka.com.cn/v6/time/hs_{symbol}/defer/last.js"
# 上证指数代码
INDEX_SYMBOL = "1A0001"

# A股市场
SH_SYMBOLS = {"6"}  # 6开头沪市
SZ_SYMBOLS = {"0", "3"}  # 0/3开头深市

# 涨停判断阈值
LIMIT_UP_THRESHOLD = 0.095  # 涨幅≥9.5%视为涨停


def symbol_to_ths(symbol: str) -> str:
    """同花顺格式：600226（无后缀，直接用6位代码）"""
    return symbol


def fetch_minute_data(symbol: str, max_retries: int = 3) -> Optional[dict]:
    """从同花顺拉取分钟级数据，返回解析后的 dict。"""
    ths_code = symbol_to_ths(symbol)
    url = THS_MINUTE_URL.format(symbol=ths_code)

    for attempt in range(max_retries):
        try:
            req = Request(url, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Referer": "https://stockpage.10jqka.com.cn/",
            })
            with urlopen(req, timeout=15) as resp:
                raw = resp.read().decode("utf-8")

            # 剥 JSONP：quotebridge_v6_time_hs_xxx_defer_last({...})
            match = re.search(r"\{.*\}", raw, re.DOTALL)
            if not match:
                print(f"    ⚠️ {symbol}: 无法解析JSONP响应")
                return None

            data = json.loads(match.group())
            # 取 hs_{ths_code} 字段
            key = f"hs_{ths_code}"
            inner = data.get(key) or data.get("items", {})
            if not inner:
                print(f"    ⚠️ {symbol}: 响应中无 {key} 字段")
                return None

            return inner

        except (URLError, json.JSONDecodeError, Exception) as e:
            if attempt < max_retries - 1:
                wait = (attempt + 1) * 2
                print(f"    ⚠️ {symbol}: 第{attempt+1}次失败({e}), {wait}s后重试")
                time.sleep(wait)
            else:
                print(f"    ❌ {symbol}: {max_retries}次重试后失败: {e}")
                return None

    return None


def get_market_conn() -> sqlite3.Connection:
    """获取 market.db 连接（复制到 /tmp 绕过 WAL 锁）。"""
    copy_path = f"/tmp/market_review_{os.getpid()}.db"
    # 清理旧副本
    for old in os.listdir("/tmp"):
        if old.startswith("market_review_") and old.endswith(".db"):
            try:
                os.remove(f"/tmp/{old}")
            except OSError:
                pass
    shutil.copy2(MARKET_DB_PATH, copy_path)
    conn = sqlite3.connect(copy_path)
    conn.execute("PRAGMA journal_mode=DELETE")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def get_review_date_prices(conn: sqlite3.Connection, symbols: list[str], rev_date: str) -> dict:
    """获取 T+1 日的开盘价和收盘价。rev_date 格式 YYYYMMDD，自动转 YYYY-MM-DD。"""
    db_date = f"{rev_date[:4]}-{rev_date[4:6]}-{rev_date[6:8]}"
    placeholders = ",".join("?" for _ in symbols)
    rows = conn.execute(f"""
        SELECT symbol, open, close, volume, turnover_rate
        FROM stock_daily
        WHERE symbol IN ({placeholders}) AND date = ?
    """, symbols + [db_date]).fetchall()

    result = {}
    for sym, open_p, close, vol, turn in rows:
        result[sym] = {
            "open": open_p,
            "close": close,
            "volume": vol,
            "turnover_rate": turn,
        }
    return result


def get_prev_close(conn: sqlite3.Connection, symbol: str, date: str) -> Optional[float]:
    """获取指定股票在指定日期的昨收。date 格式 YYYYMMDD。"""
    db_date = f"{date[:4]}-{date[4:6]}-{date[6:8]}"
    row = conn.execute("""
        SELECT close FROM stock_daily
        WHERE symbol = ? AND date < ?
        ORDER BY date DESC LIMIT 1
    """, (symbol, db_date)).fetchone()
    return row[0] if row else None


def read_predictions(score_date: str) -> list[dict]:
    """读取预测文件（自动+手动）。返回合并后的 stocks 列表。"""
    all_stocks = []

    # 1. 自动预测
    auto_path = os.path.join(PREDICTIONS_DIR, f"{score_date}.json")
    if os.path.exists(auto_path):
        with open(auto_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        for stock in data.get("predictions", []):
            stock["source"] = "auto"
            all_stocks.append(stock)
        print(f"  📖 自动预测: {len(data.get('predictions', []))}只")

    # 2. 手动分析
    manual_path = os.path.join(PREDICTIONS_DIR, f"{score_date}_manual.json")
    if os.path.exists(manual_path):
        with open(manual_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        for stock in data.get("predictions", []):
            stock["source"] = "manual"
            all_stocks.append(stock)
        print(f"  📖 手动预测: {len(data.get('predictions', []))}只")

    return all_stocks


def get_stock_name(conn: sqlite3.Connection, symbol: str) -> str:
    row = conn.execute("SELECT name FROM stock_list WHERE symbol=?", (symbol,)).fetchone()
    return row[0] if row else symbol


def run_review(score_date: str, review_date: str) -> dict:
    """执行完整的分钟级预测回顾。"""
    print(f"\n{'='*60}")
    print(f"📊 分钟级预测回顾")
    print(f"   预测日: {score_date}")
    print(f"   回顾日: {review_date}")
    print(f"{'='*60}")

    # 1. 读取预测
    all_stocks = read_predictions(score_date)
    if not all_stocks:
        print("❌ 没有找到预测数据")
        return {"error": "no_predictions"}

    print(f"  📋 共 {len(all_stocks)} 只待回顾")

    # 2. 连接数据库
    mkt_conn = get_market_conn()
    scoring_conn = sqlite3.connect(SCORING_DB_PATH)

    # 3. 获取回顾日的行情数据
    symbols = [s.get("symbol", "") for s in all_stocks if s.get("symbol")]
    prices = get_review_date_prices(mkt_conn, symbols, review_date)
    print(f"  💹 有行情数据: {len(prices)}/{len(symbols)} 只")

    # 3b. 拉取大盘指数分钟数据
    index_data = analyze_index(review_date)

    # 4. 逐只拉分钟数据 + 计算指标
    review_rows = []
    backfill_list = []
    fetched_count = 0
    failed_minute = 0
    no_minute = []

    for i, stock in enumerate(all_stocks):
        symbol = stock.get("symbol", "")
        name = stock.get("name") or get_stock_name(mkt_conn, symbol)
        expected = stock.get("direction") or stock.get("prediction", {}).get("direction")
        source = stock.get("source", "auto")

        # 昨收
        prev_close = stock.get("close") or get_prev_close(mkt_conn, symbol, review_date)
        if not prev_close or prev_close <= 0:
            print(f"  ⏭️ {symbol} {name}: 无昨收，跳过")
            continue

        # T+1 行情
        price_info = prices.get(symbol, {})
        open_p = price_info.get("open")
        close = price_info.get("close")
        open_chg = ((open_p - prev_close) / prev_close * 100) if open_p and prev_close else None
        day_chg = ((close - prev_close) / prev_close * 100) if close and prev_close else None

        # 方向归一化 + 传统方向判定
        norm_dir = normalize_direction(expected)
        open_correct, close_correct = judge_direction_correct(expected, open_chg, day_chg)

        backfill = {
            "symbol": symbol,
            "name": name,
            "source": source,
            "expected_direction": expected,
            "direction_norm": norm_dir,
            "open_chg": round(open_chg, 2) if open_chg is not None else None,
            "day_chg": round(day_chg, 2) if day_chg is not None else None,
            "prev_close": prev_close,
            "open_correct": open_correct,
            "close_correct": close_correct,
        }
        backfill_list.append(backfill)

        # 拉分钟数据
        print(f"  [{i+1}/{len(all_stocks)}] {symbol} {name} (来源:{source})...", end=" ", flush=True)
        minute_raw = fetch_minute_data(symbol)

        if not minute_raw:
            print("⚠️ 无分钟数据")
            failed_minute += 1
            no_minute.append(symbol)
            continue

        data_str = minute_raw.get("data", "")
        pre = minute_raw.get("pre") or prev_close
        minute_bars = parse_minute_data(data_str)

        if not minute_bars:
            print("⚠️ 分钟数据解析为空")
            no_minute.append(symbol)
            continue

        # 计算分钟级指标
        row = build_intraday_review_row(
            review_date=review_date,
            score_date=score_date,
            symbol=symbol,
            bars=minute_bars,
            pre=float(pre),
            expected_direction=expected,
            open_chg=open_chg,
            day_chg=day_chg,
        )
        row["name"] = name
        row["source"] = source
        review_rows.append(row)
        fetched_count += 1
        print(f"✅ {row['path_shape']} | {row['intraday_verdict']}")

        time.sleep(0.3)  # 避免请求过快

    print(f"\n{'='*60}")
    print(f"📊 分钟数据统计")
    print(f"   成功获取: {fetched_count}/{len(all_stocks)}")
    print(f"   无分钟数据: {len(no_minute)}只 {' '.join(no_minute)}")

    # 5. 保存到 scoing.db
    if review_rows:
        cursor = scoring_conn.cursor()
        # 先删再写
        cursor.execute("DELETE FROM intraday_review WHERE review_date = ?", (review_date,))
        scoring_conn.commit()

        save_sql = """
            INSERT INTO intraday_review
            (review_date, score_date, symbol, expected_direction, path_shape,
             intraday_verdict, seal_time, unseal_count, high_time, close_vs_vwap,
             tail_chg, morning_vol_pct, intraday_drawdown, is_failed_limit,
             vwap_cross_count, amount_weighted_late)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        params = []
        for r in review_rows:
            params.append((
                r.get("review_date"),
                r.get("score_date"),
                r["symbol"],
                r.get("expected_direction"),
                r.get("path_shape"),
                r.get("intraday_verdict"),
                r.get("seal_time"),
                int(r["unseal_count"]) if r.get("unseal_count") is not None else None,
                r.get("high_time"),
                float(r["close_vs_vwap"]) if r.get("close_vs_vwap") is not None else None,
                float(r["tail_chg"]) if r.get("tail_chg") is not None else None,
                float(r["morning_vol_pct"]) if r.get("morning_vol_pct") is not None else None,
                float(r["intraday_drawdown"]) if r.get("intraday_drawdown") is not None else None,
                int(r["is_failed_limit"]) if r.get("is_failed_limit") is not None else None,
                int(r["vwap_cross_count"]) if r.get("vwap_cross_count") is not None else None,
                float(r["amount_weighted_late"]) if r.get("amount_weighted_late") is not None else None,
            ))
        cursor.executemany(save_sql, params)
        scoring_conn.commit()
        print(f"  💾 写入 intraday_review 表: {len(review_rows)} 条")

    # 6. 生成完整报告
    report = generate_report(review_rows, backfill_list, score_date, review_date, index_data)

    mkt_conn.close()
    scoring_conn.close()

    return report


def analyze_index(review_date: str) -> dict:
    """获取并分析大盘指数分钟级走势。"""
    print(f"\n  📈 拉取大盘分钟数据 ({INDEX_SYMBOL} 上证指数)...", end=" ", flush=True)
    raw = fetch_minute_data(INDEX_SYMBOL)
    if not raw:
        print("❌")
        return {}

    data_str = raw.get("data", "")
    pre = float(raw.get("pre", 0))
    bars = parse_minute_data(data_str)
    if not bars or not pre:
        print("❌ 数据为空")
        return {}

    # 从分钟数据计算当日高开/低开/振幅
    open_p = bars[0].price if bars else None
    close = bars[-1].price if bars else None
    high = max(b.price for b in bars)
    low = min(b.price for b in bars)

    open_chg = (open_p - pre) / pre * 100 if open_p and pre else None
    close_chg = (close - pre) / pre * 100 if close and pre else None
    amplitude = (high - low) / pre * 100 if high and low and pre else None

    # 计算分钟级指标（复用个股逻辑，只是没有 direction 预期）
    metrics = compute_intraday_metrics(bars, float(pre))

    # 早盘(9:30-10:30)与尾盘(14:00-15:00)涨跌
    morning_open = bars[0].price if bars else None
    morning_end = None
    tail_open = None
    tail_end = close
    for b in bars:
        t = int(b.time[:2]) * 60 + int(b.time[2:4])
        if t <= 10 * 60 + 30:
            morning_end = b.price
        if 14 * 60 <= t < 14 * 60 + 1:
            tail_open = b.price

    morning_chg = ((morning_end - morning_open) / morning_open * 100
                   if morning_end and morning_open else None)
    tail_chg = ((tail_end - tail_open) / tail_open * 100
                if tail_end and tail_open else None)

    # 路径形态（复用 classify_path_shape）
    path_shape = classify_path_shape(metrics, open_chg, close_chg)

    result = {
        "name": raw.get("name", "上证指数"),
        "pre": pre,
        "open": open_p,
        "close": close,
        "high": high,
        "low": low,
        "open_chg": round(open_chg, 2) if open_chg else None,
        "close_chg": round(close_chg, 2) if close_chg else None,
        "amplitude": round(amplitude, 2) if amplitude else None,
        "morning_chg": round(morning_chg, 2) if morning_chg else None,
        "tail_chg": round(tail_chg, 2) if tail_chg else None,
        "path_shape": path_shape,
        "seal_time": metrics.get("seal_time"),  # 涨停时刻（指数不会涨停，但复用逻辑）
        "high_time": metrics.get("high_time"),
        "close_vs_vwap": metrics.get("close_vs_vwap"),
        "intraday_drawdown": metrics.get("intraday_drawdown"),
    }

    print(f"✅ {path_shape} | {open_chg:+.2f}%开 → {close_chg:+.2f}%收")
    return result


def generate_report(
    review_rows: list[dict],
    backfill_list: list[dict],
    score_date: str,
    review_date: str,
    index_data: dict | None = None,
) -> dict:
    """生成完整的回顾报告文本。"""
    lines = []
    lines.append(f"📊 预测回顾 — {score_date} 预测 vs {review_date} 实盘")
    lines.append(f"{'='*60}")
    lines.append("")

    # ── 大盘指数走势 ──
    if index_data:
        lines.append("📈 大盘分钟走势")
        lines.append("-" * 40)
        idx_name = index_data.get("name", "上证指数")
        pre = index_data.get("pre")
        open_p = index_data.get("open")
        close = index_data.get("close")
        high = index_data.get("high")
        low = index_data.get("low")
        open_chg = index_data.get("open_chg")
        close_chg = index_data.get("close_chg")
        amplitude = index_data.get("amplitude")
        path_shape = index_data.get("path_shape")
        morning_chg = index_data.get("morning_chg")
        tail_chg = index_data.get("tail_chg")
        high_time = index_data.get("high_time")
        drawdown = index_data.get("intraday_drawdown")
        close_vs_vwap = index_data.get("close_vs_vwap")

        lines.append(f"  {idx_name} | 昨收 {pre}")
        lines.append(f"  开 {open_p:.2f} ({open_chg:+.2f}%) → 收 {close:.2f} ({close_chg:+.2f}%)")
        lines.append(f"  高 {high:.2f} / 低 {low:.2f} / 振幅 {amplitude:.2f}%")
        lines.append(f"  路径形态: {path_shape}")
        lines.append(f"  早盘(9:30-10:30)涨跌: {morning_chg:+.2f}%" if morning_chg is not None else "")
        lines.append(f"  尾盘(14:00-15:00)涨跌: {tail_chg:+.2f}%" if tail_chg is not None else "")
        lines.append(f"  最高点时刻: {high_time or '—'}")
        lines.append(f"  收盘vs均价: {close_vs_vwap:+.4f}" if close_vs_vwap is not None else "")
        lines.append(f"  日内最大回撤: {drawdown:.2f}%" if drawdown is not None else "")
        if tail_chg is not None and tail_chg < -0.3:
            lines.append(f"  ⚠️ 尾盘跳水 {tail_chg:.2f}%，次日低开概率高")
        if high_time and int(high_time[:2]) * 60 + int(high_time[2:4]) < 10 * 60 + 30:
            lines.append(f"  ⚠️ 开盘30分钟内见顶，诱多信号")
        lines.append("")

    # 分组统计
    auto_stocks = [s for s in backfill_list if s.get("source") == "auto"]
    manual_stocks = [s for s in backfill_list if s.get("source") != "auto"]
    auto_review = [r for r in review_rows if r.get("source") == "auto"]
    manual_review = [r for r in review_rows if r.get("source") != "auto"]

    # ── 传统方向准确率 ──
    lines.append("📈 传统方向准确率（收盘价口径）")
    lines.append("-" * 40)

    for label, stocks in [("自动选股", auto_stocks), ("手动分析", manual_stocks), ("全部", backfill_list)]:
        acc = aggregate_accuracy(stocks)
        total = acc.get("close_total", 0) + acc.get("open_total", 0)
        if total > 0:
            lines.append(f"  {label}:")
            lines.append(f"    开盘方向: {acc.get('open_hit', 0)}/{acc.get('open_total', 0)} = {acc.get('open_accuracy', 'N/A')}%")
            lines.append(f"    收盘方向: {acc.get('close_hit', 0)}/{acc.get('close_total', 0)} = {acc.get('close_accuracy', 'N/A')}%")
            lines.append(f"    中性/未填: {acc.get('neutral_count', 0)}/{acc.get('not_filled_count', 0)}")

    lines.append("")

    # ── 分钟级路径裁定 ──
    lines.append("🕐 分钟级路径裁定（同花顺分时接口）")
    lines.append("-" * 40)

    for label, rows in [("自动选股", auto_review), ("手动分析", manual_review), ("全部", review_rows)]:
        if not rows:
            lines.append(f"  {label}: 无数据")
            continue
        verdicts = aggregate_intraday_verdicts(rows)
        judged = verdicts.get("judged_total", 0)
        true_acc = verdicts.get("true_accuracy")
        loose_acc = verdicts.get("loose_accuracy")
        lines.append(f"  {label}: 裁定{judged}只(排除{verdicts['excluded_count']}只)")
        lines.append(f"    真对: {verdicts['true_count']} | 蒙对: {verdicts['lucky_count']} | 错误: {verdicts['error_count']}")
        if true_acc is not None:
            lines.append(f"    真实准确率: {true_acc}%  |  宽口径: {loose_acc}%")
        if verdicts.get("error_stage"):
            lines.append(f"    错误环节: {verdicts['error_stage']}")

    lines.append("")

    # ── 逐只明细 ──
    lines.append("📋 逐只明细")
    lines.append("-" * 70)
    header = f"{'代码':<8} {'名称':<10} {'来源':<6} {'预测方向':<12} {'收盘%':>7} {'路径形态':<18} {'裁定'}"
    lines.append(header)
    lines.append("-" * 70)

    # 建立 review row 的 symbol→row 映射
    review_map = {r["symbol"]: r for r in review_rows}

    for stock in backfill_list:
        sym = stock["symbol"]
        name = stock["name"]
        src = stock["source"]
        direction = stock["expected_direction"] or "—"
        day_chg = stock.get("day_chg")
        day_str = f"{day_chg:+.2f}%" if day_chg is not None else "N/A"

        # 分钟级数据
        rr = review_map.get(sym)
        path = rr["path_shape"] if rr else "—"
        verdict = rr["intraday_verdict"] if rr else "—"

        lines.append(f"{sym:<8} {name:<10} {src:<6} {direction:<12} {day_str:>7} {path:<18} {verdict}")

    lines.append("")

    # ── 错误归因汇总 ──
    error_stages = {}
    for r in review_rows:
        v = r.get("intraday_verdict", "")
        if v in ("❌ 高开低走陷阱", "❌ 完全错", "❌ 看空踏空"):
            ps = r.get("path_shape", "unknown")
            if ps == "high_open_low_close":
                stage = "盘中/尾盘跳水"
            elif ps == "low_open_low_close":
                stage = "开盘判错"
            elif ps == "low_open_red":
                stage = "开盘判错"
            else:
                stage = "其他"
            error_stages[stage] = error_stages.get(stage, 0) + 1

    if error_stages:
        lines.append("🔍 错误环节归因")
        lines.append("-" * 30)
        for stage, count in sorted(error_stages.items(), key=lambda x: -x[1]):
            lines.append(f"  {stage}: {count}次")

    return {
        "text": "\n".join(lines),
        "review_rows": review_rows,
        "backfill_list": backfill_list,
        "error_stages": error_stages,
        "total": len(backfill_list),
        "minute_fetched": len(review_rows),
    }


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="分钟级预测回顾")
    parser.add_argument("--score-date", required=True, help="预测日，如 20260604")
    parser.add_argument("--review-date", required=True, help="回顾日（实盘日），如 20260605")
    args = parser.parse_args()

    report = run_review(args.score_date, args.review_date)

    if isinstance(report, dict) and "text" in report:
        print("\n" + report["text"])
    elif isinstance(report, dict) and "error" in report:
        print(f"\n❌ 错误: {report['error']}")
