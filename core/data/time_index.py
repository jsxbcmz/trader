from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

@dataclass(frozen=True)
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


# ── 预处理日期索引（回测性能优化） ──────────────────────────────


def build_date_index(df: pd.DataFrame) -> dict[str, int]:
    """为 DataFrame 预构建 日期字符串 → 行索引 的映射字典。

    一次性完成 pd.to_datetime 转换，后续查找只需 O(1) 的 dict 查询，
    避免 locate_time_index 每次调用都重复解析整列日期。

    Args:
        df: 日线数据 DataFrame，必须包含 date 列

    Returns:
        dict 映射：``{"2024-01-02": 0, "2024-01-03": 1, ...}``
    """
    if df is None or df.empty or "date" not in df.columns:
        return {}

    dates = pd.to_datetime(df["date"], errors="coerce")
    valid_mask = dates.notna()
    date_strings = dates[valid_mask].dt.strftime("%Y-%m-%d")
    valid_indices = df.index[valid_mask]

    return dict(zip(date_strings, (int(i) for i in valid_indices)))


def locate_time_index_fast(
    date_index: dict[str, int],
    target_date: str,
) -> TimeIndexResult:
    """基于预构建的日期索引进行 O(1) 快速定位。

    与 ``locate_time_index`` 返回相同的 ``TimeIndexResult``，
    但跳过了 pd.to_datetime 等重复计算，适合在回测主循环中高频调用。

    Args:
        date_index: 由 ``build_date_index`` 预构建的日期→索引映射
        target_date: 目标日期字符串，格式 ``YYYY-MM-DD``

    Returns:
        TimeIndexResult
    """
    # 标准化日期字符串格式（处理可能的 Timestamp 输入）
    if not isinstance(target_date, str):
        target_date = pd.Timestamp(target_date).strftime("%Y-%m-%d")

    idx = date_index.get(target_date)
    if idx is not None:
        return TimeIndexResult(target_date, target_date, idx, True, "")

    return TimeIndexResult(target_date, None, None, False, "指定日期无交易数据")