from __future__ import annotations

from core.screening.service import ScreeningService


def test_screening_service_main_flow(screening_request, temp_root):
    service = ScreeningService.from_root(temp_root)
    result = service.screen(screening_request)

    assert result.total == 2
    assert len(result.matches) == 2
    assert len(result.errors) == 0

    match_map = {item.symbol: item for item in result.matches}
    assert match_map["000001"].actual_date == "2026-03-27"
    assert match_map["000002"].actual_date == "2026-03-27"


def test_screening_service_summary_output(screening_request, temp_root):
    service = ScreeningService.from_root(temp_root)
    payload = service.screen_with_summary(screening_request)

    assert payload["result"].total == 2
    assert payload["errors"] == []
