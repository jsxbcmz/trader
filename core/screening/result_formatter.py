from __future__ import annotations

from core.models.screening import ScreeningResult



def format_screening_summary(result: ScreeningResult) -> str:
    return (
        f"选股完成：总数 {result.total}，"
        f"命中 {result.matched_count}，"
        f"错误 {len(result.errors)}"
    )



def format_match_lines(result: ScreeningResult, matched_only: bool = False) -> list[str]:
    lines: list[str] = []
    for item in result.matches:
        if matched_only and not item.matched:
            continue
        line = (
            f"{item.symbol} {item.name} | matched={item.matched} | "
            f"requested={item.requested_date} | actual={item.actual_date or '-'} | {item.reason}"
        )
        lines.append(line)
    return lines



def format_error_lines(result: ScreeningResult) -> list[str]:
    return [f"{item.symbol} | {item.stage} | {item.message}" for item in result.errors]
