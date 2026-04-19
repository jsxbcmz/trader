"""砖形图交易定式选股服务。

封装 BrickPatternEngine，提供进度回调和格式化输出。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from core.models.brick_pattern import (
    BrickPatternMatch,
    BrickPatternRequest,
    BrickPatternResult,
)
from core.screening.brick_pattern_engine import BrickPatternEngine


@dataclass
class BrickPatternService:
    """砖形图定式选股服务。"""

    engine: BrickPatternEngine

    @classmethod
    def from_root(cls, root: Path) -> BrickPatternService:
        return cls(engine=BrickPatternEngine.from_root(root))

    def screen(
        self,
        request: BrickPatternRequest,
        progress_callback: Callable[[dict], None] | None = None,
        cancelled_fn: Callable[[], bool] | None = None,
    ) -> BrickPatternResult:
        return self.engine.run(
            request,
            progress_callback=progress_callback,
            cancelled_fn=cancelled_fn,
        )

    def screen_with_summary(
        self,
        request: BrickPatternRequest,
        progress_callback: Callable[[dict], None] | None = None,
        cancelled_fn: Callable[[], bool] | None = None,
    ) -> dict:
        result = self.screen(request, progress_callback=progress_callback, cancelled_fn=cancelled_fn)
        return {
            "result": result,
            "summary": format_result_summary(result),
            "match_lines": format_match_lines(result),
            "filtered_lines": format_filtered_lines(result),
        }


def format_result_summary(result: BrickPatternResult) -> str:
    """格式化选股结果摘要。"""
    lines = [
        f"砖形图定式选股完成",
        f"  扫描股票: {result.total} 只",
        f"  命中定式: {result.matched_count} 只",
        f"  风险过滤: {result.risk_filtered_count} 只",
        f"  处理错误: {result.error_count} 只",
    ]
    return "\n".join(lines)


def format_match_lines(result: BrickPatternResult) -> list[str]:
    """格式化命中结果列表。"""
    lines = []
    for match in result.hit_matches:
        lines.append(f"{match.symbol} {match.name} [{match.matched_pattern}] {match.actual_date}")
    return lines


def format_filtered_lines(result: BrickPatternResult) -> list[str]:
    """格式化被风险过滤的结果列表。"""
    lines = []
    for match in result.filtered_matches:
        lines.append(
            f"{match.symbol} {match.name} [{match.matched_pattern}] "
            f"被过滤: {match.risk_reason}"
        )
    return lines
