"""看盘页的后台 worker 与小工具：UpdateWorker / IndustryDownloadWorker / build_name_initials。"""

from __future__ import annotations

from pathlib import Path

from PySide6 import QtCore

try:
    from pypinyin import Style, lazy_pinyin
except ImportError:
    Style = None
    lazy_pinyin = None

from ..history_updater import HistoryUpdater
from ..tushare_client import TushareClient, TushareClientError


class UpdateWorker(QtCore.QObject):
    progressChanged = QtCore.Signal(dict)
    finished = QtCore.Signal(dict)
    errorOccurred = QtCore.Signal(str)

    def __init__(self, stocklist_csv: Path, stock_daily_data_dir: Path, token: str | None = None):
        super().__init__()
        self.stocklist_csv = stocklist_csv
        self.stock_daily_data_dir = stock_daily_data_dir
        self.token = token
        self._cancelled = False

    @QtCore.Slot()
    def run(self):
        try:
            client = TushareClient(token=self.token) if self.token else TushareClient.from_env()
            updater = HistoryUpdater(self.stocklist_csv, self.stock_daily_data_dir, client=client)
            results, summary = updater.update_all_symbols(
                progress_callback=lambda payload: self.progressChanged.emit(payload),
                stop_checker=lambda: self._cancelled,
            )
            self.finished.emit({"results": results, "summary": summary})
        except (TushareClientError, Exception) as exc:
            self.errorOccurred.emit(str(exc))

    def cancel(self):
        self._cancelled = True


def build_name_initials(value) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if lazy_pinyin is None or Style is None:
        return ""
    return "".join(lazy_pinyin(text, style=Style.FIRST_LETTER)).lower()


class IndustryDownloadWorker(QtCore.QObject):
    finished = QtCore.Signal(dict)
    errorOccurred = QtCore.Signal(str)

    def __init__(
        self,
        sw_code: str,
        industry: str,
        industry_daily_data_dir: Path,
        stocklist_csv: Path,
        stock_daily_data_dir: Path,
        token: str | None = None,
    ):
        super().__init__()
        self.sw_code = sw_code
        self.industry = industry
        self.industry_daily_data_dir = industry_daily_data_dir
        self.stocklist_csv = stocklist_csv
        self.stock_daily_data_dir = stock_daily_data_dir
        self.token = token

    @QtCore.Slot()
    def run(self):
        try:
            client = TushareClient(token=self.token) if self.token else TushareClient.from_env()
            updater = HistoryUpdater(
                stocklist_csv=self.stocklist_csv,
                stock_daily_data_dir=self.stock_daily_data_dir,
                client=client,
                industry_daily_data_dir=self.industry_daily_data_dir,
            )
            result = updater.update_industry(self.sw_code, self.industry)
            self.finished.emit({"sw_code": self.sw_code, "industry": self.industry})
        except Exception as exc:
            self.errorOccurred.emit(str(exc))
            self.finished.emit({})

