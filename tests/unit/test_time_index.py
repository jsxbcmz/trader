from __future__ import annotations

import pandas as pd
import pytest

from core.data.time_index import locate_time_index


def test_locate_time_index_exact_match(sample_daily_frame: pd.DataFrame):
    result = locate_time_index(sample_daily_frame, "2026-03-25")
    assert result.matched is True
    assert result.actual_date == "2026-03-25"
    assert result.index == 1


def test_locate_time_index_no_match(sample_daily_frame: pd.DataFrame):
    result = locate_time_index(sample_daily_frame, "2026-03-26")
    assert result.matched is False
    assert result.actual_date is None


def test_locate_time_index_empty_frame():
    df = pd.DataFrame(columns=["date"])
    result = locate_time_index(df, "2026-03-25")
    assert result.matched is False
    assert result.reason == "日线数据为空"


def test_locate_time_index_missing_date_column(sample_daily_frame: pd.DataFrame):
    with pytest.raises(ValueError, match="date"):
        locate_time_index(sample_daily_frame.drop(columns=["date"]), "2026-03-25")
