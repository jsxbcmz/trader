from __future__ import annotations

from dataclasses import dataclass

from PySide6 import QtCore

from app.tushare_client import DEFAULT_TUSHARE_TOKEN
from app.widgets import StockChartWidget


@dataclass(frozen=True, slots=True)
class AppSettings:
    tushare_token: str
    min_visible_days: int
    max_visible_days: int
    last_selected_symbol: str = ""


class SettingsService:
    def __init__(self, settings: QtCore.QSettings | None = None):
        self.settings = settings or QtCore.QSettings("StockViewer", "StockViewer")

    def load(self) -> AppSettings:
        token = str(self.settings.value("tushare_token", "") or "").strip()
        if not token and DEFAULT_TUSHARE_TOKEN:
            token = str(DEFAULT_TUSHARE_TOKEN).strip()
            if token:
                self.settings.setValue("tushare_token", token)
                self.settings.sync()

        min_days = self._read_int("chart/min_visible_days", StockChartWidget.DEFAULT_MIN_VISIBLE_DAYS)
        max_days = self._read_int("chart/max_visible_days", StockChartWidget.DEFAULT_MAX_VISIBLE_DAYS)
        last_selected_symbol = self._normalize_symbol(self.settings.value("last_selected_symbol", ""))

        min_days = max(min_days, 1)
        if max_days <= min_days:
            min_days = StockChartWidget.DEFAULT_MIN_VISIBLE_DAYS
            max_days = StockChartWidget.DEFAULT_MAX_VISIBLE_DAYS

        return AppSettings(
            tushare_token=token,
            min_visible_days=min_days,
            max_visible_days=max_days,
            last_selected_symbol=last_selected_symbol,
        )

    def save(self, settings: AppSettings) -> AppSettings:
        normalized = self.normalize_settings(
            token=settings.tushare_token,
            min_days=settings.min_visible_days,
            max_days=settings.max_visible_days,
            last_selected_symbol=settings.last_selected_symbol,
        )
        self.settings.setValue("tushare_token", normalized.tushare_token)
        self.settings.setValue("chart/min_visible_days", normalized.min_visible_days)
        self.settings.setValue("chart/max_visible_days", normalized.max_visible_days)
        self.settings.setValue("last_selected_symbol", normalized.last_selected_symbol)
        self.settings.sync()
        return normalized

    def validate_settings(self, token: str, min_days: int, max_days: int) -> None:
        token = str(token or "").strip()
        if not token:
            raise ValueError("Tushare Token 不能为空。")
        if int(min_days) < 1:
            raise ValueError("最小可见天数必须大于等于 1。")
        if int(max_days) <= int(min_days):
            raise ValueError("最大可见天数必须大于最小可见天数。")

    def normalize_settings(
        self,
        *,
        token: str,
        min_days: int,
        max_days: int,
        last_selected_symbol: str = "",
    ) -> AppSettings:
        normalized_token = str(token or "").strip()
        normalized_min_days = int(min_days)
        normalized_max_days = int(max_days)
        self.validate_settings(normalized_token, normalized_min_days, normalized_max_days)
        return AppSettings(
            tushare_token=normalized_token,
            min_visible_days=normalized_min_days,
            max_visible_days=normalized_max_days,
            last_selected_symbol=self._normalize_symbol(last_selected_symbol),
        )

    def get_last_selected_symbol(self) -> str:
        return self._normalize_symbol(self.settings.value("last_selected_symbol", ""))

    def save_last_selected_symbol(self, symbol: str) -> str:
        normalized = self._normalize_symbol(symbol)
        self.settings.setValue("last_selected_symbol", normalized)
        self.settings.sync()
        return normalized

    def get_tushare_token(self) -> str:
        return self.load().tushare_token

    def get_chart_limits(self) -> tuple[int, int]:
        settings = self.load()
        return settings.min_visible_days, settings.max_visible_days

    def _read_int(self, key: str, default: int) -> int:
        try:
            return int(self.settings.value(key, default))
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _normalize_symbol(symbol) -> str:
        text = str(symbol or "").strip()
        if not text:
            return ""
        return text.zfill(6)
