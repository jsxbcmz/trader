from __future__ import annotations

from core.data.time_index import TIME_MODE_EXACT, TIME_MODE_ON_OR_BEFORE
from core.models.screening import ScreeningRequest
from core.screening.service import ScreeningService



def test_screening_service_exact_time_mode_misses_non_trading_day(ma_condition, temp_root):
    service = ScreeningService.from_root(temp_root)
    request = ScreeningRequest(
        condition=ma_condition,
        target_date="2026-03-28",
        time_mode=TIME_MODE_EXACT,
        include_debug=False,
    )

    result = service.screen(request)

    assert result.total == 2
    assert result.matched_count == 0
    assert len(result.errors) == 0
    assert all(item.actual_date == "" for item in result.matches)
    assert all(item.matched is False for item in result.matches)
    assert all("无交易数据" in item.reason for item in result.matches)



def test_screening_service_on_or_before_time_mode_falls_back(fallback_request, temp_root):
    service = ScreeningService.from_root(temp_root)
    result = service.screen(fallback_request)

    assert result.total == 2
    assert result.matched_count == 1

    match_map = {item.symbol: item for item in result.matches}
    assert match_map["000001"].actual_date == "2026-03-27"
    assert match_map["000001"].matched is True
    assert "最近交易日" in match_map["000001"].reason
    assert match_map["000002"].actual_date == "2026-03-27"
    assert "最近交易日" in match_map["000002"].reason
