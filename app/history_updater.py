from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Callable

import pandas as pd

from .data_loader import (
    get_last_trade_date,
    load_raw_daily_csv,
    load_stock_list,
    normalize_daily_dataframe,
    normalize_symbol,
    save_daily_csv,
)
from .tushare_client import TushareClient, TushareClientError


@dataclass
class UpdateResult:
    symbol: str
    name: str
    status: str
    rows_fetched: int
    rows_written: int
    message: str
    elapsed_seconds: float


@dataclass
class BatchUpdateSummary:
    total: int
    success: int
    skipped: int
    failed: int
    cancelled: bool
    elapsed_seconds: float


class RateLimiter:
    def __init__(self, max_calls: int = 450, period_seconds: int = 60):
        self.max_calls = max_calls
        self.period_seconds = period_seconds
        self._calls: deque[float] = deque()

    def acquire(self):
        now = time.monotonic()
        while self._calls and now - self._calls[0] >= self.period_seconds:
            self._calls.popleft()

        if len(self._calls) < self.max_calls:
            self._calls.append(now)
            return

        sleep_for = self.period_seconds - (now - self._calls[0])
        if sleep_for > 0:
            time.sleep(sleep_for)

        now = time.monotonic()
        while self._calls and now - self._calls[0] >= self.period_seconds:
            self._calls.popleft()
        self._calls.append(now)


class HistoryUpdater:
    def __init__(
        self,
        stocklist_csv: Path,
        stock_daily_data_dir: Path,
        client: TushareClient | None = None,
        default_start_date: str = "20100101",
    ):
        self.stocklist_csv = stocklist_csv
        self.stock_daily_data_dir = stock_daily_data_dir
        self.client = client or TushareClient.from_env()
        self.default_start_date = default_start_date
        self.rate_limiter = RateLimiter()
        self.df_list = load_stock_list(stocklist_csv)
        self.stock_map = {
            normalize_symbol(row["symbol"]): {
                "ts_code": str(row.get("ts_code", "") or "").strip(),
                "name": str(row.get("name", "") or "").strip(),
            }
            for _, row in self.df_list.iterrows()
        }

    def _map_tushare_daily_to_local(self, df_remote: pd.DataFrame) -> pd.DataFrame:
        if df_remote is None or df_remote.empty:
            return pd.DataFrame(columns=["date", "open", "close", "high", "low", "volume"])

        required = {"trade_date", "open", "close", "high", "low", "amount"}
        miss = required - set(df_remote.columns)
        if miss:
            raise ValueError(f"Tushare 返回数据缺少字段: {sorted(miss)}")

        result = pd.DataFrame(
            {
                "date": pd.to_datetime(df_remote["trade_date"], format="%Y%m%d", errors="coerce"),
                "open": pd.to_numeric(df_remote["open"], errors="coerce"),
                "close": pd.to_numeric(df_remote["close"], errors="coerce"),
                "high": pd.to_numeric(df_remote["high"], errors="coerce"),
                "low": pd.to_numeric(df_remote["low"], errors="coerce"),
                "volume": pd.to_numeric(df_remote["amount"], errors="coerce"),
            }
        )
        return normalize_daily_dataframe(result)

    def _get_symbol_meta(self, symbol: str) -> dict:
        key = normalize_symbol(symbol)
        meta = self.stock_map.get(key)
        if not meta or not meta.get("ts_code"):
            raise ValueError(f"股票 {key} 在 stocklist.csv 中缺少 ts_code 映射")
        return {"symbol": key, **meta}

    def update_symbol(self, symbol: str, end_date: str | None = None, full_refresh: bool = False) -> UpdateResult:
        start_ts = time.perf_counter()
        symbol = normalize_symbol(symbol)
        meta = self._get_symbol_meta(symbol)

        try:
            local_df = load_raw_daily_csv(self.stock_daily_data_dir, symbol)
            if full_refresh:
                start_date = self.default_start_date
            else:
                last_trade_date = get_last_trade_date(self.stock_daily_data_dir, symbol)
                if last_trade_date is None:
                    start_date = self.default_start_date
                else:
                    start_date = (last_trade_date + timedelta(days=1)).strftime("%Y%m%d")

            end_date = end_date or pd.Timestamp.today().strftime("%Y%m%d")
            if start_date > end_date:
                return UpdateResult(symbol, meta["name"], "skipped", 0, 0, "本地数据已是最新", time.perf_counter() - start_ts)

            self.rate_limiter.acquire()
            remote_df = self.client.fetch_daily(meta["ts_code"], start_date=start_date, end_date=end_date)
            if remote_df.empty:
                return UpdateResult(symbol, meta["name"], "skipped", 0, 0, "接口未返回新数据", time.perf_counter() - start_ts)

            mapped_df = self._map_tushare_daily_to_local(remote_df)
            combined = pd.concat([local_df, mapped_df], ignore_index=True, sort=False)
            normalized = normalize_daily_dataframe(combined)
            before_count = len(normalize_daily_dataframe(local_df)) if not local_df.empty else 0
            save_daily_csv(self.stock_daily_data_dir, symbol, normalized)
            written = max(0, len(normalized) - before_count)
            return UpdateResult(symbol, meta["name"], "updated", len(mapped_df), written, "更新成功", time.perf_counter() - start_ts)
        except TushareClientError as exc:
            return UpdateResult(symbol, meta["name"], "failed", 0, 0, str(exc), time.perf_counter() - start_ts)
        except Exception as exc:
            return UpdateResult(symbol, meta["name"], "failed", 0, 0, f"更新失败: {exc}", time.perf_counter() - start_ts)

    def update_all_symbols(
        self,
        progress_callback: Callable[[dict], None] | None = None,
        stop_checker: Callable[[], bool] | None = None,
    ) -> tuple[list[UpdateResult], BatchUpdateSummary]:
        start_ts = time.perf_counter()
        symbols = [normalize_symbol(s) for s in self.df_list["symbol"].tolist()]
        total = len(symbols)
        results: list[UpdateResult] = []
        success = skipped = failed = 0
        cancelled = False

        for idx, symbol in enumerate(symbols, start=1):
            if stop_checker and stop_checker():
                cancelled = True
                break

            meta = self.stock_map.get(symbol, {})
            if progress_callback:
                progress_callback(
                    {
                        "current": idx,
                        "total": total,
                        "symbol": symbol,
                        "name": meta.get("name", ""),
                        "stage": "updating",
                        "stage_text": "正在拉取并合并日线数据",
                        "phase_text": "开始处理",
                        "success": success,
                        "skipped": skipped,
                        "failed": failed,
                    }
                )

            result = self.update_symbol(symbol)
            results.append(result)
            if result.status == "updated":
                success += 1
            elif result.status == "skipped":
                skipped += 1
            else:
                failed += 1

            if progress_callback:
                stage_text_map = {
                    "updated": "更新完成",
                    "skipped": "无需更新",
                    "failed": "更新失败",
                }
                progress_callback(
                    {
                        "current": idx,
                        "total": total,
                        "symbol": symbol,
                        "name": meta.get("name", ""),
                        "stage": result.status,
                        "stage_text": stage_text_map.get(result.status, result.status),
                        "phase_text": f"耗时 {result.elapsed_seconds:.1f}s，新增 {result.rows_written} 条",
                        "message": result.message,
                        "success": success,
                        "skipped": skipped,
                        "failed": failed,
                    }
                )

        summary = BatchUpdateSummary(
            total=total,
            success=success,
            skipped=skipped,
            failed=failed,
            cancelled=cancelled,
            elapsed_seconds=time.perf_counter() - start_ts,
        )
        return results, summary
