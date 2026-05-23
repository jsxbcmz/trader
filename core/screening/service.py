from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from core.utils import now_iso

from core.models.screening import (
    ScreeningError,
    ScreeningMatch,
    ScreeningRequest,
    ScreeningResult,
)
from core.screening.cache_models import (
    CACHE_STATUS_COMPLETED,
    CACHE_STATUS_INTERRUPTED,
    ScreeningCacheEntry,
    compute_tdx_source_hash,
)
from core.screening.cache_repository import ScreeningCacheRepository
from core.screening.engine import ScreeningEngine
from core.screening.result_formatter import (
    format_error_lines,
    format_match_lines,
    format_screening_summary,
)


@dataclass
class ScreeningService:
    engine: ScreeningEngine
    cache_repository: ScreeningCacheRepository

    @classmethod
    def from_root(cls, root: Path) -> "ScreeningService":
        return cls(
            engine=ScreeningEngine.from_root(root),
            cache_repository=ScreeningCacheRepository(root),
        )

    def screen(
        self,
        request: ScreeningRequest,
        progress_callback: Callable[[dict], None] | None = None,
        cancelled_fn: Callable[[], bool] | None = None,
    ) -> ScreeningResult:
        return self.engine.run(request, progress_callback=progress_callback, cancelled_fn=cancelled_fn)

    def screen_with_summary(
        self,
        request: ScreeningRequest,
        progress_callback: Callable[[dict], None] | None = None,
        cancelled_fn: Callable[[], bool] | None = None,
    ) -> dict:
        result = self.screen(request, progress_callback=progress_callback, cancelled_fn=cancelled_fn)
        return {
            "result": result,
            "summary": format_screening_summary(result),
            "matches": format_match_lines(result),
            "errors": format_error_lines(result),
        }

    def screen_with_cache(
        self,
        request: ScreeningRequest,
        progress_callback: Callable[[dict], None] | None = None,
        cancelled_fn: Callable[[], bool] | None = None,
    ) -> dict:
        """带缓存的选股入口

        流程：
        1. 计算缓存键，查询是否有缓存
        2. 缓存命中且已完成 → 直接返回
        3. 缓存命中但已中断 → 从中断位置继续选股
        4. 无缓存 → 全量执行
        5. 执行完毕后写入/更新缓存
        """
        tdx_hash = compute_tdx_source_hash(request.tdx_source)
        cached = self.cache_repository.find(
            target_date=request.target_date,
            template_id=request.template_id,
            tdx_source_hash=tdx_hash,
        )

        # ── 情况 1：缓存命中且已完成 ──
        if cached is not None and cached.is_completed:
            result = self._rebuild_result_from_cache(cached, request)
            return {
                "result": result,
                "summary": format_screening_summary(result),
                "matches": format_match_lines(result),
                "errors": format_error_lines(result),
                "cache_hit": True,
            }

        # ── 情况 2：缓存命中但已中断 → 增量选股 ──
        if cached is not None and cached.is_interrupted:
            remaining_symbols = self._compute_remaining_symbols(
                request, cached.processed_count
            )
            if not remaining_symbols:
                cached.status = CACHE_STATUS_COMPLETED
                cached.updated_at = now_iso()
                self.cache_repository.upsert(cached)
                result = self._rebuild_result_from_cache(cached, request)
                return {
                    "result": result,
                    "summary": format_screening_summary(result),
                    "matches": format_match_lines(result),
                    "errors": format_error_lines(result),
                    "cache_hit": True,
                }

            incremental_request = ScreeningRequest(
                tdx_source=request.tdx_source,
                target_date=request.target_date,
                symbols=tuple(remaining_symbols),
                stock_pool_name=request.stock_pool_name,
                include_debug=request.include_debug,
                template_id=request.template_id,
                template_name=request.template_name,
            )

            wrapped_callback = self._wrap_progress_for_incremental(
                progress_callback,
                already_processed=cached.processed_count,
                total=cached.total,
                already_matched=cached.matched_count,
                already_errors=cached.error_count,
            )

            incremental_result = self.engine.run(
                incremental_request,
                progress_callback=wrapped_callback,
                cancelled_fn=cancelled_fn,
            )

            merged_entry = self._merge_cache_entry(
                cached, incremental_result, cancelled_fn
            )
            self.cache_repository.upsert(merged_entry)

            full_result = self._rebuild_result_from_cache(merged_entry, request)
            return {
                "result": full_result,
                "summary": format_screening_summary(full_result),
                "matches": format_match_lines(full_result),
                "errors": format_error_lines(full_result),
                "cache_hit": False,
                "resumed": True,
            }

        # ── 情况 3：无缓存 → 全量执行 ──
        result = self.engine.run(
            request,
            progress_callback=progress_callback,
            cancelled_fn=cancelled_fn,
        )

        was_cancelled = cancelled_fn is not None and cancelled_fn()
        entry = self._build_cache_entry(request, result, tdx_hash, was_cancelled)
        self.cache_repository.upsert(entry)

        return {
            "result": result,
            "summary": format_screening_summary(result),
            "matches": format_match_lines(result),
            "errors": format_error_lines(result),
            "cache_hit": False,
        }

    # ── 辅助方法 ──────────────────────────────────────────────

    def _compute_remaining_symbols(
        self,
        request: ScreeningRequest,
        processed_count: int,
    ) -> list[str]:
        """计算尚未处理的股票列表（按已处理数量偏移截取）"""
        pool = (
            self.engine.stock_pool_manager.get_pool_by_symbols(
                request.symbols, request.stock_pool_name
            )
            if request.symbols
            else self.engine.stock_pool_manager.get_default_pool(
                request.stock_pool_name
            )
        )
        return pool.symbols[processed_count:]

    @staticmethod
    def _wrap_progress_for_incremental(
        original_callback: Callable[[dict], None] | None,
        already_processed: int,
        total: int,
        already_matched: int,
        already_errors: int,
    ) -> Callable[[dict], None] | None:
        """包装进度回调，使增量选股的进度基于全局总量"""
        if original_callback is None:
            return None

        def wrapped(payload: dict) -> None:
            original_callback({
                "current": already_processed + payload.get("current", 0),
                "total": total,
                "symbol": payload.get("symbol", ""),
                "matched": already_matched + payload.get("matched", 0),
                "errors": already_errors + payload.get("errors", 0),
            })

        return wrapped

    def _build_cache_entry(
        self,
        request: ScreeningRequest,
        result: ScreeningResult,
        tdx_hash: str,
        was_cancelled: bool,
    ) -> ScreeningCacheEntry:
        """从选股结果构建缓存条目（精简存储）"""
        processed_count = len(result.matches) + len(result.errors)
        matched_symbols = [
            {"symbol": m.symbol, "name": m.name}
            for m in result.matches if m.matched
        ]
        now = now_iso()
        return ScreeningCacheEntry(
            target_date=request.target_date,
            template_id=request.template_id,
            template_name=request.template_name,
            tdx_source_hash=tdx_hash,
            stock_pool_name=request.stock_pool_name,
            status=CACHE_STATUS_INTERRUPTED if was_cancelled else CACHE_STATUS_COMPLETED,
            total=result.total,
            processed_count=processed_count,
            matched_symbols=matched_symbols,
            error_count=len(result.errors),
            created_at=now,
            updated_at=now,
        )

    def _merge_cache_entry(
        self,
        cached: ScreeningCacheEntry,
        incremental_result: ScreeningResult,
        cancelled_fn: Callable[[], bool] | None,
    ) -> ScreeningCacheEntry:
        """将增量选股结果合并到已有缓存条目"""
        new_processed_count = len(incremental_result.matches) + len(incremental_result.errors)
        new_matched = [
            {"symbol": m.symbol, "name": m.name}
            for m in incremental_result.matches if m.matched
        ]

        was_cancelled = cancelled_fn is not None and cancelled_fn()
        all_processed = cached.processed_count + new_processed_count
        is_completed = (not was_cancelled) and (all_processed >= cached.total)

        cached.processed_count = all_processed
        cached.matched_symbols = cached.matched_symbols + new_matched
        cached.error_count = cached.error_count + len(incremental_result.errors)
        cached.status = CACHE_STATUS_COMPLETED if is_completed else CACHE_STATUS_INTERRUPTED
        cached.updated_at = now_iso()

        return cached

    @staticmethod
    def _rebuild_result_from_cache(
        entry: ScreeningCacheEntry,
        request: ScreeningRequest,
    ) -> ScreeningResult:
        """从缓存条目重建 ScreeningResult 对象（仅包含匹配的股票）"""
        matches = tuple(
            ScreeningMatch(
                symbol=item["symbol"],
                name=item.get("name", ""),
                requested_date=entry.target_date,
                actual_date=entry.target_date,
                matched=True,
            )
            for item in entry.matched_symbols
        )
        return ScreeningResult(
            request=request,
            matches=matches,
            errors=(),
            total=entry.total,
            matched_count=entry.matched_count,
        )