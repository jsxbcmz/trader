from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from core.data.time_index import TIME_MODE_EXACT, TIME_MODE_ON_OR_BEFORE
from core.models.screening import ScreeningRequest


@pytest.fixture
def sample_daily_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "date": pd.to_datetime(["2026-03-24", "2026-03-25", "2026-03-27"]),
            "open": [10.0, 10.5, 11.0],
            "high": [10.2, 10.8, 11.3],
            "low": [9.8, 10.1, 10.7],
            "close": [10.1, 10.7, 11.2],
            "volume": [1000.0, 1200.0, 1800.0],
        }
    )


@pytest.fixture
def screening_request() -> ScreeningRequest:
    return ScreeningRequest(
        tdx_source="选股:C > MA(C,5);",
        target_date="2026-03-27",
        time_mode=TIME_MODE_EXACT,
        include_debug=True,
    )


@pytest.fixture
def temp_root(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    root.mkdir()
    (root / "stock_daily_data").mkdir()

    stocklist = pd.DataFrame(
        [
            {"symbol": "000001", "name": "平安银行", "ts_code": "000001.SZ", "industry": "银行", "area": "深圳", "market": "主板"},
            {"symbol": "000002", "name": "万科A", "ts_code": "000002.SZ", "industry": "地产", "area": "深圳", "market": "主板"},
        ]
    )
    stocklist.to_csv(root / "stocklist.csv", index=False, encoding="utf-8-sig")

    daily_one = pd.DataFrame(
        {
            "date": ["2026-03-24", "2026-03-25", "2026-03-27"],
            "open": [10.0, 10.5, 11.0],
            "high": [10.2, 10.8, 11.3],
            "low": [9.8, 10.1, 10.7],
            "close": [10.1, 10.7, 11.2],
            "volume": [1000, 1200, 1800],
        }
    )
    daily_two = pd.DataFrame(
        {
            "date": ["2026-03-24", "2026-03-25", "2026-03-27"],
            "open": [8.0, 7.8, 7.6],
            "high": [8.1, 7.9, 7.7],
            "low": [7.9, 7.6, 7.4],
            "close": [8.0, 7.7, 7.5],
            "volume": [900, 850, 800],
        }
    )
    daily_one.to_csv(root / "stock_daily_data" / "000001.csv", index=False, encoding="utf-8-sig")
    daily_two.to_csv(root / "stock_daily_data" / "000002.csv", index=False, encoding="utf-8-sig")

    return root


@pytest.fixture
def fallback_request() -> ScreeningRequest:
    return ScreeningRequest(
        tdx_source="选股:C > MA(C,5);",
        target_date="2026-03-28",
        time_mode=TIME_MODE_ON_OR_BEFORE,
        include_debug=False,
    )
