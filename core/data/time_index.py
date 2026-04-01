from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True, slots=True)
class TimeIndexResult:
    requested_date: str
    actual_date: str | None
    index: int | None
    matched: bool
    reason: str = ""


def _normalize_date(value: object) -> pd.Timestamp:
    ts = pd.Timestamp(value)
    return ts.normalize()


def locate_time_index(df: pd.DataFrame, target_date: object) -> TimeIndexResult:
    """定位目标日期在数据中的索引位置
    
    Args:
        df: 日线数据 DataFrame，必须包含 date 列
        target_date: 目标日期
        
    Returns:
        TimeIndexResult: 包含定位结果的信息
    """
    if df is None or df.empty:
        return TimeIndexResult(str(target_date), None, None, False, "日线数据为空")
    if "date" not in df.columns:
        raise ValueError("日线数据缺少 date 列")

    requested = _normalize_date(target_date)
    dates = pd.to_datetime(df["date"], errors="coerce").dt.normalize()
    valid_mask = dates.notna()
    if not valid_mask.any():
        return TimeIndexResult(requested.strftime("%Y-%m-%d"), None, None, False, "无有效日期数据")

    dates = dates[valid_mask]
    valid_indices = df.index[valid_mask]

    exact_matches = dates[dates == requested]
    if not exact_matches.empty:
        idx = int(valid_indices[exact_matches.index[0]])
        return TimeIndexResult(requested.strftime("%Y-%m-%d"), requested.strftime("%Y-%m-%d"), idx, True, "")

    return TimeIndexResult(requested.strftime("%Y-%m-%d"), None, None, False, "指定日期无交易数据")
