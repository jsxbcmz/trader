"""主板评分调度引擎（P0-2a + P1-2 截面归一化集成）。

P0-2 阶段：复用 BrickPatternEngine 的多进程并行 + V4 评分。
P1-2 阶段：先跑 CrossSectionStats 算截面分位，再用自己的 worker
把每只票的 cs_pcts 传到 screen_with_indicators（compute_common_quality_score
里 3 个待归一化因子走分位查表）。

数据有效性（原 P0-2b）已由现有机制自然覆盖：
- OHLC 字段 NaN：load_daily_csv() 的 dropna 在加载时剔除
- 当日停牌（无记录）：locate_time_index 返回未匹配
- volume=0：实测主板 113 万行数据频率为 0
"""

from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

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
from core.screening.brick_pattern import BrickPatternEngine
from core.screening.brick_pattern.helpers import _calc_indicators
from core.screening.brick_pattern.pipeline import screen_with_indicators
from core.scoring.cross_section import CrossSectionStats
from core.scoring.main_board_pool import MainBoardPool


# ── 自有 Worker（带 cs_pcts）─────────────────────────────


def _scoring_worker(args: tuple) -> dict:
    """主板评分 worker：复用 screen_with_indicators，把 cs_pcts 传下去。"""
    root_str, symbol, name, target_date, enabled_pattern_values, cs_pcts = args
    try:
        repository = StockRepository(Path(root_str))
        df = repository.get_daily_frame(symbol)
        time_result = locate_time_index(df, target_date)

        if not time_result.matched or time_result.index is None:
            return {
                "symbol": symbol, "name": name,
                "target_date": time_result.requested_date,
                "actual_date": time_result.actual_date or "",
                "error": f"日期未匹配: {time_result.reason}",
            }

        if len(df) < 10:
            return {
                "symbol": symbol, "name": name,
                "target_date": time_result.requested_date,
                "actual_date": time_result.actual_date or "",
                "error": "数据不足(少于10条)",
            }

        indicators = _calc_indicators(df)
        enabled_patterns = tuple(PatternType(v) for v in enabled_pattern_values)

        match = screen_with_indicators(
            indicators=indicators,
            index=time_result.index,
            symbol=symbol,
            name=name,
            target_date=time_result.requested_date,
            actual_date=time_result.actual_date or "",
            enabled_patterns=enabled_patterns,
            price_limit=0.0,
            cs_pcts=cs_pcts,
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
            "final_score": match.final_score,
            "grade": match.grade,
        }
        if match.score_breakdown is not None:
            result["score_breakdown"] = match.score_breakdown.to_dict()
        return result
    except Exception as exc:
        return {
            "symbol": symbol, "name": name,
            "target_date": target_date, "actual_date": "",
            "error": f"{type(exc).__name__}: {exc}",
        }


# ── 主引擎 ──────────────────────────────────────────────


