"""Trader 股票日线数据更新脚本

与项目自带的 update_all_data.py 的区别：
- 不依赖 init_databases()，不碰 scoring.db
- 直接 raw SQLite 写入 market.db，避免 WAL 锁冲突
- CLI 和微信端均可使用，行为一致

注意：除个股日线外，还会更新 930903 中证A股指数日线并重建 OAMV 活跃市值。
"""
import os, sys, time, sqlite3 as lite
from pathlib import Path
from collections import deque

import pandas as pd
import numpy as np

PROJECT = Path(__file__).resolve().parents[1]
os.chdir(str(PROJECT))
sys.path.insert(0, str(PROJECT))

from app.tushare_client import TushareClient, DEFAULT_TUSHARE_TOKEN
from core.indicators.algorithms import compute_oamv, moving_average


def to_ts_code(sym: str) -> str:
    return f"{sym}.SH" if sym.startswith("6") else f"{sym}.SZ"


def today_str() -> str:
    return pd.Timestamp.today().strftime("%Y%m%d")


class RateLimiter:
    """Tushare 限速: ~200次/分钟"""
    def __init__(self, max_calls: int = 180, period: int = 60):
        self.max = max_calls
        self.period = period
        self._ts: deque[float] = deque()

    def acquire(self):
        now = time.monotonic()
        while self._ts and now - self._ts[0] >= self.period:
            self._ts.popleft()
        if len(self._ts) >= self.max:
            sleep = self.period - (now - self._ts[0])
            if sleep > 0:
                time.sleep(sleep)
            now = time.monotonic()
            while self._ts and now - self._ts[0] >= self.period:
                self._ts.popleft()
        self._ts.append(now)


def get_stock_list(conn: lite.Connection) -> list[tuple[str, str, str]]:
    """返回 [(symbol, ts_code, name), ...]"""
    cur = conn.execute("SELECT symbol, ts_code, name FROM stock_list ORDER BY symbol")
    rows = []
    for sym, ts, name in cur.fetchall():
        ts = (ts or "").strip()
        if not ts:
            ts = to_ts_code(sym)
        rows.append((sym, ts, name or ""))
    return rows


def get_last_date(conn: lite.Connection, symbol: str) -> str | None:
    cur = conn.execute(
        "SELECT MAX(date) FROM stock_daily WHERE symbol = ?", (symbol,)
    )
    r = cur.fetchone()
    return r[0] if r and r[0] else None


def update_symbol(
    conn: lite.Connection,
    client: TushareClient,
    limiter: RateLimiter,
    symbol: str,
    ts_code: str,
    name: str,
    force_full: bool = False,
) -> dict:
    """更新单只股票日线，返回统计信息"""
    start = time.perf_counter()

    # 1. 确定起止日期
    last = None if force_full else get_last_date(conn, symbol)
    if last:
        start_date = (pd.Timestamp(last) + pd.Timedelta(days=1)).strftime("%Y%m%d")
    else:
        start_date = "20100101"
    end = today_str()

    if start_date > end:
        return {"symbol": symbol, "name": name, "status": "skipped", "rows": 0,
                "msg": "已是最新", "elapsed": time.perf_counter() - start}

    # 2. 拉取日线
    limiter.acquire()
    df = client.fetch_daily(ts_code=ts_code, start_date=start_date, end_date=end)
    if df.empty:
        return {"symbol": symbol, "name": name, "status": "skipped", "rows": 0,
                "msg": "接口无新数据", "elapsed": time.perf_counter() - start}

    # 3. 拉取换手率
    limiter.acquire()
    try:
        basic = client.fetch_daily_basic(ts_code=ts_code, start_date=start_date, end_date=end)
    except Exception:
        basic = pd.DataFrame()

    # 4. 列映射
    df["date"] = pd.to_datetime(df["trade_date"], format="%Y%m%d")
    df["volume"] = pd.to_numeric(df["amount"], errors="coerce")
    if not basic.empty and "turnover_rate" in basic.columns:
        df = df.merge(basic[["trade_date", "turnover_rate"]], on="trade_date", how="left")
    else:
        df["turnover_rate"] = None

    # 5. 写入数据库
    sql = (
        "INSERT OR REPLACE INTO stock_daily "
        "(symbol, date, open, close, high, low, volume, turnover_rate) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
    )
    written = 0
    for r in df.itertuples():
        conn.execute(sql, (
            symbol,
            str(r.date.date()),
            float(r.open) if pd.notna(r.open) else None,
            float(r.close) if pd.notna(r.close) else None,
            float(r.high) if pd.notna(r.high) else None,
            float(r.low) if pd.notna(r.low) else None,
            float(r.volume) if pd.notna(r.volume) else None,
            float(r.turnover_rate) if pd.notna(r.turnover_rate) else None,
        ))
        written += 1
    conn.commit()

    return {"symbol": symbol, "name": name, "status": "updated", "rows": written,
            "msg": f"新增 {written} 条", "elapsed": time.perf_counter() - start}


