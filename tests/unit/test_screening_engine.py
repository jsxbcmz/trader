from __future__ import annotations

import pandas as pd
import pytest

from core.models.screening import ScreeningRequest
from core.screening.engine import ScreeningEngine


def test_screening_engine_runs_default_pool(screening_request: ScreeningRequest, temp_root):
    engine = ScreeningEngine.from_root(temp_root)
    result = engine.run(screening_request)

    assert result.total == 2
    assert len(result.matches) == 2
    assert len(result.errors) == 0

    match_map = {item.symbol: item for item in result.matches}
    assert match_map["000001"].actual_date == "2026-03-27"
    assert match_map["000001"].name == "平安银行"
    assert match_map["000002"].actual_date == "2026-03-27"


def test_screening_engine_collects_symbol_errors(screening_request: ScreeningRequest, temp_root):
    csv_path = temp_root / "stock_daily_data" / "000002.csv"
    pd.DataFrame(columns=["open", "high", "low", "close", "volume"]).to_csv(
        csv_path, index=False, encoding="utf-8-sig"
    )

    engine = ScreeningEngine.from_root(temp_root)
    result = engine.run(screening_request)

    assert result.total == 2
    assert len(result.matches) == 1
    assert result.matches[0].symbol == "000001"
    assert len(result.errors) == 1
    assert result.errors[0].symbol == "000002"
    assert result.errors[0].stage == "engine"


def test_screening_engine_raise_policy_propagates_error(screening_request: ScreeningRequest, temp_root):
    csv_path = temp_root / "stock_daily_data" / "000002.csv"
    pd.DataFrame(columns=["open", "high", "low", "close", "volume"]).to_csv(
        csv_path, index=False, encoding="utf-8-sig"
    )

    engine = ScreeningEngine.from_root(temp_root)
    engine.error_policy = "raise"

    with pytest.raises(ValueError, match="date"):
        engine.run(screening_request)
