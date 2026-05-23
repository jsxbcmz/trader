from __future__ import annotations

import logging
from dataclasses import dataclass

from .requester import ApiResponse

logger = logging.getLogger(__name__)


@dataclass
class AnalysisReport:
    """汇总分析报告"""
    total_apis: int
    success_count: int
    fail_count: int
    success_rate: float
    average_elapsed_seconds: float
    slowest_api: str
    fastest_api: str
    failed_apis: list[str]


class DataAnalyzer:
    """数据分析模块，负责对接口响应数据进行汇总分析"""

    def analyze(self, responses: list[ApiResponse]) -> AnalysisReport:
        """对所有接口响应进行汇总分析"""
        total = len(responses)
        if total == 0:
            return AnalysisReport(
                total_apis=0, success_count=0, fail_count=0,
                success_rate=0.0, average_elapsed_seconds=0.0,
                slowest_api="N/A", fastest_api="N/A", failed_apis=[],
            )

        success_responses = [r for r in responses if r.success]
        failed_responses = [r for r in responses if not r.success]

        success_count = len(success_responses)
        fail_count = len(failed_responses)
        success_rate = (success_count / total) * 100

        elapsed_times = [r.elapsed_seconds for r in responses if r.elapsed_seconds > 0]
        average_elapsed = sum(elapsed_times) / len(elapsed_times) if elapsed_times else 0.0

        slowest = max(responses, key=lambda r: r.elapsed_seconds)
        fastest = min(responses, key=lambda r: r.elapsed_seconds if r.elapsed_seconds > 0 else float("inf"))

        return AnalysisReport(
            total_apis=total,
            success_count=success_count,
            fail_count=fail_count,
            success_rate=round(success_rate, 2),
            average_elapsed_seconds=round(average_elapsed, 3),
            slowest_api=f"{slowest.api_name} ({slowest.elapsed_seconds:.3f}s)",
            fastest_api=f"{fastest.api_name} ({fastest.elapsed_seconds:.3f}s)",
            failed_apis=[r.api_name for r in failed_responses],
        )

    def format_report(self, report: AnalysisReport) -> str:
        """格式化分析报告为字符串"""
        separator = "=" * 50
        lines = [
            f"\n{separator}",
            "           📊 接口采集分析报告",
            separator,
            f"  接口总数:       {report.total_apis}",
            f"  成功数量:       {report.success_count}",
            f"  失败数量:       {report.fail_count}",
            f"  成功率:         {report.success_rate}%",
            f"  平均耗时:       {report.average_elapsed_seconds}s",
            f"  最慢接口:       {report.slowest_api}",
            f"  最快接口:       {report.fastest_api}",
        ]

        if report.failed_apis:
            lines.append("  失败接口列表:")
            for api_name in report.failed_apis:
                lines.append(f"    - {api_name}")

        lines.append(separator)
        return "\n".join(lines)
