from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Callable

import pandas as pd

import numpy as np

from .chart_indicators import compute_oamv
from .data_loader import (
    DAILY_COLUMNS,
    get_index_csv_path,
    get_industry_csv_path,
    get_last_trade_date,
    load_index_csv,
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
        industry_daily_data_dir: Path | None = None,
    ):
        self.stocklist_csv = stocklist_csv
        self.stock_daily_data_dir = stock_daily_data_dir
        self.industry_daily_data_dir = industry_daily_data_dir or stock_daily_data_dir.parent / "industry_daily_data"
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
        self._sw_industry_list: list[tuple[str, str]] | None = None

    def _map_tushare_daily_to_local(self, df_remote: pd.DataFrame, df_basic: pd.DataFrame | None = None) -> pd.DataFrame:
        if df_remote is None or df_remote.empty:
            return pd.DataFrame(columns=["date", "open", "close", "high", "low", "volume", "turnover_rate"])

        required = {"trade_date", "open", "close", "high", "low", "amount"}
        miss = required - set(df_remote.columns)
        if miss:
            raise ValueError(f"Tushare 返回数据缺少字段: {sorted(miss)}")

        result = pd.DataFrame(
            {
                "trade_date": df_remote["trade_date"],
                "date": pd.to_datetime(df_remote["trade_date"], format="%Y%m%d", errors="coerce"),
                "open": pd.to_numeric(df_remote["open"], errors="coerce"),
                "close": pd.to_numeric(df_remote["close"], errors="coerce"),
                "high": pd.to_numeric(df_remote["high"], errors="coerce"),
                "low": pd.to_numeric(df_remote["low"], errors="coerce"),
                "volume": pd.to_numeric(df_remote["amount"], errors="coerce"),
            }
        )

        # 合并 daily_basic 的换手率
        if df_basic is not None and not df_basic.empty and "turnover_rate" in df_basic.columns:
            result = result.merge(
                df_basic[["trade_date", "turnover_rate"]],
                on="trade_date",
                how="left",
            )
        else:
            result["turnover_rate"] = None

        result = result.drop(columns=["trade_date"])
        return normalize_daily_dataframe(result)

    def _get_symbol_meta(self, symbol: str) -> dict:
        key = normalize_symbol(symbol)
        meta = self.stock_map.get(key)
        if not meta or not meta.get("ts_code"):
            raise ValueError(f"股票 {key} 在 stocklist.csv 中缺少 ts_code 映射")
        return {"symbol": key, **meta}

    def _needs_turnover_rate_backfill(self, local_df: pd.DataFrame) -> bool:
        """检查本地数据是否缺少换手率，需要回填。

        只要存在任意一行换手率为空，就需要回填——这样可以兜住增量同步时
        daily_basic 接口当天数据延迟、限流或临时失败导致最近若干天换手率
        为空的情况，避免这些空缺被永久遗留。
        """
        if local_df.empty:
            return False
        if "turnover_rate" not in local_df.columns:
            return True
        return local_df["turnover_rate"].isna().any()

    def _backfill_turnover_rate(self, symbol: str, meta: dict, local_df: pd.DataFrame) -> pd.DataFrame:
        """回填本地数据中缺失的换手率。

        只针对换手率为空的行去拉 daily_basic，避免每次都全量重拉。
        拉取范围用缺失日期的 [min, max] 区间一次性请求，减少接口调用次数。
        """
        if "turnover_rate" not in local_df.columns:
            missing_mask = pd.Series([True] * len(local_df), index=local_df.index)
        else:
            missing_mask = local_df["turnover_rate"].isna()

        if not missing_mask.any():
            return local_df

        missing_dates = pd.to_datetime(local_df.loc[missing_mask, "date"], errors="coerce").dropna()
        if missing_dates.empty:
            return local_df

        start_date = missing_dates.min().strftime("%Y%m%d")
        end_date = missing_dates.max().strftime("%Y%m%d")

        self.rate_limiter.acquire()
        try:
            basic_df = self.client.fetch_daily_basic(meta["ts_code"], start_date=start_date, end_date=end_date)
        except TushareClientError:
            return local_df

        if basic_df is None or basic_df.empty or "turnover_rate" not in basic_df.columns:
            return local_df

        basic_df = basic_df.copy()
        basic_df["date"] = pd.to_datetime(basic_df["trade_date"], format="%Y%m%d", errors="coerce").dt.strftime("%Y-%m-%d")
        rate_map = dict(zip(basic_df["date"], pd.to_numeric(basic_df["turnover_rate"], errors="coerce")))

        result = local_df.copy()
        if "turnover_rate" not in result.columns:
            result["turnover_rate"] = None

        date_col = pd.to_datetime(result["date"], errors="coerce").dt.strftime("%Y-%m-%d")
        existing_rate = pd.to_numeric(result["turnover_rate"], errors="coerce")
        fetched_rate = date_col.map(rate_map)
        # 只填补原本为空的行，已有的换手率保持不变
        result["turnover_rate"] = existing_rate.where(existing_rate.notna(), fetched_rate)
        return result

    def update_symbol(self, symbol: str, end_date: str | None = None, full_refresh: bool = False) -> UpdateResult:
        start_ts = time.perf_counter()
        symbol = normalize_symbol(symbol)
        meta = self._get_symbol_meta(symbol)

        try:
            local_df = load_raw_daily_csv(self.stock_daily_data_dir, symbol)

            # 检查是否需要回填换手率
            needs_backfill = self._needs_turnover_rate_backfill(local_df)
            if needs_backfill and not local_df.empty:
                local_df = self._backfill_turnover_rate(symbol, meta, local_df)

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
                if needs_backfill:
                    normalized = normalize_daily_dataframe(local_df)
                    save_daily_csv(self.stock_daily_data_dir, symbol, normalized)
                    return UpdateResult(symbol, meta["name"], "updated", 0, 0, "已补全换手率", time.perf_counter() - start_ts)
                return UpdateResult(symbol, meta["name"], "skipped", 0, 0, "本地数据已是最新", time.perf_counter() - start_ts)

            self.rate_limiter.acquire()
            remote_df = self.client.fetch_daily(meta["ts_code"], start_date=start_date, end_date=end_date)
            if remote_df.empty:
                if needs_backfill:
                    normalized = normalize_daily_dataframe(local_df)
                    save_daily_csv(self.stock_daily_data_dir, symbol, normalized)
                    return UpdateResult(symbol, meta["name"], "updated", 0, 0, "已补全换手率", time.perf_counter() - start_ts)
                return UpdateResult(symbol, meta["name"], "skipped", 0, 0, "接口未返回新数据", time.perf_counter() - start_ts)

            self.rate_limiter.acquire()
            try:
                basic_df = self.client.fetch_daily_basic(meta["ts_code"], start_date=start_date, end_date=end_date)
            except TushareClientError:
                basic_df = pd.DataFrame()

            mapped_df = self._map_tushare_daily_to_local(remote_df, basic_df)
            combined = pd.concat([local_df, mapped_df], ignore_index=True, sort=False)
            normalized = normalize_daily_dataframe(combined)
            before_count = len(normalize_daily_dataframe(local_df)) if not local_df.empty else 0
            save_daily_csv(self.stock_daily_data_dir, symbol, normalized)
            written = max(0, len(normalized) - before_count)
            msg = "更新成功（含换手率补全）" if needs_backfill else "更新成功"
            return UpdateResult(symbol, meta["name"], "updated", len(mapped_df), written, msg, time.perf_counter() - start_ts)
        except TushareClientError as exc:
            return UpdateResult(symbol, meta["name"], "failed", 0, 0, str(exc), time.perf_counter() - start_ts)
        except Exception as exc:
            return UpdateResult(symbol, meta["name"], "failed", 0, 0, f"更新失败: {exc}", time.perf_counter() - start_ts)

    INDEX_CODES = [("000001.SH", "上证指数"), ("930903.CSI", "中证A股")]

    def _map_index_daily_to_local(self, df_remote: pd.DataFrame) -> pd.DataFrame:
        if df_remote is None or df_remote.empty:
            return pd.DataFrame(columns=["date", "open", "close", "high", "low", "volume", "turnover_rate"])

        result = pd.DataFrame(
            {
                "date": pd.to_datetime(df_remote["trade_date"], format="%Y%m%d", errors="coerce"),
                "open": pd.to_numeric(df_remote["open"], errors="coerce"),
                "close": pd.to_numeric(df_remote["close"], errors="coerce"),
                "high": pd.to_numeric(df_remote["high"], errors="coerce"),
                "low": pd.to_numeric(df_remote["low"], errors="coerce"),
                "volume": pd.to_numeric(df_remote["amount"], errors="coerce"),
                "turnover_rate": None,
            }
        )
        return normalize_daily_dataframe(result)

    def update_index(self, ts_code: str, end_date: str | None = None) -> UpdateResult:
        start_ts = time.perf_counter()
        name = dict(self.INDEX_CODES).get(ts_code, ts_code)
        csv_path = get_index_csv_path(self.stock_daily_data_dir, ts_code)

        try:
            local_df = pd.read_csv(csv_path) if csv_path.exists() else pd.DataFrame(columns=DAILY_COLUMNS)

            last_date = None
            if not local_df.empty and "date" in local_df.columns:
                dates = pd.to_datetime(local_df["date"], errors="coerce").dropna()
                if not dates.empty:
                    last_date = dates.max()

            if last_date is None:
                start_date = self.default_start_date
            else:
                start_date = (last_date + timedelta(days=1)).strftime("%Y%m%d")

            end_date = end_date or pd.Timestamp.today().strftime("%Y%m%d")
            if start_date > end_date:
                return UpdateResult(ts_code, name, "skipped", 0, 0, "本地数据已是最新", time.perf_counter() - start_ts)

            self.rate_limiter.acquire()
            remote_df = self.client.fetch_index_daily(ts_code, start_date=start_date, end_date=end_date)
            if remote_df.empty:
                return UpdateResult(ts_code, name, "skipped", 0, 0, "接口未返回新数据", time.perf_counter() - start_ts)

            mapped_df = self._map_index_daily_to_local(remote_df)
            combined = pd.concat([local_df, mapped_df], ignore_index=True, sort=False)
            normalized = normalize_daily_dataframe(combined)
            before_count = len(normalize_daily_dataframe(local_df)) if not local_df.empty else 0

            self.stock_daily_data_dir.mkdir(parents=True, exist_ok=True)
            import tempfile
            with tempfile.NamedTemporaryFile("w", delete=False, suffix=".csv", dir=self.stock_daily_data_dir, encoding="utf-8-sig", newline="") as tmp:
                normalized.to_csv(tmp.name, index=False)
                temp_path = Path(tmp.name)
            temp_path.replace(csv_path)

            written = max(0, len(normalized) - before_count)

            if ts_code == "930903.CSI" and written > 0:
                self._rebuild_oamv_csv()

            return UpdateResult(ts_code, name, "updated", len(mapped_df), written, "更新成功", time.perf_counter() - start_ts)
        except TushareClientError as exc:
            return UpdateResult(ts_code, name, "failed", 0, 0, str(exc), time.perf_counter() - start_ts)
        except Exception as exc:
            return UpdateResult(ts_code, name, "failed", 0, 0, f"更新失败: {exc}", time.perf_counter() - start_ts)

    def _rebuild_oamv_csv(self):
        """读取 930903 指数数据，计算 OAMV 虚拟K线，写入独立 CSV。"""
        df = load_index_csv(self.stock_daily_data_dir, "930903.CSI")
        if df.empty or len(df) < 16:
            return

        result = compute_oamv(
            open_prices=df["open"].to_numpy(np.float64),
            high_prices=df["high"].to_numpy(np.float64),
            low_prices=df["low"].to_numpy(np.float64),
            close_prices=df["close"].to_numpy(np.float64),
            amount=df["volume"].to_numpy(np.float64),
            amount_divisor=1000.0,
        )

        oamv_df = pd.DataFrame({
            "date": df["date"],
            "open": result["oamv_open"],
            "high": result["oamv_high"],
            "low": result["oamv_low"],
            "close": result["oamv_close"],
        }).dropna(subset=["open", "high", "low", "close"]).reset_index(drop=True)

        if oamv_df.empty:
            return

        oamv_df["date"] = oamv_df["date"].dt.strftime("%Y-%m-%d")
        oamv_path = self.stock_daily_data_dir / "oamv_930903_CSI.csv"

        import tempfile
        with tempfile.NamedTemporaryFile("w", delete=False, suffix=".csv", dir=self.stock_daily_data_dir, encoding="utf-8-sig", newline="") as tmp:
            oamv_df.to_csv(tmp.name, index=False)
            temp_path = Path(tmp.name)
        temp_path.replace(oamv_path)

    def _load_sw_industry_list(self) -> list[tuple[str, str]]:
        if self._sw_industry_list is not None:
            return self._sw_industry_list
        self.rate_limiter.acquire()
        df = self.client.fetch_sw_index_list()
        if df.empty:
            self._sw_industry_list = []
            return self._sw_industry_list
        clean_name = df['name'].str.replace(r'\(申万\)$', '', regex=True)
        self._sw_industry_list = list(zip(df['ts_code'], clean_name))
        return self._sw_industry_list

    def _map_sw_daily_to_local(self, df_remote: pd.DataFrame) -> pd.DataFrame:
        if df_remote is None or df_remote.empty:
            return pd.DataFrame(columns=DAILY_COLUMNS)

        result = pd.DataFrame(
            {
                "date": pd.to_datetime(df_remote["trade_date"], format="%Y%m%d", errors="coerce"),
                "open": pd.to_numeric(df_remote["open"], errors="coerce"),
                "close": pd.to_numeric(df_remote["close"], errors="coerce"),
                "high": pd.to_numeric(df_remote["high"], errors="coerce"),
                "low": pd.to_numeric(df_remote["low"], errors="coerce"),
                "volume": pd.to_numeric(df_remote["amount"], errors="coerce"),
                "turnover_rate": None,
            }
        )
        return normalize_daily_dataframe(result)

    def update_industry(self, ts_code: str, name: str, end_date: str | None = None) -> UpdateResult:
        start_ts = time.perf_counter()
        csv_path = get_industry_csv_path(self.industry_daily_data_dir, ts_code)

        try:
            local_df = pd.read_csv(csv_path) if csv_path.exists() else pd.DataFrame(columns=DAILY_COLUMNS)

            last_date = None
            if not local_df.empty and "date" in local_df.columns:
                dates = pd.to_datetime(local_df["date"], errors="coerce").dropna()
                if not dates.empty:
                    last_date = dates.max()

            start_date = self.default_start_date if last_date is None else (last_date + timedelta(days=1)).strftime("%Y%m%d")
            end_date = end_date or pd.Timestamp.today().strftime("%Y%m%d")
            if start_date > end_date:
                return UpdateResult(ts_code, name, "skipped", 0, 0, "本地数据已是最新", time.perf_counter() - start_ts)

            self.rate_limiter.acquire()
            remote_df = self.client.fetch_sw_daily(ts_code, start_date=start_date, end_date=end_date)
            if remote_df.empty:
                return UpdateResult(ts_code, name, "skipped", 0, 0, "接口未返回新数据", time.perf_counter() - start_ts)

            mapped_df = self._map_sw_daily_to_local(remote_df)
            combined = pd.concat([local_df, mapped_df], ignore_index=True, sort=False)
            normalized = normalize_daily_dataframe(combined)
            before_count = len(normalize_daily_dataframe(local_df)) if not local_df.empty else 0

            self.industry_daily_data_dir.mkdir(parents=True, exist_ok=True)
            import tempfile
            with tempfile.NamedTemporaryFile("w", delete=False, suffix=".csv", dir=self.industry_daily_data_dir, encoding="utf-8-sig", newline="") as tmp:
                normalized.to_csv(tmp.name, index=False)
                temp_path = Path(tmp.name)
            temp_path.replace(csv_path)

            written = max(0, len(normalized) - before_count)
            return UpdateResult(ts_code, name, "updated", len(mapped_df), written, "更新成功", time.perf_counter() - start_ts)
        except TushareClientError as exc:
            return UpdateResult(ts_code, name, "failed", 0, 0, str(exc), time.perf_counter() - start_ts)
        except Exception as exc:
            return UpdateResult(ts_code, name, "failed", 0, 0, f"更新失败: {exc}", time.perf_counter() - start_ts)

    def update_all_symbols(
        self,
        progress_callback: Callable[[dict], None] | None = None,
        stop_checker: Callable[[], bool] | None = None,
    ) -> tuple[list[UpdateResult], BatchUpdateSummary]:
        start_ts = time.perf_counter()
        symbols = [normalize_symbol(s) for s in self.df_list["symbol"].tolist()]
        total = len(symbols) + len(self.INDEX_CODES)
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

        for i, (idx_code, idx_name) in enumerate(self.INDEX_CODES):
            if stop_checker and stop_checker():
                cancelled = True
                break

            idx_seq = len(symbols) + i + 1
            if progress_callback:
                progress_callback(
                    {
                        "current": idx_seq,
                        "total": total,
                        "symbol": idx_code,
                        "name": idx_name,
                        "stage": "updating",
                        "stage_text": "正在拉取指数日线数据",
                        "phase_text": "开始处理",
                        "success": success,
                        "skipped": skipped,
                        "failed": failed,
                    }
                )

            result = self.update_index(idx_code)
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
                        "current": idx_seq,
                        "total": total,
                        "symbol": idx_code,
                        "name": idx_name,
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
