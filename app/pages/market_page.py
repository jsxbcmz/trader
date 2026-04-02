from __future__ import annotations

from pathlib import Path

from PySide6 import QtCore, QtWidgets

from app.components import SettingsFormWidget
from app.services import AppSettings, SettingsService
from app.utils import start_worker
from core.screening.service import ScreeningService
from core.templates import TemplateService

try:
    from pypinyin import Style, lazy_pinyin
except ImportError:
    Style = None
    lazy_pinyin = None

from ..data_loader import load_daily_csv, load_stock_list
from ..history_updater import HistoryUpdater
from ..tushare_client import TushareClient, TushareClientError
from ..widgets import ScreeningProgressDialog, StockChartWidget, UpdateProgressDialog


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


class ScreeningWorker(QtCore.QObject):
    """选股后台任务"""
    progressChanged = QtCore.Signal(dict)
    finished = QtCore.Signal(dict)
    errorOccurred = QtCore.Signal(str)

    def __init__(self, screening_service: ScreeningService, request):
        super().__init__()
        self.screening_service = screening_service
        self.request = request
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    @QtCore.Slot()
    def run(self):
        try:
            payload = self.screening_service.screen_with_cache(
                self.request,
                progress_callback=lambda p: self.progressChanged.emit(p),
                cancelled_fn=lambda: self._cancelled,
            )
            self.finished.emit(payload)
        except Exception as exc:
            self.errorOccurred.emit(str(exc))


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
        self._screening_thread = None
        self._screening_worker = None
        self._screening_progress_dialog = None
        self.screening_service = ScreeningService.from_root(self.root)
        self.template_service = TemplateService.from_root(self.root)
        self._screening_results = []

        self.df_list = load_stock_list(self.stocklist_csv)
        self.df_list["name_initials"] = self.df_list["name"].apply(build_name_initials)
        self.df_list = self.df_list.reset_index(drop=True)

        self._setup_ui()
        self._connect_signals()
        self.reload_templates()

        self.filtered = self.df_list.copy()
        self.populate_table(self.filtered)
        self._restore_or_select_default()

    def _setup_ui(self):
        mainLayout = QtWidgets.QHBoxLayout(self)

        left = QtWidgets.QWidget()
        leftLayout = QtWidgets.QVBoxLayout(left)

        actionLayout = QtWidgets.QHBoxLayout()
        self.settingsToggleBtn = QtWidgets.QPushButton("展开设置")
        self.updateAllBtn = QtWidgets.QPushButton("更新全部股票")
        actionLayout.addWidget(self.settingsToggleBtn)
        actionLayout.addWidget(self.updateAllBtn)
        leftLayout.addLayout(actionLayout)

        self.settingsGroup = QtWidgets.QGroupBox("配置")
        settings_inner = QtWidgets.QVBoxLayout(self.settingsGroup)
        self.settingsForm = SettingsFormWidget()
        self.settingsForm.set_values(
            self._tushare_token,
            self._chart_min_visible_days,
            self._chart_max_visible_days,
        )
        self.saveSettingsBtn = QtWidgets.QPushButton("保存配置")
        settings_inner.addWidget(self.settingsForm)
        settings_inner.addWidget(self.saveSettingsBtn)
        self.settingsGroup.setVisible(False)
        leftLayout.addWidget(self.settingsGroup)

        self.search = QtWidgets.QLineEdit()
        self.search.setPlaceholderText("搜索：代码/名称/行业/地区（空格分词）")
        leftLayout.addWidget(self.search)

        self.industryBox = QtWidgets.QComboBox()
        self.industryBox.addItem("全部行业")
        industries = sorted(set(self.df_list.get("industry").dropna().astype(str).tolist()))
        self.industryBox.addItems([i for i in industries if i and i != "nan"])
        leftLayout.addWidget(self.industryBox)

        self.screeningGroup = QtWidgets.QGroupBox("选股")
        screeningLayout = QtWidgets.QFormLayout(self.screeningGroup)
        self.screeningPresetBox = QtWidgets.QComboBox()
        self.screeningDateEdit = QtWidgets.QDateEdit(QtCore.QDate.currentDate())
        self.screeningDateEdit.setCalendarPopup(True)
        self.screeningDateEdit.setDisplayFormat("yyyy-MM-dd")
        self.screeningRunBtn = QtWidgets.QPushButton("执行选股")
        screeningLayout.addRow("条件模板", self.screeningPresetBox)
        screeningLayout.addRow("目标日期", self.screeningDateEdit)
        screeningLayout.addRow("", self.screeningRunBtn)
        leftLayout.addWidget(self.screeningGroup)

        self.screeningResultTable = QtWidgets.QTableWidget()
        self.screeningResultTable.setColumnCount(2)
        self.screeningResultTable.setHorizontalHeaderLabels(["代码", "名称"])
        self.screeningResultTable.horizontalHeader().setStretchLastSection(True)
        self.screeningResultTable.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.screeningResultTable.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.screeningResultTable.setMaximumHeight(220)
        leftLayout.addWidget(self.screeningResultTable)

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

        splitter = QtWidgets.QSplitter()
        splitter.addWidget(left)
        splitter.addWidget(self.chart)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 3)
        splitter.setSizes([320, 880])

        mainLayout.addWidget(splitter)

    def _connect_signals(self):
        self.search.textChanged.connect(self.apply_filter)
        self.industryBox.currentTextChanged.connect(self.apply_filter)
        self.table.itemSelectionChanged.connect(self.on_select)
        self.screeningRunBtn.clicked.connect(self.run_screening)
        self.screeningResultTable.itemSelectionChanged.connect(self.on_screening_result_select)
        self.settingsToggleBtn.clicked.connect(self._toggle_settings_panel)
        self.saveSettingsBtn.clicked.connect(self._save_settings_from_panel)
        self.updateAllBtn.clicked.connect(self.start_update_all)

    def reload_templates(self):
        current_template_id = self.current_template_id()
        templates = self.template_service.list_templates()
        self.screeningPresetBox.blockSignals(True)
        self.screeningPresetBox.clear()
        selected_index = -1
        for index, template in enumerate(templates):
            self.screeningPresetBox.addItem(template.name, template.id)
            if current_template_id and template.id == current_template_id:
                selected_index = index
        if selected_index >= 0:
            self.screeningPresetBox.setCurrentIndex(selected_index)
        elif templates:
            self.screeningPresetBox.setCurrentIndex(0)
        self.screeningPresetBox.blockSignals(False)

    def current_template_id(self) -> str:
        return str(self.screeningPresetBox.currentData() or "").strip()

    def _show_status_message(self, message: str, timeout: int = 0):
        self.statusMessageRequested.emit(message, timeout)

    def apply_settings(self, app_settings: AppSettings):
        self._tushare_token = app_settings.tushare_token
        self._chart_min_visible_days = app_settings.min_visible_days
        self._chart_max_visible_days = app_settings.max_visible_days
        self.chart.set_visible_day_limits(self._chart_min_visible_days, self._chart_max_visible_days)
        if hasattr(self, "settingsForm"):
            self.settingsForm.set_values(
                self._tushare_token,
                self._chart_min_visible_days,
                self._chart_max_visible_days,
            )

    def _toggle_settings_panel(self):
        visible = not self.settingsGroup.isVisible()
        self.settingsGroup.setVisible(visible)
        self.settingsToggleBtn.setText("收起设置" if visible else "展开设置")

    def _save_settings_from_panel(self):
        try:
            app_settings = self.settings_service.normalize_settings(
                token=self.settingsForm.get_token(),
                min_days=self.settingsForm.get_min_days(),
                max_days=self.settingsForm.get_max_days(),
                last_selected_symbol=self._last_selected_symbol,
            )
        except ValueError as exc:
            QtWidgets.QMessageBox.warning(self, "配置无效", str(exc))
            return

        saved_settings = self.settings_service.save(app_settings)
        self.apply_settings(saved_settings)
        self._show_status_message("设置已保存", 3000)

    def _get_runtime_token(self) -> str | None:
        token = str(self._tushare_token or "").strip()
        if token:
            return token
        return None

    def _set_update_controls_enabled(self, enabled: bool):
        self.updateAllBtn.setEnabled(enabled)
        self.screeningRunBtn.setEnabled(enabled)
        self.updateRunningChanged.emit(not enabled)

    def _build_screening_request(self):
        template_id = self.current_template_id()
        if not template_id:
            raise ValueError("请选择模板")
        qdate = self.screeningDateEdit.date()
        target_date = qdate.toString("yyyy-MM-dd")
        return self.template_service.build_screening_request(
            template_id,
            target_date,
        )

    def populate_screening_results(self, result):
        # 只显示命中的股票
        self._screening_results = [match for match in result.matches if match.matched]
        self.screeningResultTable.setRowCount(len(self._screening_results))
        for row, item in enumerate(self._screening_results):
            self.screeningResultTable.setItem(row, 0, QtWidgets.QTableWidgetItem(str(item.symbol)))
            self.screeningResultTable.setItem(row, 1, QtWidgets.QTableWidgetItem(str(item.name or "")))
        self.screeningResultTable.resizeColumnsToContents()

    def run_screening(self):
        if self._screening_thread is not None:
            QtWidgets.QMessageBox.information(self, "提示", "已有选股任务正在进行中")
            return

        try:
            request = self._build_screening_request()
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "选股失败", str(exc))
            return

        self._set_update_controls_enabled(False)

        # 创建并显示选股进度弹窗
        self._screening_progress_dialog = ScreeningProgressDialog(
            self.window() if isinstance(self.window(), QtWidgets.QWidget) else self
        )
        self._screening_progress_dialog.stopRequested.connect(self._on_screening_stop_requested)
        self._screening_progress_dialog.show()

        self._screening_worker = ScreeningWorker(self.screening_service, request)
        self._screening_thread = start_worker(
            self,
            self._screening_worker,
            on_progress=self._on_screening_progress,
            on_finished=self._on_screening_finished,
            on_error=self._on_screening_error,
            on_cleanup=self._cleanup_screening_thread,
        )

    def _on_screening_stop_requested(self):
        if self._screening_worker is not None:
            self._screening_worker.cancel()

    def _on_screening_progress(self, payload: dict):
        if self._screening_progress_dialog is not None:
            self._screening_progress_dialog.update_progress(payload)

    def _on_screening_finished(self, payload: dict):
        result = payload["result"]
        was_cancelled = self._screening_worker is not None and self._screening_worker._cancelled

        self.populate_screening_results(result)

        if was_cancelled:
            summary = f"选股已停止：已处理部分中命中 {result.matched_count} 只"
        else:
            summary = payload["summary"]

        self._show_status_message(summary, 5000)

        # 关闭进度弹窗
        if self._screening_progress_dialog is not None:
            if was_cancelled:
                self._screening_progress_dialog.mark_finished(summary)
            else:
                self._screening_progress_dialog.accept()

    def _on_screening_error(self, message: str):
        # 关闭进度弹窗
        if self._screening_progress_dialog is not None:
            self._screening_progress_dialog.mark_finished(f"选股失败：{message}")
        QtWidgets.QMessageBox.warning(self, "选股失败", message)
        self._show_status_message(f"选股失败：{message}", 5000)

    def _cleanup_screening_thread(self):
        self._screening_thread = None
        self._screening_worker = None
        self._screening_progress_dialog = None
        self._set_update_controls_enabled(True)

    def on_screening_result_select(self):
        row = self.screeningResultTable.currentRow()
        if row < 0 or row >= len(self._screening_results):
            return
        symbol = self._screening_results[row].symbol
        # 清除全部股票列表的选中状态，避免两个表同时高亮
        self.table.blockSignals(True)
        self.table.clearSelection()
        self.table.blockSignals(False)
        self._load_symbol(symbol)

    def _ensure_token(self) -> str | None:
        token = self._get_runtime_token()
        if token:
            return token
        QtWidgets.QMessageBox.warning(self, "缺少 Token", "未配置 Tushare Token，无法更新历史数据。")
        return None

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
        industry = self.industryBox.currentText().strip()

        df = self.df_list

        if industry and industry != "全部行业":
            df = df[df["industry"].astype(str) == industry]

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
        # 清除选股结果列表的选中状态，避免两个表同时高亮
        self.screeningResultTable.blockSignals(True)
        self.screeningResultTable.clearSelection()
        self.screeningResultTable.blockSignals(False)
        self._load_symbol(symbol)

    def _load_symbol(self, symbol: str):
        """统一的股票选中与图表加载入口"""
        self._last_selected_symbol = str(symbol).zfill(6)
        try:
            df_daily = load_daily_csv(self.stock_daily_data_dir, symbol)
            self.chart.set_daily(df_daily)
            self._show_status_message(f"{symbol}  共 {len(df_daily)} 条日线", 2000)
        except Exception:
            self._show_status_message(f"{symbol} 暂无本地日线，可先执行更新", 3000)

    def start_update_all(self, token: str | None = None):
        actual_token = str(token or "").strip() or self._ensure_token()
        if not actual_token:
            return
        self._start_update(token=actual_token)

    def _start_update(self, token: str):
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
            token=token,
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