@dataclass
class MainBoardScoringEngine:
    repository: StockRepository
    main_board_pool: MainBoardPool = field(init=False)
    cross_section: CrossSectionStats = field(init=False)
    brick_engine: BrickPatternEngine
    max_workers: int = 8
    use_cross_section: bool = True

    def __post_init__(self):
        self.main_board_pool = MainBoardPool(repository=self.repository)
        self.cross_section = CrossSectionStats(repository=self.repository, max_workers=self.max_workers)

    @classmethod
    def from_root(cls, root: Path, use_cross_section: bool = True) -> "MainBoardScoringEngine":
        repository = StockRepository(root=root)
        return cls(
            repository=repository,
            brick_engine=BrickPatternEngine.from_root(root),
            use_cross_section=use_cross_section,
        )

    def score_date(
        self,
        target_date: str,
        progress_callback: Callable[[dict], None] | None = None,
        cancelled_fn: Callable[[], bool] | None = None,
    ) -> BrickPatternResult:
        """对指定日期跑主板全量评分。

        当 use_cross_section=True 时，先跑 CrossSectionStats 算分位（P1-1）+ 缓存，
        然后传给评分 worker（P1-2）。否则直接走原 BrickPatternEngine 路径。
        """
        if not self.use_cross_section:
            return self._run_via_brick_engine(target_date, progress_callback, cancelled_fn)

        cs_df = self.cross_section.compute_and_save(target_date)
        return self._run_with_cs_pcts(target_date, cs_df, progress_callback, cancelled_fn)

    # ── 内部 ────────────────────────────────────────────

    def _run_via_brick_engine(
        self,
        target_date: str,
        progress_callback: Callable[[dict], None] | None,
        cancelled_fn: Callable[[], bool] | None,
    ) -> BrickPatternResult:
        """走原 BrickPatternEngine（无截面分位）— 用于关闭归一化时对比。"""
        candidates = self.main_board_pool.list_active()
        symbols = tuple(s.symbol for s in candidates)
        request = BrickPatternRequest(
            target_date=target_date,
            stock_pool_name="main_board",
            symbols=symbols,
        )
        return self.brick_engine.run(
            request,
            progress_callback=progress_callback,
            cancelled_fn=cancelled_fn,
        )

    def _run_with_cs_pcts(
        self,
        target_date: str,
        cs_df: pd.DataFrame,
        progress_callback: Callable[[dict], None] | None,
        cancelled_fn: Callable[[], bool] | None,
    ) -> BrickPatternResult:
        """带 cs_pcts 的多进程评分。"""
        candidates = self.main_board_pool.list_active()
        # 把 cs_df 转成 symbol → pcts dict 加速查询
        cs_map: dict[str, dict[str, float]] = {}
        if not cs_df.empty:
            for _, row in cs_df.iterrows():
                cs_map[row["symbol"]] = {
                    "day_change_pct": float(row["day_change_pct"]),
                    "force_ratio_pct": float(row["force_ratio_pct"]),
                    "short_trend_slope_pct": float(row["short_trend_slope_pct"]),
                }

        enabled_pattern_values = (
            PatternType.N_SHAPE_JUMP.value,
            PatternType.SIDEWAYS_JUMP.value,
            PatternType.UPTREND_CONTINUE.value,
        )
        root_str = str(self.repository.root)
        task_args = [
            (
                root_str, s.symbol, s.name, target_date,
                enabled_pattern_values, cs_map.get(s.symbol),
            )
            for s in candidates
        ]

        request = BrickPatternRequest(
            target_date=target_date,
            stock_pool_name="main_board",
            symbols=tuple(s.symbol for s in candidates),
        )
        total = len(task_args)

        if progress_callback is not None and total > 0:
            progress_callback({"current": 0, "total": total, "matched": 0, "errors": 0})

        all_matches: list[BrickPatternMatch] = []
        matched_count = error_count = 0
        completed = 0

        with ProcessPoolExecutor(max_workers=self.max_workers) as executor:
            futures = {executor.submit(_scoring_worker, a): a[1] for a in task_args}
            for future in as_completed(futures):
                completed += 1
                if cancelled_fn is not None and cancelled_fn():
                    break
                result = future.result()
                match = _result_to_match(result)
                all_matches.append(match)
                if match.error:
                    error_count += 1
                if match.final_matched:
                    matched_count += 1
                if progress_callback is not None and completed % 20 == 0:
                    progress_callback({
                        "current": completed, "total": total,
                        "matched": matched_count, "errors": error_count,
                    })

        return BrickPatternResult(
            request=request,
            matches=tuple(all_matches),
            total=total,
            matched_count=matched_count,
            error_count=error_count,
        )


def _result_to_match(result: dict) -> BrickPatternMatch:
    bd = None
    if "score_breakdown" in result and result["score_breakdown"]:
        bd = ScoreBreakdown.from_dict(result["score_breakdown"])
    return BrickPatternMatch(
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
        error=result.get("error", ""),
    )
