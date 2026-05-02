"""曲线形态匹配引擎。

基于 MASS 算法（z-normalized 欧氏距离）在全市场股票中搜索
与模板曲线形态最相似的走势片段。

优先使用 stumpy 库（O(n log n)），缺失时回退到 numpy 实现。
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from core.data.repository import StockRepository
from core.models.curve_match import (
    CurveMatchItem,
    CurveMatchRequest,
    CurveMatchResult,
)
from core.stock_pool.manager import StockPoolManager

try:
    import stumpy

    HAS_STUMPY = True
except ImportError:
    HAS_STUMPY = False

DEFAULT_PROGRESS_INTERVAL = 20
DEFAULT_MAX_WORKERS = max(1, (os.cpu_count() or 2) - 1)

SIMILARITY_BASELINE = 8.0


def mass_search(template: np.ndarray, series: np.ndarray) -> np.ndarray:
    """计算模板与序列所有子序列的 z-normalized 欧氏距离。

    返回长度为 len(series) - len(template) + 1 的距离数组。
    """
    if HAS_STUMPY:
        return stumpy.mass(template, series)
    return _mass_numpy_fallback(template, series)


def _mass_numpy_fallback(template: np.ndarray, series: np.ndarray) -> np.ndarray:
    m = len(template)
    t_std = template.std()
    if t_std < 1e-8:
        return np.full(len(series) - m + 1, np.inf)
    t_norm = (template - template.mean()) / t_std

    shape = (len(series) - m + 1, m)
    strides = (series.strides[0], series.strides[0])
    windows = np.lib.stride_tricks.as_strided(series, shape=shape, strides=strides)

    means = windows.mean(axis=1, keepdims=True)
    stds = windows.std(axis=1, keepdims=True)
    stds = np.where(stds < 1e-8, np.inf, stds)
    w_norm = (windows - means) / stds

    return np.linalg.norm(w_norm - t_norm, axis=1)


def _multi_scale_search(
    template: np.ndarray, series: np.ndarray, scale_factors: tuple[float, ...] = (0.8, 0.9, 1.0, 1.1, 1.2)
) -> tuple[int, float, float]:
    """多尺度搜索，返回 (best_idx, best_dist, best_scale)。"""
    from scipy.interpolate import interp1d

    best_dist = np.inf
    best_idx = -1
    best_scale = 1.0

    for scale in scale_factors:
        new_len = max(4, int(len(template) * scale))
        if new_len > len(series):
            continue
        if abs(scale - 1.0) < 1e-6:
            scaled = template
        else:
            x_old = np.linspace(0, 1, len(template))
            x_new = np.linspace(0, 1, new_len)
            scaled = interp1d(x_old, template, kind="linear")(x_new)

        dp = mass_search(scaled, series)
        if len(dp) == 0:
            continue
        min_idx = int(np.argmin(dp))
        min_dist = float(dp[min_idx])

        if min_dist < best_dist:
            best_dist = min_dist
            best_idx = min_idx
            best_scale = scale

    return best_idx, best_dist, best_scale


def _worker_match_stock(args: tuple) -> dict:
    """进程池工作函数：匹配单只股票。"""
    root_str, symbol, stock_name, template_list, enable_multi_scale = args

    try:
        repository = StockRepository(Path(root_str))
        df = repository.get_daily_frame(symbol)

        if df.empty or len(df) < 10:
            return {"symbol": symbol, "name": stock_name, "error": "数据不足"}

        close = df["close"].values.astype(np.float64)
        template = np.array(template_list, dtype=np.float64)
        m = len(template)

        if len(close) < m + 1:
            return {"symbol": symbol, "name": stock_name, "error": "数据长度不足"}

        if enable_multi_scale:
            best_idx, best_dist, best_scale = _multi_scale_search(template, close)
            actual_m = max(4, int(m * best_scale))
        else:
            dp = mass_search(template, close)
            if len(dp) == 0:
                return {"symbol": symbol, "name": stock_name, "error": "距离计算失败"}
            best_idx = int(np.argmin(dp))
            best_dist = float(dp[best_idx])
            actual_m = m

        if best_idx < 0 or not np.isfinite(best_dist):
            return {"symbol": symbol, "name": stock_name, "error": "无有效匹配"}

        dates = df["date"]
        start_date = dates.iloc[best_idx]
        end_idx = min(best_idx + actual_m - 1, len(dates) - 1)
        end_date = dates.iloc[end_idx]

        fmt = lambda d: d.strftime("%Y-%m-%d") if hasattr(d, "strftime") else str(d)[:10]

        similarity = max(0.0, (1 - best_dist / SIMILARITY_BASELINE)) * 100

        return {
            "symbol": symbol,
            "name": stock_name,
            "start_index": best_idx,
            "start_date": fmt(start_date),
            "end_date": fmt(end_date),
            "distance": round(best_dist, 4),
            "similarity": round(similarity, 1),
        }
    except Exception as exc:
        return {"symbol": symbol, "name": stock_name, "error": str(exc)}


@dataclass
class CurveMatchEngine:
    repository: StockRepository
    stock_pool_manager: StockPoolManager
    progress_interval: int = DEFAULT_PROGRESS_INTERVAL
    max_workers: int = DEFAULT_MAX_WORKERS

    @classmethod
    def from_root(cls, root: Path) -> CurveMatchEngine:
        repository = StockRepository(root)
        stock_pool_manager = StockPoolManager(repository)
        return cls(repository=repository, stock_pool_manager=stock_pool_manager)

    def run(
        self,
        request: CurveMatchRequest,
        progress_callback: Callable[[dict], None] | None = None,
        cancelled_fn: Callable[[], bool] | None = None,
    ) -> CurveMatchResult:
        t0 = time.time()

        pool = (
            self.stock_pool_manager.get_pool_by_symbols(request.symbols, request.stock_pool_name)
            if request.symbols
            else self.stock_pool_manager.get_default_pool(request.stock_pool_name)
        )

        stock_map = {stock.symbol: stock for stock in pool.stocks}
        total = len(pool.symbols)
        interval = max(1, self.progress_interval)

        if progress_callback is not None and total > 0:
            progress_callback({"current": 0, "total": total, "symbol": "", "matched": 0})

        root_str = str(self.repository.root)
        template_list = request.template.tolist()

        task_args = [
            (root_str, symbol, stock_map.get(symbol).name if stock_map.get(symbol) else "", template_list, request.enable_multi_scale)
            for symbol in pool.symbols
        ]

        all_results: list[dict] = []
        completed = 0

        with ProcessPoolExecutor(max_workers=self.max_workers) as executor:
            futures = {executor.submit(_worker_match_stock, args): args[1] for args in task_args}

            for future in as_completed(futures):
                completed += 1
                result = future.result()

                if not result.get("error"):
                    all_results.append(result)

                if progress_callback is not None and (completed % interval == 0 or completed == total):
                    progress_callback({
                        "current": completed,
                        "total": total,
                        "symbol": result["symbol"],
                        "matched": len(all_results),
                    })

                if cancelled_fn is not None and cancelled_fn():
                    for pending in futures:
                        pending.cancel()
                    break

        all_results.sort(key=lambda r: r["distance"])
        top_results = all_results[: request.top_k]

        matches = [
            CurveMatchItem(
                symbol=r["symbol"],
                name=r["name"],
                start_index=r["start_index"],
                start_date=r["start_date"],
                end_date=r["end_date"],
                distance=r["distance"],
                similarity=r["similarity"],
            )
            for r in top_results
        ]

        return CurveMatchResult(
            request=request,
            matches=matches,
            total_scanned=completed,
            scan_time_seconds=round(time.time() - t0, 2),
        )
