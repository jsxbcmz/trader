from __future__ import annotations

import os
import pickle
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from core.data.repository import StockRepository
from core.data.time_index import locate_time_index
from core.expression.evaluator import EvaluationContext, evaluate_at_index
from core.expression.parser import TdxLexer, TdxParser, TdxTranspiler, TdxTranspileError
from core.expression.parser.transpiler import transpile_tdx_source
from core.models.screening import ScreeningError, ScreeningRequest, ScreeningResult
from core.screening.error_policy import DEFAULT_ERROR_POLICY, normalize_error_policy
from core.screening.result_models import SingleRunResult, build_debug_payload, stock_name_of
from core.stock_pool.manager import StockPoolManager

# 进度回调默认间隔：每处理多少只股票触发一次
DEFAULT_PROGRESS_INTERVAL = 20
# 默认并行进程数（使用 CPU 核心数，但至少留一个核心给系统）
DEFAULT_MAX_WORKERS = max(1, os.cpu_count() - 1 or 1)


def _screen_single_stock(args: tuple) -> dict:
    """并行工作函数：处理单只股票的选股
    
    优化：使用预编译的序列化表达式，避免重复解析
    """
    (root, symbol, stock_info, serialized_expr, target_date, include_debug) = args

    try:
        # 每个进程创建自己的 repository
        repository = StockRepository(Path(root))

        # 反序列化预编译的表达式（比重新解析快得多）
        expression = pickle.loads(serialized_expr)

        # 获取数据
        df = repository.get_daily_frame(symbol)
        time_result = locate_time_index(df, target_date)

        if not time_result.matched or time_result.index is None:
            return {
                "symbol": symbol,
                "name": stock_info.get("name", ""),
                "requested_date": time_result.requested_date,
                "actual_date": time_result.actual_date or "",
                "matched": False,
                "reason": time_result.reason,
                "error": None,
            }

        context = EvaluationContext(df=df, target_index=time_result.index)
        value = evaluate_at_index(expression, context)
        matched = bool(value)
        reason = "命中" if matched else "条件不满足"

        return {
            "symbol": symbol,
            "name": stock_info.get("name", ""),
            "requested_date": time_result.requested_date,
            "actual_date": time_result.actual_date or "",
            "matched": matched,
            "reason": reason,
            "error": None,
        }
    except Exception as exc:
        return {
            "symbol": symbol,
            "name": stock_info.get("name", ""),
            "requested_date": target_date,
            "actual_date": "",
            "matched": False,
            "reason": "",
            "error": str(exc),
        }