def update_all(client: TushareClient, force_full: bool = False, skip_existing: bool = False):
    """全量更新所有股票"""
    t0 = time.perf_counter()

    print(f"[1/3] 连接数据库 market.db ...")
    conn = lite.connect(str(PROJECT / "db" / "market.db"), timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")

    stocks = get_stock_list(conn)
    total = len(stocks)
    print(f"     股票池: {total} 只")

    if skip_existing:
        existing = {
            r[0] for r in conn.execute(
                "SELECT DISTINCT symbol FROM stock_daily"
            ).fetchall()
        }
        stocks = [(s, t, n) for s, t, n in stocks if s not in existing]
        print(f"     跳过已有: {total - len(stocks)} 只, 需更新: {len(stocks)} 只")
        total = len(stocks)

    limiter = RateLimiter()
    results = {"updated": 0, "skipped": 0, "failed": 0}
    details = []

    print(f"\n[2/3] 开始{'全量' if force_full else '增量'}更新...")
    for idx, (sym, ts, name) in enumerate(stocks, 1):
        try:
            r = update_symbol(conn, client, limiter, sym, ts, name, force_full)
        except Exception as e:
            r = {"symbol": sym, "name": name, "status": "failed", "rows": 0,
                 "msg": str(e)[:80], "elapsed": 0}

        results[r["status"]] = results.get(r["status"], 0) + 1
        details.append(r)

        pct = idx / total * 100
        status_char = {"updated": "✓", "skipped": "·", "failed": "✗"}.get(r["status"], "?")
        print(f"  [{idx}/{total} {pct:4.1f}%] {status_char} {sym} {name}: "
              f"{r['msg']} ({r['elapsed']:.1f}s)")

    conn.close()

    # 汇总
    elapsed = time.perf_counter() - t0
    print(f"\n[3/3] {'='*40}")
    print(f"  总处理:  {total} 只")
    print(f"  已更新:  {results.get('updated', 0)} 只")
    print(f"  已跳过:  {results.get('skipped', 0)} 只")
    print(f"  失败:    {results.get('failed', 0)} 只")
    print(f"  总耗时:  {elapsed:.1f}s ({elapsed/60:.1f}min)")

    return results, details, elapsed


def update_index_and_oamv(client: TushareClient, limiter: RateLimiter):
    """更新 930903 中证A股指数日线并重建 OAMV 活跃市值。"""
    t0 = time.perf_counter()
    conn = lite.connect(str(PROJECT / "db" / "market.db"), timeout=10)
    ts_code = "930903.CSI"

    # 1. 确定起止日期
    cur = conn.execute("SELECT MAX(date) FROM index_daily WHERE ts_code = ?", (ts_code,))
    row = cur.fetchone()
    last_date = row[0] if row and row[0] else None
    if last_date:
        start_date = (pd.Timestamp(last_date) + pd.Timedelta(days=1)).strftime("%Y%m%d")
    else:
        start_date = "20100101"
    end_date = pd.Timestamp.today().strftime("%Y%m%d")

    if start_date > end_date:
        print("  指数已是最新，跳过")
        conn.close()
        return

    # 2. 拉取指数日线
    limiter.acquire()
    remote_df = client.fetch_index_daily(ts_code, start_date=start_date, end_date=end_date)
    if remote_df.empty:
        print("  指数接口无新数据，跳过")
        conn.close()
        return

    # 3. 列映射并写入
    mapped = pd.DataFrame({
        "date": pd.to_datetime(remote_df["trade_date"], format="%Y%m%d"),
        "open": pd.to_numeric(remote_df["open"], errors="coerce"),
        "close": pd.to_numeric(remote_df["close"], errors="coerce"),
        "high": pd.to_numeric(remote_df["high"], errors="coerce"),
        "low": pd.to_numeric(remote_df["low"], errors="coerce"),
        "volume": pd.to_numeric(remote_df["amount"], errors="coerce"),
        "turnover_rate": None,
    }).dropna(subset=["date", "open", "close", "high", "low", "volume"])
    if mapped.empty:
        print("  指数数据映射后为空，跳过")
        conn.close()
        return

    sql = (
        "INSERT OR REPLACE INTO index_daily "
        "(ts_code, date, open, close, high, low, volume, turnover_rate) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
    )
    written = 0
    for r in mapped.itertuples(index=False):
        conn.execute(sql, (
            ts_code,
            str(r.date.date()),
            float(r.open), float(r.close), float(r.high), float(r.low),
            float(r.volume), None,
        ))
        written += 1
    conn.commit()
    print(f"  指数 {ts_code}: 新增 {written} 条")

    # 4. 重建 OAMV
    all_index = conn.execute(
        "SELECT date, open, close, high, low, volume FROM index_daily "
        "WHERE ts_code = ? ORDER BY date", (ts_code,)
    ).fetchall()
    if len(all_index) < 16:
        print("  指数数据不足16条，无法计算 OAMV")
        conn.close()
        return

    idx_df = pd.DataFrame(all_index, columns=["date", "open", "close", "high", "low", "volume"])
    idx_df["date"] = pd.to_datetime(idx_df["date"])

    result = compute_oamv(
        open_prices=idx_df["open"].to_numpy(np.float64),
        high_prices=idx_df["high"].to_numpy(np.float64),
        low_prices=idx_df["low"].to_numpy(np.float64),
        close_prices=idx_df["close"].to_numpy(np.float64),
        amount=idx_df["volume"].to_numpy(np.float64),
        amount_divisor=1000.0,
    )

    oamv_df = pd.DataFrame({
        "date": idx_df["date"],
        "open": result["oamv_open"],
        "high": result["oamv_high"],
        "low": result["oamv_low"],
        "close": result["oamv_close"],
    }).dropna(subset=["open", "high", "low", "close"]).reset_index(drop=True)
    oamv_df["date"] = oamv_df["date"].dt.strftime("%Y-%m-%d")

    # 批量写入
    oamv_sql = (
        "INSERT OR REPLACE INTO oamv_daily "
        "(date, open, close, high, low) VALUES (?, ?, ?, ?, ?)"
    )
    oamv_rows = [
        (str(r.date), float(r.open), float(r.close), float(r.high), float(r.low))
        for r in oamv_df.itertuples(index=False)
    ]
    conn.executemany(oamv_sql, oamv_rows)
    conn.commit()

    idx_elapsed = time.perf_counter() - t0
    print(f"  OAMV 活跃市值: 已重建 ({len(oamv_df)} 条, {idx_elapsed:.1f}s)")
    conn.close()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="更新 trader 股票日线数据")
    parser.add_argument("--full", action="store_true", help="全量刷新（重新拉取所有数据）")
    parser.add_argument("--skip-existing", action="store_true", help="跳过已有数据的股票（仅拉取缺失的）")
    parser.add_argument("--symbol", type=str, help="仅更新单只股票（6位代码）")
    args = parser.parse_args()

    token = os.getenv("TUSHARE_TOKEN", "").strip() or DEFAULT_TUSHARE_TOKEN
    if not token:
        print("错误: 未配置 TUSHARE_TOKEN")
        sys.exit(1)
    client = TushareClient(token=token)

    if args.symbol:
        conn = lite.connect(str(PROJECT / "db" / "market.db"), timeout=30)
        stocks = get_stock_list(conn)
        conn.close()
        target = [s for s in stocks if s[0] == args.symbol]
        if not target:
            print(f"未找到股票 {args.symbol}")
            sys.exit(1)
        sym, ts, name = target[0]
        print(f"单只更新: {sym} {name} ({ts})")
        conn = lite.connect(str(PROJECT / "db" / "market.db"), timeout=30)
        conn.execute("PRAGMA journal_mode=WAL")
        limiter = RateLimiter()
        r = update_symbol(conn, client, limiter, sym, ts, name, args.full)
        conn.close()
        print(f"  {'✓' if r['status']=='updated' else '✗'} {r['msg']} ({r['elapsed']:.1f}s)")
    else:
        results, details, elapsed = update_all(client, force_full=args.full, skip_existing=args.skip_existing)
        print(f"\n--- 更新指数与 OAMV ---")
        limiter = RateLimiter()
        update_index_and_oamv(client, limiter)
        print("--- 完成 ---")
