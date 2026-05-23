"""砖形图定式选股主流程。

- screen_with_indicators  基于已计算指标做单日检测
- screen_single_stock     单股检测入口（计算指标 + 检测）
- _worker_screen_stock    多进程 worker
- BrickPatternEngine      并行批量选股引擎
"""

from __future__ import annotations

import os
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from core.data.repository import StockRepository
from core.data.time_index import locate_time_index
from core.models.brick_pattern import (
    BrickPatternMatch,
    BrickPatternRequest,
    BrickPatternResult,
    PatternType,
    ScoreBreakdown,
)
from core.stock_pool.manager import StockPoolManager

from .helpers import _calc_indicators, check_prerequisites
from .detectors import (
    detect_n_shape_jump,
    detect_sideways_jump,
    detect_uptrend_continue,
)
from .scoring import (
    compute_common_quality_score,
    compute_macd_auxiliary_score,
    compute_risk_penalty,
    compute_signal_strength_score,
)

DEFAULT_PROGRESS_INTERVAL = 20
DEFAULT_MAX_WORKERS = max(1, (os.cpu_count() or 2) - 1)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 选股入口
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def screen_single_stock(
    df: pd.DataFrame,
    index: int,
    symbol: str,
    name: str,
    target_date: str,
    actual_date: str,
    enabled_patterns: tuple[PatternType, ...],
    price_limit: float = 0.0,
) -> BrickPatternMatch:
    """对单只股票执行完整的砖形图定式选股流程。"""
    if len(df) < 10:
        return BrickPatternMatch(
            symbol=symbol, name=name, target_date=target_date,
            actual_date=actual_date, error="数据不足(少于10条)",
        )

    indicators = _calc_indicators(df)
    return screen_with_indicators(
        indicators=indicators,
        index=index,
        symbol=symbol,
        name=name,
        target_date=target_date,
        actual_date=actual_date,
        enabled_patterns=enabled_patterns,
        price_limit=price_limit,
    )

