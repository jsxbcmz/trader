from __future__ import annotations

from PySide6 import QtCore

from app.services.settings_service import AppSettings, SettingsService


def make_settings(scope: str) -> QtCore.QSettings:
    return QtCore.QSettings(QtCore.QSettings.IniFormat, QtCore.QSettings.UserScope, "StockViewerTests", scope)


def test_load_returns_defaults_when_empty():
    settings = make_settings("defaults")
    settings.clear()
    service = SettingsService(settings)

    loaded = service.load()

    assert isinstance(loaded, AppSettings)
    assert loaded.min_visible_days >= 1
    assert loaded.max_visible_days > loaded.min_visible_days
    assert loaded.last_selected_symbol == ""


def test_save_and_load_round_trip():
    settings = make_settings("roundtrip")
    settings.clear()
    service = SettingsService(settings)

    saved = service.save(
        AppSettings(
            tushare_token="  token-123  ",
            min_visible_days=45,
            max_visible_days=160,
            last_selected_symbol="123",
        )
    )
    loaded = service.load()

    assert saved.tushare_token == "token-123"
    assert saved.last_selected_symbol == "000123"
    assert loaded == saved


def test_invalid_chart_limits_are_rejected():
    settings = make_settings("invalid-limits")
    settings.clear()
    service = SettingsService(settings)

    try:
        service.validate_settings("token", 20, 20)
    except ValueError as exc:
        assert "最大可见天数必须大于最小可见天数" in str(exc)
    else:
        raise AssertionError("Expected ValueError for invalid chart limits")


def test_last_selected_symbol_round_trips():
    settings = make_settings("last-symbol")
    settings.clear()
    service = SettingsService(settings)

    saved = service.save_last_selected_symbol("1")

    assert saved == "000001"
    assert service.get_last_selected_symbol() == "000001"
