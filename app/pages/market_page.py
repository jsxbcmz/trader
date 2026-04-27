from __future__ import annotations

from pathlib import Path

from PySide6 import QtCore, QtWidgets

from app.services import AppSettings, SettingsService
from app.utils import start_worker

try:
    from pypinyin import Style, lazy_pinyin
except ImportError:
    Style = None
    lazy_pinyin = None

from ..data_loader import load_daily_csv, load_stock_list
from ..history_updater import HistoryUpdater
from ..tushare_client import TushareClient, TushareClientError
from ..chart_layout import DEFAULT_SUB_CHARTS, SubChartSelector, SubChartType
from ..widgets import StockChartWidget, UpdateProgressDialog


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


class MarketPage(QtWidgets.QWidget):
    statusMessageRequested = QtCore.Signal(str, int)
    updateRunningChanged = QtCore.Signal(bool)

    def __init__(self, root: Path, settings_service: SettingsService, parent: QtWidgets.QWidget | None = None):
        super().__init__(parent)
        self.root = root
        self.settings_service = settings_service
        self.stocklist_csv = self.root / "stocklist.csv"
        self.stock_daily_data_dir = self.root / "stock_daily_data"
        app_settings = self.settings_service.load()
        self._last_selected_symbol = app_settings.last_selected_symbol
        self._tushare_token = app_settings.tushare_token
        self._chart_min_visible_days = app_settings.min_visible_days
        self._chart_max_visible_days = app_settings.max_visible_days
        self._update_thread = None
        self._update_worker = None
        self._progress_dialog = None
        self.df_list = load_stock_list(self.stocklist_csv)
        self.df_list["name_initials"] = self.df_list["name"].apply(build_name_initials)
        self.df_list = self.df_list.reset_index(drop=True)

        self._setup_ui()
        self._connect_signals()

        self.filtered = self.df_list.copy()
        self.populate_table(self.filtered)
        self._restore_or_select_default()

    def _setup_ui(self):
        mainLayout = QtWidgets.QHBoxLayout(self)

        left = QtWidgets.QWidget()
        leftLayout = QtWidgets.QVBoxLayout(left)

        self.updateAllBtn = QtWidgets.QPushButton("更新全部股票")
        leftLayout.addWidget(self.updateAllBtn)

        self.search = QtWidgets.QLineEdit()
        self.search.setPlaceholderText("搜索：代码/名称/行业/地区（空格分词）")
        leftLayout.addWidget(self.search)

        self.table = QtWidgets.QTableWidget()
        self.table.setColumnCount(3)
        self.table.setHorizontalHeaderLabels(["代码", "名称", "行业"])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        leftLayout.addWidget(self.table, 1)

        self.chart = StockChartWidget()
        self.chart.set_visible_day_limits(self._chart_min_visible_days, self._chart_max_visible_days)
        self.chart.onHover.connect(self.on_hover)

        self.subChartSelector = SubChartSelector()
        saved_types = self._load_sub_chart_selection()
        if saved_types:
            self.subChartSelector.set_selected(saved_types)
            self.chart.set_visible_sub_charts(saved_types)
        self.subChartSelector.selectionChanged.connect(self._on_sub_chart_changed)

        chartToolbar = QtWidgets.QHBoxLayout()
        chartToolbar.setContentsMargins(0, 0, 0, 0)
        chartToolbar.addStretch(1)
        chartToolbar.addWidget(self.subChartSelector)

        rightWidget = QtWidgets.QWidget()
        rightLayout = QtWidgets.QVBoxLayout(rightWidget)
        rightLayout.setContentsMargins(0, 0, 0, 0)
        rightLayout.setSpacing(2)
        rightLayout.addLayout(chartToolbar)
        rightLayout.addWidget(self.chart, 1)

        splitter = QtWidgets.QSplitter()
        splitter.addWidget(left)
        splitter.addWidget(rightWidget)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 3)
        splitter.setSizes([320, 880])

        mainLayout.addWidget(splitter)

    def _connect_signals(self):
        self.search.textChanged.connect(self.apply_filter)
        self.table.itemSelectionChanged.connect(self.on_select)
        self.updateAllBtn.clicked.connect(self.start_update_all)

    def _show_status_message(self, message: str, timeout: int = 0):
        self.statusMessageRequested.emit(message, timeout)

    def apply_settings(self, app_settings: AppSettings):
        self._tushare_token = app_settings.tushare_token
        self._chart_min_visible_days = app_settings.min_visible_days
        self._chart_max_visible_days = app_settings.max_visible_days
        self.chart.set_visible_day_limits(self._chart_min_visible_days, self._chart_max_visible_days)

    def _on_sub_chart_changed(self, selected: list[SubChartType]):
        self.chart.set_visible_sub_charts(selected)
        from PySide6.QtCore import QSettings
        settings = QSettings()
        settings.setValue("chart/visible_sub_charts", ",".join(str(t) for t in selected))
        settings.sync()

    @staticmethod
    def _load_sub_chart_selection() -> list[SubChartType] | None:
        from PySide6.QtCore import QSettings
        settings = QSettings()
        raw = settings.value("chart/visible_sub_charts", None)
        if not raw:
            return None
        names = [s.strip() for s in str(raw).split(",") if s.strip()]
        types = []
        for name in names:
            try:
                types.append(SubChartType(name))
            except ValueError:
                pass
        return types if types else None

    def _set_update_controls_enabled(self, enabled: bool):
        self.updateAllBtn.setEnabled(enabled)
        self.updateRunningChanged.emit(not enabled)

    def _select_symbol_in_table(self, symbol: str) -> bool:
        target = str(symbol or "").strip()
        if not target:
            return False
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item is not None and item.text() == target:
                self.table.selectRow(row)
                return True
        return False

    def _restore_or_select_default(self):
        if self.table.rowCount() <= 0:
            return
        if self._select_symbol_in_table(self._last_selected_symbol):
            return
        self.table.selectRow(0)

    def populate_table(self, df):
        self.table.setRowCount(len(df))
        for r, (_, row) in enumerate(df.iterrows()):
            symbol = str(row["symbol"]).zfill(6)
            name = str(row.get("name", ""))
            industry = str(row.get("industry", ""))

            self.table.setItem(r, 0, QtWidgets.QTableWidgetItem(symbol))
            self.table.setItem(r, 1, QtWidgets.QTableWidgetItem(name))
            self.table.setItem(r, 2, QtWidgets.QTableWidgetItem(industry))

        self.table.resizeColumnsToContents()

    def apply_filter(self, *_):
        text = self.search.text().strip().lower()

        df = self.df_list

        if text:
            parts = [p for p in text.split() if p]
            for p in parts:
                mask = (
                    df["symbol"].astype(str).str.lower().str.contains(p, na=False)
                    | df["name"].astype(str).str.contains(p, na=False)
                    | df["name_initials"].astype(str).str.contains(p, na=False)
                    | df["ts_code"].astype(str).str.lower().str.contains(p, na=False)
                    | df["industry"].astype(str).str.contains(p, na=False)
                    | df["area"].astype(str).str.contains(p, na=False)
                )
                df = df[mask]

        self.filtered = df.copy()
        self.populate_table(self.filtered)
        self._restore_or_select_default()

    def on_select(self):
        r = self.table.currentRow()
        if r < 0:
            return
        symbol_item = self.table.item(r, 0)
        if symbol_item is None:
            return
        symbol = symbol_item.text()
        self._load_symbol(symbol)

    def _load_symbol(self, symbol: str):
        """统一的股票选中与图表加载入口"""
        self._last_selected_symbol = str(symbol).zfill(6)

        stock_name = self._find_stock_name(symbol)
        self.chart.set_stock_info(self._last_selected_symbol, stock_name)

        try:
            df_daily = load_daily_csv(self.stock_daily_data_dir, symbol)
            self.chart.set_daily(df_daily)
            self._show_status_message(f"{symbol}  共 {len(df_daily)} 条日线", 2000)
        except Exception:
            self._show_status_message(f"{symbol} 暂无本地日线，可先执行更新", 3000)

    def _find_stock_name(self, symbol: str) -> str:
        """从股票列表表格中查找股票名称。"""
        target = str(symbol).zfill(6)
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item and item.text() == target:
                name_item = self.table.item(row, 1)
                return name_item.text() if name_item else ""
        return ""

    def start_update_all(self):
        self._start_update()

    def _start_update(self):
        if self._update_thread is not None:
            QtWidgets.QMessageBox.information(self, "提示", "已有更新任务正在进行中")
            return

        self._progress_dialog = UpdateProgressDialog(self.window() if isinstance(self.window(), QtWidgets.QWidget) else self)
        self._progress_dialog.cancelRequested.connect(self._cancel_update)
        self._progress_dialog.show()
        self._set_update_controls_enabled(False)

        self._update_worker = UpdateWorker(
            self.stocklist_csv,
            self.stock_daily_data_dir,
            token=self._tushare_token,
        )
        self._update_thread = start_worker(
            self,
            self._update_worker,
            on_progress=self._on_update_progress,
            on_finished=self._on_update_finished,
            on_error=self._on_update_error,
            on_cleanup=self._cleanup_update_thread,
        )

    def _cancel_update(self):
        if self._update_worker is not None:
            self._update_worker.cancel()
            if self._progress_dialog is not None:
                self._progress_dialog.mark_cancel_requested()
            self._show_status_message("已请求取消更新任务", 3000)

    def _on_update_progress(self, payload: dict):
        if self._progress_dialog is not None:
            self._progress_dialog.update_progress(payload)
        current = payload.get("current", 0)
        total = payload.get("total", 0)
        symbol = payload.get("symbol", "")
        success = payload.get("success", 0)
        skipped = payload.get("skipped", 0)
        failed = payload.get("failed", 0)
        stage_text = str(payload.get("stage_text", "") or payload.get("stage", "处理中"))
        self._show_status_message(
            f"更新中 {current}/{total} | 成功 {success} | 跳过 {skipped} | 失败 {failed} | {stage_text} | 当前 {symbol}"
        )

    def _on_update_finished(self, payload: dict):
        summary = payload["summary"]
        if self._progress_dialog is not None:
            self._progress_dialog.accept()
        self._show_status_message(
            f"批量更新完成 | 成功 {summary.success} | 跳过 {summary.skipped} | 失败 {summary.failed}",
            8000,
        )
        QtWidgets.QMessageBox.information(
            self,
            "批量更新完成",
            f"总数: {summary.total}\n成功: {summary.success}\n跳过: {summary.skipped}\n失败: {summary.failed}\n取消: {'是' if summary.cancelled else '否'}",
        )
        if self._last_selected_symbol:
            try:
                self.on_select()
            except Exception:
                pass

    def _on_update_error(self, message: str):
        if self._progress_dialog is not None:
            self._progress_dialog.mark_finished()
        self._show_status_message(f"更新失败：{message}", 8000)
        QtWidgets.QMessageBox.warning(self, "更新失败", message)

    def _cleanup_update_thread(self):
        self._update_thread = None
        self._update_worker = None
        self._set_update_controls_enabled(True)

    def on_hover(self, info: dict):
        d = info.get("date")
        ds = d.strftime("%Y-%m-%d") if hasattr(d, "strftime") else str(d)

        msg = (
            f"{ds}  "
            f"O:{info['open']:.2f} "
            f"H:{info['high']:.2f} "
            f"L:{info['low']:.2f} "
            f"C:{info['close']:.2f} "
            f"成交额:{info['amount_yi']:.4f}亿"
        )
        self._show_status_message(msg)

    def persist_page_state(self):
        if self._last_selected_symbol:
            self.settings_service.save_last_selected_symbol(self._last_selected_symbol)