def screen_with_indicators(
    indicators: dict[str, np.ndarray],
    index: int,
    symbol: str,
    name: str,
    target_date: str,
    actual_date: str,
    enabled_patterns: tuple[PatternType, ...],
    price_limit: float = 0.0,
    cs_pcts: dict[str, float] | None = None,
) -> BrickPatternMatch:
    """基于已经预计算好的指标执行单日定式检测（V3评分）。

    cs_pcts: P1-2 截面归一化分位字典。含则 compute_common_quality_score 走分位查表，否则走绝对阈值。
    """
    close_arr = indicators["close"]
    if index < 0 or index >= len(close_arr) or len(close_arr) < 10:
        return BrickPatternMatch(
            symbol=symbol, name=name, target_date=target_date,
            actual_date=actual_date, error="数据不足(少于10条)",
        )

    close_val = float(close_arr[index])
    if price_limit > 0 and close_val > price_limit:
        return BrickPatternMatch(
            symbol=symbol, name=name, target_date=target_date,
            actual_date=actual_date,
            prerequisite_detail=f"股价{close_val:.2f}超过限制{price_limit:.0f}",
        )

    # ── 步骤1：必备前提检测 ──
    prereq_passed, prereq_detail = check_prerequisites(indicators, index)
    if not prereq_passed:
        return BrickPatternMatch(
            symbol=symbol, name=name, target_date=target_date,
            actual_date=actual_date,
            prerequisite_passed=False,
            prerequisite_detail=prereq_detail,
        )

    # ── 步骤2：三种定式检测 ──
    pattern_detectors = {
        PatternType.N_SHAPE_JUMP: detect_n_shape_jump,
        PatternType.SIDEWAYS_JUMP: detect_sideways_jump,
        PatternType.UPTREND_CONTINUE: detect_uptrend_continue,
    }

    pattern_results = []
    for pt in enabled_patterns:
        detector = pattern_detectors.get(pt)
        if detector is None:
            continue
        result = detector(indicators, index)
        pattern_results.append(result)

    matched_results = [r for r in pattern_results if r.matched]

    if not matched_results:
        return BrickPatternMatch(
            symbol=symbol, name=name, target_date=target_date,
            actual_date=actual_date,
            prerequisite_passed=True,
            prerequisite_detail="前提通过",
            pattern_matches=tuple(pattern_results),
        )

    # ── 步骤3-5：对每个匹配的定式计算完整分数，取最高 ──
    best_match_result = None
    best_breakdown = None
    best_final = -1.0

    signal_score, signal_items = compute_signal_strength_score(indicators, index)

    for match_r in matched_results:
        specific_score = match_r.score
        specific_items = match_r.extra.get("specific_items", {})

        common_score, common_items = compute_common_quality_score(
            indicators, index, match_r.pattern_type, cs_pcts=cs_pcts,
        )

        macd_score, macd_items = compute_macd_auxiliary_score(
            indicators, index, match_r.pattern_type,
        )

        risk_penalty, risk_items, risk_details_list = compute_risk_penalty(
            indicators, index, match_r.pattern_type,
        )

        # P3 战法加分（红柱比 / 地量 / 金叉时间）
        from .scoring import compute_p3_bonus
        bonus_score, bonus_items = compute_p3_bonus(
            indicators, index, match_r.pattern_type,
        )

        breakdown = ScoreBreakdown(
            specific_score=specific_score,
            specific_items=specific_items,
            common_score=common_score,
            common_items=common_items,
            macd_score=macd_score,
            macd_items=macd_items,
            signal_score=signal_score,
            signal_items=signal_items,
            risk_penalty=risk_penalty,
            risk_items=risk_items,
            bonus_score=bonus_score,
            bonus_items=bonus_items,
        )

        if breakdown.final_score > best_final:
            best_final = breakdown.final_score
            best_breakdown = breakdown
            best_match_result = match_r
            best_risk_details = risk_details_list

    triggered_risks = [r for r in best_risk_details if r.triggered]
    risk_reason = "; ".join(r.description for r in triggered_risks) if triggered_risks else ""

    return BrickPatternMatch(
        symbol=symbol, name=name, target_date=target_date,
        actual_date=actual_date,
        prerequisite_passed=True,
        prerequisite_detail="前提通过",
        pattern_matches=tuple(pattern_results),
        risk_filters=tuple(best_risk_details),
        final_matched=True,
        matched_pattern=best_match_result.pattern_type.value,
        risk_rejected=False,
        risk_reason=risk_reason,
        final_score=best_breakdown.final_score,
        grade=best_breakdown.grade,
        score_breakdown=best_breakdown,
    )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 多进程 worker + 引擎
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _worker_screen_stock(args: tuple) -> dict:
    """进程池工作函数：处理单只股票的砖形图定式选股。"""
    (root_str, symbol, stock_name, target_date, enabled_pattern_values, price_limit) = args

    try:
        from core.data.database import init_databases
        init_databases(Path(root_str))

        repository = StockRepository(Path(root_str))
        df = repository.get_daily_frame(symbol)
        time_result = locate_time_index(df, target_date)

        if not time_result.matched or time_result.index is None:
            return {
                "symbol": symbol,
                "name": stock_name,
                "target_date": time_result.requested_date,
                "actual_date": time_result.actual_date or "",
                "error": f"日期未匹配: {time_result.reason}",
            }

        enabled_patterns = tuple(PatternType(v) for v in enabled_pattern_values)

        match = screen_single_stock(
            df=df,
            index=time_result.index,
            symbol=symbol,
            name=stock_name,
            target_date=time_result.requested_date,
            actual_date=time_result.actual_date or "",
            enabled_patterns=enabled_patterns,
            price_limit=price_limit,
        )

        result = {
            "symbol": match.symbol,
            "name": match.name,
            "target_date": match.target_date,
            "actual_date": match.actual_date,
            "prerequisite_passed": match.prerequisite_passed,
            "prerequisite_detail": match.prerequisite_detail,
            "final_matched": match.final_matched,
            "matched_pattern": match.matched_pattern,
            "risk_rejected": match.risk_rejected,
            "risk_reason": match.risk_reason,
            "error": match.error,
            "summary": match.format_summary(),
            "final_score": match.final_score,
            "grade": match.grade,
        }

        if match.score_breakdown is not None:
            result["score_breakdown"] = match.score_breakdown.to_dict()

        return result
    except Exception as exc:
        return {
            "symbol": symbol,
            "name": stock_name,
            "target_date": target_date,
            "actual_date": "",
            "error": str(exc),
        }

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 引擎主类
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@dataclass
class BrickPatternEngine:
    """砖形图交易定式选股引擎。"""

    repository: StockRepository
    stock_pool_manager: StockPoolManager
    progress_interval: int = DEFAULT_PROGRESS_INTERVAL
    max_workers: int = DEFAULT_MAX_WORKERS

    @classmethod
    def from_root(cls, root: Path) -> BrickPatternEngine:
        repository = StockRepository(root)
        stock_pool_manager = StockPoolManager(repository)
        return cls(repository=repository, stock_pool_manager=stock_pool_manager)

    def run(
        self,
        request: BrickPatternRequest,
        progress_callback: Callable[[dict], None] | None = None,
        cancelled_fn: Callable[[], bool] | None = None,
    ) -> BrickPatternResult:
        """执行砖形图定式选股。"""
        pool = (
            self.stock_pool_manager.get_pool_by_symbols(request.symbols, request.stock_pool_name)
            if request.symbols
            else self.stock_pool_manager.get_default_pool(request.stock_pool_name)
        )

        stock_map = {stock.symbol: stock for stock in pool.stocks}
        total = len(pool.symbols)
        interval = max(1, self.progress_interval)

        if progress_callback is not None and total > 0:
            progress_callback({
                "current": 0,
                "total": total,
                "symbol": "",
                "matched": 0,
                "errors": 0,
            })

        enabled_pattern_values = tuple(p.value for p in request.enabled_patterns)
        root_str = str(self.repository.root)

        task_args = [
            (
                root_str,
                symbol,
                stock_map.get(symbol).name if stock_map.get(symbol) else "",
                request.target_date,
                enabled_pattern_values,
                request.price_limit,
            )
            for symbol in pool.symbols
        ]

        all_matches: list[BrickPatternMatch] = []
        matched_count = 0
        risk_filtered_count = 0
        error_count = 0
        completed = 0

        with ProcessPoolExecutor(max_workers=self.max_workers) as executor:
            futures = {executor.submit(_worker_screen_stock, args): args[1] for args in task_args}

            for future in as_completed(futures):
                completed += 1
                result = future.result()

                if result.get("error"):
                    error_count += 1
                    all_matches.append(BrickPatternMatch(
                        symbol=result["symbol"],
                        name=result.get("name", ""),
                        target_date=result.get("target_date", ""),
                        actual_date=result.get("actual_date", ""),
                        error=result["error"],
                    ))
                else:
                    bd = None
                    if "score_breakdown" in result and result["score_breakdown"]:
                        bd = ScoreBreakdown.from_dict(result["score_breakdown"])

                    match = BrickPatternMatch(
                        symbol=result["symbol"],
                        name=result.get("name", ""),
                        target_date=result.get("target_date", ""),
                        actual_date=result.get("actual_date", ""),
                        prerequisite_passed=result.get("prerequisite_passed", False),
                        prerequisite_detail=result.get("prerequisite_detail", ""),
                        final_matched=result.get("final_matched", False),
                        matched_pattern=result.get("matched_pattern", ""),
                        risk_rejected=result.get("risk_rejected", False),
                        risk_reason=result.get("risk_reason", ""),
                        final_score=result.get("final_score", 0.0),
                        grade=result.get("grade", ""),
                        score_breakdown=bd,
                    )
                    all_matches.append(match)

                    if match.final_matched:
                        matched_count += 1
                    if match.risk_rejected:
                        risk_filtered_count += 1

                if progress_callback is not None and (completed % interval == 0 or completed == total):
                    progress_callback({
                        "current": completed,
                        "total": total,
                        "symbol": result["symbol"],
                        "matched": matched_count,
                        "errors": error_count,
                    })

                if cancelled_fn is not None and cancelled_fn():
                    for pending_future in futures:
                        pending_future.cancel()
                    break

        return BrickPatternResult(
            request=request,
            matches=tuple(all_matches),
            total=total,
            matched_count=matched_count,
            risk_filtered_count=risk_filtered_count,
            error_count=error_count,
        )