@dataclass
class ScreeningEngine:
    repository: StockRepository
    stock_pool_manager: StockPoolManager
    error_policy: str = DEFAULT_ERROR_POLICY
    progress_interval: int = DEFAULT_PROGRESS_INTERVAL
    max_workers: int = DEFAULT_MAX_WORKERS

    @classmethod
    def from_root(cls, root: Path) -> "ScreeningEngine":
        repository = StockRepository(root)
        stock_pool_manager = StockPoolManager(repository)
        return cls(repository=repository, stock_pool_manager=stock_pool_manager)

    def run(
        self,
        request: ScreeningRequest,
        progress_callback: Callable[[dict], None] | None = None,
        cancelled_fn: Callable[[], bool] | None = None,
    ) -> ScreeningResult:
        policy = normalize_error_policy(self.error_policy)

        # 解析通达信条件代码（验证语法）
        tdx_source = request.tdx_source
        if not tdx_source or not tdx_source.strip():
            raise ValueError("通达信条件代码不能为空")

        # 预编译表达式并序列化（只解析一次，所有进程共享）
        try:
            expression = transpile_tdx_source(tdx_source)
            serialized_expr = pickle.dumps(expression)
        except Exception as exc:
            raise ValueError(f"通达信条件解析失败: {exc}") from exc

        pool = (
            self.stock_pool_manager.get_pool_by_symbols(request.symbols, request.stock_pool_name)
            if request.symbols
            else self.stock_pool_manager.get_default_pool(request.stock_pool_name)
        )

        stock_map = {stock.symbol: stock for stock in pool.stocks}
        matches = []
        errors = []
        total = len(pool.symbols)
        interval = max(1, self.progress_interval)

        # 立即发送初始进度，让用户知道任务已开始
        if progress_callback is not None and total > 0:
            progress_callback({
                "current": 0,
                "total": total,
                "symbol": "",
                "matched": 0,
                "errors": 0,
            })

        # 准备并行任务参数（传递序列化后的表达式，而不是源码）
        root_str = str(self.repository.root)
        task_args = [
            (
                root_str,
                symbol,
                {"name": stock_map.get(symbol).name if stock_map.get(symbol) else ""},
                serialized_expr,  # 预编译的序列化表达式
                request.target_date,
                request.include_debug,
            )
            for symbol in pool.symbols
        ]

        # 使用进程池并行处理
        matched_count = 0
        completed = 0

        with ProcessPoolExecutor(max_workers=self.max_workers) as executor:
            futures = {executor.submit(_screen_single_stock, args): args[1] for args in task_args}

            for future in as_completed(futures):
                completed += 1
                result = future.result()

                if result["error"]:
                    if policy == "raise":
                        raise ValueError(result["error"])
                    errors.append(ScreeningError(
                        symbol=result["symbol"],
                        stage="engine",
                        message=result["error"],
                    ))
                else:
                    from core.models.screening import ScreeningMatch
                    matches.append(ScreeningMatch(
                        symbol=result["symbol"],
                        name=result["name"],
                        requested_date=result["requested_date"],
                        actual_date=result["actual_date"],
                        matched=result["matched"],
                        reason=result["reason"],
                    ))
                    if result["matched"]:
                        matched_count += 1

                # 按间隔发送进度回调
                if progress_callback is not None and (completed % interval == 0 or completed == total):
                    progress_callback({
                        "current": completed,
                        "total": total,
                        "symbol": result["symbol"],
                        "matched": matched_count,
                        "errors": len(errors),
                    })

                # 检查是否被取消，取消时取消剩余任务并返回已有结果
                if cancelled_fn is not None and cancelled_fn():
                    for pending_future in futures:
                        pending_future.cancel()
                    break

        return ScreeningResult(
            request=request,
            matches=tuple(matches),
            errors=tuple(errors),
            total=total,
            matched_count=matched_count,
        )

    # ── 回测专用：轻量级单进程选股 ──────────────────────────────

    def run_fast_for_backtest(
        self,
        expression: Any,
        target_date: str,
        stock_pool_name: str,
        preloaded_data: dict[str, pd.DataFrame] | None = None,
        prebuilt_date_indices: dict[str, dict[str, int]] | None = None,
        cached_pool: Any | None = None,
    ) -> list[dict[str, str]]:
        """回测专用的轻量级选股方法。

        与 ``run()`` 的区别：
        - 单进程执行，无进程池创建/销毁开销
        - 接受预编译的表达式对象，无需重复解析
        - 接受预加载的数据和预构建的日期索引，避免重复 IO 和日期解析
        - 返回简化的匹配结果列表，而非完整的 ScreeningResult

        Args:
            expression: 预编译的表达式节点（由 transpile_tdx_source 生成）
            target_date: 目标日期字符串 YYYY-MM-DD
            stock_pool_name: 股票池名称
            preloaded_data: 预加载的日线数据 {symbol: DataFrame}
            prebuilt_date_indices: 预构建的日期索引 {symbol: {date_str: row_index}}
            cached_pool: 缓存的股票池对象（避免每次重新获取）

        Returns:
            匹配的股票列表 [{"symbol": "000001", "name": "平安银行"}, ...]
        """
        from core.data.time_index import locate_time_index_fast

        pool = cached_pool if cached_pool is not None else self.stock_pool_manager.get_default_pool(stock_pool_name)
        stock_map = {stock.symbol: stock for stock in pool.stocks}
        matched_stocks: list[dict[str, str]] = []

        for symbol in pool.symbols:
            try:
                # 优先使用预加载数据
                if preloaded_data is not None and symbol in preloaded_data:
                    daily_df = preloaded_data[symbol]
                else:
                    daily_df = self.repository.get_daily_frame(symbol)

                if daily_df is None or daily_df.empty:
                    continue

                # 优先使用预构建的日期索引进行 O(1) 定位
                if prebuilt_date_indices is not None and symbol in prebuilt_date_indices:
                    time_result = locate_time_index_fast(
                        prebuilt_date_indices[symbol], target_date,
                    )
                else:
                    time_result = locate_time_index(daily_df, target_date)

                if not time_result.matched or time_result.index is None:
                    continue

                context = EvaluationContext(df=daily_df, target_index=time_result.index)
                value = evaluate_at_index(expression, context)

                if bool(value):
                    stock_info = stock_map.get(symbol)
                    matched_stocks.append({
                        "symbol": symbol,
                        "name": stock_info.name if stock_info else "",
                    })
            except Exception:
                continue

        return matched_stocks
