from __future__ import annotations

from core.screening.service import ScreeningService



def test_screening_service_main_flow(screening_request, temp_root):
    service = ScreeningService.from_root(temp_root)
    result = service.screen(screening_request)

    assert result.total == 2
    assert result.matched_count == 1
    assert len(result.matches) == 2
    assert len(result.errors) == 0

    match_map = {item.symbol: item for item in result.matches}
    assert match_map["000001"].matched is True
    assert match_map["000001"].actual_date == "2026-03-27"
    assert match_map["000002"].matched is False



def test_screening_service_summary_output(screening_request, temp_root):
    service = ScreeningService.from_root(temp_root)
    payload = service.screen_with_summary(screening_request)

    assert payload["result"].matched_count == 1
    assert any("000001" in line for line in payload["matches"])
    assert payload["errors"] == []
    assert "命中" in payload["summary"] or "matched" in payload["summary"].lower()
