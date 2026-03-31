from __future__ import annotations

from pathlib import Path

from PySide6 import QtCore, QtWidgets

from app.services import AppSettings, SettingsService
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
        settingsLayout = QtWidgets.QFormLayout(self.settingsGroup)
        self.tokenEdit = QtWidgets.QLineEdit(self._tushare_token)
        self.tokenEdit.setPlaceholderText("请输入 Tushare Token")
        self.minDaysSpin = QtWidgets.QSpinBox()
        self.minDaysSpin.setRange(1, 10000)
        self.minDaysSpin.setValue(self._chart_min_visible_days)
        self.maxDaysSpin = QtWidgets.QSpinBox()
        self.maxDaysSpin.setRange(2, 10000)
        self.maxDaysSpin.setValue(self._chart_max_visible_days)
        self.saveSettingsBtn = QtWidgets.QPushButton("保存配置")
        settingsLayout.addRow("Tushare Token", self.tokenEdit)
        settingsLayout.addRow("最小可见天数", self.minDaysSpin)
        settingsLayout.addRow("最大可见天数", self.maxDaysSpin)
        settingsLayout.addRow("", self.saveSettingsBtn)
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
        self.screeningModeBox = QtWidgets.QComboBox()
        self.screeningModeBox.addItems(["exact", "on_or_before"])
        self.screeningRunBtn = QtWidgets.QPushButton("执行选股")
        screeningLayout.addRow("条件模板", self.screeningPresetBox)
        screeningLayout.addRow("目标日期", self.screeningDateEdit)
        screeningLayout.addRow("时间模式", self.screeningModeBox)
        screeningLayout.addRow("", self.screeningRunBtn)
        leftLayout.addWidget(self.screeningGroup)

        self.screeningResultTable = QtWidgets.QTableWidget()
        self.screeningResultTable.setColumnCount(6)
        self.screeningResultTable.setHorizontalHeaderLabels(["代码", "名称", "请求日期", "实际日期", "命中", "说明"])
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
        self.table.itemDoubleClicked.connect(lambda _: self.on_select())
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
        if hasattr(self, "tokenEdit"):
            self.tokenEdit.setText(self._tushare_token)
        if hasattr(self, "minDaysSpin"):
            self.minDaysSpin.setValue(self._chart_min_visible_days)
        if hasattr(self, "maxDaysSpin"):
            self.maxDaysSpin.setValue(self._chart_max_visible_days)

    def _toggle_settings_panel(self):
        visible = not self.settingsGroup.isVisible()
        self.settingsGroup.setVisible(visible)
        self.settingsToggleBtn.setText("收起设置" if visible else "展开设置")

    def _save_settings_from_panel(self):
        token = self.tokenEdit.text().strip()
        min_days = self.minDaysSpin.value()
        max_days = self.maxDaysSpin.value()

        try:
            app_settings = self.settings_service.normalize_settings(
                token=token,
                min_days=min_days,
                max_days=max_days,
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
            time_mode=self.screeningModeBox.currentText().strip() or "exact",
        )

    def populate_screening_results(self, result):
        self._screening_results = list(result.matches)
        self.screeningResultTable.setRowCount(len(self._screening_results))
        for row, item in enumerate(self._screening_results):
            values = [
                item.symbol,
                item.name,
                item.requested_date,
                item.actual_date,
                "是" if item.matched else "否",
                item.reason,
            ]
            for col, value in enumerate(values):
                self.screeningResultTable.setItem(row, col, QtWidgets.QTableWidgetItem(str(value or "")))
        self.screeningResultTable.resizeColumnsToContents()

    def run_screening(self):
        try:
            request = self._build_screening_request()
            payload = self.screening_service.screen_with_summary(request)
            result = payload["result"]
            self.populate_screening_results(result)
            self._show_status_message(payload["summary"], 5000)
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "选股失败", str(exc))
            self._show_status_message(f"选股失败：{exc}", 5000)

    def on_screening_result_select(self):
        row = self.screeningResultTable.currentRow()
        if row < 0 or row >= len(self._screening_results):
            return
        symbol = self._screening_results[row].symbol
        self._select_symbol_in_table(symbol)

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

        self._update_thread = QtCore.QThread(self)
        self._update_worker = UpdateWorker(
            self.stocklist_csv,
            self.stock_daily_data_dir,
            token=token,
        )
        self._update_worker.moveToThread(self._update_thread)
        self._update_thread.started.connect(self._update_worker.run)
        self._update_worker.progressChanged.connect(self._on_update_progress)
        self._update_worker.finished.connect(self._on_update_finished)
        self._update_worker.errorOccurred.connect(self._on_update_error)
        self._update_worker.finished.connect(self._update_thread.quit)
        self._update_worker.finished.connect(self._update_worker.deleteLater)
        self._update_thread.finished.connect(self._update_thread.deleteLater)
        self._update_thread.finished.connect(self._cleanup_update_thread)
        self._update_thread.start()

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
            self._progress_dialog.mark_finished()
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
