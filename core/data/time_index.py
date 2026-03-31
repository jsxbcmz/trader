from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


TIME_MODE_EXACT = "exact"
TIME_MODE_ON_OR_BEFORE = "on_or_before"
SUPPORTED_TIME_MODES = {TIME_MODE_EXACT, TIME_MODE_ON_OR_BEFORE}


@dataclass(frozen=True, slots=True)
class TimeIndexResult:
    requested_date: str
    actual_date: str | None
    index: int | None
    matched: bool
    fallback_used: bool = False
    reason: str = ""


def _normalize_date(value: object) -> pd.Timestamp:
    ts = pd.Timestamp(value)
    return ts.normalize()


def locate_time_index(df: pd.DataFrame, target_date: object, mode: str = TIME_MODE_EXACT) -> TimeIndexResult:
    if mode not in SUPPORTED_TIME_MODES:
        raise ValueError(f"不支持的时间定位模式: {mode}")
    if df is None or df.empty:
        return TimeIndexResult(str(target_date), None, None, False, False, "日线数据为空")
    if "date" not in df.columns:
        raise ValueError("日线数据缺少 date 列")

    requested = _normalize_date(target_date)
    dates = pd.to_datetime(df["date"], errors="coerce").dt.normalize()
    valid_mask = dates.notna()
    if not valid_mask.any():
        return TimeIndexResult(requested.strftime("%Y-%m-%d"), None, None, False, False, "无有效日期数据")

    dates = dates[valid_mask]
    valid_indices = df.index[valid_mask]

    exact_matches = dates[dates == requested]
    if not exact_matches.empty:
        idx = int(valid_indices[exact_matches.index[0]])
        return TimeIndexResult(requested.strftime("%Y-%m-%d"), requested.strftime("%Y-%m-%d"), idx, True, False, "")

    if mode == TIME_MODE_EXACT:
        return TimeIndexResult(requested.strftime("%Y-%m-%d"), None, None, False, False, "指定日期无交易数据")

    candidates = dates[dates <= requested]
    if candidates.empty:
        return TimeIndexResult(requested.strftime("%Y-%m-%d"), None, None, False, False, "指定日期之前无可用交易数据")

    actual = candidates.iloc[-1]
    idx = int(valid_indices[candidates.index[-1]])
    return TimeIndexResult(
        requested.strftime("%Y-%m-%d"),
        actual.strftime("%Y-%m-%d"),
        idx,
        True,
        actual != requested,
        "" if actual == requested else "已回退到最近交易日",
    )
